"""Add W3 orchestrator tables — prioritization + orchestrator + sub-agents.

Revision ID: 20260518_0001
Revises: 20260504_0007
Create Date: 2026-05-03

Context
-------

W3 introduces F6 (ROI prioritization agent) and F7-REVISED (orchestrator
+ N parallel sub-agents).

Five tables land here:

- ``prioritization_runs``   — F6 ROI scorer output. One row per
  ``/orchestrator/prioritize`` invocation.
- ``orchestrator_runs``     — F7-REVISED batch coordinator. One row
  per ``/orchestrator/run`` invocation. UNIQUE on
  ``(workspace_id, prioritization_run_id)`` is the idempotency
  gate — re-POSTing ``/run`` with the same prioritization_run_id
  returns the existing orchestrator_run instead of spawning a
  fresh batch.
- ``sub_agent_runs``        — Per-theme worker. One row per
  spawned sub-agent. ``theme_id`` references
  ``feedback_clusters.cluster_id`` semantically (column name kept
  as ``theme_id`` per spec).
- ``decision_provenance``   — Many-to-many link between decisions
  the orchestrator persists and their citing feedback items.
  ``weight`` is uniform ``1.0 / len(citations)`` in v1 (computed
  by the orchestrator at persistence; sub-agent emits only a
  cited_feedback_item_ids list).
- ``conflict_resolutions``  — Audit row for each pair of contradicting
  sub-agent decisions the orchestrator's moderation pass resolved.

No FK constraints in DDL (matches ``20260504_0004_connector_sync.py``
convention: application-layer cleanup; workspace deletion in W4-W5
cascades to these tables at the store layer).

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT
EXISTS``. SQLite path is identical — both DDL clauses are supported.

Downgrade
---------

Drops the 5 tables (with their indexes). No column-on-existing-table
retrofits in this migration, so downgrade is a clean reversal.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260518_0001"
down_revision: Union[str, Sequence[str], None] = "20260504_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # prioritization_runs ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prioritization_runs (
            run_id              TEXT PRIMARY KEY,
            workspace_id        TEXT NOT NULL,
            triggered_by        TEXT NOT NULL,
            status              TEXT NOT NULL,
            started_at          TEXT NOT NULL,
            completed_at        TEXT,
            input_snapshot_json TEXT NOT NULL,
            output_json         TEXT,
            error               TEXT,
            orchestrator_run_id TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_prio_runs_ws_recent "
        "ON prioritization_runs(workspace_id, started_at DESC)"
    )
    if dialect == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_prio_runs_running "
            "ON prioritization_runs(status) WHERE status = 'running'"
        )
    else:
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_prio_runs_running "
            "ON prioritization_runs(status)"
        )

    # orchestrator_runs ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            run_id                TEXT PRIMARY KEY,
            workspace_id          TEXT NOT NULL,
            prioritization_run_id TEXT NOT NULL,
            triggered_by          TEXT NOT NULL,
            top_n                 INTEGER NOT NULL,
            status                TEXT NOT NULL,
            started_at            TEXT NOT NULL,
            completed_at          TEXT,
            summary_json          TEXT,
            error                 TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_orch_runs_ws_recent "
        "ON orchestrator_runs(workspace_id, started_at DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orch_runs_idempotency "
        "ON orchestrator_runs(workspace_id, prioritization_run_id)"
    )

    # sub_agent_runs ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sub_agent_runs (
            run_id              TEXT PRIMARY KEY,
            orchestrator_run_id TEXT NOT NULL,
            workspace_id        TEXT NOT NULL,
            theme_id            TEXT NOT NULL,
            project_id          TEXT,
            status              TEXT NOT NULL,
            started_at          TEXT NOT NULL,
            completed_at        TEXT,
            decisions_count     INTEGER NOT NULL DEFAULT 0,
            conflicts_count     INTEGER NOT NULL DEFAULT 0,
            error               TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagent_runs_orch_status "
        "ON sub_agent_runs(orchestrator_run_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagent_runs_ws_recent "
        "ON sub_agent_runs(workspace_id, started_at DESC)"
    )

    # decision_provenance ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_provenance (
            decision_id      TEXT NOT NULL,
            feedback_item_id TEXT NOT NULL,
            weight           REAL NOT NULL,
            created_at       TEXT NOT NULL,
            PRIMARY KEY (decision_id, feedback_item_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_provenance_item "
        "ON decision_provenance(feedback_item_id)"
    )

    # conflict_resolutions ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conflict_resolutions (
            resolution_id          TEXT PRIMARY KEY,
            orchestrator_run_id    TEXT NOT NULL,
            decision_a_id          TEXT NOT NULL,
            decision_b_id          TEXT NOT NULL,
            subject                TEXT NOT NULL,
            resolution_text        TEXT NOT NULL,
            resolution_decision_id TEXT,
            created_at             TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_conflict_resolutions_orch "
        "ON conflict_resolutions(orchestrator_run_id)"
    )


def downgrade() -> None:
    # Reverse-order drops; indexes go with the tables on Postgres but
    # explicit drops keep SQLite happy.
    op.execute(
        "DROP INDEX IF EXISTS idx_conflict_resolutions_orch"
    )
    op.execute("DROP TABLE IF EXISTS conflict_resolutions")

    op.execute("DROP INDEX IF EXISTS idx_decision_provenance_item")
    op.execute("DROP TABLE IF EXISTS decision_provenance")

    op.execute("DROP INDEX IF EXISTS idx_subagent_runs_ws_recent")
    op.execute("DROP INDEX IF EXISTS idx_subagent_runs_orch_status")
    op.execute("DROP TABLE IF EXISTS sub_agent_runs")

    op.execute("DROP INDEX IF EXISTS idx_orch_runs_idempotency")
    op.execute("DROP INDEX IF EXISTS idx_orch_runs_ws_recent")
    op.execute("DROP TABLE IF EXISTS orchestrator_runs")

    op.execute("DROP INDEX IF EXISTS idx_prio_runs_running")
    op.execute("DROP INDEX IF EXISTS idx_prio_runs_ws_recent")
    op.execute("DROP TABLE IF EXISTS prioritization_runs")
