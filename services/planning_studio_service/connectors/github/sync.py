"""Workspace-scoped GitHub sync orchestration.

W2 watch point #4 (sync idempotency on retry): every persistence
call goes through ``connectors.store.upsert_repo_snapshot`` which
uses INSERT...ON CONFLICT on the composite PK
``(workspace_id, provider, repo_id)``. Re-running a sync replaces
the snapshot row in place — no duplicates, ever.

Failure-mode routing (per the v4 plan):
- ``GitHubUnauthorized``  → mark credential needs_reauth; close
                              run as ``needs_reauth`` (the
                              connector-tile's "Reconnect →"
                              variant fires).
- ``GitHubRateLimited``   → close run as ``rate_limited`` (the
                              polling job will skip on next tick
                              if the reset is still in the future).
- ``GitHubTransient``     → already retried 3x in the client;
                              close run as ``error`` with the
                              underlying message.
- ``GitHubNotFound`` (per repo) → skip that repo, log, continue
                              with the others; record
                              ``repos_synced = N - skipped``.

W2 scope: top-3 repos by ``pushed_at`` per the engineering plan
(F3, "Pulls repo tree + open issue list + last 100 commits per
connected repo"). The 100-commits cap is a future bump; W2 ships
10 to keep the snapshot small.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from .. import store as connectors_store
from ..base import ConnectorStatus
from .app_jwt import GitHubAppConfig, installation_access_token
from .client import (
    GitHubClient,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)

if TYPE_CHECKING:
    from ...store import PlanningStudioStore


logger = logging.getLogger(__name__)


def _select_top_repos(
    repos: list[dict[str, Any]], k: int = 3
) -> list[dict[str, Any]]:
    """Pick the ``k`` most-recently-pushed repos. Treats missing
    ``pushed_at`` as "very old" so quiet repos sort last."""

    def key(r: dict[str, Any]) -> str:
        return r.get("pushed_at") or ""

    return sorted(repos, key=key, reverse=True)[:k]


async def _sync_one_repo(
    *,
    client: GitHubClient,
    repo: dict[str, Any],
    store: "PlanningStudioStore",
    workspace_id: str,
) -> bool:
    """Snapshot a single repo. Returns True on success, False on
    per-repo skip (404). Other errors propagate to the workspace
    sync wrapper so they can be classified into a status.

    The snapshot blob is intentionally small — ~100 tree paths
    + 20 issues + 10 commits per repo. The W3 planner reads this
    whole; size affects no other consumer.
    """
    repo_id = str(repo["id"])
    repo_full_name = repo["full_name"]
    default_branch = repo.get("default_branch") or "main"
    visibility = "private" if repo.get("private") else "public"

    try:
        tree = await client.get_repo_tree(
            repo_full_name=repo_full_name,
            ref=default_branch,
            recursive=True,
        )
        issues = await client.list_open_issues(
            repo_full_name=repo_full_name,
            per_page=20,
        )
        commits = await client.list_recent_commits(
            repo_full_name=repo_full_name,
            branch=default_branch,
            per_page=10,
        )
    except GitHubNotFound:
        logger.info(
            "github_sync skipping %s for workspace %s (404)",
            repo_full_name,
            workspace_id,
        )
        return False

    # Trim the tree payload to the top 100 entries — keeps the
    # snapshot reasonable for repos with deep trees. The W3
    # planner doesn't need the full tree.
    tree_entries = (tree.get("tree") or [])[:100]

    snapshot: dict[str, Any] = {
        "tree_top": [
            {
                "path": e.get("path"),
                "type": e.get("type"),
                "size": e.get("size"),
            }
            for e in tree_entries
        ],
        "open_issues": [
            {
                "number": i.get("number"),
                "title": i.get("title"),
                "labels": [
                    label.get("name")
                    for label in (i.get("labels") or [])
                ],
                "created_at": i.get("created_at"),
            }
            for i in issues
        ],
        "recent_commits": [
            {
                "sha": c.get("sha"),
                "message": (c.get("commit") or {}).get("message"),
                "author": ((c.get("commit") or {}).get("author") or {}).get(
                    "name"
                ),
                "date": ((c.get("commit") or {}).get("author") or {}).get(
                    "date"
                ),
            }
            for c in commits
        ],
    }

    connectors_store.upsert_repo_snapshot(
        store,
        workspace_id=workspace_id,
        provider="github",
        repo_id=repo_id,
        repo_full_name=repo_full_name,
        default_branch=default_branch,
        visibility=visibility,
        snapshot=snapshot,
        status="fresh",
    )
    return True


async def sync_workspace(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    trigger: str,
    config: GitHubAppConfig,
    http: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Sync top-3 repos for a single workspace's GitHub connector.

    Args:
        store: shared planning-studio store.
        workspace_id: which workspace to sync. The credential row
            is fetched by composite PK ``(workspace_id, 'github')``.
        trigger: ``'manual' | 'scheduled' | 'install'`` — written
            to the sync_run row for observability.
        config: GitHubAppConfig (app_id + private_key + slug).
        http: optional injected httpx.AsyncClient. When None, a
            fresh client is created with a 30s timeout.

    Returns:
        ``{run_id, status, repos_synced}``. Status mirrors the
        sync_run row: 'ok' / 'error' / 'rate_limited' /
        'needs_reauth'.
    """
    from ...byok import decrypt_api_key  # noqa: PLC0415

    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="github"
    )
    if cred is None:
        return {
            "run_id": None,
            "status": "skipped",
            "repos_synced": 0,
            "reason": "no_credential",
        }

    installation_id = cred.get("installation_id")
    if not installation_id:
        return {
            "run_id": None,
            "status": "error",
            "repos_synced": 0,
            "reason": "missing_installation_id",
        }

    run_id = connectors_store.start_sync_run(
        store,
        workspace_id=workspace_id,
        provider="github",
        trigger=trigger,
    )

    owns_http = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=30.0)
    try:
        try:
            installation_token, _ = await installation_access_token(
                config=config,
                installation_id=installation_id,
                http=http,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else None
            if status in (401, 403, 404):
                # The install was uninstalled or the App's keys
                # rotated. Mark credential needs_reauth and close
                # the run.
                connectors_store.mark_credential_status(
                    store,
                    workspace_id=workspace_id,
                    provider="github",
                    status="needs_reauth",
                )
                connectors_store.finish_sync_run(
                    store,
                    run_id=run_id,
                    status="needs_reauth",
                    repos_synced=0,
                    error=f"installation token mint failed: HTTP {status}",
                )
                return {
                    "run_id": run_id,
                    "status": "needs_reauth",
                    "repos_synced": 0,
                }
            connectors_store.finish_sync_run(
                store,
                run_id=run_id,
                status="error",
                repos_synced=0,
                error=f"installation token mint failed: HTTP {status}",
            )
            return {
                "run_id": run_id,
                "status": "error",
                "repos_synced": 0,
            }

        client = GitHubClient(
            installation_token=installation_token, http=http
        )

        try:
            repos = await client.list_installation_repos(per_page=100)
        except GitHubUnauthorized:
            connectors_store.mark_credential_status(
                store,
                workspace_id=workspace_id,
                provider="github",
                status="needs_reauth",
            )
            connectors_store.finish_sync_run(
                store,
                run_id=run_id,
                status="needs_reauth",
                repos_synced=0,
                error="installation_repos: 401",
            )
            return {
                "run_id": run_id,
                "status": "needs_reauth",
                "repos_synced": 0,
            }
        except GitHubRateLimited as exc:
            connectors_store.finish_sync_run(
                store,
                run_id=run_id,
                status="rate_limited",
                repos_synced=0,
                error=str(exc),
            )
            return {
                "run_id": run_id,
                "status": "rate_limited",
                "repos_synced": 0,
                # The scheduler reads this to skip the workspace
                # until GitHub's rate-limit reset has elapsed —
                # avoids hammering when we already know the next
                # call will fail. May be None if GitHub's response
                # didn't carry an X-RateLimit-Reset header.
                "rate_limit_reset_at": exc.reset_at,
            }
        except GitHubError as exc:
            connectors_store.finish_sync_run(
                store,
                run_id=run_id,
                status="error",
                repos_synced=0,
                error=str(exc),
            )
            return {
                "run_id": run_id,
                "status": "error",
                "repos_synced": 0,
            }

        synced = 0
        skipped = 0
        last_error: str | None = None
        for repo in _select_top_repos(repos, k=3):
            try:
                if await _sync_one_repo(
                    client=client,
                    repo=repo,
                    store=store,
                    workspace_id=workspace_id,
                ):
                    synced += 1
                else:
                    skipped += 1
            except GitHubUnauthorized:
                connectors_store.mark_credential_status(
                    store,
                    workspace_id=workspace_id,
                    provider="github",
                    status="needs_reauth",
                )
                connectors_store.finish_sync_run(
                    store,
                    run_id=run_id,
                    status="needs_reauth",
                    repos_synced=synced,
                    error="repo sync: 401",
                )
                return {
                    "run_id": run_id,
                    "status": "needs_reauth",
                    "repos_synced": synced,
                }
            except GitHubRateLimited as exc:
                connectors_store.finish_sync_run(
                    store,
                    run_id=run_id,
                    status="rate_limited",
                    repos_synced=synced,
                    error=str(exc),
                )
                return {
                    "run_id": run_id,
                    "status": "rate_limited",
                    "repos_synced": synced,
                    "rate_limit_reset_at": exc.reset_at,
                }
            except GitHubTransient as exc:
                last_error = str(exc)
                logger.warning(
                    "github_sync transient on %s for workspace %s: %s",
                    repo.get("full_name"),
                    workspace_id,
                    exc,
                )
                # Keep going — partial syncs surface in last_error.

        if synced == 0 and last_error is not None:
            connectors_store.finish_sync_run(
                store,
                run_id=run_id,
                status="error",
                repos_synced=0,
                error=last_error,
            )
            return {
                "run_id": run_id,
                "status": "error",
                "repos_synced": 0,
            }

        connectors_store.finish_sync_run(
            store,
            run_id=run_id,
            status="ok",
            repos_synced=synced,
            error=last_error,
        )
        return {
            "run_id": run_id,
            "status": "ok",
            "repos_synced": synced,
            "skipped": skipped,
        }
    finally:
        if owns_http:
            await http.aclose()
