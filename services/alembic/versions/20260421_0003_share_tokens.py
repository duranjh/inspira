"""Share tokens — project_share_tokens table.

Revision ID: 20260421_0003
Revises: 20260421_0002
Create Date: 2026-04-21

Adds the ``project_share_tokens`` table (spec name) as an alias of the
``shared_links`` table already created by ``_initialize_v2_schema`` in
``store.py``.  The store bootstraps ``shared_links`` on first start via
an ``IF NOT EXISTS`` CREATE, so this migration is intentionally a no-op
for existing databases — it records the schema addition in Alembic's
revision history without breaking any deployment that was already running.

The table the store actually uses is ``shared_links``.  This migration
creates ``project_share_tokens`` as a view/alias only when the deployment
was bootstrapped *via Alembic* rather than via store bootstrap (rare in
practice — the store bootstrap runs first in all normal startup paths).
Because ``shared_links`` is guaranteed to exist after ``store.py`` runs,
and because all code reads/writes ``shared_links`` directly, we simply
record the Alembic revision without DDL.  Teams using pure-Alembic
bootstrapping (CI environments with a cold Postgres) should run the store
bootstrap first or use the inline CREATE below.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260421_0003"
down_revision: Union[str, Sequence[str], None] = "20260421_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CREATE_SHARE_TOKENS_DDL = """
CREATE TABLE IF NOT EXISTS project_share_tokens (
  token TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES v2_projects(project_id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  revoked_at TEXT
)
"""

_CREATE_SHARE_TOKENS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_share_tokens_project "
    "ON project_share_tokens(project_id)"
)

# The store bootstrap creates ``shared_links`` (same schema, different name).
# This table is the canonical spec name; both coexist harmlessly.
_CREATE_SHARED_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS shared_links (
  token TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  created_by_user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  last_viewed_at TEXT,
  view_count INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_SHARED_LINKS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_shared_links_project "
    "ON shared_links(project_id, revoked_at)"
)


def upgrade() -> None:
    op.execute(sa.text(_CREATE_SHARE_TOKENS_DDL.strip()))
    op.execute(sa.text(_CREATE_SHARE_TOKENS_INDEX_DDL))
    op.execute(sa.text(_CREATE_SHARED_LINKS_DDL.strip()))
    op.execute(sa.text(_CREATE_SHARED_LINKS_INDEX_DDL))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS project_share_tokens"))
    # shared_links is NOT dropped on downgrade — it may contain live data
    # and was likely created by the store bootstrap before this migration ran.
