"""Drop credit ledger + voice tables (PR 2).

Revision ID: 20260424_0001
Revises: 20260422_0008
Create Date: 2026-04-24

Removes the legacy credit ledger and the entire voice realtime feature.
PR 2 replaced credits with plan-tier entitlements (no per-call metering)
and scrapped voice entirely (was a margin-killer at current price points
and never had unit-economic viability without overage billing).

Tables dropped
--------------

Credit ledger (no production data of value — packs never charged real
money via the Noop billing provider, so the rows are best-effort
ledger entries that no one downstream depends on):
- ``user_credits``
- ``credit_transactions``
- ``credit_packs`` (if it exists; some deploys may have skipped this)

Voice realtime (feature deleted entirely; rows have no consumer):
- ``voice_usage``
- ``voice_sessions``

Drop order matters: ``voice_usage`` → ``voice_sessions`` (FK) and
``credit_transactions`` → ``user_credits`` (FK).

Idempotency
-----------

Each DROP is wrapped with ``IF EXISTS`` (Postgres) and a ``try/except``
on SQLite (which lacks ``DROP TABLE IF EXISTS`` only in ancient
versions; modern SQLite supports it directly). Both branches use
``DROP TABLE IF EXISTS`` so re-running this migration is safe.

Downgrade is intentionally a no-op. Recreating these tables would
require restoring the entire credits / voice subsystem the previous
PR removed; that's out of scope for a downgrade and the code paths
that read these tables no longer exist.
"""
from __future__ import annotations

from alembic import op


# Alembic identifiers.
revision = "20260424_0001"
down_revision = "20260422_0008"
branch_labels = None
depends_on = None


_TABLES_DROP_ORDER: tuple[str, ...] = (
    # FKs first.
    "voice_usage",
    "voice_sessions",
    "credit_transactions",
    "user_credits",
    # Pack catalog (existed only in some deployments).
    "credit_packs",
)


def upgrade() -> None:
    """Drop credit + voice tables. Safe to re-run."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    for table_name in _TABLES_DROP_ORDER:
        if dialect == "postgresql":
            op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
        else:
            # SQLite: no CASCADE keyword needed (FKs don't enforce by
            # default in many SQLite configs and IF EXISTS handles
            # missing tables cleanly).
            op.execute(f"DROP TABLE IF EXISTS {table_name}")


def downgrade() -> None:
    """No-op. The credits + voice subsystems are gone from code; we don't
    rebuild the tables on downgrade because nothing reads them."""
    pass
