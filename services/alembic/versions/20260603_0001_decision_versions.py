"""Add decision_versions table + decisions.current_version_int for W2-θ comment cascade.

Revision ID: 20260603_0001
Revises: 20260518_0001
Create Date: 2026-05-03

Context
-------

Wave 2 θ ships the highlight-and-comment + cascade flow. When a user
comments on a decision and triggers a regenerate, the new version is
appended to ``decision_versions`` (companion table mirroring
``summary_versions`` at store.py:816); the decisions row itself is
updated with the new statement/rationale and ``current_version_int``
is bumped.

Lazy v1 (no backfill)
---------------------

The v1 row for a decision is inserted by ``cascade.py`` the first
time a cascade fires for that decision (snapshot of the current
``decisions.statement|rationale|subject`` taken at that moment, then
v2 appended). This avoids touching every existing decision row in
this migration and matches the ``summary_versions`` precedent
(no v0 backfill).

Read-side fallback: if no ``decision_versions`` row exists for a
``decision_id`` (which is the steady state for any decision that has
never been cascaded), the canvas read falls back to
``decisions.created_at`` for ``last_changed_at`` and treats the
implicit version as 1.

Idempotency
-----------

``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS`` work
identically on SQLite and Postgres for TEXT/INTEGER schema. Mirrors
``20260518_0001_w3_orchestrator.py``.

Downgrade
---------

Drops indexes → table → column. SQLite's column-drop needs a
table-rebuild dance via ``op.batch_alter_table``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260603_0001"
# Re-chained 2026-05-03: was "20260518_0001"; bumped to chain after κ's
# connector_credentials_metadata migration so the alembic tree stays
# linear. Originally created in parallel to γ + κ migrations, producing
# a 3-way fork alembic upgrade refuses to resolve. The decision_versions
# table is orthogonal to both project_state (γ) and connector_credentials
# metadata (κ), so it runs cleanly on top of either ancestor.
down_revision: Union[str, Sequence[str], None] = "20260519_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_versions (
            version_id                 TEXT PRIMARY KEY,
            decision_id                TEXT NOT NULL,
            version_int                INTEGER NOT NULL,
            statement                  TEXT NOT NULL,
            rationale                  TEXT,
            subject                    TEXT,
            version_hash               TEXT NOT NULL,
            prior_version_id           TEXT,
            change_note                TEXT,
            cascade_id                 TEXT,
            cascaded_from_decision_ids TEXT,
            created_at                 TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_versions_unique "
        "ON decision_versions(decision_id, version_int)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_versions_latest "
        "ON decision_versions(decision_id, version_int DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_versions_cascade "
        "ON decision_versions(cascade_id)"
    )

    op.add_column(
        "decisions",
        sa.Column(
            "current_version_int",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cascade_runs (
            cascade_id          TEXT PRIMARY KEY,
            workspace_id        TEXT NOT NULL,
            project_id          TEXT NOT NULL,
            triggered_by        TEXT NOT NULL,
            scope_mode          TEXT NOT NULL,
            status              TEXT NOT NULL,
            commented_decisions TEXT NOT NULL,
            affected_scope      TEXT,
            diff_summary        TEXT,
            error               TEXT,
            started_at          TEXT NOT NULL,
            completed_at        TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cascade_runs_project_recent "
        "ON cascade_runs(project_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cascade_runs_workspace "
        "ON cascade_runs(workspace_id, started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cascade_runs_workspace")
    op.execute("DROP INDEX IF EXISTS idx_cascade_runs_project_recent")
    op.execute("DROP TABLE IF EXISTS cascade_runs")

    with op.batch_alter_table("decisions") as batch:
        batch.drop_column("current_version_int")

    op.execute("DROP INDEX IF EXISTS idx_decision_versions_cascade")
    op.execute("DROP INDEX IF EXISTS idx_decision_versions_latest")
    op.execute("DROP INDEX IF EXISTS idx_decision_versions_unique")
    op.execute("DROP TABLE IF EXISTS decision_versions")
