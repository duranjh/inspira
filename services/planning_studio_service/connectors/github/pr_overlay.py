"""Overlay a project's scaffold on the workspace repo tree (Wave F.3).

Backs the artifact-viewer "Repo" tab's ``PRs/<category>/<slug>/`` folders.
For a given project, we fetch the workspace's base GitHub tree (via F.2's
``fetch_repo_tree``) and merge the project's latest scaffold file list on
top. Each tree entry is tagged with a ``source`` so the FE can render a
"modified" badge:

- ``base``      — exists only in the GitHub repo
- ``scaffold``  — exists only in the project scaffold (new file)
- ``modified``  — present in both (path collision = scaffold overrides)

Two helpers exposed to the API layer:
- ``build_overlay_tree``: returns the merged tree + project metadata.
- ``fetch_overlay_file``: returns scaffold content for ``scaffold`` /
  ``modified`` paths; for ``base`` paths returns a sentinel so the FE
  falls through to the existing F.2 ``/repo/file`` route. We deliberately
  avoid a server-side redirect — the FE owns the dispatch.

Owner-only auth: the API layer guards with ``_require_owned_project``
matching the existing artifact CRUD pattern. ``get_scaffold`` itself
enforces user-scoped ownership.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .app_jwt import GitHubAppConfig
from .repo_browse import RepoBrowseError, fetch_repo_tree

if TYPE_CHECKING:
    from ...store import PlanningStudioStore


logger = logging.getLogger(__name__)


_OVERLAY_CACHE_TTL_SECONDS = 60.0

# (workspace_id, project_id, latest_scaffold_id_or_empty) -> (expires_at, payload)
_OVERLAY_CACHE: dict[
    tuple[str, str, str], tuple[float, dict[str, Any]],
] = {}


_KNOWN_CATEGORIES = frozenset(
    {"bug", "feature", "complaint", "praise", "question", "general"},
)


class PrOverlayError(Exception):
    """Carries status_code + detail dict so the route layer can surface
    it as a standard ``HTTPException``. Mirrors F.2's ``RepoBrowseError``
    so the FE's ``parseRepoBrowseError`` helper handles both uniformly.
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


def _cache_get(
    key: tuple[str, str, str],
) -> dict[str, Any] | None:
    cached = _OVERLAY_CACHE.get(key)
    if cached is None:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _OVERLAY_CACHE.pop(key, None)
        return None
    return payload


def _cache_put(
    key: tuple[str, str, str], payload: dict[str, Any],
) -> None:
    _OVERLAY_CACHE[key] = (
        time.monotonic() + _OVERLAY_CACHE_TTL_SECONDS, payload,
    )


def _resolve_project_context(
    store: "PlanningStudioStore", *, project_id: str,
) -> dict[str, Any]:
    """Return ``{project_id, workspace_id, title, dominant_category}``.

    Raises ``PrOverlayError(404)`` when the project is missing OR has
    no workspace_id (no PR overlay possible without a connected
    workspace; the FE renders the empty-state CTA in that case).
    """
    row = store._get_v2_project(project_id)  # noqa: SLF001
    if row is None:
        raise PrOverlayError(
            status_code=404,
            error="project_not_found",
        )
    workspace_id = row.get("workspace_id")
    if not workspace_id:
        raise PrOverlayError(
            status_code=404,
            error="project_not_found",
            message="project is not associated with a workspace",
        )
    metadata = row.get("metadata") or {}
    raw_category = metadata.get("dominant_category")
    category = (
        raw_category
        if isinstance(raw_category, str) and raw_category in _KNOWN_CATEGORIES
        else "general"
    )
    return {
        "project_id": project_id,
        "workspace_id": str(workspace_id),
        "title": str(row.get("title") or ""),
        "dominant_category": category,
    }


