"""Add user_access_tokens for Personal Access Tokens (PATs).

Revision ID: 20260422_0008
Revises: 20260422_0006
Create Date: 2026-04-22

Context
-------

Personal Access Tokens let external automations (Zapier, a user's own
script, the MCP server) hit the v2 API without a browser cookie.
Users mint a named token from Account Settings → API tokens; the raw
string is shown ONCE in a copy-once dialog and then discarded — we
persist only the SHA-256 hash.

Schema
------

* ``token_id`` -- ``tok_<hex>``. Primary key so revoke / list
  endpoints can reference a token without ever exposing the raw value.
* ``token_hash`` -- SHA-256 of the raw token bytes. UNIQUE so lookups
  on the resolve path are O(index) and a duplicate raw mint is a hard
  error not a silent overwrite.
* ``scopes_json`` -- JSON array of scope strings. v1 ships with every
  token carrying ``[]`` which resolves to full read+write, matching
  the session-cookie grant. Future expansion: ``"read:projects"``,
  ``"write:topics"``, etc. — the column already exists so we don't
  need another migration when we scope.
* ``last_used_at`` -- touched on every successful resolve. Lets the
  list view show "Last used 3h ago" and gives ops a way to spot
  dormant tokens for pruning.
* ``revoked_at`` -- soft-delete marker. We never hard-delete tokens
  so audit / forensics can trace who acted when; the index below
  keeps "list my active tokens" fast.

Chain note
----------

``down_revision = "20260422_0006"``. The BYOK migration
(``20260422_0007``) was developed in parallel off the same base; the
two branches are joined later by the ``3e31dfcdb483`` merge revision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0008"
down_revision: Union[str, Sequence[str], None] = "20260422_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_access_tokens",
        sa.Column("token_id", sa.Text(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "scopes_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_uat_user",
        "user_access_tokens",
        ["user_id", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "idx_uat_hash",
        "user_access_tokens",
        ["token_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_uat_hash", table_name="user_access_tokens")
    op.drop_index("idx_uat_user", table_name="user_access_tokens")
    op.drop_table("user_access_tokens")
