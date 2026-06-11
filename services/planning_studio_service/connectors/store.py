"""Connector store helpers — free functions over PlanningStudioStore.

Workspace-scoped at the schema layer (composite PK
``(workspace_id, provider)`` on connector_credentials). Every
helper takes ``workspace_id`` as a keyword arg; there is no
user-keyed lookup path. A user who belongs to two workspaces and
connects GitHub on each writes two separate rows.

Encryption: ``encrypted_token`` columns hold Fernet ciphertext via
``byok.encrypt_api_key`` / ``decrypt_api_key`` (single global
``INSPIRA_BYOK_SECRET``). Plaintext never lives in the DB and never
gets logged — decrypt happens function-local at sync time, then
the in-memory string falls out of scope.
"""
from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any, Literal

from .base import ConnectorState, ConnectorStatus

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


def _now(store: "PlanningStudioStore") -> str:
    """ISO-8601 UTC timestamp matching the rest of the store."""
    from ..store import now_timestamp

    return now_timestamp()


# ---------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------


def upsert_credential(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
    encrypted_token: str,
    installation_id: str | None = None,
    account_login: str | None = None,
    account_avatar_url: str | None = None,
    scopes: list[str] | None = None,
) -> None:
    """Insert-or-replace a connector credential row.

    PK collision (re-installing a connector for the same workspace)
    is the expected upsert path. The composite PK
    ``(workspace_id, provider)`` keeps each (workspace, provider)
    pair to one row — multi-account same-provider in one workspace
    is intentionally closed off in v4.
    """
    now = _now(store)
    scopes_json = json.dumps(scopes or [])

    with store._connect() as connection:
        # SQLite + Postgres both support INSERT ... ON CONFLICT for
        # the upsert path. Workspace-scoped: composite PK is the
        # collision target.
        connection.execute(
            """
            INSERT INTO connector_credentials (
                workspace_id, provider, encrypted_token,
                installation_id, account_login, account_avatar_url,
                scopes_json, created_at, last_refreshed_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'connected')
            ON CONFLICT (workspace_id, provider) DO UPDATE SET
                encrypted_token    = excluded.encrypted_token,
                installation_id    = excluded.installation_id,
                account_login      = excluded.account_login,
                account_avatar_url = excluded.account_avatar_url,
                scopes_json        = excluded.scopes_json,
                last_refreshed_at  = excluded.last_refreshed_at,
                status             = 'connected'
            """,
            (
                workspace_id,
                provider,
                encrypted_token,
                installation_id,
                account_login,
                account_avatar_url,
                scopes_json,
                now,
                now,
            ),
        )
        connection.commit()


def get_credential(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
) -> dict[str, Any] | None:
    """Fetch a credential row; None when absent.

    Returns a dict with the encrypted token still encrypted —
    callers decrypt at use time via ``byok.decrypt_api_key``. The
    ``metadata`` key is the json-loaded ``metadata_json`` column;
    free-form per-provider destination/config (e.g. Linear default
    team, GitHub default repo).
    """
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT
                workspace_id, provider, encrypted_token,
                installation_id, account_login, account_avatar_url,
                scopes_json, created_at, last_refreshed_at, status,
                metadata_json
            FROM connector_credentials
            WHERE workspace_id = ? AND provider = ?
            """,
            (workspace_id, provider),
        ).fetchone()
    if row is None:
        return None
    try:
        metadata = json.loads(row[10] or "{}")
    except (TypeError, ValueError):
        metadata = {}
    return {
        "workspace_id": row[0],
        "provider": row[1],
        "encrypted_token": row[2],
        "installation_id": row[3],
        "account_login": row[4],
        "account_avatar_url": row[5],
        "scopes_json": row[6],
        "created_at": row[7],
        "last_refreshed_at": row[8],
        "status": row[9],
        "metadata": metadata,
    }


def set_credential_metadata(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
    metadata: dict[str, Any],
) -> bool:
    """Overwrite the credential row's metadata blob.

    Returns True when a row was updated, False when no credential
    exists for ``(workspace_id, provider)``. Callers should treat
    False as "connect the provider first" and surface a clear 400
    rather than auto-creating a credential row.
    """
    metadata_json = json.dumps(metadata)
    with store._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE connector_credentials
            SET metadata_json = ?
            WHERE workspace_id = ? AND provider = ?
            """,
            (metadata_json, workspace_id, provider),
        )
        connection.commit()
        return cursor.rowcount > 0


