"""Add users.{openai,anthropic}_api_key_encrypted columns for BYOK.

Revision ID: 20260422_0007
Revises: 20260422_0006
Create Date: 2026-04-22

Context
-------

Bring Your Own Key (BYOK): technical users can paste their own OpenAI or
Anthropic key in Account Settings. Future planner turns then bill the
user's provider directly (no Inspira credit decrement) and the canvas
shows a discreet "Your key" badge so they always know where the cost is
going.

We never store the raw key. The application encrypts with Fernet using
the ``INSPIRA_BYOK_SECRET`` env var; the columns hold only the base64
ciphertext blob. Rotation plan: a future migration adds a ``*_encrypted_kid``
tag column, but the initial shape is single-key so no tag is needed yet.

Nullable by design — most users will never set a key, and a NULL row reads
as "not configured" in the application layer. Empty string is also
normalised to NULL by the store helper.

Chain note
----------

down_revision = ``20260422_0006`` — chains after the private-notes
migration. Alembic applies 0006 (topics.private_notes) and 0007
(users.{openai,anthropic}_api_key_encrypted) in order on
``alembic upgrade head``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260422_0007"
down_revision: Union[str, Sequence[str], None] = "20260422_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable TEXT — cheap to store, portable across SQLite (dev) and
    # Postgres (prod, Neon). We don't impose a length bound because Fernet
    # ciphertext grows linearly with plaintext and future rotation formats
    # may include a KID prefix that pushes the payload over any tight cap.
    #
    # Idempotency: Postgres path uses ADD COLUMN IF NOT EXISTS; the SQLite
    # path cannot express IF NOT EXISTS for ADD COLUMN so the store's
    # retrofit helper (``_ensure_user_byok_columns``) catches the
    # "duplicate column name" error on its own dev-side bootstrap path.
    # At alembic time we trust the revision chain — this function only
    # runs when the column is known to be missing.
    op.add_column(
        "users",
        sa.Column("openai_api_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("anthropic_api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "anthropic_api_key_encrypted")
    op.drop_column("users", "openai_api_key_encrypted")
