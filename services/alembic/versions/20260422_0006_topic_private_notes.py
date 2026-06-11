"""Add topics.private_notes column for per-topic private annotations.

Revision ID: 20260422_0006
Revises: 20260422_0004
Create Date: 2026-04-22

Context
-------

Private notes are short annotations the user writes inside a topic that
are visible only to them. They MUST NOT be sent to the planner / LLM
prompt — the adapter builds its ``current_topic_view`` explicitly from
whitelisted fields, so simply storing the notes on the topic row is
safe as long as no prompt-assembly code adds ``private_notes`` to the
view dict.

Nullable by design: most topics will never have a note, and a NULL row
reads as "no note" in the application layer. Empty string is also
treated as "clear the note" by the endpoint handler; both shapes end
up invisible in the UI.

Chain note
----------

down_revision = "20260422_0005" — chains after the project-archiving
migration that landed while this one was being written. Alembic will
apply 0005 (v2_projects.archived_at) and 0006 (topics.private_notes)
in that order on ``alembic upgrade head``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0006"
down_revision: Union[str, Sequence[str], None] = "20260422_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable — no default, existing rows stay NULL (which the app
    # reads as "no private note"). TEXT keeps the column portable
    # across SQLite (dev) and Postgres (prod, Neon).
    op.add_column(
        "topics",
        sa.Column("private_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("topics", "private_notes")
