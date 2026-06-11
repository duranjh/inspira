"""Add cluster + embedding columns + feedback_clusters table.

Revision ID: 20260504_0007
Revises: 20260504_0006
Create Date: 2026-05-02

Context
-------

Layered on top of F4's feedback_items table. Adds:
- ``cluster_id``       — nullable FK-by-convention into the new
  ``feedback_clusters`` table. NULL for un-clustered items
  (LLM/embedding flag off, or cluster pipeline not yet run).
- ``embedding_json``   — JSON-serialized array of floats (1536
  dims for text-embedding-3-small). Stored as TEXT so the schema
  works on both SQLite and Postgres without pgvector.

Plus the new ``feedback_clusters`` table:
- ``cluster_id``       — TEXT PK
- ``workspace_id``     — workspace-scoped (every cluster lives in
  exactly one workspace)
- ``centroid_json``    — running average of member embeddings
- ``theme``            — auto-generated label, NULL until F8+
- ``item_count``       — denormalized count for the inbox tile
- ``created_at`` / ``updated_at``

Index on (workspace_id, updated_at DESC) for the cluster-list
endpoint's "newest first" sort.

Idempotency
-----------

Both columns + table use IF NOT EXISTS; SQLite's
``ALTER TABLE ADD COLUMN`` errors if the column exists, so we
catch + swallow on that path. Postgres path uses
``ADD COLUMN IF NOT EXISTS``.

Downgrade
---------

Drop the table + nullify-and-keep the columns (SQLite can't drop
columns without a table rebuild, so we leave them — cheaper for
a demo-era rollback and the ALTER cost is on the order of seconds
even for 1M rows).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260504_0007"
down_revision: Union[str, Sequence[str], None] = "20260504_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE feedback_items "
            "ADD COLUMN IF NOT EXISTS cluster_id TEXT"
        )
        op.execute(
            "ALTER TABLE feedback_items "
            "ADD COLUMN IF NOT EXISTS embedding_json TEXT"
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_clusters (
                cluster_id     TEXT PRIMARY KEY,
                workspace_id   TEXT NOT NULL,
                centroid_json  TEXT NOT NULL,
                theme          TEXT,
                item_count     INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_clusters_workspace "
            "ON feedback_clusters(workspace_id, updated_at DESC)"
        )
        return

    # SQLite. ADD COLUMN errors on existing column; tolerate.
    for col_def in (
        "ALTER TABLE feedback_items ADD COLUMN cluster_id TEXT",
        "ALTER TABLE feedback_items ADD COLUMN embedding_json TEXT",
    ):
        try:
            op.execute(col_def)
        except Exception:  # noqa: BLE001
            # Column already exists — idempotent retrofit path.
            pass
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_clusters (
            cluster_id     TEXT PRIMARY KEY,
            workspace_id   TEXT NOT NULL,
            centroid_json  TEXT NOT NULL,
            theme          TEXT,
            item_count     INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_clusters_workspace "
        "ON feedback_clusters(workspace_id, updated_at)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_feedback_clusters_workspace")
    op.execute("DROP TABLE IF EXISTS feedback_clusters")
    # SQLite can't DROP COLUMN cleanly without a table rebuild; the
    # leftover ``cluster_id`` and ``embedding_json`` columns are
    # harmless (NULL on rows from the next upgrade). Postgres can
    # drop, but for symmetry with SQLite we leave them too.
