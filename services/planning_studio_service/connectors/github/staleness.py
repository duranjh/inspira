"""Multi-PR staleness detection for the artifact viewer (Wave F.5).

Layers on top of F.3's PR overlay. Every overlay records the main SHA
it was drafted against (``v2_projects.base_main_sha``, written through
from ``build_overlay_tree`` on first successful build). When main
moves, this module compares the recorded baseline to the current
``default_branch`` head and intersects the GitHub-reported changed
files with the scaffold's own paths — surfacing how many of the
scaffold's files are at risk of conflict.

Three layers in the design brief: passive detection (this module),
soft-block on edit (FE modal), and the actual refresh (F.6, deferred).
F.5 lays the foundation; the response carries everything the UI needs
for both the banner and the per-file chevrons.

Cache: project-scoped, 60s TTL, ``(workspace_id, project_id)`` —
cleaner invalidation than coupling to the overlay's scaffold-aware
cache, since staleness can flip purely because of external pushes to
main with no local change to the scaffold.

Pagination cap: GitHub's compare API returns the first ~300 files in
the response body; larger diffs need ``Link: rel="next"`` follow-up
requests. For F.5 we treat that as a known limitation — the response
flags ``truncated=True`` so the FE can hint to the partner that the
affected count is a lower bound. F.6's 3-way diff will follow the
pagination chain.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from .app_jwt import GitHubAppConfig, installation_access_token
from .client import (
    GitHubClient,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)
from .pr_overlay import (
    PrOverlayError,
    _load_scaffold_manifest,
    _resolve_project_context,
)
from .repo_browse import RepoBrowseError, _resolve_repo_destination

if TYPE_CHECKING:
    from ...store import PlanningStudioStore


logger = logging.getLogger(__name__)


_STALENESS_CACHE_TTL_SECONDS = 60.0

# (workspace_id, project_id) -> (expires_at_monotonic, payload)
_STALENESS_CACHE: dict[
    tuple[str, str], tuple[float, dict[str, Any]],
] = {}


# Cap the affected-paths sample returned to the FE. The banner shows
# "N changes affect M files" and a few chevroned files — five is enough
# to telegraph the shape of the conflict without bloating the payload.
_AFFECTED_PATHS_SAMPLE_SIZE = 5


def _cache_get(
    key: tuple[str, str],
) -> dict[str, Any] | None:
    cached = _STALENESS_CACHE.get(key)
    if cached is None:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _STALENESS_CACHE.pop(key, None)
        return None
    return payload


def _cache_put(
    key: tuple[str, str], payload: dict[str, Any],
) -> None:
    _STALENESS_CACHE[key] = (
        time.monotonic() + _STALENESS_CACHE_TTL_SECONDS, payload,
    )


def reset_cache_for_tests() -> None:
    """Test-only hook — clear the in-process staleness cache.

    Matches the precedent set by ``pr_overlay.reset_cache_for_tests`` and
    ``repo_browse.reset_cache_for_tests`` so each test case can guarantee
    a cold lookup without test-flag branches in production code.
    """
    _STALENESS_CACHE.clear()


def invalidate_cache_for_project(
    *, workspace_id: str, project_id: str,
) -> None:
    """Pop the 60s cache entry for one project — Wave F.6 refresh hook.

    Without this, after the partner resolves a refresh the staleness
    banner would linger for up to 60s (next FE poll interval). Calling
    this from the refresh handler lets the next ``useStaleness.refresh()``
    return the post-refresh "not stale" payload immediately.

    No-op when the key isn't cached. Safe to call without checking.
    """
    _STALENESS_CACHE.pop((workspace_id, project_id), None)


def _extract_main_moved_at(compare_body: dict[str, Any]) -> str | None:
    """Pull the head commit's committer date from a compare response.

    Returns the ISO-8601 string GitHub already provides; we do not parse
    or re-format it server-side (the FE renders the relative "N ago"
    label). When the compare payload has no commits in scope (status =
    "identical" or unexpected shape), returns ``None``.
    """
    commits = compare_body.get("commits")
    if not isinstance(commits, list) or not commits:
        return None
    head_commit = commits[-1]
    if not isinstance(head_commit, dict):
        return None
    commit_inner = head_commit.get("commit")
    if not isinstance(commit_inner, dict):
        return None
    committer = commit_inner.get("committer")
    if not isinstance(committer, dict):
        return None
    date = committer.get("date")
    return date if isinstance(date, str) else None


def _legacy_payload(project_row: dict[str, Any]) -> dict[str, Any]:
    """Return the staleness shape for projects with no recorded baseline.

    Pre-F.5 projects (and anything that hasn't yet hit
    ``build_overlay_tree`` post-merge) have ``base_main_sha = NULL``.
    We surface ``legacy=True, is_stale=False`` and skip GitHub entirely
    — the row self-heals on the next overlay-tree fetch via the
    ``set_project_base_main_sha`` write-through.
    """
    return {
        "is_stale": False,
        "base_main_sha": None,
        "current_main_sha": None,
        "main_moved_at": None,
        "affected_files_count": 0,
        "scaffold_files_count": 0,
        "affected_paths_sample": [],
        "last_partner_edit": project_row.get("last_partner_edit"),
        "scaffold_drafted_at": project_row.get("created_at"),
        "legacy": True,
        "truncated": False,
    }


async def compute_staleness(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    user_id: str,
    app_config: GitHubAppConfig,
) -> dict[str, Any]:
    """Return the project's staleness payload, cached for 60s.

    Flow:
    1. Resolve project context (404 if missing/unowned/no workspace).
    2. Read ``base_main_sha`` — if NULL, return legacy payload (no
       GitHub call; the row self-heals on next overlay-tree open).
    3. Resolve current main SHA via ``get_repo_metadata`` →
       ``get_branch_sha`` (two-call pattern matching existing helpers).
    4. If ``base == current``, return non-stale payload.
    5. Otherwise call ``compare_commits`` and intersect the changed
       files with the scaffold's paths.
    """
    ctx = _resolve_project_context(store, project_id=project_id)
    workspace_id = ctx["workspace_id"]

    cache_key = (workspace_id, project_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    project_row = store._get_v2_project(project_id)  # noqa: SLF001
    # _resolve_project_context already guarantees the row exists, but
    # mypy doesn't know that — and the read above is needed regardless
    # for base_main_sha + last_partner_edit + created_at.
    if project_row is None:  # pragma: no cover - defensive
        raise PrOverlayError(
            status_code=404,
            error="project_not_found",
        )

    base_main_sha = project_row.get("base_main_sha")
    if not base_main_sha:
        payload = _legacy_payload(project_row)
        _cache_put(cache_key, payload)
        return payload

    # Resolve workspace's connected repo (raises RepoBrowseError 409 if
    # the workspace isn't connected — the route layer translates).
    installation_id, repo_full_name = _resolve_repo_destination(
        store, workspace_id=workspace_id,
    )

    # Two GitHub calls + (optional) compare. Wrapped in a single async
    # HTTP client so the token mint + reads share a connection pool.
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            token, _expires_at = await installation_access_token(
                installation_id=installation_id,
                config=app_config,
                http=http,
            )
            client = GitHubClient(installation_token=token, http=http)

            metadata = await client.get_repo_metadata(
                repo_full_name=repo_full_name,
            )
            default_branch = metadata.get("default_branch") or "main"
            current_main_sha = await client.get_branch_sha(
                repo_full_name=repo_full_name, branch=default_branch,
            )

            if current_main_sha == base_main_sha:
                # No drift — short-circuit before compare_commits.
                scaffold_files_count = len(
                    _load_scaffold_manifest(
                        store, project_id=project_id, user_id=user_id,
                    )[1],
                )
                payload = {
                    "is_stale": False,
                    "base_main_sha": base_main_sha,
                    "current_main_sha": current_main_sha,
                    "main_moved_at": None,
                    "affected_files_count": 0,
                    "scaffold_files_count": scaffold_files_count,
                    "affected_paths_sample": [],
                    "last_partner_edit": project_row.get("last_partner_edit"),
                    "scaffold_drafted_at": project_row.get("created_at"),
                    "legacy": False,
                    "truncated": False,
                }
                _cache_put(cache_key, payload)
                return payload

            compare_body = await client.compare_commits(
                repo_full_name=repo_full_name,
                base=base_main_sha,
                head=current_main_sha,
            )
    except GitHubNotFound as exc:
        # Most likely ``base_main_sha`` was force-pushed out of the
        # repo's history. Surface a distinct error so the FE can clear
        # the badge with a "this baseline no longer exists" hint.
        raise PrOverlayError(
            status_code=409,
            error="base_sha_unreachable",
            message=(
                "the recorded base commit is no longer in the repo's "
                "history — main may have been force-pushed"
            ),
            extra={"base_main_sha": base_main_sha},
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

    # Intersect the GitHub-reported changed files with the project's
    # scaffold manifest. Only paths that the scaffold actually owns
    # count toward ``affected_files_count`` — drift in unrelated
    # corners of the repo doesn't matter to this overlay.
    _, scaffold_files = _load_scaffold_manifest(
        store, project_id=project_id, user_id=user_id,
    )
    scaffold_paths = {sf["path"] for sf in scaffold_files}

    raw_files = compare_body.get("files")
    changed_files: list[str] = []
    if isinstance(raw_files, list):
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            filename = entry.get("filename")
            if isinstance(filename, str) and filename:
                changed_files.append(filename)

    affected = [p for p in changed_files if p in scaffold_paths]
    affected_count = len(affected)
    # Stable sample order so the FE renders the same chevrons across
    # 60s cache hits within a session.
    affected_sample = sorted(affected)[:_AFFECTED_PATHS_SAMPLE_SIZE]

    # GitHub returns up to ~300 files per page; the response is
    # truncated when there are more. We don't follow pagination in
    # F.5 — flag ``truncated=True`` and let the FE hint about it.
    truncated = isinstance(raw_files, list) and len(raw_files) >= 300

    main_moved_at = _extract_main_moved_at(compare_body)

    payload: dict[str, Any] = {
        # ``is_stale`` is True iff the SHAs diverged AND at least one
        # of the changed files overlaps the scaffold. Drift in
        # untouched corners of the repo doesn't dirty this overlay.
        "is_stale": affected_count > 0,
        "base_main_sha": base_main_sha,
        "current_main_sha": current_main_sha,
        "main_moved_at": main_moved_at,
        "affected_files_count": affected_count,
        "scaffold_files_count": len(scaffold_files),
        "affected_paths_sample": affected_sample,
        "last_partner_edit": project_row.get("last_partner_edit"),
        "scaffold_drafted_at": project_row.get("created_at"),
        "legacy": False,
        "truncated": truncated,
    }
    _cache_put(cache_key, payload)
    return payload
