"""Add v2_scaffold_refresh_history table for F.6 Refresh PR.

Revision ID: 20260606_0001
Revises: 20260605_0001
Create Date: 2026-05-14

Context
-------

Wave F.6 (#147) adds the "Refresh PR with Inspira" + 3-way diff flow.
Each refresh kickoff inserts a row here so:

* A concurrent second POST sees ``status='in_progress'`` and 409s
  rather than racing two scaffold generations against each other.
* The GET /refresh-diff endpoint can resolve back to the precise
  pre/post scaffold pair via ``previous_scaffold_id`` +
  ``new_scaffold_id``.
* The audit trail captures which refreshes the partner resolved
  (``status='resolved'``) versus those that completed but were never
  reviewed.

Indexes
-------

``idx_v2_scaffold_refresh_history_project`` on ``(project_id,
created_at DESC)`` powers the "show me the latest refresh for this
project" lookup (used by the 409 concurrency guard).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260606_0001"
# Chains off 20260605_0001 (staleness columns), the actual head at
# cdd9091. Always run ``alembic heads`` rather than ``ls -t`` when
# picking down_revision — filename dates can be non-linear.
down_revision: Union[str, Sequence[str], None] = "20260605_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS for dev-DB re-run safety; mirrors the F.5 pattern.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS v2_scaffold_refresh_history (
            refresh_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            previous_scaffold_id TEXT NULL,
            new_scaffold_id TEXT NULL,
            base_main_sha_before TEXT NOT NULL,
            base_main_sha_after TEXT NULL,
            preserve_partner_edits INTEGER NOT NULL DEFAULT 1,
            changed_paths TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'in_progress',
            created_at TEXT NOT NULL,
            resolved_at TEXT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_v2_scaffold_refresh_history_project "
        "ON v2_scaffold_refresh_history (project_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_v2_scaffold_refresh_history_status "
        "ON v2_scaffold_refresh_history (project_id, status)"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS idx_v2_scaffold_refresh_history_status"
    )
    op.execute(
        "DROP INDEX IF EXISTS idx_v2_scaffold_refresh_history_project"
    )
    op.execute("DROP TABLE IF EXISTS v2_scaffold_refresh_history")
