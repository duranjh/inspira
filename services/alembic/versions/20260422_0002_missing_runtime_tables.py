"""Create tables the store bootstraps but the baseline migration forgot.

Revision ID: 20260422_0002
Revises: 20260422_0001
Create Date: 2026-04-22

Context
-------

The baseline migration (``20260421_0001_baseline``) captured most of the
schema that existed at the time, but seven tables were only ever created
by ``PlanningStudioStore._initialize_schema`` via inline
``CREATE TABLE IF NOT EXISTS``. That path only ran under the old
in-container SQLite story. When the runtime store was ported to psycopg +
Neon Postgres (``feat(store): port runtime store to Postgres``), the
CREATE statements stopped running at startup — psycopg does not replay
them, and the tables never landed in Neon.

The practical effect: any code path that touches one of these tables
returns a 500 on production. The first visible symptom was
``POST /api/v2/projects/from-template`` → ``create_v2_project`` →
``invalidate_cached_suggestions`` → ``DELETE FROM suggestions_cache``
failing with ``psycopg.errors.UndefinedTable: relation
"suggestions_cache" does not exist``. That blocks template kickoff
entirely. The same class of error is latent in credits, subscriptions,
password reset, and scaffold flows — most of which just hadn't been
exercised on production yet.

Fix
---

Create all seven missing tables in one shot, matching the column order /
types / constraints / indexes already defined in ``store.py`` so the two
schemas stay byte-compatible. ``CREATE TABLE IF NOT EXISTS`` + ``CREATE
INDEX IF NOT EXISTS`` make the migration a no-op in the SQLite local-dev
case (where ``_initialize_schema`` already created them) and a real
create on Postgres where they were missing.

Down-migration intentionally drops them; if you need to preserve data
you should take a dump first. This is fine for pre-launch Inspira — no
production customers yet — but would need re-thinking once real usage is
on the line.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0002"
down_revision: Union[str, Sequence[str], None] = "20260422_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Upgrade: create every missing table. Each statement is issued separately
# so a failure on one table gives us a clean traceback instead of a confusing
# "syntax error" from a multi-statement batch. Ordered so any future FK
# dependencies can be added without re-sorting (e.g. user_credits before
# credit_transactions even though the latter has no direct FK today).
# ---------------------------------------------------------------------------

_STATEMENTS: list[str] = [
    # Per-user daily token accounting. Protects against a single user
    # burning the entire OpenAI budget. PK is (user_id, day_utc) so each
    # day's counters live in a single row per user and accumulate via
    # UPDATE ... SET tokens_in = tokens_in + ?.
    """
    CREATE TABLE IF NOT EXISTS user_usage (
        user_id TEXT NOT NULL,
        day_utc TEXT NOT NULL,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        request_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, day_utc)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_usage_day ON user_usage(day_utc)",

    # Per-user cache for AI-generated project suggestions. TTL enforced
    # in app code (suggestions module) against generated_at. One row per
    # user; new rows REPLACE via INSERT ... ON CONFLICT DO UPDATE.
    """
    CREATE TABLE IF NOT EXISTS suggestions_cache (
        user_id TEXT PRIMARY KEY,
        suggestions_json TEXT NOT NULL,
        generated_at TEXT NOT NULL
    )
    """,

    # Stripe-backed subscription state per user. One row per user; users
    # without a row are treated as Free tier. ``plan`` matches a slug in
    # billing/plans.py (free|pro|team). Stripe IDs are nullable so the
    # Noop provider can record local "subscribed" rows without Stripe.
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        user_id TEXT PRIMARY KEY,
        plan TEXT NOT NULL,
        status TEXT NOT NULL,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        current_period_end TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(stripe_customer_id)",

    # User credit balances. One row per user, populated on first read via
    # credits.ensure_initial_grant(). The ledger of credit movements lives
    # in credit_transactions — this table is just the running-sum snapshot.
    # CHECK constraint prevents concurrent-debit TOCTOU from going negative
    # (the caller converts IntegrityError → HTTP 402).
    """
    CREATE TABLE IF NOT EXISTS user_credits (
        user_id TEXT PRIMARY KEY,
        balance_credits INTEGER NOT NULL DEFAULT 0 CHECK (balance_credits >= 0),
        updated_at TEXT NOT NULL
    )
    """,

    # Append-only credit ledger. Every debit and credit lands here. ``delta``
    # is signed: negative = charge, positive = refund/grant/purchase. ``reason``
    # is a short underscore-cased slug filterable for reporting.
    # ``reference_id`` is optional — typically the scaffold_id a charge was
    # for, so a transaction can point back at the thing it paid for.
    """
    CREATE TABLE IF NOT EXISTS credit_transactions (
        transaction_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL,
        reference_id TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_credit_txns_user ON credit_transactions(user_id, created_at)",

    # Persisted code scaffolds (paid-tier feature). One row per successful
    # generation; failed generations land in credit_transactions with
    # reason='scaffold_failed' instead. manifest_json holds the full file
    # list so zip downloads are repeatable without re-hitting the LLM.
    """
    CREATE TABLE IF NOT EXISTS scaffolds (
        scaffold_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        framework TEXT NOT NULL,
        language TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        FOREIGN KEY(project_id) REFERENCES v2_projects(project_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scaffolds_project ON scaffolds(project_id, deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_scaffolds_user ON scaffolds(user_id, deleted_at)",

    # Password reset tokens. We persist sha256(token_hex) — never the raw
    # token — so a DB dump cannot mint resets. Tokens are 32 random bytes
    # (256 bits); the hash is 64 hex characters. A token is consumable iff
    # used_at IS NULL AND expires_at > now.
    """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prt_user ON password_reset_tokens(user_id, used_at)",
]


_DROP_STATEMENTS: list[str] = [
    # Reverse dependency order: ledger before balance, tokens before their
    # owners, etc. Using IF EXISTS so the downgrade is idempotent.
    "DROP INDEX IF EXISTS idx_prt_user",
    "DROP TABLE IF EXISTS password_reset_tokens",
    "DROP INDEX IF EXISTS idx_scaffolds_user",
    "DROP INDEX IF EXISTS idx_scaffolds_project",
    "DROP TABLE IF EXISTS scaffolds",
    "DROP INDEX IF EXISTS idx_credit_txns_user",
    "DROP TABLE IF EXISTS credit_transactions",
    "DROP TABLE IF EXISTS user_credits",
    "DROP INDEX IF EXISTS idx_subscriptions_customer",
    "DROP TABLE IF EXISTS subscriptions",
    "DROP TABLE IF EXISTS suggestions_cache",
    "DROP INDEX IF EXISTS idx_user_usage_day",
    "DROP TABLE IF EXISTS user_usage",
]


def upgrade() -> None:
    for stmt in _STATEMENTS:
        op.execute(sa.text(stmt.strip()))


def downgrade() -> None:
    for stmt in _DROP_STATEMENTS:
        op.execute(sa.text(stmt))
