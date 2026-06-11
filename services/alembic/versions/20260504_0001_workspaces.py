"""Add workspaces table — multi-tenant foundation for the v4 B2B pivot.

Revision ID: 20260504_0001
Revises: 20260429_0003
Create Date: 2026-05-02

Context
-------

The v4 pivot turns Inspira into a B2B product where a workspace is
the unit of isolation, billing, and member management. Today the
codebase carries a single-tenant placeholder: ``audit_log.workspace_id``
is hardcoded to ``"ws-default"`` at ``store.py:5624``. This migration
is the first of five that build a real workspace concept; the
audit-log shim swap happens in W4 once every endpoint has been
migrated to enforce workspace scope.

Schema
------

``workspaces`` — one row per workspace.

- ``workspace_id`` -- ``ws-<10 hex>``. Primary key, opaque.
- ``slug`` -- URL/UI-safe handle, UNIQUE. Backfill (migration 0005)
  assigns ``"personal-<user_id[:8]>"`` for every existing user; new
  B2B workspaces pick free-form slugs (``"acme"``, ``"acme-mobile"``).
- ``billing_owner_user_id`` -- the user whose Stripe customer the
  workspace bills against. Stays the creator until ownership is
  transferred (W6+ flow).
- ``plan_tier`` -- ``free|pro|team|enterprise``. Default ``free``.
  ``team`` is the codebase slug for the product-facing "Frontier"
  tier per the May-2026 pricing-strategy lock — slug rename out of
  scope. CHECK constraint enforces the four-value set so a typo at
  the store layer can't poison billing logic.
- ``stripe_customer_id`` -- nullable until billing wires up in W6.
- ``settings_json`` -- JSON blob for per-workspace feature flags,
  branding, etc. Default ``"{}"``.
- ``archived_at`` -- soft-delete timestamp; NULL means active.
  Reserved now so the W6 enterprise-archive flow doesn't need
  another migration.

No foreign keys
---------------

Following the convention from #094 (documents) and #089
(next_steps_artifacts): no explicit FK constraints. Workspace
teardown is handled at the store layer in W4-W5.

Chain
-----

``down_revision = "20260429_0003"`` chains directly off the
documents migration (current head). The four sibling migrations
(0002 members, 0003 connector_credentials, 0004 connector_sync,
0005 backfill) chain off this one in order.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS`` for idempotent
re-apply. SQLite goes through ``op.create_table`` and relies on
alembic revision tracking. Partial-index syntax differs between
backends — Postgres gets a ``WHERE archived_at IS NULL`` partial
index; SQLite falls back to a full plan_tier index (older SQLite
versions lack partial-index support and the table is small).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_0001"
down_revision: Union[str, Sequence[str], None] = "20260429_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id          TEXT PRIMARY KEY,
                slug                  TEXT NOT NULL UNIQUE,
                name                  TEXT NOT NULL,
                created_at            TEXT NOT NULL,
                billing_owner_user_id TEXT NOT NULL,
                plan_tier             TEXT NOT NULL DEFAULT 'free'
                                      CHECK (plan_tier IN ('free','pro','team','enterprise')),
                stripe_customer_id    TEXT,
                settings_json         TEXT NOT NULL DEFAULT '{}',
                archived_at           TEXT
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspaces_billing_owner "
            "ON workspaces(billing_owner_user_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspaces_active_plan "
            "ON workspaces(plan_tier) WHERE archived_at IS NULL"
        )
        return

    # SQLite / other backends.
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.Text(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("billing_owner_user_id", sa.Text(), nullable=False),
        sa.Column(
            "plan_tier",
            sa.Text(),
            nullable=False,
            server_default="free",
        ),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column(
            "settings_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "plan_tier IN ('free','pro','team','enterprise')",
            name="ck_workspaces_plan_tier",
        ),
    )
    op.create_index(
        "idx_workspaces_billing_owner",
        "workspaces",
        ["billing_owner_user_id"],
        unique=False,
    )
    op.create_index(
        "idx_workspaces_active_plan",
        "workspaces",
        ["plan_tier"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_workspaces_active_plan", table_name="workspaces")
    op.drop_index("idx_workspaces_billing_owner", table_name="workspaces")
    op.drop_table("workspaces")
