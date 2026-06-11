"""Refresh PR with Inspira — orchestrator for Wave F.6 (#147).

Re-runs the scaffold adapter against the fresh main with the project's
current scaffold as redraft reference. Persists a new scaffold, resets
the staleness baseline, and records a row in
``v2_scaffold_refresh_history`` so the FE's 3-way diff endpoint can
resolve the precise pre/post pair.

The 3-way diff itself is computed by the diff route (api.py) — not
here — because the diff is a read against the persisted state, not a
side-effect of the refresh run.

Concurrency model:
- The first POST /refresh-overlay for a project inserts a row with
  ``status='in_progress'`` BEFORE the LLM call. A concurrent second
  POST sees that row and raises ``PrOverlayError(409,
  refresh_in_progress)``. Whichever request wins the insert race
  proceeds; the loser surfaces a clean 409 with the in-progress
  refresh_id so the FE can poll the same handle.
- On adapter failure the row is updated to ``status='failed'`` so a
  retry can proceed.

Provider rule: the caller (route handler) does tier dispatch and
passes the resolved adapter. This helper is provider-agnostic — it
never reads ``preferred_model_tier`` directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from .code_scaffold import sanitize_scaffold_manifest
from ..connectors.github.app_jwt import (
    GitHubAppConfig,
    installation_access_token,
)
from ..connectors.github.client import (
    GitHubClient,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)
from ..connectors.github.pr_overlay import (
    PrOverlayError,
    _load_scaffold_manifest,
    _resolve_project_context,
)
from ..connectors.github.repo_browse import (
    RepoBrowseError,
    _resolve_repo_destination,
)
from ..connectors.github.repo_context import fetch_repo_context
from ..connectors.github import staleness as staleness_module

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


logger = logging.getLogger(__name__)


def _scaffold_files_to_manifest_dict(
    files: list[dict[str, Any]],
) -> dict[str, str]:
    """Reduce a scaffold's file list to ``{path: content}`` for the
    redraft-reference prompt section. Skips non-string content (e.g.
    accidentally-binary entries) defensively.
    """
    out: dict[str, str] = {}
    for entry in files:
        path = entry.get("path")
        content = entry.get("content")
        if isinstance(path, str) and isinstance(content, str):
            out[path] = content
    return out


def _changed_paths(
    previous: list[dict[str, Any]], new: list[dict[str, Any]],
) -> list[str]:
    """Set-difference of file paths plus content-divergent files.

    A file is "changed" if its path is new, removed, or its content
    differs between the previous + new scaffold manifests. Returns a
    sorted list so the response payload is stable across calls.
    """
    prev_by_path = {
        e["path"]: e.get("content")
        for e in previous
        if isinstance(e, dict) and isinstance(e.get("path"), str)
    }
    new_by_path = {
        e["path"]: e.get("content")
        for e in new
        if isinstance(e, dict) and isinstance(e.get("path"), str)
    }
    changed: set[str] = set()
    for path, content in new_by_path.items():
        if path not in prev_by_path or prev_by_path[path] != content:
            changed.add(path)
    for path in prev_by_path:
        if path not in new_by_path:
            changed.add(path)
    return sorted(changed)


async def refresh_pr_overlay(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    user_id: str,
    app_config: GitHubAppConfig,
    scaffold_adapter: Any,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Re-run the scaffold adapter against fresh main + previous draft.

    Returns ``{scaffold_id, refresh_id, base_main_sha, changed_paths,
    changed_count}``.

    Raises ``PrOverlayError(404)`` for unknown project, 409
    ``refresh_in_progress`` for a concurrent kickoff, or propagates
    ``RepoBrowseError`` from the underlying GitHub helpers.
    """
    ctx = _resolve_project_context(store, project_id=project_id)
    workspace_id = ctx["workspace_id"]

    # Concurrency guard — surface 409 to a second POST while the first
    # is still running. Cheap point read (single indexed row by
    # project_id + status='in_progress').
    in_progress = store.get_in_progress_refresh_for_project(
        project_id=project_id,
    )
    if in_progress is not None:
        raise PrOverlayError(
            status_code=409,
            error="refresh_in_progress",
            message=(
                "A refresh is already running for this project. "
                "Poll the existing refresh_id for status."
            ),
            extra={"refresh_id": in_progress["refresh_id"]},
        )

    project_row = store._get_v2_project(project_id)  # noqa: SLF001
    if project_row is None:  # pragma: no cover - defensive
        raise PrOverlayError(
            status_code=404, error="project_not_found",
        )

    base_main_sha_before = project_row.get("base_main_sha") or ""

    # Load the current scaffold (its files already reflect partner
    # edits via autosave + per-file original_content baselines from
    # BE-0). Empty manifest is a legal state — partner has never
    # generated a scaffold — but refresh is meaningless then.
    previous_scaffold_id, previous_files = _load_scaffold_manifest(
        store, project_id=project_id, user_id=user_id,
    )
    if not previous_files:
        raise PrOverlayError(
            status_code=409,
            error="no_scaffold_to_refresh",
            message=(
                "There's no scaffold to refresh yet. Generate the "
                "first artifact before requesting a refresh."
            ),
        )

    # Insert the in-progress row BEFORE the LLM call so the 409
    # concurrency guard is race-safe. If the second POST arrives
    # between this insert and the adapter call, it sees the row and
    # 409s cleanly.
    refresh_row = store.create_scaffold_refresh_history(
        project_id=project_id,
        base_main_sha_before=base_main_sha_before,
        previous_scaffold_id=previous_scaffold_id,
        preserve_partner_edits=True,
    )
    refresh_id = refresh_row["refresh_id"]

    try:
        # Fresh repo context — same helper F.1 uses for the initial
        # scaffold generation. Degrades to None on missing GitHub
        # creds or transient errors; the adapter handles None.
        try:
            repo_context = await fetch_repo_context(
                store, workspace_id=workspace_id, timeout_s=12.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[refresh_pr] repo_context unavailable for project=%s: %s",
                project_id, exc,
            )
            repo_context = None

        # Two-call pattern mirroring staleness.py — resolve the current
        # main SHA so we can reset the baseline AFTER the redraft
        # succeeds. Failures here are upstream errors; surface them
        # before persisting a half-built refresh row.
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
                metadata = await client.get_repo_metadata(
                    repo_full_name=repo_full_name,
                )
                default_branch = metadata.get("default_branch") or "main"
                current_main_sha = await client.get_branch_sha(
                    repo_full_name=repo_full_name, branch=default_branch,
                )
        except GitHubUnauthorized as exc:
            raise RepoBrowseError(
                status_code=502, error="github_unauthorized",
                message="GitHub rejected the installation token",
            ) from exc
        except GitHubRateLimited as exc:
            raise RepoBrowseError(
                status_code=429, error="github_rate_limited",
            ) from exc
        except GitHubNotFound as exc:
            raise RepoBrowseError(
                status_code=404, error="github_branch_missing",
                message="default branch ref not found",
            ) from exc
        except (GitHubTransient, httpx.HTTPError) as exc:
            raise RepoBrowseError(
                status_code=502, error="github_upstream",
                message=str(exc),
            ) from exc

        previous_scaffold_dict = _scaffold_files_to_manifest_dict(
            previous_files,
        )

        # Re-use the kickoff state for the adapter call — title +
        # plan-summary + topics + decisions. These don't change
        # mid-refresh; the adapter's job is "redraft the same intent
        # against the new main + previous draft." Pulled straight
        # from the store rather than the artifact overlay (the
        # overlay only carries scaffold pointer + chat messages).
        project_title = (project_row.get("title") or "").strip() or "Untitled"
        topics = store.list_topics(project_id=project_id, user_id=user_id)
        decisions = store.list_decisions(
            project_id=project_id, user_id=user_id,
        )
        try:
            summary_row = store.latest_summary_version(project_id=project_id)
        except Exception:  # noqa: BLE001
            summary_row = None
        summary_markdown = ""
        if summary_row and isinstance(summary_row, dict):
            summary_markdown = (
                summary_row.get("content_markdown") or ""
            ).strip()
        locale: str | None = None

        # Adapter call wrapped in run_in_executor to keep the FastAPI
        # event loop unblocked — same shape as F.1's generate-stream
        # handler. 120s soft cap matches the scaffold adapter's
        # config default.
        loop = asyncio.get_running_loop()
        gen_kwargs: dict[str, Any] = {
            "project_title": project_title,
            "summary_markdown": summary_markdown,
            "topics": topics,
            "decisions": decisions,
            "locale": locale,
            "repo_context": repo_context,
            "previous_scaffold": previous_scaffold_dict,
        }
        if model_override is not None:
            gen_kwargs["model_override"] = model_override

        parsed = await loop.run_in_executor(
            None,
            lambda: scaffold_adapter.generate(**gen_kwargs),
        )

        # Sanitize defensively in case the adapter's own pass let
        # something through. ``sanitize_scaffold_manifest`` is
        # idempotent.
        sanitize_scaffold_manifest(parsed)
        new_files = parsed.get("files") or []
        framework = parsed.get("framework") or "react-vite"
        language = parsed.get("language") or "typescript"

        new_manifest = {
            "framework": framework,
            "language": language,
            "files": new_files,
            "readme_preview": parsed.get("readme_preview", ""),
            "post_install_steps": parsed.get("post_install_steps", []),
            "truncation_note": parsed.get("truncation_note", ""),
        }
        new_scaffold = store.create_scaffold(
            project_id=project_id,
            user_id=user_id,
            framework=framework,
            language=language,
            manifest_json=json.dumps(new_manifest),
        )
        new_scaffold_id = new_scaffold["scaffold_id"]

        # Flip the project's artifact overlay to point at the new
        # scaffold. Preserves other overlay fields (messages, etc.).
        existing_artifact = store.get_v2_project_artifact(
            project_id=project_id,
        ) or {}
        existing_artifact["latest_scaffold_id"] = new_scaffold_id
        if model_override:
            existing_artifact["model_used"] = model_override
        store.set_v2_project_artifact(
            project_id=project_id, artifact=existing_artifact,
        )

        # Reset baseline to the post-refresh main SHA — unconditional
        # write via the F.6-added setter. Otherwise the original
        # snapshot would persist and the banner would never clear.
        store.reset_project_base_main_sha(
            project_id=project_id, base_main_sha=current_main_sha,
        )

        changed = _changed_paths(previous_files, new_files)

        store.update_scaffold_refresh_history(
            refresh_id=refresh_id,
            status="completed",
            new_scaffold_id=new_scaffold_id,
            base_main_sha_after=current_main_sha,
            changed_paths=changed,
        )

        # Pop the 60s staleness cache so the partner's next
        # useStaleness.refresh() returns post-refresh state
        # immediately rather than waiting up to 60s.
        staleness_module.invalidate_cache_for_project(
            workspace_id=workspace_id, project_id=project_id,
        )

        return {
            "scaffold_id": new_scaffold_id,
            "refresh_id": refresh_id,
            "base_main_sha": current_main_sha,
            "changed_paths": changed,
            "changed_count": len(changed),
        }
    except (PrOverlayError, RepoBrowseError):
        store.update_scaffold_refresh_history(
            refresh_id=refresh_id, status="failed",
        )
        raise
    except Exception:
        store.update_scaffold_refresh_history(
            refresh_id=refresh_id, status="failed",
        )
        raise
