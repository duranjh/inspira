"""Shelves — user-owned named containers for grouping projects.

Revision ID: 20260421_0002
Revises: 20260421_0001
Create Date: 2026-04-21

Adds the ``shelves`` table + the ``shelf_id`` nullable column on
``v2_projects``. Semantics: a shelf is a per-user named grouping of
projects; a project belongs to at most one shelf at a time; a project
whose ``shelf_id`` is NULL sits on the implicit "Unfiled" shelf (never
materialised). Deleting a shelf un-shelves its member projects rather
than cascading to project deletion — the un-shelve happens in the store
layer, not here (nothing schema-level to enforce).

Every DDL uses ``IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS`` (on the
Postgres branch) so running the migration against a DB that was already
bootstrapped by ``store.py._initialize_v2_projects_schema`` / the idempotent
``_ensure_v2_projects_shelf_column`` retrofit is a no-op, not an error.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260421_0002"
down_revision: Union[str, Sequence[str], None] = "20260421_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CREATE_SHELVES_DDL = """
CREATE TABLE IF NOT EXISTS shelves (
    shelf_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
)
"""


_CREATE_SHELVES_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_shelves_user "
    "ON shelves(user_id, deleted_at)"
)


def _add_shelf_id_column() -> None:
    """Add ``shelf_id`` to ``v2_projects``, tolerant of pre-existing column.

    The store's ``_ensure_v2_projects_shelf_column`` may already have added
    this column on DB bootstrap (it runs on every service start so fresh
    installs don't need alembic). Postgres supports ``ADD COLUMN IF NOT
    EXISTS`` directly; SQLite we wrap in a try/except that catches the
    "duplicate column" complaint.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE IF EXISTS v2_projects "
                "ADD COLUMN IF NOT EXISTS shelf_id TEXT"
            )
        )
        return

    try:
        op.execute(sa.text("ALTER TABLE v2_projects ADD COLUMN shelf_id TEXT"))
    except Exception as exc:  # pragma: no cover — error-path guard
        message = str(exc).lower()
        if "duplicate column" in message or "no such table" in message:
            return
        raise


def upgrade() -> None:
    op.execute(sa.text(_CREATE_SHELVES_DDL.strip()))
    op.execute(sa.text(_CREATE_SHELVES_INDEX_DDL))
    _add_shelf_id_column()


def downgrade() -> None:
    # Drop the shelves table. The ``shelf_id`` column on v2_projects is
    # intentionally NOT removed — SQLite pre-3.35 doesn't support
    # DROP COLUMN, and leaving a nullable column behind is harmless even
    # on fresh Postgres downgrades (the store layer just stops reading it).
    op.execute(sa.text("DROP TABLE IF EXISTS shelves"))
