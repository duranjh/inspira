"""Drop F1/F2 dead tables: next_steps_artifacts + business_plan_phases.

Revision ID: 20260512_0001
Revises: 20260603_0001
Create Date: 2026-05-12

Context
-------

F1 (Business Plan tab on Summary, #092 / Item 3) and F2 (Next Steps tab on
Summary, #089 / Item 2) were partially built then scrapped during the
2026-05-04 Wave 3.x rebuild — ``LlmModesPanel`` dropped both tabs in commit
``440bace``. This sweep removes the backend routes, store methods, adapter
methods, schemas, prompts, tests, and i18n keys that remained as dead
surfaces.

Two tables become orphaned:

- ``next_steps_artifacts`` (created in 20260428_0002, F2)
- ``business_plan_phases`` (created in 20260429_0002, F1)

Both are dropped here. The original CREATE migrations are kept unmodified so
fresh local-dev databases can still walk the alembic chain forward without
gaps — they create the tables and this migration drops them, ~10ms of
wasted DDL but mechanically correct.

Kept symbols
------------

The ``business_plan_usage`` table (introduced in 20260428_0001) is NOT
touched. The Document feature (#094, Item 3 redesign) reuses it as the
cap counter for all 7 doc types — see ``_generate_document`` in
``openai_adapter.py`` and ``increment_business_plan_usage`` in
``store.py``. Renaming to ``document_usage`` is deferred per #095.

Downgrade
---------

Recreates both tables with the same shape the original CREATE migrations
shipped (`20260428_0002_next_steps_artifacts.py` for next_steps_artifacts,
`20260429_0002_business_plan_phases.py` for business_plan_phases). Required
for CI's reversibility smoke (downgrade base → upgrade head). Application
code that consumed these tables is gone, so the tables would sit empty
after a real-world downgrade — but the migration chain still walks
cleanly in both directions.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260512_0001"
down_revision: Union[str, Sequence[str], None] = "20260603_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # IF EXISTS so a database where the F1/F2 CREATE migrations were
        # skipped (or where the prod retrofit path created the tables but
        # alembic_version stayed at 3e31dfcdb483 — see #090) doesn't error.
        op.execute("DROP INDEX IF EXISTS idx_next_steps_user")
        op.execute("DROP INDEX IF EXISTS idx_next_steps_project")
        op.execute("DROP TABLE IF EXISTS next_steps_artifacts")
        op.execute("DROP INDEX IF EXISTS idx_business_plan_phases_project")
        op.execute("DROP TABLE IF EXISTS business_plan_phases")
        return

    # SQLite / other backends.
    try:
        op.drop_index("idx_next_steps_user", table_name="next_steps_artifacts")
    except Exception:  # noqa: BLE001 — already dropped, fine
        pass
    try:
        op.drop_index(
            "idx_next_steps_project", table_name="next_steps_artifacts",
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        op.drop_table("next_steps_artifacts")
    except Exception:  # noqa: BLE001
        pass
    try:
        op.drop_index(
            "idx_business_plan_phases_project", table_name="business_plan_phases",
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        op.drop_table("business_plan_phases")
    except Exception:  # noqa: BLE001
        pass


def downgrade() -> None:
    """Recreate next_steps_artifacts + business_plan_phases.

    Mirrors the shape from the original CREATE migrations
    (20260428_0002 and 20260429_0002). Required for alembic
    reversibility smoke; the application code that consumed these
    tables is gone, so a real-world downgrade leaves them empty.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # next_steps_artifacts (mirrors 20260428_0002)
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

        # business_plan_phases (mirrors 20260429_0002)
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
            "generated_at", sa.DateTime(timezone=True), nullable=False,
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True,
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

    op.create_table(
        "business_plan_phases",
        sa.Column("phase_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("phase_index", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column(
            "user_edited_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), nullable=False,
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