def mark_credential_status(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
    status: Literal["connected", "needs_reauth", "revoked"],
) -> None:
    """Update the status column on an existing credential row."""
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE connector_credentials
            SET status = ?
            WHERE workspace_id = ? AND provider = ?
            """,
            (status, workspace_id, provider),
        )
        connection.commit()


def delete_credential(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
) -> bool:
    """Remove a credential. Returns True if a row was deleted.

    Snapshots survive the credential delete (they're keyed on
    workspace_id + repo_id, useful for audit if a partner
    disconnects then reconnects).
    """
    with store._connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM connector_credentials
            WHERE workspace_id = ? AND provider = ?
            """,
            (workspace_id, provider),
        )
        connection.commit()
        return cursor.rowcount > 0


def workspaces_with_active_credential(
    store: "PlanningStudioStore",
    provider: str,
) -> list[str]:
    """List workspace_ids with a non-revoked credential for the
    given provider. Drives the polling job's per-tick scan."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT workspace_id
            FROM connector_credentials
            WHERE provider = ? AND status != 'revoked'
            """,
            (provider,),
        ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------
# Repo snapshots
# ---------------------------------------------------------------------


def upsert_repo_snapshot(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
    repo_id: str,
    repo_full_name: str,
    default_branch: str | None,
    visibility: str | None,
    snapshot: dict[str, Any],
    status: str = "fresh",
) -> None:
    """Insert-or-replace a repo snapshot row.

    Snapshot is replaced wholesale (no diff at the sync layer; the
    W3 planner reads whole snapshots).
    """
    now = _now(store)
    snapshot_json = json.dumps(snapshot)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO repo_snapshots (
                workspace_id, provider, repo_id, repo_full_name,
                default_branch, visibility, last_sync_at,
                snapshot_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (workspace_id, provider, repo_id) DO UPDATE SET
                repo_full_name = excluded.repo_full_name,
                default_branch = excluded.default_branch,
                visibility     = excluded.visibility,
                last_sync_at   = excluded.last_sync_at,
                snapshot_json  = excluded.snapshot_json,
                status         = excluded.status
            """,
            (
                workspace_id,
                provider,
                repo_id,
                repo_full_name,
                default_branch,
                visibility,
                now,
                snapshot_json,
                status,
            ),
        )
        connection.commit()


def list_repo_snapshots(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the workspace's recent snapshots, newest first."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT
                workspace_id, provider, repo_id, repo_full_name,
                default_branch, visibility, last_sync_at,
                snapshot_json, status
            FROM repo_snapshots
            WHERE workspace_id = ? AND provider = ?
            ORDER BY last_sync_at DESC
            LIMIT ?
            """,
            (workspace_id, provider, limit),
        ).fetchall()
    return [
        {
            "workspace_id": r[0],
            "provider": r[1],
            "repo_id": r[2],
            "repo_full_name": r[3],
            "default_branch": r[4],
            "visibility": r[5],
            "last_sync_at": r[6],
            "snapshot_json": r[7],
            "status": r[8],
        }
        for r in rows
    ]


def count_repo_snapshots(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
) -> int:
    """Quick repo count for the connected-tile meta line."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) FROM repo_snapshots
            WHERE workspace_id = ? AND provider = ?
            """,
            (workspace_id, provider),
        ).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------
# Sync runs
# ---------------------------------------------------------------------


def start_sync_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
    trigger: str,
    parent_run_id: str | None = None,
) -> str:
    """Open a new sync_run row; return run_id."""
    run_id = f"run-{secrets.token_hex(5)}"
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO connector_sync_runs (
                run_id, workspace_id, provider, trigger,
                started_at, status, repos_synced, parent_run_id
            )
            VALUES (?, ?, ?, ?, ?, 'running', 0, ?)
            """,
            (run_id, workspace_id, provider, trigger, now, parent_run_id),
        )
        connection.commit()
    return run_id


def finish_sync_run(
    store: "PlanningStudioStore",
    *,
    run_id: str,
    status: str,
    repos_synced: int,
    error: str | None = None,
) -> None:
    """Close a sync_run row with final status."""
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE connector_sync_runs
            SET finished_at = ?,
                status = ?,
                repos_synced = ?,
                error = ?
            WHERE run_id = ?
            """,
            (now, status, repos_synced, error, run_id),
        )
        connection.commit()


