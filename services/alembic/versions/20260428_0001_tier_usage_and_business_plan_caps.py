"""Add tier_usage + business_plan_usage tables for monthly cap counters (#080).

Revision ID: 20260428_0001
Revises: 20260424_0002
Create Date: 2026-04-28

Context
-------

Issue #080 tracks the monthly token-cap implementation following the
2026-04-28 LLM-tier overhaul. Founder-locked policy:

- Free plan:    BASE = 2M output tokens/mo
- Pro plan:     BASE = 2M, PRO = 2.25M output tokens/mo (~750 PRO turns)
- Frontier:     BASE = 2M, PRO = 1.5M, FRONTIER = 4.5M output tokens/mo (~1500 turns)
- Pro plan:     Business Plan trial = 1/month (free, separate from token cap)
- Frontier:     Business Plan = up to 100/month (with fair-use disclaimer)

This migration adds two counter tables:

1. ``tier_usage`` — one row per (user_id, tier). Tracks
   ``output_tokens_used`` for the current monthly window. Lazy reset:
   the application layer clears the counter when ``window_started_at``
   is older than the start of the current calendar month.

2. ``business_plan_usage`` — one row per user. Tracks
   ``plans_used_this_month`` for the per-feature business-plan trial
   counter (Pro 1/mo + Frontier 100/mo). Same lazy-reset semantics.

Why two tables instead of one
-----------------------------

Topic_turn token usage and business-plan generation count are unrelated
ceilings that get checked at different code sites. Splitting keeps the
read path simple (no ``WHERE feature='...'`` filter on every increment)
and matches the existing pattern in this codebase of one table per
distinct domain concept. The artifact-generator feature in #086 will
get its own counter table when that work lands.

No foreign keys
---------------

Recent migrations in this repo skip explicit FOREIGN KEY constraints
and rely on application-layer cleanup (see eg. ``user_access_tokens``
in 20260422_0008). Following that convention here. ON DELETE CASCADE
semantics are enforced in the store-layer's user-deletion path.

Chain note
----------

``down_revision = "20260424_0002"`` chains after the terms-accepted-at
migration. Alembic applies 0002 (terms_accepted_at) and then 0001 of
2026-04-28 (this migration) on ``alembic upgrade head``.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS`` for idempotent re-apply.
SQLite goes through SQLAlchemy's ``op.create_table`` which raises on a
re-attempt; alembic's revision tracker prevents the re-attempt on a
DB that already has the migration applied.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260428_0001"
down_revision: Union[str, Sequence[str], None] = "20260424_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS tier_usage (
                user_id TEXT NOT NULL,
                tier TEXT NOT NULL,
                output_tokens_used BIGINT NOT NULL DEFAULT 0,
                window_started_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (user_id, tier)
            )
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS business_plan_usage (
                user_id TEXT NOT NULL PRIMARY KEY,
                plans_used_this_month INTEGER NOT NULL DEFAULT 0,
                window_started_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        return

    # SQLite / other backends.
    op.create_table(
        "tier_usage",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column(
            "output_tokens_used",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "tier"),
    )
    op.create_table(
        "business_plan_usage",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column(
            "plans_used_this_month",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("business_plan_usage")
    op.drop_table("tier_usage")