def _load_scaffold_manifest(
    store: "PlanningStudioStore", *, project_id: str, user_id: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return ``(latest_scaffold_id_or_None, list_of_file_entries)``.

    Empty list when no artifact has been generated, when the scaffold
    row is missing/cross-user, or when ``manifest_json`` fails to parse.
    Mirrors the defensive parsing pattern used by the artifact GET
    endpoint in ``api.py``.
    """
    artifact = store.get_v2_project_artifact(project_id=project_id)
    if artifact is None:
        return None, []
    scaffold_id = artifact.get("latest_scaffold_id")
    if not scaffold_id:
        return None, []
    row = store.get_scaffold(
        scaffold_id=str(scaffold_id), user_id=user_id,
    )
    if row is None:
        return str(scaffold_id), []
    try:
        manifest = json.loads(row.get("manifest_json") or "{}")
    except (TypeError, ValueError):
        manifest = {}
    raw_files = manifest.get("files") if isinstance(manifest, dict) else None
    files: list[dict[str, Any]] = []
    if isinstance(raw_files, list):
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            content = entry.get("content")
            if not isinstance(path, str) or not path:
                continue
            files.append(
                {"path": path, "content": content if isinstance(content, str) else ""},
            )
    return str(scaffold_id), files


def _merge_overlay(
    *,
    base_tree: list[dict[str, Any]],
    scaffold_files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge scaffold files on top of base tree.

    Returns ``(merged_entries, warnings)``. Comparison is case-exact —
    matches GitHub's native semantics. We surface case-folded collisions
    (e.g. ``src/Foo.tsx`` and ``src/foo.tsx``) as a warning so the FE
    can hint to the reviewer that some entries may collide on
    case-insensitive filesystems; both entries are kept either way.
    """
    by_path: dict[str, dict[str, Any]] = {}
    base_paths_lower: dict[str, str] = {}

    for entry in base_tree:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        by_path[path] = {
            "path": path,
            "type": "blob",
            "size": entry.get("size"),
            "source": "base",
        }
        base_paths_lower.setdefault(path.lower(), path)

    collisions: dict[str, list[str]] = {}
    for sf in scaffold_files:
        path = sf["path"]
        size = len(sf["content"].encode("utf-8"))
        if path in by_path:
            by_path[path] = {
                "path": path,
                "type": "blob",
                "size": size,
                "source": "modified",
            }
        else:
            by_path[path] = {
                "path": path,
                "type": "blob",
                "size": size,
                "source": "scaffold",
            }
        # Case-fold collision check — only flag when the lowercased
        # path matches a DIFFERENT cased path already in the map.
        lower = path.lower()
        existing_cased = base_paths_lower.get(lower)
        if existing_cased is not None and existing_cased != path:
            collisions.setdefault(lower, [existing_cased]).append(path)
        else:
            base_paths_lower.setdefault(lower, path)

    merged = sorted(by_path.values(), key=lambda e: e["path"])
    warnings: list[dict[str, Any]] = []
    for lower, paths in collisions.items():
        # De-dup while preserving order.
        seen: set[str] = set()
        dedup: list[str] = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                dedup.append(p)
        if len(dedup) >= 2:
            warnings.append({"kind": "case_collision", "paths": dedup})
    return merged, warnings


async def build_overlay_tree(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    user_id: str,
    app_config: GitHubAppConfig,
) -> dict[str, Any]:
    """Return the project's overlay tree payload, cached for 60s.

    Cache key includes ``latest_scaffold_id`` so a Regenerate
    automatically invalidates without an explicit bust.
    Raises ``PrOverlayError`` (404 missing project / no workspace) or
    propagates ``RepoBrowseError`` (409 not_connected / 404 ref / etc.)
    """
    ctx = _resolve_project_context(store, project_id=project_id)
    workspace_id = ctx["workspace_id"]

    # Resolve scaffold first — the cache key needs ``latest_scaffold_id``
    # before we hit GitHub.
    scaffold_id, scaffold_files = _load_scaffold_manifest(
        store, project_id=project_id, user_id=user_id,
    )

    cache_key = (workspace_id, project_id, scaffold_id or "")
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Base tree fetch — surfaces F.2's RepoBrowseError unchanged
    # (route layer translates it).
    base_payload = await fetch_repo_tree(
        store=store,
        workspace_id=workspace_id,
        ref="main",
        recursive=True,
        app_config=app_config,
    )

    base_tree = base_payload.get("tree") or []
    merged, warnings = _merge_overlay(
        base_tree=base_tree, scaffold_files=scaffold_files,
    )

    payload: dict[str, Any] = {
        "project_id": project_id,
        "project_title": ctx["title"],
        "dominant_category": ctx["dominant_category"],
        "repo_full_name": base_payload.get("repo_full_name") or "",
        "base_ref": base_payload.get("ref") or "main",
        "base_sha": base_payload.get("sha") or "",
        "tree": merged,
        "truncated": bool(base_payload.get("truncated")),
        "warnings": warnings,
    }

    # F.5 staleness write-through: snapshot the main SHA the overlay was
    # drafted against. ``set_project_base_main_sha`` is idempotent —
    # only writes when the column is currently NULL, so subsequent
    # overlay rebuilds (which happen post-scaffold and would otherwise
    # silently re-baseline) leave the original snapshot intact. Wrap
    # defensively: a write-through failure must NOT break overlay
    # rendering — that would erode partner trust in the surface to
    # avoid a feature that's purely advisory.
    base_sha_for_snapshot = payload["base_sha"]
    if base_sha_for_snapshot:
        try:
            store.set_project_base_main_sha(
                project_id=project_id,
                base_main_sha=base_sha_for_snapshot,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "set_project_base_main_sha failed for %s — overlay "
                "still served; staleness will fall back to legacy "
                "until the next successful write-through",
                project_id,
            )

    _cache_put(cache_key, payload)
    return payload


async def fetch_overlay_file(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    user_id: str,
    path: str,
    app_config: GitHubAppConfig,
) -> dict[str, Any]:
    """Return the file content for a scaffold/modified path, or a
    ``source: "base"`` sentinel for paths that exist only in the repo.

    The FE bridges the sentinel by re-issuing against F.2's
    ``/repo/file`` route. Server-side redirect would tangle two cache
    layers; FE dispatch is the clean split.

    Reads ``source`` from the merged overlay tree so the per-file
    answer agrees with the tree's badge for the same row.
    """
    overlay = await build_overlay_tree(
        store,
        project_id=project_id,
        user_id=user_id,
        app_config=app_config,
    )

    entry = next(
        (e for e in overlay.get("tree") or [] if e.get("path") == path),
        None,
    )
    source = entry.get("source") if isinstance(entry, dict) else None

    if source in ("scaffold", "modified"):
        _, scaffold_files = _load_scaffold_manifest(
            store, project_id=project_id, user_id=user_id,
        )
        scaffold_by_path = {sf["path"]: sf["content"] for sf in scaffold_files}
        return {
            "path": path,
            "content": scaffold_by_path.get(path, ""),
            "binary": False,
            "source": source,
            "encoding": "utf-8",
        }

    # Either present only in the base repo, or not in the overlay at
    # all — both routes fall through to F.2's /repo/file via the FE.
    return {
        "path": path,
        "content": None,
        "binary": False,
        "source": "base",
        "encoding": "utf-8",
    }


def reset_cache_for_tests() -> None:
    """Test-only hook — drops the in-process overlay cache.

    Mirrors ``repo_browse.reset_cache_for_tests`` so pytest cases can
    guarantee a cold cache between assertions.
    """
    _OVERLAY_CACHE.clear()
