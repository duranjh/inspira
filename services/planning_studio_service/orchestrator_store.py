"""Orchestrator store helpers — free functions over PlanningStudioStore.

Workspace-scoped throughout; every helper requires ``workspace_id`` as
a keyword arg and writes / reads only within that workspace.

The five W3 tables (``prioritization_runs``, ``orchestrator_runs``,
``sub_agent_runs``, ``decision_provenance``, ``conflict_resolutions``)
land in alembic ``20260518_0001_w3_orchestrator.py`` and inline DDL in
``store.py`` so the test path stays in sync.

The two-step back-pointer dance (``create_orchestrator_run``)
- INSERT orchestrator_runs with prioritization_run_id FK
- UPDATE prioritization_runs.orchestrator_run_id
runs inside one ``BEGIN ... COMMIT`` so the back-pointer never lags.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import PlanningStudioStore


def _now(store: "PlanningStudioStore") -> str:
    """ISO-8601 UTC timestamp (delegated to store)."""
    from .store import now_timestamp

    return now_timestamp()


# ---------------------------------------------------------------------
# prioritization_runs
# ---------------------------------------------------------------------


def create_prioritization_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    triggered_by: str,
    input_snapshot: dict[str, Any],
) -> str:
    """Insert a fresh prioritization_runs row (status='running'). Returns run_id."""
    run_id = f"pr-{secrets.token_hex(5)}"
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO prioritization_runs (
                run_id, workspace_id, triggered_by, status,
                started_at, completed_at,
                input_snapshot_json, output_json, error,
                orchestrator_run_id
            )
            VALUES (?, ?, ?, 'running', ?, NULL, ?, NULL, NULL, NULL)
            """,
            (
                run_id, workspace_id, triggered_by,
                now, json.dumps(input_snapshot),
            ),
        )
        connection.commit()
    return run_id


def complete_prioritization_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    run_id: str,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Mark a prioritization run as completed (output) or errored.

    Either ``output`` (success) or ``error`` (failure) — passing both
    or neither raises. Writes ``status``, ``completed_at``, and the
    relevant output column.
    """
    if (output is None) == (error is None):
        raise ValueError("exactly one of output / error must be provided")
    now = _now(store)
    status = "completed" if error is None else "error"
    output_json = json.dumps(output) if output is not None else None
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE prioritization_runs
            SET status = ?, completed_at = ?, output_json = ?, error = ?
            WHERE run_id = ? AND workspace_id = ?
            """,
            (status, now, output_json, error, run_id, workspace_id),
        )
        connection.commit()


def get_prioritization_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Read a single prioritization run by id, scoped to workspace.

    Returns ``None`` if the run doesn't exist OR exists in a different
    workspace — callers should treat that as 404 (don't leak existence).
    """
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT run_id, workspace_id, triggered_by, status,
                   started_at, completed_at,
                   input_snapshot_json, output_json, error,
                   orchestrator_run_id
            FROM prioritization_runs
            WHERE run_id = ? AND workspace_id = ?
            """,
            (run_id, workspace_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0],
        "workspace_id": row[1],
        "triggered_by": row[2],
        "status": row[3],
        "started_at": row[4],
        "completed_at": row[5],
        "input_snapshot": json.loads(row[6]) if row[6] else None,
        "output": json.loads(row[7]) if row[7] else None,
        "error": row[8],
        "orchestrator_run_id": row[9],
    }


# ---------------------------------------------------------------------
# orchestrator_runs
# ---------------------------------------------------------------------


