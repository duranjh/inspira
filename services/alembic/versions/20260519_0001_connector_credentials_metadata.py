"""Add metadata_json column to connector_credentials.

Revision ID: 20260519_0001
Revises: 20260518_0001
Create Date: 2026-05-03

Context
-------

W2 κ (export modals) needs per-credential default-destination state
— e.g. "send issues to the Engineering team / acme-corp/inspira-app".
The Send-to-Linear / Send-to-GitHub modals fetch this and display it
prominently before Send is enabled.

Stored as a free-form JSON blob keyed by the row's ``provider`` so a
new provider can add its own destination fields without a schema
change. Linear shape: ``{"default_team_id", "default_team_name",
"default_project_id?", "default_project_name?"}``. GitHub shape:
``{"default_owner", "default_repo"}``.

Defaults to ``'{}'`` so existing rows are read-safe immediately. No
backfill required.

Downgrade drops the column — destination metadata is small and
easily re-entered if a roll-forward follows.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260519_0001"
# Re-chained 2026-05-03: was "20260518_0001"; bumped to chain after γ's
# project_state migration so the alembic tree stays linear. Originally
# created in parallel to 20260518_0002 (γ) and 20260603_0001 (θ), which
# together produced a 3-way fork alembic upgrade refuses to resolve.
# Fly's prod DB has 20260518_0002 applied; this migration runs cleanly
# on top of it (column add only, orthogonal to project_state changes).
down_revision: Union[str, Sequence[str], None] = "20260518_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connector_credentials",
        sa.Column(
            "metadata_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        # SQLite < 3.35 lacks DROP COLUMN; batch-mode rebuilds the
        # table. Modern SQLite (3.35+, shipped 2021) supports it
        # natively, but batch_alter_table is the broadly-compatible
        # choice and matches the rest of this migrations dir.
        with op.batch_alter_table("connector_credentials") as batch_op:
            batch_op.drop_column("metadata_json")
    else:
        op.drop_column("connector_credentials", "metadata_json")
