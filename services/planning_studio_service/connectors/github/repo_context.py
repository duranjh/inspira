"""Pull repo context from GitHub for the orchestrator + scaffold gen.

Product decision: before starting a canvas, Inspira always pulls
the latest repo state from GitHub — i.e. don't generate against a
stale snapshot, always read fresh.

Cost / latency discipline: we DON'T pull every file. We pull:

  - The default branch's top-level tree (depth-1)
  - README at the repo root (any common name + casing)
  - package.json / pyproject.toml / Cargo.toml / go.mod (one of)

That's typically 3-5 round trips per fetch. For a 200-row CSV that
spawns N=10 promotes per import the cost is ~30-50 GitHub API calls
total — well under the 5000 calls/hour installation budget.

The shape returned mirrors what the orchestrator's prompt builders
want:

    {
      "repo_full_name": "acme/instagram-clone",
      "default_branch": "main",
      "head_sha": "abc1234…",
      "top_level_files": [
        {"path": "README.md", "type": "blob"},
        {"path": "src", "type": "tree"},
        ...
      ],
      "readme_excerpt": "# Instagram clone…",   # first ~3000 chars
      "manifest_kind": "package.json",         # or pyproject.toml, ...
      "manifest_excerpt": "{...}",
      "fetched_at": "2026-05-04T22:30:01+00:00",
    }

Returns ``None`` (no exception) when the workspace has no GitHub
credential — the orchestrator should fall through to a non-repo-
aware prompt rather than 5xx the canvas.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import store as connectors_store
from .app_jwt import installation_access_token
from .client import GitHubClient, GitHubNotFound, GitHubTransient
from .oauth import load_app_config_from_env

logger = logging.getLogger(__name__)


# Order matters — we pick the first manifest we find. JS/TS first
# because Inspira's scaffold generator currently emits react-vite +
# TypeScript by default; Python next; then the long tail.
_MANIFEST_CANDIDATES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "Gemfile",
)
_README_CANDIDATES: tuple[str, ...] = (
    "README.md",
    "Readme.md",
    "readme.md",
    "README.MD",
    "README",
    "README.txt",
)
_README_EXCERPT_CHARS = 3000
_MANIFEST_EXCERPT_CHARS = 4000


def _decode_contents_payload(payload: dict[str, Any]) -> str | None:
    """Best-effort decode of a GET /contents/{path} response."""
    encoding = (payload.get("encoding") or "").lower()
    raw = payload.get("content")
    if not isinstance(raw, str):
        return None
    if encoding == "base64":
        try:
            return base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
    # Fallback: GitHub sometimes returns plaintext for tiny files.
    return raw


async def fetch_repo_context(
    store,
    *,
    workspace_id: str,
    timeout_s: float = 12.0,
) -> dict[str, Any] | None:
    """Pull a fresh slice of repo context for the workspace's default
    GitHub destination.

    Returns ``None`` when:
      - no github credential is wired
      - default destination (owner/repo) is missing
      - GitHub App env secrets aren't set
      - upstream 404s on the default branch (empty repo)

    All other upstream errors (transient / unauthorized / rate-limited)
    are caught + logged + return ``None`` so the orchestrator caller
    can fall through to a non-repo-aware prompt rather than 5xx.
    """
    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="github",
    )
    if cred is None:
        return None
    metadata = cred.get("metadata") or {}
    owner = metadata.get("default_owner")
    repo = metadata.get("default_repo")
    if not owner or not repo:
        return None
    repo_full_name = f"{owner}/{repo}"
    installation_id = cred.get("installation_id")
    if not installation_id:
        return None

    configs = load_app_config_from_env()
    if configs is None:
        return None
    app_config, _ = configs

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            token, _expires_at = await installation_access_token(
                installation_id=installation_id,
                config=app_config,
                http=http,
            )
            client = GitHubClient(installation_token=token, http=http)
            return await _fetch_with_client(client, repo_full_name)
    except GitHubNotFound:
        # Empty repo — no default branch ref. Return None so the
        # orchestrator falls through.
        return None
    except (httpx.HTTPError, GitHubTransient) as exc:
        logger.warning(
            "fetch_repo_context: upstream failure for ws=%s repo=%s: %s",
            workspace_id, repo_full_name, exc,
        )
        return None


async def _fetch_with_client(
    client: GitHubClient, repo_full_name: str,
) -> dict[str, Any]:
    """Inner fetch — assumes the client is authenticated and the repo
    exists. Raises on transport-level failures."""
    repo_meta = await client.get_repo_metadata(repo_full_name=repo_full_name)
    default_branch = str(repo_meta.get("default_branch") or "main")
    head_sha = await client.get_branch_sha(
        repo_full_name=repo_full_name, branch=default_branch,
    )

    # Top-level (non-recursive) tree.
    tree = await client.get_repo_tree(
        repo_full_name=repo_full_name,
        ref=head_sha,
        recursive=False,
    )
    top_level_entries: list[dict[str, Any]] = []
    for entry in tree.get("tree") or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        ttype = entry.get("type")
        if isinstance(path, str) and isinstance(ttype, str):
            top_level_entries.append({"path": path, "type": ttype})
    top_level_paths = {e["path"] for e in top_level_entries}

    # README — try common casings; first hit wins.
    readme_excerpt: str | None = None
    for candidate in _README_CANDIDATES:
        if candidate not in top_level_paths:
            continue
        payload = await client.get_file_contents(
            repo_full_name=repo_full_name, path=candidate, ref=head_sha,
        )
        if payload is None:
            continue
        decoded = _decode_contents_payload(payload)
        if decoded:
            readme_excerpt = decoded[:_README_EXCERPT_CHARS]
            break

    # Manifest — first matching kind wins.
    manifest_kind: str | None = None
    manifest_excerpt: str | None = None
    for candidate in _MANIFEST_CANDIDATES:
        if candidate not in top_level_paths:
            continue
        payload = await client.get_file_contents(
            repo_full_name=repo_full_name, path=candidate, ref=head_sha,
        )
        if payload is None:
            continue
        decoded = _decode_contents_payload(payload)
        if decoded:
            manifest_kind = candidate
            manifest_excerpt = decoded[:_MANIFEST_EXCERPT_CHARS]
            break

    return {
        "repo_full_name": repo_full_name,
        "default_branch": default_branch,
        "head_sha": head_sha,
        "top_level_files": top_level_entries,
        "readme_excerpt": readme_excerpt,
        "manifest_kind": manifest_kind,
        "manifest_excerpt": manifest_excerpt,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