def create_orchestrator_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    prioritization_run_id: str,
    triggered_by: str,
    top_n: int,
) -> tuple[str, bool]:
    """Idempotent UPSERT of an orchestrator run.

    Idempotency key: ``UNIQUE (workspace_id, prioritization_run_id)``.
    If a run already exists for this pair, returns ``(existing_run_id,
    False)``. Otherwise inserts a new row with ``status='running'`` and
    updates ``prioritization_runs.orchestrator_run_id`` back-pointer in
    the same transaction.

    Returns ``(run_id, is_new)``.
    """
    now = _now(store)
    with store._connect() as connection:
        existing = connection.execute(
            """
            SELECT run_id FROM orchestrator_runs
            WHERE workspace_id = ? AND prioritization_run_id = ?
            """,
            (workspace_id, prioritization_run_id),
        ).fetchone()
        if existing is not None:
            return (existing[0], False)
        run_id = f"or-{secrets.token_hex(5)}"
        try:
            connection.execute(
                """
                INSERT INTO orchestrator_runs (
                    run_id, workspace_id, prioritization_run_id,
                    triggered_by, top_n, status,
                    started_at, completed_at, summary_json, error
                )
                VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, NULL, NULL)
                """,
                (
                    run_id, workspace_id, prioritization_run_id,
                    triggered_by, top_n, now,
                ),
            )
            connection.execute(
                """
                UPDATE prioritization_runs
                SET orchestrator_run_id = ?
                WHERE run_id = ? AND workspace_id = ?
                """,
                (run_id, prioritization_run_id, workspace_id),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            # Race: a concurrent UPSERT won the idempotency slot. Re-read.
            connection.rollback()
            existing = connection.execute(
                """
                SELECT run_id FROM orchestrator_runs
                WHERE workspace_id = ? AND prioritization_run_id = ?
                """,
                (workspace_id, prioritization_run_id),
            ).fetchone()
            if existing is None:
                raise
            return (existing[0], False)
    return (run_id, True)


def complete_orchestrator_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    run_id: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Terminal-state update for an orchestrator run.

    Pass exactly one of ``summary`` (success) or ``error`` (failure).
    """
    if (summary is None) == (error is None):
        raise ValueError("exactly one of summary / error must be provided")
    now = _now(store)
    status = "completed" if error is None else "error"
    summary_json = json.dumps(summary) if summary is not None else None
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE orchestrator_runs
            SET status = ?, completed_at = ?, summary_json = ?, error = ?
            WHERE run_id = ? AND workspace_id = ?
            """,
            (status, now, summary_json, error, run_id, workspace_id),
        )
        connection.commit()


def _load_sub_agents_with_label(
    connection: sqlite3.Connection,
    orchestrator_run_id: str,
) -> list[dict[str, Any]]:
    """Load sub-agent rows for one orchestrator run, joining
    ``feedback_clusters.theme`` so callers see human-readable labels
    alongside the opaque ``theme_id`` (== ``feedback_clusters.cluster_id``).

    LEFT JOIN — legacy rows whose cluster has been deleted return
    ``theme_label=None`` rather than dropping the sub-agent.
    """
    sub_rows = connection.execute(
        """
        SELECT s.run_id, s.theme_id, s.project_id, s.status,
               s.started_at, s.completed_at,
               s.decisions_count, s.conflicts_count, s.error,
               fc.theme,
               vp.title
        FROM sub_agent_runs s
        LEFT JOIN feedback_clusters fc
          ON fc.cluster_id = s.theme_id
         AND fc.workspace_id = s.workspace_id
        LEFT JOIN v2_projects vp
          ON vp.project_id = s.project_id
         AND vp.workspace_id = s.workspace_id
        WHERE s.orchestrator_run_id = ?
        ORDER BY s.started_at
        """,
        (orchestrator_run_id,),
    ).fetchall()
    return [
        {
            "sub_agent_run_id": s[0],
            "theme_id": s[1],
            "project_id": s[2],
            "status": s[3],
            "started_at": s[4],
            "completed_at": s[5],
            "decisions_count": int(s[6]),
            "conflicts_count": int(s[7]),
            "error": s[8],
            # Prefer the cluster theme (the topic the sub-agent is
            # drafting against). Fall back to the v2_projects.title
            # the sub-agent is writing into — that title is set by
            # the orchestrator on project creation and is already a
            # concise issue label. Either is friendlier than the
            # opaque cluster_id we used to surface.
            "theme_label": s[9] or s[10],
        }
        for s in sub_rows
    ]


