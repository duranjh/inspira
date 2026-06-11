"""Add business_plan_phases table for Item 3 / F1: Business plan tab (#092).

Revision ID: 20260429_0002
Revises: 20260429_0001
Create Date: 2026-04-29

Context
-------

Issue #092 ships F1 — the Business plan tab on the Summary page
(LlmModesPanel). The tab body is a 5-phase pager (Vision/Concept/Idea
templates × 5 phases each). Each phase is a single ``gpt-5.5`` call,
synchronous (NOT 202 + poll like Next Steps), cached server-side in
this table. Product decisions:

- Model: ``gpt-5.5`` regardless of user tier (Frontier model pinned).
- Plan gate: Free → upgrade CTA. Pro + Frontier only.
- Cap accounting: ``business_plan_usage`` from #080 (NOT
  ``tier_usage``). Pro = 1 plan/month free trial, Frontier = 100/month.
- Cap counts a plan, not a phase: increment only on phase 0 of a NEW
  plan instance ((project_id, template) with no rows yet).
- Cap blocks ANY POST when at limit (regenerates included).

This migration adds one cell-keyed table:

``business_plan_phases`` — one row per (project_id, template,
phase_index). Regenerate replaces the row's content. Edit (PATCH)
sets ``user_edited_at`` and updates content. Template switch deletes
all rows for the OLD template. No versioning/history — cost auditing
is satisfied by ``business_plan_usage``.

Why mutated cells, not versioned artifacts
------------------------------------------

Next Steps versioned because each generation was a snapshot of the
whole artifact (cards). Business plan phases are individual cells:
"this is my Phase 2 right now". Versioning would clutter the FE
(which version is "current"?), inflate the table 5× per regenerate,
and complicate the read path.

No foreign keys
---------------

Same rationale as ``20260428_0002_next_steps_artifacts.py`` — recent
migrations skip explicit FOREIGN KEY constraints and rely on
application-layer cleanup. Project deletion in the store layer
cascades to this table.

Chain note
----------

``down_revision = "20260429_0001"`` chains after the merge migration
that joined the byok-terms branch with the tier_usage / next_steps
branch (#090). Alembic applies 0001 (merge), then 0002 (this
migration) on ``alembic upgrade head``.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS`` for idempotent re-apply
(matches the runtime retrofit ``_ensure_business_plan_phases_table``).
SQLite goes through SQLAlchemy's ``op.create_table`` which raises on
re-attempt; alembic's revision tracker prevents the re-attempt on a
DB that already has the migration applied.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260429_0002"
down_revision: Union[str, Sequence[str], None] = "20260429_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS business_plan_phases (
                phase_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                template TEXT NOT NULL,
                phase_index INTEGER NOT NULL,
                content_json TEXT NOT NULL,
                user_edited_at TIMESTAMPTZ,
                generated_at TIMESTAMPTZ NOT NULL,
                model_id TEXT NOT NULL,
                CONSTRAINT uq_business_plan_phases_project_template_index
                    UNIQUE (project_id, template, phase_index)
            )
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_business_plan_phases_project
            ON business_plan_phases(project_id)
            """
        )
        return

    # SQLite / other backends.
    op.create_table(
        "business_plan_phases",
        sa.Column("phase_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("phase_index", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column(
            "user_edited_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "template",
            "phase_index",
            name="uq_business_plan_phases_project_template_index",
        ),
    )
    op.create_index(
        "idx_business_plan_phases_project",
        "business_plan_phases",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_business_plan_phases_project",
        table_name="business_plan_phases",
    )
    op.drop_table("business_plan_phases")
