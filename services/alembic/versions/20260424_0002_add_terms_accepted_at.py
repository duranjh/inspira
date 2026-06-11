"""Add users.terms_accepted_at column for Terms-of-Service acceptance audit.

Revision ID: 20260424_0002
Revises: 20260424_0001
Create Date: 2026-04-24

Context
-------

PR 3 gates signup on an explicit Terms / Privacy acceptance checkbox. The
backend enforces the gate (400 ``terms_required`` when the body sets
``terms_accepted=False``), and on acceptance stamps the current UTC time
into ``users.terms_accepted_at`` so we can later prove which version of
the legal docs each user agreed to (by cross-referencing with the
``users.created_at`` timestamp).

Nullable by design — the legacy system user, pre-gate anonymous rows, and
any deployment that pre-dates PR 3 all read as NULL (``not captured``) in
the application layer. Only rows minted by the post-gate signup route
get a non-null value.

Chain note
----------

``down_revision = "20260424_0001"`` chains after the
credits-and-voice drop migration that landed earlier on 2026-04-24.
Alembic applies 0001 (drop credits + voice) and then 0002
(users.terms_accepted_at) on ``alembic upgrade head``.

Idempotency
-----------

Postgres uses ``ADD COLUMN IF NOT EXISTS`` which is natively idempotent,
so re-running this migration against a DB that already has the column is
a no-op. SQLite doesn't support that syntax on ADD COLUMN, but the store
bootstrap helper ``_ensure_user_terms_accepted_column`` handles the
dev-side retrofit case with a duplicate-column catch — alembic here
trusts the revision chain.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260424_0002"
down_revision: Union[str, Sequence[str], None] = "20260424_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        # ADD COLUMN IF NOT EXISTS is idempotent on Postgres; safe to re-run.
        op.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ"
        )
        return
    # SQLite / other backends — use the SQLAlchemy op so dialect-specific
    # type mapping kicks in. The column is nullable so existing rows read
    # as "not captured" in the application layer without a default value.
    op.add_column(
        "users",
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "terms_accepted_at")
