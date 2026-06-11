"""FastAPI router for the v4 connectors surface.

W2 C1 ships GET /api/v2/connectors (the Connectors page B1.3 reads
this on mount). W2 C2 (this slice) adds the five GitHub endpoints:
- POST /github/oauth/start          (admin+) → mint state, return install URL
- GET  /github/oauth/callback       (no scope; state token IS the auth)
- POST /github/install              (admin+) → re-confirm an installation
- DELETE /github                    (admin+) → disconnect
- POST /github/sync                 (member+) → trigger a sync run

The router is a factory because the dependencies (``_current_user``
and the ``current_workspace_member`` it returns) are closures over
the request-scoped store built inside ``create_app()``. Same
pattern as the workspaces router (see workspaces/router.py).

W2 watch points applied here:
- #1 CSRF: state-token verification in the callback compares the
  bound user_id to the current session's resolved user_id. Mismatch
  → redirect with reason=state_user_mismatch.
- #2 App JWT vs installation token: this module does NOT mint App
  JWTs directly. The /sync handler delegates to
  ``connectors.github.sync.sync_workspace`` which uses the
  app_jwt module for token mint and the client module for repo
  reads. Strict separation.
- #3 Webhooks: NOT in C2. The router exposes no webhook endpoint.
  W4/F9 adds it with HMAC-SHA256 signature verification.
- #4 Sync idempotency: /sync delegates to ``sync_workspace``,
  which calls ``connectors.store.upsert_repo_snapshot`` (INSERT...
  ON CONFLICT) — no duplicates on retry.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Callable

import posixpath
import uuid

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..agents import tiers
from ..feedback_items import cluster as feedback_cluster
from ..feedback_items import store as feedback_store
from ..feedback_items.embedding import (
    embed_texts_batch,
    is_embeddings_enabled,
)
from ..feedback_items.llm_classify import (
    ItemForClassify,
    classify_items_with_fallback,
    is_llm_enabled,
)
from ..feedback_items.llm_merge import merge_clusters_via_llm
from ..workspaces.models import Role, WorkspaceMember
from . import registry, store as connectors_store
from .base import ConnectorTier
from .github.app_jwt import GitHubAppConfig
from .github.oauth import (
    GitHubOAuthConfig,
    OAuthStateError,
    OAuthStateExpired,
    OAuthStateInvalidSignature,
    OAuthStateUserMismatch,
    build_install_url,
    consume_state_token,
    exchange_user_code,
    issue_state_token,
    load_app_config_from_env,
)
from .github.sync import sync_workspace
from .linear import client as linear_client
from .linear import sync as linear_sync

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


logger = logging.getLogger(__name__)

_CurrentWorkspaceMemberFactory = Callable[..., Callable[..., WorkspaceMember]]
_CurrentUserCallable = Callable[..., dict[str, Any]]


class GitHubInstallBody(BaseModel):
    """Re-confirm an existing installation. Used when the partner
    navigates back from GitHub to /connectors manually (without
    going through the OAuth callback path)."""

    installation_id: str


class GithubOAuthStartBody(BaseModel):
    """Optional caller hint for the post-OAuth redirect target.

    The wizard's Step 2 sets ``redirect_to="/onboarding?step=2"`` so
    the GitHub round-trip lands the user back inside the wizard
    rather than the default /connectors page. Path is allowlist-
    validated at issue time; invalid values fall through to the
    default redirect.
    """

    redirect_to: str | None = None


def _live_descriptor_payload(
    store: "PlanningStudioStore",
    *,
    descriptor,
    workspace_id: str,
) -> dict[str, Any]:
    """Compose the LIVE-tier tile payload (interactive, with state)."""
    if descriptor.provider == "github":
        state = connectors_store.state_for(
            store,
            workspace_id=workspace_id,
            provider=descriptor.provider,
        )
        state_payload: dict[str, Any] = {
            "status": state.status.value,
            "account": state.account,
            "primary_repo_full_name": state.primary_repo_full_name,
            "repo_count": state.repo_count,
            "last_sync_at": state.last_sync_at,
            "last_successful_sync_at": state.last_successful_sync_at,
            "last_error": state.last_error,
        }
        actions: dict[str, str] = {
            "connect": "/api/v2/connectors/github/oauth/start",
            "sync": "/api/v2/connectors/github/sync",
            "disconnect": "/api/v2/connectors/github",
        }
    elif descriptor.provider == "linear":
        # F4 wires Linear: state is real, identical shape to GitHub
        # so the FE tile component renders without a branch.
        state = connectors_store.state_for(
            store,
            workspace_id=workspace_id,
            provider="linear",
        )
        # Linear has no "primary repo" — re-purpose the field for
        # the workspace's account name + an item count from
        # feedback_items so the connected meta line reads
        # "Acme · 247 issues synced · last sync 4 min ago".
        counts = feedback_store.count_items(
            store, workspace_id=workspace_id, source="linear"
        )
        state_payload = {
            "status": state.status.value,
            "account": state.account,
            "primary_repo_full_name": None,
            "repo_count": counts.total,
            "last_sync_at": state.last_sync_at,
            "last_successful_sync_at": state.last_successful_sync_at,
            "last_error": state.last_error,
        }
        actions = {
            "connect": "/api/v2/connectors/linear/connect",
            "sync": "/api/v2/connectors/linear/sync",
            "disconnect": "/api/v2/connectors/linear",
        }
    elif descriptor.provider == "csv_json":
        # CSV / JSON paste-in is stateless from a credential
        # standpoint (no token to store). The "connected" state
        # really means "we've ingested at least one paste".
        counts = feedback_store.count_items(
            store, workspace_id=workspace_id, source="csv-import"
        )
        status_str = "connected" if counts.total > 0 else "not_connected"
        state_payload = {
            "status": status_str,
            "account": None,
            "primary_repo_full_name": None,
            "repo_count": counts.total,
            "last_sync_at": counts.last_ingested_at,
            "last_successful_sync_at": counts.last_ingested_at,
            "last_error": None,
        }
        actions = {
            "import": "/api/v2/connectors/csv/import",
        }
    else:
        # Catch-all for any future LIVE entry not yet wired —
        # render as idle-Live, no actions.
        state_payload = {
            "status": "not_implemented",
            "account": None,
            "primary_repo_full_name": None,
            "repo_count": 0,
            "last_sync_at": None,
            "last_successful_sync_at": None,
            "last_error": None,
        }
        actions = {}

    return {
        "provider": descriptor.provider,
        "display_name": descriptor.display_name,
        "summary": descriptor.summary,
        "logo_slug": descriptor.logo_slug,
        "state": state_payload,
        "actions": actions,
    }


def _coming_soon_payload(descriptor) -> dict[str, Any]:
    """Compose the coming-soon-tier tile payload (mailto only)."""
    return {
        "provider": descriptor.provider,
        "display_name": descriptor.display_name,
        "summary": descriptor.summary,
        "contact_route": descriptor.contact_route,
    }


def _future_payload(descriptor) -> dict[str, Any]:
    """Compose the future-tier tile payload (greyed, no actions)."""
    return {
        "provider": descriptor.provider,
        "display_name": descriptor.display_name,
        "summary": descriptor.summary,
    }


def _frontend_base_url(request: Request) -> str:
    """Mirror of auth._resolve_frontend_base_url, deferred-imported
    to avoid a top-level circular dep against auth.py."""
    from ..auth import _resolve_frontend_base_url  # noqa: PLC0415

    return _resolve_frontend_base_url(request)


def _connectors_redirect(
    request: Request,
    *,
    status: str,
    reason: str | None = None,
    redirect_to: str | None = None,
) -> RedirectResponse:
    """Build the Location URL the OAuth callback redirects to.

    Status / reason are FE-rendered:
    - status=connected           → toast "GitHub connected"
    - status=error reason=...    → inline error on the tile

    When ``redirect_to`` is supplied (bound into the state token at
    issue time), append the status to it instead of falling back to
    ``/connectors``. This lets the wizard's GitHub-OAuth round-trip
    return to ``/onboarding?step=2&status=connected`` rather than
    losing wizard state. The path is allowlist-validated at issue
    time (see ``oauth._validate_redirect_to``); we trust it here.
    """
    base = _frontend_base_url(request)
    target = redirect_to or "/connectors"
    # Preserve any existing query string in redirect_to.
    separator = "&" if "?" in target else "?"
    qs = f"{separator}status={status}"
    if reason:
        qs += f"&reason={reason}"
    return RedirectResponse(
        url=f"{base}{target}{qs}", status_code=303
    )


# Local-repo upload caps + filters (Onboarding Wizard Step 2 path B).
_LOCAL_REPO_PER_FILE_BYTES = 1 * 1024 * 1024  # 1 MB per file
_LOCAL_REPO_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB total
_LOCAL_REPO_EXCLUDE_DIRS = (
    ".git",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".cache",
    ".idea",
    ".vscode",
    "target",  # Rust / Java
    ".DS_Store",
)
_LOCAL_REPO_ALLOWED_EXTS = frozenset(
    {
        ".py", ".pyi", ".pyw",
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".go", ".rs", ".rb", ".java", ".kt", ".kts",
        ".swift", ".m", ".mm",
        ".c", ".cc", ".cpp", ".h", ".hh", ".hpp",
        ".cs", ".fs",
        ".php", ".scala", ".clj", ".ex", ".exs",
        ".css", ".scss", ".sass", ".less",
        ".html", ".htm", ".vue", ".svelte",
        ".md", ".mdx", ".rst", ".txt",
        ".yml", ".yaml", ".toml", ".json", ".sql",
        ".dockerfile", ".sh", ".bash", ".zsh",
        ".gitignore", ".env",  # bare .env not .env files w/ secrets
    }
)
_LOCAL_REPO_ALLOWED_BARENAMES = frozenset(
    {
        "Dockerfile",
        "Makefile",
        "README",
        "LICENSE",
        ".gitignore",
        ".env.example",
    }
)


def _local_repo_path_safe(rel_path: str) -> str | None:
    """Normalize + validate an uploaded file's relative path.

    Returns the safe normalized path on success, ``None`` if the
    path attempts traversal or is otherwise unsafe (audit concern
    #6 — path-traversal defense).
    """
    if not rel_path:
        return None
    # Reject absolute paths outright.
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        return None
    # Normalize via posixpath (browsers send / separators, even on
    # Windows — we don't want os.path.normpath messing with `\`).
    normalized = posixpath.normpath(rel_path.replace("\\", "/"))
    if normalized.startswith("..") or "/.." in normalized:
        return None
    if normalized == "." or not normalized:
        return None
    # Reject any segment matching an excluded dir.
    parts = normalized.split("/")
    for segment in parts:
        if segment in _LOCAL_REPO_EXCLUDE_DIRS:
            return None
    return normalized


def _local_repo_path_allowed(normalized: str) -> bool:
    """Should we keep this file based on extension + bare name?"""
    base = normalized.rsplit("/", 1)[-1]
    if base in _LOCAL_REPO_ALLOWED_BARENAMES:
        return True
    # Treat lockfiles + obvious binaries as skip.
    if base in {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
    }:
        return False
    # Extension allowlist.
    _, ext = posixpath.splitext(base)
    return ext.lower() in _LOCAL_REPO_ALLOWED_EXTS


async def _ingest_local_repo_files(
    files: list[UploadFile],
) -> tuple[dict[str, bytes], int, int]:
    """Read + filter the multipart upload.

    Returns (accepted: {normalized_path -> bytes}, skipped_count,
    total_bytes). Raises HTTPException(413) when any cap is breached.
    """
    accepted: dict[str, bytes] = {}
    skipped = 0
    total_bytes = 0
    for upload in files:
        # FastAPI surfaces the FE-supplied filename verbatim. The
        # browser populates this from `webkitRelativePath` when the
        # FE builds the FormData with the relative path.
        raw_name = upload.filename or ""
        safe_path = _local_repo_path_safe(raw_name)
        if safe_path is None:
            skipped += 1
            continue
        if not _local_repo_path_allowed(safe_path):
            skipped += 1
            continue
        content = await upload.read()
        if len(content) > _LOCAL_REPO_PER_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "file_too_large",
                    "path": safe_path,
                    "max_bytes": _LOCAL_REPO_PER_FILE_BYTES,
                },
            )
        total_bytes += len(content)
        if total_bytes > _LOCAL_REPO_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "upload_too_large",
                    "max_bytes": _LOCAL_REPO_TOTAL_BYTES,
                },
            )
        accepted[safe_path] = content
    return accepted, skipped, total_bytes


def make_connectors_router(
    store: "PlanningStudioStore",
    current_user: _CurrentUserCallable,
    current_workspace_member: _CurrentWorkspaceMemberFactory,
) -> APIRouter:
    """Build the ``/api/v2/connectors`` router with closed-over deps."""
    router = APIRouter(prefix="/api/v2/connectors", tags=["connectors"])

    def _require_github_config() -> tuple[GitHubAppConfig, GitHubOAuthConfig]:
        """Helper: fetch the env-backed GitHub App config or 503.

        Surfaced as 503 (not 500) so a deploy missing GitHub
        secrets doesn't crash the whole connectors page — the
        other connectors / coming-soon / future tiers still
        render. The FE renders the GitHub tile in an error
        state with this reason.
        """
        configs = load_app_config_from_env()
        if configs is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "github_not_configured",
                    "message": (
                        "GitHub App secrets are not set on the "
                        "deployment. Contact the workspace admin."
                    ),
                },
            )
        return configs

    @router.get("")
    def list_connectors_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        """Return the three-tier connector list for the current
        workspace.

        Drives the Connectors page (B1.3). LIVE tier carries
        runtime state (connected / not_connected / needs_reauth /
        error / not_implemented); COMING_SOON carries mailto
        contact routes; FUTURE is greyed.
        """
        ws_id = member.workspace_id
        return {
            "live": [
                _live_descriptor_payload(
                    store, descriptor=d, workspace_id=ws_id
                )
                for d in registry.LIVE
            ],
            "coming_soon": [
                _coming_soon_payload(d) for d in registry.COMING_SOON
            ],
            "future": [
                _future_payload(d) for d in registry.FUTURE
            ],
        }

    # -----------------------------------------------------------
    # GitHub App OAuth — 5 endpoints (W2 C2)
    # -----------------------------------------------------------

    @router.post("/github/oauth/start")
    def github_oauth_start_route(
        body: GithubOAuthStartBody | None = None,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, str]:
        """Mint a state token + return the GitHub install URL.

        Admin+ scope: connecting a third-party data source is a
        workspace-admin action (members can't add new sources
        unilaterally).

        ``redirect_to`` (optional body field): a FE path to bounce
        back to after the install completes. Allowlisted to
        ``/connectors`` and ``/onboarding`` (with optional query
        string) — see ``oauth._validate_redirect_to``. The wizard
        uses this to return to ``/onboarding?step=2`` mid-flow.
        Falls through to the default ``/connectors`` redirect when
        absent or invalid.
        """
        app_config, oauth_config = _require_github_config()
        redirect_to = body.redirect_to if body else None
        state = issue_state_token(
            user_id=member.user_id,
            workspace_id=member.workspace_id,
            session_secret=oauth_config.session_secret,
            redirect_to=redirect_to,
        )
        install_url = build_install_url(
            app_slug=app_config.app_slug, state=state
        )
        return {
            "install_url": install_url,
            "state_token": state,
        }

    @router.get("/github/oauth/callback")
    async def github_oauth_callback_route(
        request: Request,
        background_tasks: BackgroundTasks,
        state: str | None = None,
        code: str | None = None,
        installation_id: str | None = None,
        setup_action: str | None = None,
        error: str | None = None,
        user: dict = Depends(current_user),
    ) -> RedirectResponse:
        """Handle GitHub's redirect after the install flow.

        Verifies the state token (CSRF gate), exchanges the OAuth
        code for a user-token, persists the credential row, kicks
        off an initial sync in the background, and redirects the
        browser back to the FE Connectors page.

        The CSRF gate is in two parts (W2 watch point #1):
        1. ``consume_state_token`` verifies signature + expiry.
        2. The bound user_id in the payload must match the
           ``current_user`` resolved from the session cookie.

        If a different user clicks this callback link (cookie-jar
        mismatch), step 2 fires and we redirect with
        ``reason=state_user_mismatch``.
        """
        # GitHub returned an error to us (user denied install,
        # etc.). Redirect cleanly, no DB writes.
        if error:
            return _connectors_redirect(
                request, status="error", reason=error
            )

        if not state or not installation_id:
            return _connectors_redirect(
                request,
                status="error",
                reason="missing_state_or_installation_id",
            )

        configs = load_app_config_from_env()
        if configs is None:
            return _connectors_redirect(
                request,
                status="error",
                reason="github_not_configured",
            )
        app_config, oauth_config = configs

        # Anonymous / system users cannot complete an install. The
        # state token's bound user_id must match a real session.
        session_user_id = user.get("user_id") or ""
        if user.get("is_system") or not session_user_id:
            return _connectors_redirect(
                request, status="error", reason="auth_required"
            )

        try:
            payload = consume_state_token(
                state,
                session_secret=oauth_config.session_secret,
                expected_user_id=session_user_id,
            )
        except OAuthStateExpired:
            return _connectors_redirect(
                request, status="error", reason="expired_state"
            )
        except OAuthStateInvalidSignature:
            return _connectors_redirect(
                request, status="error", reason="invalid_state"
            )
        except OAuthStateUserMismatch:
            return _connectors_redirect(
                request,
                status="error",
                reason="state_user_mismatch",
            )

        workspace_id = payload["w"]
        # Bound redirect path (set when the wizard's Step 2 calls
        # /oauth/start with redirect_to="/onboarding?step=2").
        # Already allowlist-validated at issue time; trustworthy
        # because the state token is signed.
        redirect_to: str | None = payload.get("r")

        # Re-verify the user is still admin+ in the workspace —
        # membership could have changed between oauth/start and
        # this callback. If they're no longer admin (or were
        # removed), don't persist.
        from ..workspaces.store import get_member  # noqa: PLC0415
        from ..workspaces.models import role_at_least  # noqa: PLC0415

        member = get_member(
            store, workspace_id=workspace_id, user_id=session_user_id
        )
        if member is None or not role_at_least(member.role, Role.admin):
            return _connectors_redirect(
                request,
                status="error",
                reason="workspace_role_insufficient",
                redirect_to=redirect_to,
            )

        # Exchange the user OAuth code for an access token (we
        # only use this to verify the user owns the install; we
        # never make API calls with it). Per the GitHub App spec,
        # ``code`` may or may not be present depending on whether
        # the App is configured with user-OAuth at install. If
        # absent, we skip the exchange and treat the install as
        # confirmed by the (signed, user-bound) state token alone.
        encrypted_token = ""
        async with httpx.AsyncClient(timeout=10.0) as http:
            if code:
                try:
                    body = await exchange_user_code(
                        code=code,
                        config=oauth_config,
                        http=http,
                    )
                    user_oauth_token = body.get("access_token") or ""
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "github oauth code exchange failed: %s", exc
                    )
                    return _connectors_redirect(
                        request,
                        status="error",
                        reason="github_error",
                        redirect_to=redirect_to,
                    )
            else:
                user_oauth_token = ""

            # Encrypt the user OAuth token (or an empty string
            # placeholder when user-OAuth wasn't configured) via
            # Fernet. Workspace-scoped via the store helper's
            # composite-PK enforcement (W2 watch point #1).
            from ..byok import encrypt_api_key  # noqa: PLC0415

            encrypted_token = encrypt_api_key(user_oauth_token or "<no-user-token>")

        connectors_store.upsert_credential(
            store,
            workspace_id=workspace_id,
            provider="github",
            encrypted_token=encrypted_token,
            installation_id=installation_id,
            account_login=None,  # filled by the first sync
            scopes=[],
        )

        # Kick off an initial sync in the background so the user's
        # tile flips from idle → connected on the next page load
        # without waiting for the next 60-min polling tick.
        background_tasks.add_task(
            _run_install_sync,
            store=store,
            workspace_id=workspace_id,
            app_config=app_config,
        )

        return _connectors_redirect(
            request,
            status="connected",
            reason=None,
            redirect_to=redirect_to,
        )

    @router.post("/github/install")
    async def github_install_route(
        body: GitHubInstallBody,
        background_tasks: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        """Re-confirm an installation_id for the current workspace.

        Used when the partner navigates back from GitHub manually
        (e.g., closed the redirect tab and returned to /connectors
        directly). Idempotent on the composite PK — re-running with
        the same installation_id replaces the row in place.
        """
        app_config, _ = _require_github_config()

        # Encrypted token is empty here — the install path persists
        # only the installation_id, not a user OAuth token.
        from ..byok import encrypt_api_key  # noqa: PLC0415

        connectors_store.upsert_credential(
            store,
            workspace_id=member.workspace_id,
            provider="github",
            encrypted_token=encrypt_api_key("<no-user-token>"),
            installation_id=body.installation_id,
            account_login=None,
            scopes=[],
        )

        background_tasks.add_task(
            _run_install_sync,
            store=store,
            workspace_id=member.workspace_id,
            app_config=app_config,
        )

        return {
            "ok": True,
            "installation_id": body.installation_id,
        }

    @router.delete("/github")
    def github_disconnect_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, bool]:
        """Disconnect the GitHub connector for this workspace.

        Removes the credential row. Snapshots survive (audit trail
        — useful if the partner reconnects). The GitHub-side
        installation revocation is the partner's action in the
        GitHub UI; we don't call any GitHub revoke endpoint.
        """
        deleted = connectors_store.delete_credential(
            store,
            workspace_id=member.workspace_id,
            provider="github",
        )
        return {"disconnected": deleted}

    @router.get("/github/repo-context")
    async def github_repo_context_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Pull a fresh slice of repo context — top-level tree, README
        excerpt, and the manifest file (package.json / pyproject.toml /
        etc.) — for the workspace's connected GitHub repo.

        Founder direction (2026-05-04): "Inspira should consistently
        every single before it starts a canvas, should pull the latest
        repo from GitHub." Canvas spawn (`promote-from-cluster`) calls
        this internally; this endpoint exposes the same data so the FE
        can show "Inspira read your repo (N files, README detected)"
        on the Connectors page.

        Returns 409 ``no_github_connection`` when the workspace has no
        GitHub credential or no default repo configured.
        """
        from .github.repo_context import fetch_repo_context

        ctx = await fetch_repo_context(
            store, workspace_id=member.workspace_id,
        )
        if ctx is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "no_github_connection",
                    "message": (
                        "Connect a GitHub repo on the Connectors page "
                        "to pull repo context."
                    ),
                },
            )
        return {"context": ctx}

    @router.post("/github/sync", status_code=202)
    async def github_sync_now_route(
        background_tasks: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Trigger a manual sync run. Returns 202 immediately; the
        actual fetch runs in the background."""
        app_config, _ = _require_github_config()

        cred = connectors_store.get_credential(
            store,
            workspace_id=member.workspace_id,
            provider="github",
        )
        if cred is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "github_not_connected"},
            )

        background_tasks.add_task(
            _run_install_sync,
            store=store,
            workspace_id=member.workspace_id,
            app_config=app_config,
            trigger="manual",
        )
        return {"status": "queued"}

    # -----------------------------------------------------------
    # GitHub repo file browser — read-only main/ (Wave F.2)
    # -----------------------------------------------------------

    @router.get("/github/repo/tree")
    async def github_repo_tree_route(
        ref: str = "main",
        recursive: bool = True,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Return the workspace repo as a recursive git tree.

        Backs the artifact-viewer "Repo" tab. Tree response is cached
        for 60s per (workspace_id, repo_full_name, ref) to stay under
        the 5000/hr installation rate limit when partners click around.

        Returns 409 ``github_not_connected`` when the workspace has no
        GitHub credential or no default destination repo configured —
        the FE maps both states to the same "Connect a GitHub repo"
        empty-state CTA.
        """
        from .github.repo_browse import (  # noqa: PLC0415
            RepoBrowseError,
            fetch_repo_tree,
        )

        app_config, _ = _require_github_config()
        try:
            return await fetch_repo_tree(
                store=store,
                workspace_id=member.workspace_id,
                ref=ref,
                recursive=recursive,
                app_config=app_config,
            )
        except RepoBrowseError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail,
            ) from exc

    @router.get("/github/repo/file")
    async def github_repo_file_route(
        path: str,
        ref: str = "main",
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Return the file content + binary detection for the workspace
        repo at ``path``.

        Read-only — edit + commit-back is a separate route (Wave
        F-later). Cap: 1 MiB (mirrors GitHub Contents API's own inline
        limit). Binary files (UTF-8 decode fails) surface as
        ``{content: null, binary: true}`` so the FE renders a "cannot
        preview" placeholder instead of garbled bytes.
        """
        from .github.repo_browse import (  # noqa: PLC0415
            RepoBrowseError,
            fetch_repo_file,
        )

        app_config, _ = _require_github_config()
        try:
            return await fetch_repo_file(
                store=store,
                workspace_id=member.workspace_id,
                path=path,
                ref=ref,
                app_config=app_config,
            )
        except RepoBrowseError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail,
            ) from exc

    # -----------------------------------------------------------
    # Linear connector — API-key flow (W2 F4)
    # -----------------------------------------------------------

    @router.post("/linear/connect")
    async def linear_connect_route(
        body: LinearConnectBody,
        background_tasks: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        """Validate + persist a Linear API key for this workspace.

        Workspace-scoped via the dependency: the token row lands
        keyed on (workspace_id, 'linear'). A re-connect of the
        same workspace replaces the row in place via the composite
        PK upsert path.
        """
        api_key = body.api_key.strip()
        if not api_key:
            raise HTTPException(
                status_code=422,
                detail={"error": "api_key_required"},
            )
        try:
            viewer = await linear_client.validate_key(api_key)
        except linear_client.LinearAuthError:
            raise HTTPException(
                status_code=401,
                detail={"error": "linear_auth_failed"},
            )
        except linear_client.LinearRateLimited:
            raise HTTPException(
                status_code=429,
                detail={"error": "linear_rate_limited"},
            )
        except linear_client.LinearTransient as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "linear_transient", "message": str(exc)},
            )

        from ..byok import encrypt_api_key  # noqa: PLC0415

        connectors_store.upsert_credential(
            store,
            workspace_id=member.workspace_id,
            provider="linear",
            encrypted_token=encrypt_api_key(api_key),
            installation_id=None,
            account_login=viewer.get("name") or viewer.get("id"),
            scopes=[],
        )
        # Kick off a first sync in the background so the tile
        # flips from idle → connected on the next page load
        # without waiting for the polling tick.
        background_tasks.add_task(
            _run_linear_sync,
            store=store,
            workspace_id=member.workspace_id,
            trigger="install",
        )
        return {
            "ok": True,
            "account": {
                "id": viewer.get("id"),
                "name": viewer.get("name"),
            },
        }

    @router.post("/linear/sync", status_code=202)
    async def linear_sync_now_route(
        background_tasks: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Trigger a manual Linear sync run."""
        cred = connectors_store.get_credential(
            store,
            workspace_id=member.workspace_id,
            provider="linear",
        )
        if cred is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "linear_not_connected"},
            )
        background_tasks.add_task(
            _run_linear_sync,
            store=store,
            workspace_id=member.workspace_id,
            trigger="manual",
        )
        return {"status": "queued"}

    @router.delete("/linear")
    def linear_disconnect_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, bool]:
        """Drop the Linear credential. feedback_items rows survive."""
        deleted = connectors_store.delete_credential(
            store,
            workspace_id=member.workspace_id,
            provider="linear",
        )
        return {"disconnected": deleted}

    # -----------------------------------------------------------
    # Destination metadata — drives Send-to-Linear / Send-to-GitHub
    # modals (W2 κ). Read/write the credential row's metadata_json.
    # -----------------------------------------------------------

    @router.get("/{provider}/destination")
    async def get_destination_route(
        provider: str,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        """Return the configured default destination for a provider.

        Modal calls this on open to decide whether Send is enabled or
        a configure-destination CTA should be shown instead. ``display``
        is a human-readable string (Linear team name or
        ``owner/repo``); the structured fields are echoed back so the
        caller can re-render its own preview without a second call.

        GitHub auto-config: when the provider is `github` and the
        user has an installation but no `default_owner` / `default_repo`
        persisted yet, the route lists the installation's accessible
        repos and persists the first one as the default. This means
        a partner who completes the Wizard's GitHub step gets a
        working "Push to GitHub" button on first open instead of
        "destination_not_configured" + a developer-facing PUT-curl
        hint that nobody knows what to do with.
        """
        if provider not in ("linear", "github"):
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_provider"},
            )
        cred = connectors_store.get_credential(
            store, workspace_id=member.workspace_id, provider=provider
        )
        if cred is None:
            return {
                "configured": False,
                "display": None,
                "metadata": {},
                "hint": (
                    f"Connect {provider} first, then configure a default "
                    "destination."
                ),
            }
        metadata = cred.get("metadata") or {}
        if provider == "linear":
            team_id = metadata.get("default_team_id")
            team_name = metadata.get("default_team_name")
            project_name = metadata.get("default_project_name")
            if not team_id:
                return {
                    "configured": False,
                    "display": None,
                    "metadata": metadata,
                    "hint": (
                        "Pick a Linear team to send issues to — open "
                        "the Connectors page to choose a team."
                    ),
                }
            display = team_name or team_id
            if project_name:
                display = f"{display} · {project_name}"
            return {
                "configured": True,
                "display": display,
                "metadata": metadata,
                "hint": None,
            }
        # provider == "github"
        owner = metadata.get("default_owner")
        repo = metadata.get("default_repo")
        if not owner or not repo:
            # Lazy auto-config: if the installation is wired, list its
            # accessible repos and persist the first one as default.
            installation_id = cred.get("installation_id")
            if installation_id:
                from .github.app_jwt import (  # noqa: PLC0415
                    installation_access_token,
                )
                from .github.oauth import (  # noqa: PLC0415
                    load_app_config_from_env,
                )
                from .github.client import (  # noqa: PLC0415
                    GitHubClient,
                    GitHubUnauthorized,
                )
                import httpx  # noqa: PLC0415

                configs = load_app_config_from_env()
                if configs is not None:
                    app_config, _ = configs
                    import asyncio  # noqa: PLC0415

                    async def _list_repos() -> list[dict[str, Any]]:
                        async with httpx.AsyncClient(timeout=4.0) as http:
                            token, _exp = await installation_access_token(
                                installation_id=installation_id,
                                config=app_config,
                                http=http,
                            )
                            client = GitHubClient(
                                installation_token=token, http=http,
                            )
                            return await client.list_installation_repos(
                                per_page=10,
                            )

                    try:
                        # Hard 5s cap so a hung GitHub request can't
                        # take down the destination route — Fly's
                        # proxy times out at ~30s and strips CORS
                        # headers, surfacing as "Failed to fetch" on
                        # the FE. Better to return "not configured"
                        # quickly and let the FE retry.
                        repos = await asyncio.wait_for(
                            _list_repos(), timeout=5.0,
                        )
                    except (
                        GitHubUnauthorized,
                        httpx.HTTPError,
                        asyncio.TimeoutError,
                        Exception,  # noqa: BLE001
                    ):
                        repos = []
                    if repos:
                        first = repos[0]
                        new_owner = (
                            first.get("owner", {}).get("login")
                            if isinstance(first.get("owner"), dict)
                            else first.get("owner")
                        )
                        new_repo = first.get("name")
                        if new_owner and new_repo:
                            metadata = {
                                **metadata,
                                "default_owner": new_owner,
                                "default_repo": new_repo,
                            }
                            connectors_store.set_credential_metadata(
                                store,
                                workspace_id=member.workspace_id,
                                provider="github",
                                metadata=metadata,
                            )
                            return {
                                "configured": True,
                                "display": f"{new_owner}/{new_repo}",
                                "metadata": metadata,
                                "hint": None,
                            }
            return {
                "configured": False,
                "display": None,
                "metadata": metadata,
                "hint": (
                    "Pick a repo to push to — open the Connectors "
                    "page to choose which repository receives the "
                    "code Inspira drafts."
                ),
            }
        return {
            "configured": True,
            "display": f"{owner}/{repo}",
            "metadata": metadata,
            "hint": None,
        }

    @router.put("/{provider}/destination")
    def put_destination_route(
        provider: str,
        body: ConnectorDestinationBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        """Set the default destination on the credential row.

        Admin-only — destination is workspace-wide. Body shape is
        provider-flexible: Linear accepts ``{team_id, team_name,
        project_id?, project_name?}``; GitHub accepts ``{owner, repo}``.
        Unknown keys are ignored; missing required keys return 422 so
        the caller can't half-configure the row.
        """
        if provider not in ("linear", "github"):
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_provider"},
            )
        if provider == "linear":
            if not body.team_id or not body.team_name:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "missing_fields",
                        "required": ["team_id", "team_name"],
                    },
                )
            metadata: dict[str, Any] = {
                "default_team_id": body.team_id,
                "default_team_name": body.team_name,
            }
            if body.project_id:
                metadata["default_project_id"] = body.project_id
            if body.project_name:
                metadata["default_project_name"] = body.project_name
        else:
            if not body.owner or not body.repo:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "missing_fields",
                        "required": ["owner", "repo"],
                    },
                )
            metadata = {
                "default_owner": body.owner,
                "default_repo": body.repo,
            }
        ok = connectors_store.set_credential_metadata(
            store,
            workspace_id=member.workspace_id,
            provider=provider,
            metadata=metadata,
        )
        if not ok:
            raise HTTPException(
                status_code=409,
                detail={"error": f"{provider}_not_connected"},
            )
        return {"ok": True, "metadata": metadata}

    # -----------------------------------------------------------
    # CSV / JSON paste-in import (W2 F4)
    # -----------------------------------------------------------

    @router.get("/feedback/items")
    def list_feedback_items_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
        source: str | None = None,
        status: str | None = None,
        archived: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List ingested feedback items for the active workspace.

        Drives the F6 inbox. Workspace-scoped via the dependency.
        Pagination + optional source/status/archived filters.

        ``archived`` query param drives the New / Archive tabs:
          - "true"  → cluster_id IS NOT NULL (sifted through)
          - "false" → cluster_id IS NULL (still raw)
          - omitted → no constraint
        """
        if limit <= 0 or limit > 500:
            raise HTTPException(
                status_code=422,
                detail={"error": "limit_out_of_range", "max": 500},
            )
        archived_filter: bool | None
        if archived is None:
            archived_filter = None
        elif archived.lower() == "true":
            archived_filter = True
        elif archived.lower() == "false":
            archived_filter = False
        else:
            raise HTTPException(
                status_code=422,
                detail={"error": "archived_must_be_true_or_false"},
            )
        items = feedback_store.list_items(
            store,
            workspace_id=member.workspace_id,
            source=source,
            status=status,  # type: ignore[arg-type]
            archived=archived_filter,
            limit=limit,
            offset=offset,
        )
        counts = feedback_store.count_items(
            store, workspace_id=member.workspace_id, source=source
        )
        return {
            "items": [it.model_dump(mode="json") for it in items],
            "total": counts.total,
            "queued": counts.queued,
        }

    @router.post("/feedback/recluster", status_code=202)
    def recluster_feedback_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Wipe and rebuild clusters for the workspace's feedback.

        Clears every feedback_clusters row + cluster_id assignments,
        then re-runs embeddings clustering at the current threshold
        and the LLM merge pass. Used when the partner wants to fold
        existing items that landed at a tighter threshold (or via
        the title-normalisation fallback).

        Does NOT touch v2_projects — auto-promotion of new clusters
        happens on the next CSV import or via the Re-run button.
        Reason: re-promoting from a recluster would create duplicate
        v2_projects for clusters that were already promoted under a
        different cluster_id; the partner can clean up via the
        Kanban bulk-delete first if they want a true reset.
        """
        ws = member.workspace_id
        # Phase 1: collect every feedback_item id in this workspace.
        with store._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_id, title, body
                FROM feedback_items
                WHERE workspace_id = ?
                """,
                (ws,),
            ).fetchall()
            connection.execute(
                "UPDATE feedback_items SET cluster_id = NULL, "
                "embedding_json = NULL WHERE workspace_id = ?",
                (ws,),
            )
            connection.execute(
                "DELETE FROM feedback_clusters WHERE workspace_id = ?",
                (ws,),
            )
            connection.commit()
        items = [(r[0], r[1] or "", r[2] or "") for r in rows]
        if not items:
            return {"items": 0, "clusters_after": 0}

        # Phase 2: re-cluster from scratch, embeddings if available.
        clusters_touched: set[str] = set()
        if is_embeddings_enabled():
            texts = [f"{t}\n{b}".strip() for _, t, b in items]
            vectors = embed_texts_batch(texts)
            for (item_id, _, _), vec in zip(items, vectors):
                if vec is None:
                    continue
                try:
                    cid, _ = feedback_cluster.assign_or_create_cluster(
                        store, workspace_id=ws, item_id=item_id,
                        embedding=vec,
                    )
                    clusters_touched.add(cid)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "recluster: assign failed for %s", item_id,
                    )
        else:
            for item_id, title, _ in items:
                try:
                    result = feedback_cluster.assign_or_create_title_cluster(
                        store, workspace_id=ws, item_id=item_id,
                        title=title,
                    )
                    if result is not None:
                        clusters_touched.add(result[0])
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "recluster: title-assign failed for %s", item_id,
                    )

        # Phase 3: LLM merge pass — semantic-synonym dedupe.
        merge_stats = {"clusters_absorbed": 0}
        try:
            merge_stats = merge_clusters_via_llm(store, workspace_id=ws)
        except Exception:  # noqa: BLE001
            logger.exception("recluster: llm_merge raised")

        with store._connect() as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM feedback_clusters WHERE workspace_id = ?",
                (ws,),
            ).fetchone()[0]

        return {
            "items": len(items),
            "clusters_after": int(after),
            "absorbed": merge_stats.get("clusters_absorbed", 0),
        }

    # NOTE: `/feedback/items/bulk-delete` MUST be registered before the
    # `/feedback/items/{item_id}` route below. FastAPI matches the
    # path template first, then the HTTP method — so a POST to the
    # static "bulk-delete" path would otherwise resolve against
    # `/{item_id}` (treating "bulk-delete" as an item_id), see only
    # PATCH registered there, and return 405 Method Not Allowed.
    @router.post("/feedback/items/bulk-delete")
    def bulk_delete_feedback_items_route(
        body: FeedbackItemBulkDeleteBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Delete a batch of feedback_items rows.

        Member-or-above scope: any teammate can clean up the inbox
        (parity with the paste path that lets them grow it).
        Workspace-scoped via the WHERE clause inside
        ``feedback_store.bulk_delete_items`` — ids that don't belong
        to the caller's workspace are silently skipped, so the count
        is the truth, not the input length.
        """
        deleted = feedback_store.bulk_delete_items(
            store,
            workspace_id=member.workspace_id,
            item_ids=body.item_ids,
        )
        return {"ok": True, "deleted": deleted}

    @router.patch("/feedback/items/{item_id}")
    def update_feedback_item_route(
        item_id: str,
        body: FeedbackItemUpdateBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Manual category override from the F6 inbox.

        The classifier flagged this row, the partner disagrees,
        they pick the right bucket. The override survives any
        future re-imports (content_hash idempotency leaves the
        existing row alone).
        """
        new_category = body.type_hint.strip().lower()
        if new_category not in ALLOWED_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_category",
                    "allowed": sorted(ALLOWED_CATEGORIES),
                },
            )
        ok = feedback_store.update_category(
            store,
            item_id=item_id,
            workspace_id=member.workspace_id,
            category=new_category,
        )
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "feedback_item_not_found"},
            )
        item = feedback_store.get_item(
            store,
            item_id=item_id,
            workspace_id=member.workspace_id,
        )
        return {
            "ok": True,
            "item": item.model_dump(mode="json") if item else None,
        }

    # -----------------------------------------------------------
    # Local repo upload (Onboarding Wizard Step 2 path B)
    # -----------------------------------------------------------

    @router.post("/local-repo/upload")
    async def local_repo_upload_route(
        request: Request,
        files: list[UploadFile],
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        """Persist a partner-uploaded folder as a repo snapshot.

        Used by the Onboarding Wizard Step 2 "Upload local folder"
        path for partners who don't want to wire GitHub OAuth (or
        whose code lives outside GitHub). Each uploaded file's
        ``filename`` carries the relative path inside the folder
        (FE sets it from ``webkitRelativePath``).

        Admin+ scope: matches GitHub install scope — connecting a
        code source is a privileged workspace action.

        Filtering + size caps:
        - Per-file 1 MB cap; total 50 MB cap.
        - Excludes common build/dependency dirs (.git/, node_modules/,
          dist/, build/, .venv/, __pycache__/) and lockfile/binary
          extensions.
        - Path traversal defense: each path is normalized via
          posixpath.normpath and rejected if it escapes the upload
          root (`..` segments, absolute paths).

        Persists via the same ``upsert_repo_snapshot`` GitHub sync
        uses, so the artifact-viewer code-gen path treats both
        sources interchangeably. Provider tag is ``local-repo``.
        """
        accepted, skipped, total_bytes = await _ingest_local_repo_files(
            files
        )
        if not accepted:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "no_source_files",
                    "message": (
                        "Couldn't find any source files in the upload."
                    ),
                },
            )

        # Build snapshot in the same shape as GitHub's sync emits,
        # but with empty open_issues / recent_commits and tree_top
        # populated from the uploaded paths (sorted, top 100).
        sorted_paths = sorted(accepted.keys())
        tree_top = [
            {
                "path": p,
                "type": "blob",
                "size": len(accepted[p]),
            }
            for p in sorted_paths[:100]
        ]
        snapshot = {
            "tree_top": tree_top,
            "open_issues": [],
            "recent_commits": [],
            "source": "local-repo-upload",
            "file_count": len(accepted),
            "total_bytes": total_bytes,
        }

        # Stable repo_id per workspace+upload — random UUID4 so two
        # uploads from the same workspace produce two snapshots
        # rather than overwriting (compare GitHub which is keyed by
        # the GitHub repo id). FE uses the latest snapshot.
        repo_id = f"local-{uuid.uuid4().hex[:16]}"
        repo_full_name = (
            sorted_paths[0].split("/", 1)[0] if "/" in sorted_paths[0] else "uploaded"
        )
        connectors_store.upsert_repo_snapshot(
            store,
            workspace_id=member.workspace_id,
            provider="local-repo",
            repo_id=repo_id,
            repo_full_name=repo_full_name,
            default_branch=None,
            visibility="private",
            snapshot=snapshot,
            status="fresh",
        )
        return {
            "ok": True,
            "accepted": len(accepted),
            "skipped": skipped,
            "total_bytes": total_bytes,
            "repo_id": repo_id,
        }

    @router.post("/csv/import")
    def csv_import_route(
        body: CsvImportBody,
        background: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Persist a batch of pasted feedback rows into feedback_items.

        Member-or-above scope: any teammate can paste a CSV (it's
        not a privileged source-of-truth action like wiring an API
        key). Idempotent at the row level via the
        (workspace_id, content_hash) UNIQUE: re-pasting the same
        rows produces ``inserted=0, skipped=N``.
        """
        if len(body.rows) == 0:
            raise HTTPException(
                status_code=422,
                detail={"error": "no_rows"},
            )
        if len(body.rows) > MAX_CSV_IMPORT_ROWS:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "too_many_rows",
                    "max": MAX_CSV_IMPORT_ROWS,
                    "received": len(body.rows),
                },
            )

        # F5+ batched LLM classify: when the LLM flag is on, pre-
        # classify the rows that lack a partner-supplied type_hint
        # in batched LLM calls before iterating into upsert_item.
        # The LLM path is feature-gated; with the flag off, this
        # is a no-op + upsert_item's inline rule-based fallback
        # populates the same column.
        valid_rows: list[tuple[int, Any, str]] = []
        for idx, row in enumerate(body.rows):
            title = (row.title or "").strip()
            if title:
                valid_rows.append((idx, row, title))

        llm_categories: dict[int, str] = {}
        if is_llm_enabled():
            unhinted = [
                (idx, row, title)
                for idx, row, title in valid_rows
                if not (row.type_hint and row.type_hint.strip())
            ]
            if unhinted:
                results = classify_items_with_fallback(
                    [
                        ItemForClassify(title=title, body=row.body or "")
                        for _, row, title in unhinted
                    ]
                )
                for (idx, _, _), category in zip(unhinted, results):
                    llm_categories[idx] = category

        inserted = 0
        skipped = 0
        skipped += len(body.rows) - len(valid_rows)  # blank-title rows
        new_item_ids: list[tuple[str, str, str]] = []  # (item_id, title, body)
        for idx, row, title in valid_rows:
            type_hint = row.type_hint or llm_categories.get(idx) or None
            try:
                item_id, was_new = feedback_store.upsert_item(
                    store,
                    workspace_id=member.workspace_id,
                    source=row.source or "csv-import",
                    external_id=None,
                    title=title,
                    body=row.body or "",
                    author=row.author or None,
                    author_email=row.author_email or None,
                    received_at=row.received_at or None,
                    type_hint=type_hint,
                    raw_payload=row.model_dump(mode="json"),
                )
                if was_new:
                    inserted += 1
                    new_item_ids.append((item_id, title, row.body or ""))
                else:
                    skipped += 1
            except ValueError:
                skipped += 1

        # Embedding-based clustering (W2 F5+ embeddings slice).
        # Skipped silently when the feature flag is off; otherwise
        # batch-embeds new items and assigns them to existing
        # clusters (or creates new ones). Per-item failures don't
        # block the import.
        clustered = 0
        cluster_ids_touched: set[str] = set()
        if new_item_ids and is_embeddings_enabled():
            texts = [
                f"{title}\n{body}".strip()
                for _, title, body in new_item_ids
            ]
            vectors = embed_texts_batch(texts)
            for (item_id, _, _), vec in zip(new_item_ids, vectors):
                if vec is None:
                    continue
                try:
                    cid, _was_new = feedback_cluster.assign_or_create_cluster(
                        store,
                        workspace_id=member.workspace_id,
                        item_id=item_id,
                        embedding=vec,
                    )
                    cluster_ids_touched.add(cid)
                    clustered += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "csv import: cluster assignment failed for %s",
                        item_id,
                    )
        elif new_item_ids:
            # Title-normalisation fallback (no embeddings required).
            # Buys us "obvious duplicate" merging — same lowercased,
            # punctuation-stripped, stopword-filtered tokens. The
            # Kanban then sees one card per cluster instead of one
            # per raw feedback item. Embeddings (when enabled)
            # supersede this with semantic similarity.
            for item_id, title, _ in new_item_ids:
                try:
                    result = feedback_cluster.assign_or_create_title_cluster(
                        store,
                        workspace_id=member.workspace_id,
                        item_id=item_id,
                        title=title,
                    )
                    if result is not None:
                        cluster_ids_touched.add(result[0])
                        clustered += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "csv import: title-cluster assignment failed for %s",
                        item_id,
                    )

        # LLM merge pass — fold clusters that describe the same root
        # issue but used different surface words ("page crashed" /
        # "white screen" / "doesn't load"). Scheduled as a background
        # task so the response returns fast: synchronous merges on
        # large workspaces can exceed Cloudflare's 100s edge timeout
        # and hang the wizard's "Importing…" UI even though the
        # server-side work completed. Auto-promote (below) still runs
        # synchronously so the Kanban populates when the wizard
        # advances; the next Kanban poll picks up merged-state once
        # the background merge finishes (auto-spawn is idempotent).
        if cluster_ids_touched:
            background.add_task(
                merge_clusters_via_llm,
                store,
                workspace_id=member.workspace_id,
            )

        # Auto-promote: clusters touched by this import become v2_projects
        # rows (Draft state) so the Kanban populates immediately.
        # Idempotent — clusters already linked to a project are skipped.
        # Tier-capped (#172): only the top-N most-urgent clusters
        # auto-promote per call; the rest stay in Inbox archive.
        auto_promoted = 0
        deferred = 0
        if cluster_ids_touched:
            plan_slug = (
                store.get_subscription(user_id=member.user_id) or {}
            ).get("plan")
            kanban_tier = tiers.kanban_tier_for_plan(plan_slug)
            try:
                auto_promoted, deferred = (
                    feedback_cluster.ensure_v2_projects_for_clusters(
                        store,
                        workspace_id=member.workspace_id,
                        user_id=member.user_id,
                        cluster_ids=cluster_ids_touched,
                        plan_tier=kanban_tier,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "csv import: auto-promote v2_projects failed",
                )

        return {
            "inserted": inserted,
            "skipped": skipped,
            "clustered": clustered,
            "auto_promoted": auto_promoted,
            "deferred": deferred,  # #172 — clusters held in Inbox archive
            "total": len(body.rows),
        }

    @router.get("/feedback/clusters")
    def list_feedback_clusters_route(
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        """List clusters for the current workspace's inbox view.

        Drives the F6+ cluster-grouping UI. Workspace-scoped via
        the dependency. Centroids excluded from the response —
        clients only need cluster_id + theme + item_count.
        """
        clusters = feedback_cluster.list_clusters_for_inbox(
            store, workspace_id=member.workspace_id
        )
        return {"clusters": clusters}

    return router


MAX_CSV_IMPORT_ROWS = 5000

# Mirrors feedback_items.classify.ALLOWED_HINT_VALUES — kept inline
# here so the router import surface stays tight (the classifier
# module isn't otherwise needed by the router).
ALLOWED_CATEGORIES = {
    "bug",
    "feature",
    "complaint",
    "praise",
    "question",
    "noise",
}


class FeedbackItemUpdateBody(BaseModel):
    """Body for PATCH /feedback/items/{item_id} — F6 manual override."""

    type_hint: str = Field(min_length=1, max_length=40)


class FeedbackItemBulkDeleteBody(BaseModel):
    """Body for POST /feedback/items/bulk-delete."""

    item_ids: list[str] = Field(min_length=1, max_length=500)


class LinearConnectBody(BaseModel):
    """Body for POST /linear/connect."""

    api_key: str = Field(min_length=10, max_length=400)


class ConnectorDestinationBody(BaseModel):
    """Body for PUT /api/v2/connectors/{provider}/destination.

    All fields optional in the schema so the same shape covers both
    providers; the route enforces per-provider required-field rules
    so a half-configured row never lands.
    """

    # Linear
    team_id: str | None = Field(default=None, max_length=80)
    team_name: str | None = Field(default=None, max_length=120)
    project_id: str | None = Field(default=None, max_length=80)
    project_name: str | None = Field(default=None, max_length=120)
    # GitHub
    owner: str | None = Field(default=None, max_length=120)
    repo: str | None = Field(default=None, max_length=120)


class CsvImportRow(BaseModel):
    """One row from the CSV / JSON paste-in dialog."""

    title: str
    body: str = ""
    author: str = ""
    author_email: str = ""
    source: str = "csv-import"
    received_at: str = ""
    type_hint: str = ""


class CsvImportBody(BaseModel):
    """Batch payload from POST /csv/import."""

    rows: list[CsvImportRow] = Field(default_factory=list)


async def _run_linear_sync(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    trigger: str = "install",
) -> None:
    """Background-task wrapper around linear sync_workspace."""
    try:
        await linear_sync.sync_workspace(
            store=store,
            workspace_id=workspace_id,
            trigger=trigger,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "linear_sync background task failed for workspace %s",
            workspace_id,
        )


async def _run_install_sync(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    app_config: GitHubAppConfig,
    trigger: str = "install",
) -> None:
    """Background-task wrapper around sync_workspace.

    FastAPI's BackgroundTasks runs this after the response ships,
    so the OAuth callback redirect (or the manual sync 202)
    returns immediately. Errors are swallowed + logged — sync
    failures don't break the user-visible flow.
    """
    try:
        await sync_workspace(
            store=store,
            workspace_id=workspace_id,
            trigger=trigger,
            config=app_config,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "github_sync background task failed for workspace %s",
            workspace_id,
        )
