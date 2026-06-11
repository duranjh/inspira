"""Add documents table for #094 Item 3 redesign — domain-aware doc-type generator.

Revision ID: 20260429_0003
Revises: 20260429_0002
Create Date: 2026-04-29

Context
-------

Issue #094 replaces #092's per-phase Business-Plan pager with a unified,
domain-derived document generator. The single ``documents`` table holds
artifacts for all 7 v1 doc types (business_plan, prd, story_outline,
event_plan, marketing_plan, research_proposal, course_outline).

Product decisions:

- Doc type is derived from ``project.metadata_json.domain``, NOT picked
  by the user.
- Generation is **on-demand only, NEVER background**: the user clicks
  Generate, the backend kicks off a gpt-5.5 call asynchronously, and
  the FE polls until status flips to ``completed`` or ``failed``.
- Plan gate: Free → upgrade CTA. Pro + Frontier only.
- Cap accounting reuses the existing ``business_plan_usage`` table from
  #080 (treat it as the document_usage cap counter going forward; the
  column name ``plans_used_this_month`` reads as user-month-doc count).
  Pro = 1 doc/mo trial, Frontier = 100 docs/mo. Strict-block at limit.

Schema
------

``documents`` — one row per generation attempt.

- ``status`` flows in_progress → completed | failed.
- ``content_json`` is null until completed; for completed rows it
  carries the full structured document (sections list with prose,
  metadata per doc type — exact shape lives in
  agents/schemas.py per doc type).
- ``model_id`` is always ``"gpt-5.5"`` for #094 today; column future-
  proofs against a model rotation.
- ``plan_tier`` records which plan bucket counted (pro|frontier).
- ``doc_type`` is the allowlisted slug — see VALID_DOC_TYPES in store.py.
- One project + doc_type can have multiple ``completed`` rows over
  time (regenerations); the FE reads the latest completed.

No foreign keys
---------------

Recent migrations skip explicit FOREIGN KEY constraints and rely on
application-layer cleanup (see #089 + #092 + the user_access_tokens
migration). Following that convention here. Project deletion in the
store-layer cascades to this table.

Chain
-----

``down_revision = "20260429_0002"`` chains after the business_plan_phases
migration. Both #092's table and this one survive long-term — #092 stays
deprecated for migration data even after #094's FE replaces its UX.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS`` for idempotent re-apply.
SQLite goes through SQLAlchemy's ``op.create_table``; alembic's revision
tracker prevents the re-attempt on a DB that already has the migration
applied.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260429_0003"
down_revision: Union[str, Sequence[str], None] = "20260429_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                doc_type TEXT NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_documents_project_doctype
            ON documents(project_id, doc_type, generated_at DESC)
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_user
            ON documents(user_id)
            """
        )
        return

    # SQLite / other backends.
    op.create_table(
        "documents",
        sa.Column("document_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
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
        "idx_documents_project_doctype",
        "documents",
        ["project_id", "doc_type", "generated_at"],
        unique=False,
    )
    op.create_index(
        "idx_documents_user",
        "documents",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_documents_user", table_name="documents")
    op.drop_index("idx_documents_project_doctype", table_name="documents")
    op.drop_table("documents")
