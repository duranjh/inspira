"""Add users.preferred_model_tier column.

Revision ID: 20260422_0004
Revises: 20260422_0003
Create Date: 2026-04-22

Context
-------

The subscription-tier-gated LLM model picker lets users pick a persistent
default tier (``base`` / ``pro`` / ``frontier``) in Account Settings. The
persisted value is per-user and nullable — a NULL row means "use the
plan default" and the application code resolves it every turn via
``agents.tiers.resolve_tier_for_user``.

One new column, no new table. Using ``op.add_column`` (SQLAlchemy syntax)
so the migration runs cleanly on both SQLite (local dev) and Postgres
(Neon production). We deliberately avoid a CHECK constraint on the
column value because:

- Enum sets change faster than migrations (product adds a "mid" tier,
  etc).
- The application validates the slug against ``ModelTier`` before every
  write, so a bad value can't reach the DB through the normal paths.
- A DB-level CHECK would break rollback compatibility: an old binary
  running against a newer DB would refuse to boot.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0004"
down_revision: Union[str, Sequence[str], None] = "20260422_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable by design — a NULL value means "fall back to plan default"
    # which is what the resolve_tier_for_user helper expects. No default
    # value so existing rows stay explicitly unset.
    op.add_column(
        "users",
        sa.Column("preferred_model_tier", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "preferred_model_tier")
