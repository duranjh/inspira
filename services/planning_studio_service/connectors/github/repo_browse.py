"""Read-only file browser for the workspace's connected GitHub repo (Wave F.2).

Backs the artifact-viewer "Repo" tab: partner-facing surface that renders
their default branch (``main``) as an interactive file tree without giving
the LLM write access. Edit + commit-back is out of scope for F.2 — those
land on a separate write surface in a later wave.

Two helpers exposed to the connectors router:
- ``fetch_repo_tree``: recursive git tree at a ref, FE-shaped projection,
  60s in-process cache keyed by (workspace_id, repo_full_name, ref).
- ``fetch_repo_file``: single file content + strict UTF-8 decode so binary
  files surface as ``binary: true`` to the FE (rather than the silent
  ``errors='replace'`` fallback used for repo-context excerpts).

Both raise ``RepoBrowseError(status_code, detail)`` on failure; the route
layer translates that to ``HTTPException``. The 60s cache matters because
GitHub installation rate limits sit at 5000/hour — a partner clicking
around the Repo tab during a session would otherwise burn the budget on
the same tree.
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from .. import store as connectors_store
from .app_jwt import GitHubAppConfig, installation_access_token
from .client import (
    GitHubClient,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)

if TYPE_CHECKING:
    from ...store import PlanningStudioStore


logger = logging.getLogger(__name__)


# GitHub Contents API itself only returns inline content for files <= 1 MB
# (larger files require the Blobs API). We mirror that cap on the route
# rather than silently truncating.
_MAX_FILE_BYTES = 1 * 1024 * 1024
_TREE_CACHE_TTL_SECONDS = 60.0

# (workspace_id, repo_full_name, ref) -> (expires_at_monotonic, payload)
_TREE_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


class RepoBrowseError(Exception):
    """Carries status_code + detail dict so the route layer can surface
    it as a standard ``HTTPException`` without leaking module internals.
    """

    def __init__(
        self,
        *,
        status_code: int,
        error: str,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        detail: dict[str, Any] = {"error": error}
        if message is not None:
            detail["message"] = message
        if extra:
            detail.update(extra)
        super().__init__(error)
        self.status_code = status_code
        self.detail = detail


def _resolve_repo_destination(
    store: "PlanningStudioStore", *, workspace_id: str,
) -> tuple[str, str]:
    """Return (installation_id, repo_full_name) for the workspace.

    Both a missing credential row AND a credential without a configured
    default destination collapse to the same ``github_not_connected``
    409 — from the FE's perspective the partner needs to visit the
    Connectors page in either case, so a single error code keeps the
    empty-state handler uncluttered.
    """
    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="github",
    )
    if cred is None:
        raise RepoBrowseError(
            status_code=409,
            error="github_not_connected",
            message=(
                "Connect a GitHub repo on the Connectors page to browse "
                "files."
            ),
        )
    installation_id = cred.get("installation_id")
    metadata = cred.get("metadata") or {}
    owner = metadata.get("default_owner")
    repo = metadata.get("default_repo")
    if not installation_id or not owner or not repo:
        raise RepoBrowseError(
            status_code=409,
            error="github_not_connected",
            message=(
                "Pick a repo to browse — open the Connectors page to "
                "choose which repository to read."
            ),
        )
    return installation_id, f"{owner}/{repo}"


def _cache_get(key: tuple[str, str, str]) -> dict[str, Any] | None:
    cached = _TREE_CACHE.get(key)
    if cached is None:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _TREE_CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: tuple[str, str, str], payload: dict[str, Any]) -> None:
    _TREE_CACHE[key] = (
        time.monotonic() + _TREE_CACHE_TTL_SECONDS, payload,
    )


def _slim_tree_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Project a GitHub tree entry to the FE-shaped subset.

    Drops the per-entry mode/sha/url GitHub returns — the FE only needs
    path + type + (optional) size to render the tree, and shrinking the
    payload tightens the websocket-ish refresh path for big repos.
    """
    out: dict[str, Any] = {
        "path": entry.get("path"),
        "type": entry.get("type"),
    }
    size = entry.get("size")
    if size is not None:
        out["size"] = size
    return out