def _row_to_orchestrator_run_dict(
    row: tuple[Any, ...],
    sub_agents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Shape an orchestrator_runs row + its sub-agents as the API response."""
    return {
        "run_id": row[0],
        "workspace_id": row[1],
        "prioritization_run_id": row[2],
        "triggered_by": row[3],
        "top_n": int(row[4]),
        "status": row[5],
        "started_at": row[6],
        "completed_at": row[7],
        "summary": json.loads(row[8]) if row[8] else None,
        "error": row[9],
        "sub_agents": sub_agents,
    }


def get_orchestrator_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Read a single orchestrator run + its sub-agent rows.

    Returns the run dict with a ``sub_agents`` list (each carrying
    ``theme_label`` joined from ``feedback_clusters``), or ``None`` if
    not found in this workspace.
    """
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT run_id, workspace_id, prioritization_run_id,
                   triggered_by, top_n, status,
                   started_at, completed_at, summary_json, error
            FROM orchestrator_runs
            WHERE run_id = ? AND workspace_id = ?
            """,
            (run_id, workspace_id),
        ).fetchone()
        if row is None:
            return None
        sub_agents = _load_sub_agents_with_label(connection, row[0])
    return _row_to_orchestrator_run_dict(row, sub_agents)


def list_orchestrator_runs(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    status: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """List orchestrator runs in a workspace, newest first.

    ``status`` filters by ``running``/``completed``/``error`` when set;
    ``None`` returns all statuses. Each run's ``sub_agents`` array is
    embedded with ``theme_label`` joined from ``feedback_clusters``.

    Caller is responsible for clamping ``limit`` to a sensible range
    (the route does ``max(1, min(limit, 25))``).
    """
    with store._connect() as connection:
        if status is None:
            run_rows = connection.execute(
                """
                SELECT run_id, workspace_id, prioritization_run_id,
                       triggered_by, top_n, status,
                       started_at, completed_at, summary_json, error
                FROM orchestrator_runs
                WHERE workspace_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        else:
            run_rows = connection.execute(
                """
                SELECT run_id, workspace_id, prioritization_run_id,
                       triggered_by, top_n, status,
                       started_at, completed_at, summary_json, error
                FROM orchestrator_runs
                WHERE workspace_id = ? AND status = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (workspace_id, status, limit),
            ).fetchall()
        return [
            _row_to_orchestrator_run_dict(
                row, _load_sub_agents_with_label(connection, row[0])
            )
            for row in run_rows
        ]


# ---------------------------------------------------------------------
# sub_agent_runs
# ---------------------------------------------------------------------


def create_sub_agent_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    orchestrator_run_id: str,
    theme_id: str,
) -> str:
    """Insert a fresh sub_agent_runs row (status='running'). Returns run_id."""
    run_id = f"sa-{secrets.token_hex(5)}"
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO sub_agent_runs (
                run_id, orchestrator_run_id, workspace_id, theme_id,
                project_id, status, started_at, completed_at,
                decisions_count, conflicts_count, error
            )
            VALUES (?, ?, ?, ?, NULL, 'running', ?, NULL, 0, 0, NULL)
            """,
            (run_id, orchestrator_run_id, workspace_id, theme_id, now),
        )
        connection.commit()
    return run_id


def complete_sub_agent_run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    sub_agent_run_id: str,
    project_id: str | None,
    decisions_count: int,
    conflicts_count: int,
    error: str | None = None,
) -> None:
    """Mark a sub-agent run terminal. ``error`` set → status='error'."""
    now = _now(store)
    status = "error" if error is not None else "completed"
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE sub_agent_runs
            SET project_id = ?, status = ?, completed_at = ?,
                decisions_count = ?, conflicts_count = ?, error = ?
            WHERE run_id = ? AND workspace_id = ?
            """,
            (
                project_id, status, now,
                decisions_count, conflicts_count, error,
                sub_agent_run_id, workspace_id,
            ),
        )
        connection.commit()


def count_active_sub_agents(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
) -> int:
    """Count workspace-scoped sub_agent_runs rows that are still
    in flight (status='running'). Drives the per-tier concurrency
    cap enforced at start-canvas time."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM sub_agent_runs
            WHERE workspace_id = ? AND status = 'running'
            """,
            (workspace_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def list_sub_agent_runs(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    orchestrator_run_id: str,
) -> list[dict[str, Any]]:
    """All sub-agent runs for a single orchestrator run, ordered by start."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT run_id, theme_id, project_id, status,
                   started_at, completed_at,
                   decisions_count, conflicts_count, error
            FROM sub_agent_runs
            WHERE orchestrator_run_id = ? AND workspace_id = ?
            ORDER BY started_at
            """,
            (orchestrator_run_id, workspace_id),
        ).fetchall()
    return [
        {
            "sub_agent_run_id": r[0],
            "theme_id": r[1],
            "project_id": r[2],
            "status": r[3],
            "started_at": r[4],
            "completed_at": r[5],
            "decisions_count": int(r[6]),
            "conflicts_count": int(r[7]),
            "error": r[8],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------
# decision_provenance
# ---------------------------------------------------------------------


def record_decision_provenance(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
    cited_feedback_item_ids: list[str],
) -> None:
    """Persist a many-to-many link from one decision to its citing items.

    Weight is uniform ``1.0 / len(citations)`` per the v1 spec — sub-agents
    emit only the ID list, never per-cite weights. Empty list is a no-op
    (some decisions are general-purpose and don't cite specific items).
    """
    if not cited_feedback_item_ids:
        return
    weight = 1.0 / len(cited_feedback_item_ids)
    now = _now(store)
    with store._connect() as connection:
        for item_id in cited_feedback_item_ids:
            connection.execute(
                """
                INSERT INTO decision_provenance (
                    decision_id, feedback_item_id, weight, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (decision_id, item_id, weight, now),
            )
        connection.commit()


def list_provenance_for_decision(
    store: "PlanningStudioStore",
    *,
    decision_id: str,
) -> list[dict[str, Any]]:
    """Read all citations for one decision."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT feedback_item_id, weight, created_at
            FROM decision_provenance
            WHERE decision_id = ?
            ORDER BY created_at
            """,
            (decision_id,),
        ).fetchall()
    return [
        {"feedback_item_id": r[0], "weight": float(r[1]), "created_at": r[2]}
        for r in rows
    ]


