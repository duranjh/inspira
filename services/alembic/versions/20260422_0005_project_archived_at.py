"""Add v2_projects.archived_at column for the project-archiving feature.

Revision ID: 20260422_0005
Revises: 20260422_0004
Create Date: 2026-04-22

Context
-------

Users want a softer alternative to deletion: archive a project to hide
it from the main projects list but keep it fully restorable. This is a
weaker state than ``deleted_at`` — deletion still wins when both are
set (the store's ``list_archived_v2_projects`` filters on
``deleted_at IS NULL``).

One new column, no new table. Mirror the pattern already used for
``deleted_at``: a nullable ISO-8601 text timestamp that the
application layer writes via ``now_timestamp()``. Running ``archive``
idempotently re-stamps the column; running ``unarchive`` clears it
back to NULL.

Using ``op.add_column`` (SQLAlchemy helper) so the migration runs
cleanly on both SQLite (local dev) and Postgres (Neon production). We
deliberately avoid a CHECK constraint or index on the column because
the access pattern is always ``WHERE user_id = ? AND archived_at IS
[NOT] NULL`` — the existing ``idx_v2_projects_user`` index already
covers the user filter, and a nullable timestamp index would not pay
for itself at the expected archive-rate (a handful of archived
projects per user).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0005"
down_revision: Union[str, Sequence[str], None] = "20260422_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable ISO-8601 timestamp. NULL means "active"; a stamp means
    # "hidden from the default list". Matches the deleted_at shape so
    # the runtime schema bootstrap in store.py can retrofit identically
    # on older deployments.
    op.add_column(
        "v2_projects",
        sa.Column("archived_at", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_projects", "archived_at")