async def fetch_repo_tree(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    ref: str,
    recursive: bool,
    app_config: GitHubAppConfig,
) -> dict[str, Any]:
    """Return the FE-shaped tree payload, hitting the 60s cache when warm.

    Raises ``RepoBrowseError`` for any not-connected / not-found / upstream
    failure.
    """
    installation_id, repo_full_name = _resolve_repo_destination(
        store, workspace_id=workspace_id,
    )

    cache_key = (workspace_id, repo_full_name, ref)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            token, _expires_at = await installation_access_token(
                installation_id=installation_id,
                config=app_config,
                http=http,
            )
            client = GitHubClient(installation_token=token, http=http)
            raw = await client.get_repo_tree(
                repo_full_name=repo_full_name,
                ref=ref,
                recursive=recursive,
            )
    except GitHubNotFound as exc:
        raise RepoBrowseError(
            status_code=404,
            error="ref_not_found",
            message=(
                f"branch or commit '{ref}' not found on {repo_full_name}"
            ),
        ) from exc
    except GitHubUnauthorized as exc:
        raise RepoBrowseError(
            status_code=502,
            error="github_unauthorized",
            message="GitHub rejected the installation token",
        ) from exc
    except GitHubRateLimited as exc:
        raise RepoBrowseError(
            status_code=429,
            error="github_rate_limited",
        ) from exc
    except (GitHubTransient, httpx.HTTPError) as exc:
        raise RepoBrowseError(
            status_code=502,
            error="github_upstream",
            message=str(exc),
        ) from exc

    tree_entries = raw.get("tree") or []
    slim_tree = [
        _slim_tree_entry(e)
        for e in tree_entries
        if isinstance(e, dict) and isinstance(e.get("path"), str)
    ]

    payload: dict[str, Any] = {
        "repo_full_name": repo_full_name,
        "ref": ref,
        "sha": raw.get("sha") or "",
        "tree": slim_tree,
        "truncated": bool(raw.get("truncated")),
    }
    _cache_put(cache_key, payload)
    return payload


async def fetch_repo_file(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    path: str,
    ref: str,
    app_config: GitHubAppConfig,
) -> dict[str, Any]:
    """Return the FE-shaped file payload.

    Text files come back as ``{content: <str>, binary: False, ...}``.
    Binary files (UTF-8 decode fails) come back as
    ``{content: None, binary: True, ...}`` so the FE can render a
    "cannot preview" placeholder instead of garbled bytes.
    """
    installation_id, repo_full_name = _resolve_repo_destination(
        store, workspace_id=workspace_id,
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            token, _expires_at = await installation_access_token(
                installation_id=installation_id,
                config=app_config,
                http=http,
            )
            client = GitHubClient(installation_token=token, http=http)
            payload = await client.get_file_contents(
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
            )
    except GitHubUnauthorized as exc:
        raise RepoBrowseError(
            status_code=502,
            error="github_unauthorized",
            message="GitHub rejected the installation token",
        ) from exc
    except GitHubRateLimited as exc:
        raise RepoBrowseError(
            status_code=429,
            error="github_rate_limited",
        ) from exc
    except (GitHubTransient, httpx.HTTPError) as exc:
        raise RepoBrowseError(
            status_code=502,
            error="github_upstream",
            message=str(exc),
        ) from exc

    if payload is None:
        raise RepoBrowseError(
            status_code=404,
            error="file_not_found",
            message=f"{path}: not found at ref '{ref}'",
        )

    size = payload.get("size")
    if isinstance(size, int) and size > _MAX_FILE_BYTES:
        raise RepoBrowseError(
            status_code=413,
            error="file_too_large",
            extra={"size": size, "max_bytes": _MAX_FILE_BYTES},
        )

    sha = payload.get("sha") or ""
    encoding = (payload.get("encoding") or "").lower()
    raw_content = payload.get("content")

    decoded: str | None = None
    is_binary = False
    if isinstance(raw_content, str):
        if encoding == "base64":
            try:
                raw_bytes = base64.b64decode(raw_content, validate=False)
            except (binascii.Error, ValueError):
                is_binary = True
            else:
                try:
                    decoded = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    # Strict — falling back to errors="replace" would
                    # mask the binary signal the FE relies on to render
                    # a "cannot preview" placeholder.
                    is_binary = True
        else:
            decoded = raw_content

    if is_binary or decoded is None:
        return {
            "path": path,
            "content": None,
            "binary": True,
            "sha": sha,
            "encoding": "base64",
        }
    return {
        "path": path,
        "content": decoded,
        "binary": False,
        "sha": sha,
        "encoding": "utf-8",
    }


def reset_cache_for_tests() -> None:
    """Test-only hook — drops the in-process tree cache between cases.

    Keeps the production code clean of test-flag branches while letting
    test_repo_browse.py guarantee a cold cache before each assertion.
    """
    _TREE_CACHE.clear()