def latest_sync_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
) -> dict[str, Any] | None:
    """Most recent run for this (workspace, provider). None if no
    run has ever happened."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT
                run_id, workspace_id, provider, trigger,
                started_at, finished_at, status, repos_synced,
                error, parent_run_id
            FROM connector_sync_runs
            WHERE workspace_id = ? AND provider = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (workspace_id, provider),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0],
        "workspace_id": row[1],
        "provider": row[2],
        "trigger": row[3],
        "started_at": row[4],
        "finished_at": row[5],
        "status": row[6],
        "repos_synced": row[7],
        "error": row[8],
        "parent_run_id": row[9],
    }


def latest_successful_sync_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
) -> dict[str, Any] | None:
    """Most recent ``status='ok'`` run. Drives the design's
    "last successful sync 6 hours ago" copy on error tiles."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT
                run_id, started_at, finished_at, repos_synced
            FROM connector_sync_runs
            WHERE workspace_id = ? AND provider = ? AND status = 'ok'
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (workspace_id, provider),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0],
        "started_at": row[1],
        "finished_at": row[2],
        "repos_synced": row[3],
    }


def reconcile_orphaned_runs(
    store: "PlanningStudioStore",
    *,
    older_than_minutes: int = 30,
) -> int:
    """Mark long-running rows as timed-out.

    Used by the polling-job scheduler at start-of-loop to clean up
    runs orphaned by a Fly machine restart mid-sync. Returns the
    number of rows updated.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    ).isoformat(timespec="seconds")
    with store._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE connector_sync_runs
            SET status = 'error',
                error = 'orphaned: machine restart or crash',
                finished_at = ?
            WHERE status = 'running' AND started_at < ?
            """,
            (_now(store), cutoff),
        )
        connection.commit()
        return cursor.rowcount


# ---------------------------------------------------------------------
# Composite state — drives GET /api/v2/connectors
# ---------------------------------------------------------------------


def state_for(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    provider: str,
) -> ConnectorState:
    """Compose runtime state for a single (workspace, connector)
    pair. Returns ``not_connected`` when no credential exists.

    The FE renders this directly into the tile's connected /
    error / idle visual variants.
    """
    cred = get_credential(
        store, workspace_id=workspace_id, provider=provider
    )
    if cred is None:
        return ConnectorState(status=ConnectorStatus.not_connected)

    if cred["status"] == "needs_reauth":
        last_ok = latest_successful_sync_run(
            store, workspace_id=workspace_id, provider=provider
        )
        return ConnectorState(
            status=ConnectorStatus.needs_reauth,
            account=cred.get("account_login"),
            last_successful_sync_at=(
                last_ok["finished_at"] if last_ok else None
            ),
        )
    if cred["status"] == "revoked":
        # Treat revoked as not_connected for FE purposes; revocation
        # is an admin-side action (manual SQL or future API), and
        # the FE just shows the idle Live variant for the partner
        # to re-OAuth.
        return ConnectorState(status=ConnectorStatus.not_connected)

    last_run = latest_sync_run(
        store, workspace_id=workspace_id, provider=provider
    )
    last_ok = latest_successful_sync_run(
        store, workspace_id=workspace_id, provider=provider
    )
    snapshots = list_repo_snapshots(
        store, workspace_id=workspace_id, provider=provider, limit=50
    )
    primary = snapshots[0] if snapshots else None
    repo_count = count_repo_snapshots(
        store, workspace_id=workspace_id, provider=provider
    )

    if last_run is not None and last_run["status"] == "error":
        return ConnectorState(
            status=ConnectorStatus.error,
            account=cred.get("account_login"),
            primary_repo_full_name=(
                primary["repo_full_name"] if primary else None
            ),
            repo_count=repo_count,
            last_sync_at=last_run["started_at"],
            last_successful_sync_at=(
                last_ok["finished_at"] if last_ok else None
            ),
            last_error=last_run.get("error"),
        )

    return ConnectorState(
        status=ConnectorStatus.connected,
        account=cred.get("account_login"),
        primary_repo_full_name=(
            primary["repo_full_name"] if primary else None
        ),
        repo_count=repo_count,
        last_sync_at=(
            last_run["finished_at"] or last_run["started_at"]
            if last_run
            else None
        ),
        last_successful_sync_at=(
            last_ok["finished_at"] if last_ok else None
        ),
    )
