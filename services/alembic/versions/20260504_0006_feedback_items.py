"""Add feedback_items table — receives ingested feedback from connectors.

Revision ID: 20260504_0006
Revises: 20260504_0005
Create Date: 2026-05-02

Context
-------

W2 F4 lands the Linear connector + the CSV/JSON paste-in endpoint.
Both sources write here. The schema is shared by every connector
that ingests user feedback (vs. repo metadata, which lives in
``repo_snapshots`` from migration 0004) — Linear, CSV/JSON now,
Intercom / Productboard / Salesforce / Help Scout when those
ship.

The W2 F5 ingestion pipeline reads from this table: the queued
worker picks ``status='queued'`` rows, classifies + dedupes, and
flips them to ``status='classified'`` (or ``'discarded'`` for
noise). F5 will add classification columns (``classification_json``,
``embedding_id``, ``cluster_id``) — leaving them nullable here so
F4 can ship before F5.

Idempotency
-----------

``UNIQUE (workspace_id, content_hash)`` makes re-imports safe.
``content_hash`` is a sha256 of the canonical content string built
from the source. For Linear / GitHub the hash includes the
external_id; for CSV imports it's a hash of (title|body|
received_at) so the same row pasted twice doesn't dupe.

Workspace scope
---------------

Every column is workspace-scoped. The W4 audit shim swap reads
``request.state.workspace_id`` for every write into this table —
no ``ws-default`` legacy here, the table starts clean.

Downgrade
---------

Drop. Items are workspace-owned but always re-fetchable from the
source; rolling back is partner-affecting but not data-loss-
permanent (Linear / Intercom / etc. still hold the original
records).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_0006"
down_revision: Union[str, Sequence[str], None] = "20260504_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_items (
                item_id           TEXT PRIMARY KEY,
                workspace_id      TEXT NOT NULL,
                source            TEXT NOT NULL,
                external_id       TEXT,
                content_hash      TEXT NOT NULL,
                title             TEXT NOT NULL,
                body              TEXT NOT NULL DEFAULT '',
                author            TEXT,
                author_email      TEXT,
                received_at       TEXT,
                ingested_at       TEXT NOT NULL,
                type_hint         TEXT,
                raw_payload_json  TEXT,
                status            TEXT NOT NULL DEFAULT 'queued'
                                  CHECK (status IN
                                    ('queued','classified','discarded','promoted')),
                UNIQUE (workspace_id, content_hash)
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_items_workspace "
            "ON feedback_items(workspace_id, ingested_at DESC)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_items_queued "
            "ON feedback_items(status) WHERE status = 'queued'"
        )
        return

    # SQLite path.
    op.create_table(
        "feedback_items",
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "body", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("author_email", sa.Text(), nullable=True),
        sa.Column("received_at", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.Text(), nullable=False),
        sa.Column("type_hint", sa.Text(), nullable=True),
        sa.Column("raw_payload_json", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="queued",
        ),
        sa.PrimaryKeyConstraint("item_id", name="pk_feedback_items"),
        sa.UniqueConstraint(
            "workspace_id", "content_hash", name="uq_feedback_items_hash"
        ),
        sa.CheckConstraint(
            "status IN ('queued','classified','discarded','promoted')",
            name="ck_feedback_items_status",
        ),
    )
    op.create_index(
        "idx_feedback_items_workspace",
        "feedback_items",
        ["workspace_id", "ingested_at"],
    )
    op.create_index(
        "idx_feedback_items_status",
        "feedback_items",
        ["status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # The upgrade path creates dialect-specific indexes — Postgres
    # gets a partial index named ``idx_feedback_items_queued``;
    # SQLite gets a full-status index named
    # ``idx_feedback_items_status``. Drop the right one (with
    # IF EXISTS so a half-failed upgrade can still be rolled back).
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_feedback_items_queued")
    else:
        op.drop_index(
            "idx_feedback_items_status", table_name="feedback_items"
        )
    op.drop_index(
        "idx_feedback_items_workspace", table_name="feedback_items"
    )
    op.drop_table("feedback_items")
