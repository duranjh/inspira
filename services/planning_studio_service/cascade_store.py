"""Cascade store helpers — free functions over PlanningStudioStore.

Workspace + project scoped throughout. Mirrors ``orchestrator_store.py``
patterns: free functions, ``store`` as first positional arg, every
helper takes ``workspace_id`` as a kwarg, returns dicts.

Tables touched:

- ``cascade_runs`` — one row per ``POST /regenerate-cascade`` invocation.
  FE polls ``GET /regenerate-cascade/{cascade_id}`` against this row.
- ``decision_versions`` — append-only history of decision rewrites. v1
  is lazy: the snapshot of the original ``decisions.statement`` is
  inserted by ``cascade.py`` the first time a cascade fires for a
  given decision (then v2+ stacked on top).
- ``decisions.current_version_int`` — read-side pointer to the latest
  ``decision_versions.version_int``. Default 1 for never-cascaded
  decisions.

The lazy-v1 dance keeps the migration backfill-free and matches the
``summary_versions`` precedent (no v0 backfill).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import PlanningStudioStore


def _now(store: "PlanningStudioStore") -> str:
    from .store import now_timestamp

    return now_timestamp()


def compute_version_hash(*, statement: str, rationale: str | None, subject: str | None) -> str:
    """SHA256 over the version's content fields. Matches ``summary_versions`` precedent."""
    payload = f"{statement}|{rationale or ''}|{subject or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------
# cascade_runs
# ---------------------------------------------------------------------


