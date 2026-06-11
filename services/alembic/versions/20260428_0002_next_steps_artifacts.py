"""Add next_steps_artifacts table for Item 2 / F2: Next Steps tab (#089).

Revision ID: 20260428_0002
Revises: 20260428_0001
Create Date: 2026-04-28

Context
-------

Issue #089 ships F2 — the Next Steps tab on the Summary page (LlmModesPanel).
The feature is on-demand only (NEVER background): the user clicks Generate,
the backend kicks off a gpt-5 call asynchronously, and the FE polls until
status flips to ``completed`` or ``failed``. Founder lock-in 2026-04-28:

- Model: ``gpt-5`` regardless of user tier (PRO model pinned).
- Plan gate: Free → upgrade CTA. Pro + Frontier only.
- Cap accounting: PRO users → PRO bucket; Frontier users → FRONTIER
  bucket. Reuses ``tier_usage`` from #080.
- Concurrency: per-project Postgres advisory lock prevents dual-fire.

This migration adds one counter table:

``next_steps_artifacts`` — one row per generation attempt (history kept,
not replaced). FE polls by ``artifact_id`` while a generation is in
flight; on tab open, the GET endpoint returns the latest ``completed``
row. The history doubles as an audit trail for gpt-5 cost over time.

Status states
-------------

- ``in_progress`` — row created at endpoint top; LLM call running in a
  FastAPI BackgroundTask. ``content_json`` and ``output_tokens_estimate``
  are null in this state.
- ``completed`` — LLM call returned successfully and the sanitized
  payload was stored in ``content_json``. ``completed_at`` is stamped.
- ``failed`` — LLM call raised. ``error_message`` carries the exception
  string. ``completed_at`` is stamped.

No foreign keys
---------------

Recent migrations in this repo skip explicit FOREIGN KEY constraints
and rely on application-layer cleanup (see eg. ``user_access_tokens``
in 20260422_0008, plus ``tier_usage`` in 20260428_0001). Following
that convention here. Project deletion in the store-layer cascades to
this table.

Chain note
----------

``down_revision = "20260428_0001"`` chains after the tier_usage caps
migration. Alembic applies 0001 (tier_usage + business_plan_usage) and
then 0002 (this migration) on ``alembic upgrade head``.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS`` for idempotent re-apply.
SQLite goes through SQLAlchemy's ``op.create_table`` which raises on
re-attempt; alembic's revision tracker prevents the re-attempt on a
DB that already has the migration applied.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260428_0002"
down_revision: Union[str, Sequence[str], None] = "20260428_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS next_steps_artifacts (
                artifact_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                content_json TEXT,
                error_message TEXT,
                model_id TEXT NOT NULL,
                plan_tier TEXT NOT NULL,
                output_tokens_estimate INTEGER,
                generated_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ
            )
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_next_steps_project
            ON next_steps_artifacts(project_id, generated_at DESC)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_next_steps_user
            ON next_steps_artifacts(user_id)
            """
        )
        return

    # SQLite / other backends.
    op.create_table(
        "next_steps_artifacts",
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("plan_tier", sa.Text(), nullable=False),
        sa.Column("output_tokens_estimate", sa.Integer(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_next_steps_project",
        "next_steps_artifacts",
        ["project_id", "generated_at"],
        unique=False,
    )
    op.create_index(
        "idx_next_steps_user",
        "next_steps_artifacts",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_next_steps_user", table_name="next_steps_artifacts")
    op.drop_index("idx_next_steps_project", table_name="next_steps_artifacts")
    op.drop_table("next_steps_artifacts")