# ---------------------------------------------------------------------
# conflict_resolutions
# ---------------------------------------------------------------------


def record_conflict_resolution(
    store: "PlanningStudioStore",
    *,
    orchestrator_run_id: str,
    decision_a_id: str,
    decision_b_id: str,
    subject: str,
    resolution_text: str,
    resolution_decision_id: str | None,
) -> str:
    """Persist a moderated conflict between two sub-agent decisions.

    ``resolution_decision_id`` is nullable: if the moderator's output is
    a textual rationale rather than a new persisted decision, we still
    record the row (the resolution_text + originating decisions form a
    full audit trail). Returns the new resolution_id.
    """
    resolution_id = f"cr-{secrets.token_hex(5)}"
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO conflict_resolutions (
                resolution_id, orchestrator_run_id,
                decision_a_id, decision_b_id, subject,
                resolution_text, resolution_decision_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution_id, orchestrator_run_id,
                decision_a_id, decision_b_id, subject,
                resolution_text, resolution_decision_id, now,
            ),
        )
        connection.commit()
    return resolution_id


def list_conflict_resolutions(
    store: "PlanningStudioStore",
    *,
    orchestrator_run_id: str,
) -> list[dict[str, Any]]:
    """All conflict resolutions for one orchestrator run, ordered."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT resolution_id, decision_a_id, decision_b_id,
                   subject, resolution_text, resolution_decision_id,
                   created_at
            FROM conflict_resolutions
            WHERE orchestrator_run_id = ?
            ORDER BY created_at
            """,
            (orchestrator_run_id,),
        ).fetchall()
    return [
        {
            "resolution_id": r[0],
            "decision_a_id": r[1],
            "decision_b_id": r[2],
            "subject": r[3],
            "resolution_text": r[4],
            "resolution_decision_id": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def increment_sub_agent_conflicts(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    sub_agent_run_id: str,
    by: int = 1,
) -> None:
    """Bump the conflicts_count on a still-running sub-agent.

    Cheap helper used by the orchestrator's conflict detector when it
    flags a decision as part of a conflict pair before the sub-agent
    finishes. The terminal ``complete_sub_agent_run`` overrides this
    with the final tally.
    """
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE sub_agent_runs
            SET conflicts_count = conflicts_count + ?
            WHERE run_id = ? AND workspace_id = ?
            """,
            (by, sub_agent_run_id, workspace_id),
        )
        connection.commit()
