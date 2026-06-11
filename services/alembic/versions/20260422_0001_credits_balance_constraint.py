"""Credits balance non-negative constraint.

Revision ID: 20260422_0001
Revises: 20260421_0003
Create Date: 2026-04-22

Adds ``CHECK (balance_credits >= 0)`` to ``user_credits.balance_credits`` so
that concurrent debit calls that would race past the application-level balance
check (TOCTOU) fail with an IntegrityError rather than silently going negative.
The caller converts IntegrityError → HTTP 402 Payment Required.

SQLite does not support ``ALTER TABLE … ADD CONSTRAINT``, so we use the
standard four-step table-swap pattern (SQLite FAQ §11):
  1. Create the replacement table with the constraint.
  2. Copy all rows.
  3. Drop the old table.
  4. Rename the replacement.

Idempotency notes (added after the initial authoring)
-----------------------------------------------------

The baseline migration (``20260421_0001_baseline``) never created
``user_credits`` — that table only ever existed under the old SQLite
bootstrap inside ``PlanningStudioStore._initialize_schema``. On Neon the
original table therefore does not exist, so the straight copy-and-swap
above explodes with ``UndefinedTable`` while in the middle of the
migration, leaving a half-built ``user_credits_new`` behind.

To make this migration safe regardless of whether the table is present,
we now:

  * DROP any dangling ``user_credits_new`` from a prior failed attempt.
  * Skip the data-copy step when ``user_credits`` doesn't exist (the
    newer ``20260422_0002_missing_runtime_tables`` migration creates the
    table fresh with the CHECK constraint already baked in, so nothing is
    lost by skipping).
  * Only rename ``user_credits_new → user_credits`` when we actually
    built a replacement; on the "table didn't exist" path we just drop
    the (empty) replacement and let 0002 take over.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0001"
down_revision: Union[str, Sequence[str], None] = "20260421_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CREATE_NEW_TABLE = """
CREATE TABLE IF NOT EXISTS user_credits_new (
    user_id TEXT PRIMARY KEY,
    balance_credits INTEGER NOT NULL DEFAULT 0 CHECK (balance_credits >= 0),
    updated_at TEXT NOT NULL
)
"""

_COPY_DATA = "INSERT INTO user_credits_new SELECT * FROM user_credits"

_DROP_OLD = "DROP TABLE IF EXISTS user_credits"

_RENAME = "ALTER TABLE user_credits_new RENAME TO user_credits"

_DROP_DANGLING_NEW = "DROP TABLE IF EXISTS user_credits_new"

# Downgrade restores the table WITHOUT the constraint, preserving data.
_CREATE_OLD_TABLE = """
CREATE TABLE IF NOT EXISTS user_credits_old_restore (
    user_id TEXT PRIMARY KEY,
    balance_credits INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
)
"""


def _user_credits_exists(bind) -> bool:
    """Return True when ``user_credits`` is present in the target DB.

    Uses SQLAlchemy's inspector so this works on both SQLite (where the
    table exists because store.py bootstraps it) and Postgres (where the
    baseline migration forgot to create it and Neon therefore doesn't
    have it until the 0002 migration runs).
    """
    inspector = sa.inspect(bind)
    return inspector.has_table("user_credits")


def upgrade() -> None:
    bind = op.get_bind()

    # Always start by clearing any half-built table from a prior failed
    # attempt. Safe no-op when nothing is dangling.
    op.execute(sa.text(_DROP_DANGLING_NEW))

    if not _user_credits_exists(bind):
        # Table doesn't exist yet — the 20260422_0002 migration will
        # create it fresh with the CHECK constraint, so we have nothing
        # to do here. Leave the revision stamped so downstream migrations
        # aren't blocked.
        return

    op.execute(sa.text(_CREATE_NEW_TABLE.strip()))
    op.execute(sa.text(_COPY_DATA))
    op.execute(sa.text(_DROP_OLD))
    op.execute(sa.text(_RENAME))


def downgrade() -> None:
    bind = op.get_bind()
    if not _user_credits_exists(bind):
        # Nothing to downgrade from — the original upgrade was a no-op.
        return

    # Reconstruct without the CHECK — safe even if some rows have negative
    # balances (shouldn't happen post-constraint, but be defensive).
    op.execute(sa.text(_CREATE_OLD_TABLE.strip()))
    op.execute(
        sa.text(
            "INSERT INTO user_credits_old_restore SELECT * FROM user_credits"
        )
    )
    op.execute(sa.text("DROP TABLE IF EXISTS user_credits"))
    op.execute(
        sa.text(
            "ALTER TABLE user_credits_old_restore RENAME TO user_credits"
        )
    )
