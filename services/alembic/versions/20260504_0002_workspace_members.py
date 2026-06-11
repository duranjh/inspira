"""Add workspace_members table — RBAC for v4 multi-tenant.

Revision ID: 20260504_0002
Revises: 20260504_0001
Create Date: 2026-05-02

Context
-------

Sibling of 0001 (workspaces). Stores the membership graph: who
belongs to which workspace and at what role. The role enum drives
the ``current_workspace_member(role_min)`` FastAPI dependency that
gates W1 endpoints from day one and becomes the F10 isolation gate
in W4-W5.

Role choice — four values, not five
-----------------------------------

An earlier draft considered a five-role split
(``owner|admin|planner|reviewer|viewer``) but we ship four:
``owner|admin|member|viewer``. Per-resource verbs (plan vs.
review) are already encoded in ``audit_log.action`` — collapsing
them to a workspace role would force per-feature scope tables in
W5. Industry default (Linear, Notion, GitHub) is also four-role.

Schema
------

``workspace_members`` — composite PK (workspace_id, user_id).

- ``workspace_id`` -- references workspaces.workspace_id at the
  application layer (no FK constraint; matches #094 convention).
- ``user_id`` -- references users.user_id at the application layer.
- ``role`` -- one of owner / admin / member / viewer. CHECK
  constraint enforces the set so a typo can't open a privilege
  loophole. Ordering for ``role_min`` comparison is enforced in
  Python at ``workspaces/models.py``.
- ``created_at`` -- when the membership was added (ISO-8601 UTC).
- ``invited_by`` -- nullable user_id of the inviter. NULL for
  backfilled personal-workspace owners (no inviter exists).

Chain
-----

``down_revision = "20260504_0001"``.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS``. SQLite goes through
``op.create_table`` and relies on alembic revision tracking.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_0002"
down_revision: Union[str, Sequence[str], None] = "20260504_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_members (
                workspace_id TEXT NOT NULL,
                user_id      TEXT NOT NULL,
                role         TEXT NOT NULL
                             CHECK (role IN ('owner','admin','member','viewer')),
                created_at   TEXT NOT NULL,
                invited_by   TEXT,
                PRIMARY KEY (workspace_id, user_id)
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_ws_members_user "
            "ON workspace_members(user_id)"
        )
        return

    # SQLite / other backends.
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "workspace_id", "user_id", name="pk_workspace_members"
        ),
        sa.CheckConstraint(
            "role IN ('owner','admin','member','viewer')",
            name="ck_workspace_members_role",
        ),
    )
    op.create_index(
        "idx_ws_members_user",
        "workspace_members",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_ws_members_user", table_name="workspace_members")
    op.drop_table("workspace_members")
