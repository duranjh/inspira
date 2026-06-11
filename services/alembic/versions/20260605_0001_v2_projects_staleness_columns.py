"""Add v2_projects.base_main_sha + v2_projects.last_partner_edit for F.5 staleness.

Revision ID: 20260605_0001
Revises: 20260513_0001
Create Date: 2026-05-14

Context
-------

Wave F.5 (multi-PR staleness handling, #147) records two new pieces of
state on each v2_project:

* ``base_main_sha`` — the main-branch SHA the PR overlay was drafted
  against. Written through from ``build_overlay_tree`` on first
  successful build (the store setter is idempotent — only fills the
  column when it's currently NULL, preserving the original snapshot).
* ``last_partner_edit`` — timestamp of the most recent partner edit
  across the overlay. Setter shipped now, production call sites land
  in F.6 alongside the actual edit-save endpoint.

Both columns are nullable: pre-F.5 projects (no recorded base SHA)
self-heal the next time the partner opens the PR overlay tree. The
staleness compute path treats NULL ``base_main_sha`` as ``legacy=True,
is_stale=False`` and skips the GitHub compare call entirely.

Index
-----

``idx_v2_projects_base_main_sha`` on ``(workspace_id, base_main_sha)``
supports a future "show me every open PR built against this SHA"
sweep when main moves — the Kanban could batch-mark every affected
project stale in one query. Not used in F.5's per-project route, but
the schema cost is trivial and the index lands cleanly here.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260605_0001"
# Chains off 20260513_0001 (artifact_comments), the actual head at
# e1fd6e6. The migration tree's date ordering is non-linear here:
# 20260603_0001 (decision_versions) was retroactively inserted into
# the chain BEFORE 20260512_0001 + 20260513_0001, so even though the
# filename date implies "after", the head is 20260513_0001 by chain
# order. Always run ``alembic heads`` rather than ``ls -t`` when
# picking a new migration's down_revision.
down_revision: Union[str, Sequence[str], None] = "20260513_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres + SQLite both accept ``op.add_column``; for re-runs against
    # a partially-applied dev DB we wrap in try/except keyed on the
    # "duplicate column" message to keep alembic upgrade idempotent.
    # Mirrors the 20260518_0002_project_state.py pattern.
    try:
        op.add_column(
            "v2_projects",
            sa.Column("base_main_sha", sa.Text(), nullable=True),
        )
    except Exception as exc:  # pragma: no cover - re-run safety
        if "duplicate column" not in str(exc).lower():
            raise

    try:
        op.add_column(
            "v2_projects",
            sa.Column("last_partner_edit", sa.Text(), nullable=True),
        )
    except Exception as exc:  # pragma: no cover - re-run safety
        if "duplicate column" not in str(exc).lower():
            raise

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_v2_projects_base_main_sha "
        "ON v2_projects(workspace_id, base_main_sha)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_v2_projects_base_main_sha")
    with op.batch_alter_table("v2_projects") as batch:
        batch.drop_column("last_partner_edit")
        batch.drop_column("base_main_sha")