def create_cascade_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    project_id: str,
    triggered_by: str,
    scope_mode: str,
    commented_decisions: list[dict[str, Any]],
    affected_scope: dict[str, Any] | None = None,
) -> str:
    """Insert a fresh cascade_runs row (status='pending'). Returns cascade_id.

    ``affected_scope`` is optional at create time — the BackgroundTask
    recomputes / re-confirms it before running and updates the row.
    """
    cascade_id = f"csc-{secrets.token_hex(5)}"
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO cascade_runs (
                cascade_id, workspace_id, project_id, triggered_by,
                scope_mode, status, commented_decisions, affected_scope,
                diff_summary, error, started_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, ?, NULL)
            """,
            (
                cascade_id, workspace_id, project_id, triggered_by,
                scope_mode,
                json.dumps(commented_decisions),
                json.dumps(affected_scope) if affected_scope is not None else None,
                now,
            ),
        )
        connection.commit()
    return cascade_id


def update_cascade_status(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    cascade_id: str,
    status: str,
    affected_scope: dict[str, Any] | None = None,
    diff_summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Patch a cascade_runs row. Sets ``completed_at`` when status is terminal."""
    now = _now(store)
    completed_at = now if status in ("complete", "failed") else None
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE cascade_runs
            SET status         = ?,
                affected_scope = COALESCE(?, affected_scope),
                diff_summary   = COALESCE(?, diff_summary),
                error          = COALESCE(?, error),
                completed_at   = COALESCE(?, completed_at)
            WHERE cascade_id = ? AND workspace_id = ?
            """,
            (
                status,
                json.dumps(affected_scope) if affected_scope is not None else None,
                json.dumps(diff_summary) if diff_summary is not None else None,
                error,
                completed_at,
                cascade_id, workspace_id,
            ),
        )
        connection.commit()


def get_cascade_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    cascade_id: str,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """Read a cascade_runs row, scoped to workspace (and optionally project).

    Returns ``None`` if the cascade doesn't exist OR exists in a different
    workspace / project — callers treat that as 404 (don't leak existence).
    """
    sql = (
        "SELECT cascade_id, workspace_id, project_id, triggered_by, "
        "scope_mode, status, commented_decisions, affected_scope, "
        "diff_summary, error, started_at, completed_at "
        "FROM cascade_runs WHERE cascade_id = ? AND workspace_id = ?"
    )
    params: tuple[Any, ...] = (cascade_id, workspace_id)
    if project_id is not None:
        sql += " AND project_id = ?"
        params = (cascade_id, workspace_id, project_id)
    with store._connect() as connection:
        row = connection.execute(sql, params).fetchone()
    if row is None:
        return None
    return {
        "cascade_id": row[0],
        "workspace_id": row[1],
        "project_id": row[2],
        "triggered_by": row[3],
        "scope_mode": row[4],
        "status": row[5],
        "commented_decisions": json.loads(row[6]) if row[6] else [],
        "affected_scope": json.loads(row[7]) if row[7] else None,
        "diff_summary": json.loads(row[8]) if row[8] else None,
        "error": row[9],
        "started_at": row[10],
        "completed_at": row[11],
    }


# ---------------------------------------------------------------------
# decision_versions
# ---------------------------------------------------------------------


def get_latest_version_int(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
) -> int:
    """Return the highest known version for a decision (default 1).

    Reads ``MAX(version_int) FROM decision_versions`` rather than
    ``decisions.current_version_int`` so the persist path is **self-
    healing**: if a prior cascade landed a ``decision_versions`` row
    but failed before bumping ``current_version_int`` (3-step write
    that is NOT in one transaction today), the next cascade still
    computes the correct ``new_v`` and avoids a UNIQUE-constraint
    collision on ``(decision_id, version_int)``.

    Falls back to ``decisions.current_version_int`` when no
    ``decision_versions`` rows exist (steady state for any decision
    that has never been cascaded — v1 is lazy-inserted on first
    cascade, see ``ensure_v1_snapshot``).
    """
    with store._connect() as connection:
        max_row = connection.execute(
            "SELECT MAX(version_int) FROM decision_versions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if max_row is not None and max_row[0] is not None:
            return int(max_row[0])
        # No decision_versions row exists yet — fall back to the pointer.
        row = connection.execute(
            "SELECT current_version_int FROM decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
    if row is None:
        return 1
    return int(row[0])


def get_decision_version(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
    version_int: int,
) -> dict[str, Any] | None:
    """Read one ``decision_versions`` row by (decision_id, version_int)."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT version_id, decision_id, version_int, statement, rationale,
                   subject, version_hash, prior_version_id, change_note,
                   cascade_id, cascaded_from_decision_ids, created_at
            FROM decision_versions
            WHERE decision_id = ? AND version_int = ?
            """,
            (decision_id, version_int),
        ).fetchone()
    if row is None:
        return None
    return _row_to_version_dict(row)


def list_versions_for_decision(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
) -> list[dict[str, Any]]:
    """All versions of a decision, latest first."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT version_id, decision_id, version_int, statement, rationale,
                   subject, version_hash, prior_version_id, change_note,
                   cascade_id, cascaded_from_decision_ids, created_at
            FROM decision_versions
            WHERE decision_id = ?
            ORDER BY version_int DESC
            """,
            (decision_id,),
        ).fetchall()
    return [_row_to_version_dict(r) for r in rows]


def insert_decision_version(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
    version_int: int,
    statement: str,
    rationale: str | None,
    subject: str | None,
    prior_version_id: str | None,
    change_note: str | None,
    cascade_id: str | None,
    cascaded_from_decision_ids: list[str] | None,
) -> str:
    """Insert one decision_versions row. Returns version_id.

    Caller is responsible for advancing ``decisions.current_version_int``
    via ``update_decision_for_cascade`` (combined transaction).
    """
    version_id = f"dv-{secrets.token_hex(5)}"
    now = _now(store)
    version_hash = compute_version_hash(
        statement=statement, rationale=rationale, subject=subject,
    )
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO decision_versions (
                version_id, decision_id, version_int, statement, rationale,
                subject, version_hash, prior_version_id, change_note,
                cascade_id, cascaded_from_decision_ids, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id, decision_id, version_int, statement, rationale,
                subject, version_hash, prior_version_id, change_note,
                cascade_id,
                json.dumps(cascaded_from_decision_ids) if cascaded_from_decision_ids else None,
                now,
            ),
        )
        connection.commit()
    return version_id


def update_decision_for_cascade(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
    statement: str,
    rationale: str | None,
    current_version_int: int,
) -> None:
    """Advance ``decisions`` to the new version's content + bump pointer.

    Pairs with ``insert_decision_version`` — call inside the same logical
    transaction (caller wraps both).
    """
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE decisions
            SET statement           = ?,
                rationale           = ?,
                current_version_int = ?,
                updated_at          = ?
            WHERE decision_id = ?
            """,
            (statement, rationale, current_version_int, now, decision_id),
        )
        connection.commit()


def ensure_v1_snapshot(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
) -> str | None:
    """Lazy-insert the v1 snapshot for a decision if missing.

    Returns the v1 ``version_id`` (existing or freshly-inserted). Returns
    ``None`` if the decision itself doesn't exist (caller should 404).

    Idempotent — the UNIQUE (decision_id, version_int) index makes
    repeat calls a no-op.
    """
    existing = get_decision_version(store, decision_id=decision_id, version_int=1)
    if existing is not None:
        return existing["version_id"]
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT statement, rationale, created_at
            FROM decisions WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
    if row is None:
        return None
    statement = row[0]
    rationale = row[1]
    return insert_decision_version(
        store,
        decision_id=decision_id,
        version_int=1,
        statement=statement,
        rationale=rationale,
        subject=None,
        prior_version_id=None,
        change_note="initial",
        cascade_id=None,
        cascaded_from_decision_ids=None,
    )


def _row_to_version_dict(row: Any) -> dict[str, Any]:
    return {
        "version_id": row[0],
        "decision_id": row[1],
        "version_int": int(row[2]),
        "statement": row[3],
        "rationale": row[4],
        "subject": row[5],
        "version_hash": row[6],
        "prior_version_id": row[7],
        "change_note": row[8],
        "cascade_id": row[9],
        "cascaded_from_decision_ids": json.loads(row[10]) if row[10] else [],
        "created_at": row[11],
    }
