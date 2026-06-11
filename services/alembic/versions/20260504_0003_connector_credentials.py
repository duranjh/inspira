"""Add connector_credentials table — encrypted token store for v4 connectors.

Revision ID: 20260504_0003
Revises: 20260504_0002
Create Date: 2026-05-02

Context
-------

Sibling of 0001 (workspaces). Stores per-workspace connector
authentication state for the v4 ingestion pipeline. W1 ships the
GitHub connector; W2 adds Linear and CSV/JSON. The table is shaped
so adding a provider in W2+ is purely a row-shape question, not a
schema change.

What's encrypted vs not
-----------------------

For GitHub: ``encrypted_token`` holds the user-side OAuth token
used to verify install ownership. The actual GitHub App
*installation token* is 1-hour ephemeral and re-minted at each
sync via ``app_jwt() → installation_access_token()`` — never
persisted. The ``installation_id`` column carries the non-secret
GitHub installation reference.

For Linear (W2): ``encrypted_token`` holds the workspace API key
directly. ``installation_id`` is NULL.

Encryption uses ``byok.encrypt_api_key`` / ``decrypt_api_key``
(Fernet, ``INSPIRA_BYOK_SECRET`` env var). The same utility that
encrypts user OpenAI / Anthropic keys.

Schema
------

``connector_credentials`` — composite PK (workspace_id, provider).

- ``workspace_id`` + ``provider`` -- one connection per provider
  per workspace. PK collision gives free upsert idempotency: a
  re-install of the same GitHub App attempts to insert again,
  hits the PK, and the upsert path replaces. Multi-account same-
  provider in one workspace is intentionally closed off in v4 (see
  the v4 plan's risk callouts; structural change deferred to W7+).
- ``encrypted_token`` -- Fernet ciphertext via ``byok``.
- ``installation_id`` -- nullable. GitHub-specific.
- ``account_login`` -- e.g. "acme-corp". Drives the design's
  "Connected · Acme Corp / acme-platform · 3 repos" string.
- ``account_avatar_url`` -- optional avatar for the connector tile.
- ``scopes_json`` -- JSON array of granted scopes. Default ``"[]"``.
- ``created_at`` -- ISO-8601 UTC.
- ``last_refreshed_at`` -- nullable; updated on each successful
  sync that re-validates the token.
- ``status`` -- ``connected | needs_reauth | revoked``. CHECK
  constraint enforces the set. Drives the connector tile's idle /
  error visual states. The partial-status index (Postgres) speeds
  up the polling job's "find all connectors that need attention"
  scan.

Chain
-----

``down_revision = "20260504_0002"``.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS``; SQLite goes through
``op.create_table``. Postgres gets a partial index on non-connected
status (rare rows, fast scan); SQLite falls back to a full status
index.

Downgrade safety
----------------

Downgrade ``DROP TABLE`` rather than nullifying — these rows hold
encrypted secrets. Forcing re-OAuth on re-upgrade is the safer
default vs. preserving stale ciphertext from a rolled-back
deployment.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_0003"
down_revision: Union[str, Sequence[str], None] = "20260504_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_credentials (
                workspace_id       TEXT NOT NULL,
                provider           TEXT NOT NULL,
                encrypted_token    TEXT NOT NULL,
                installation_id    TEXT,
                account_login      TEXT,
                account_avatar_url TEXT,
                scopes_json        TEXT NOT NULL DEFAULT '[]',
                created_at         TEXT NOT NULL,
                last_refreshed_at  TEXT,
                status             TEXT NOT NULL DEFAULT 'connected'
                                   CHECK (status IN ('connected','needs_reauth','revoked')),
                PRIMARY KEY (workspace_id, provider)
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_conn_creds_attention "
            "ON connector_credentials(status) WHERE status != 'connected'"
        )
        return

    # SQLite / other backends.
    op.create_table(
        "connector_credentials",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("installation_id", sa.Text(), nullable=True),
        sa.Column("account_login", sa.Text(), nullable=True),
        sa.Column("account_avatar_url", sa.Text(), nullable=True),
        sa.Column(
            "scopes_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_refreshed_at", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="connected",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "provider", name="pk_connector_credentials"
        ),
        sa.CheckConstraint(
            "status IN ('connected','needs_reauth','revoked')",
            name="ck_connector_credentials_status",
        ),
    )
    op.create_index(
        "idx_conn_creds_attention",
        "connector_credentials",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_conn_creds_attention", table_name="connector_credentials"
    )
    op.drop_table("connector_credentials")
