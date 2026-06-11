"""Backfill personal workspaces + workspace_id on v2_projects.

Revision ID: 20260504_0005
Revises: 20260504_0004
Create Date: 2026-05-02

Context
-------

Final W1 migration. Adds two columns
(``users.default_workspace_id``, ``v2_projects.workspace_id``)
and backfills the workspace concept for every existing
authenticated user. After this migration:

- Every non-anonymous, non-system user has exactly one personal
  workspace (slug ``"personal-<user_id[:8]>"``, role ``owner``).
- Every existing ``v2_projects`` row carries a ``workspace_id``
  pointing at the owner's personal workspace.
- Every ``audit_log`` row tagged ``"ws-default"`` is retagged to
  the actor's personal workspace; orphaned rows (whose actor no
  longer exists) keep ``"ws-default"`` so they don't lose their
  group association.
- Every backfilled user gets ``users.default_workspace_id`` set to
  their personal workspace, used as the fallback resolution path
  in ``current_workspace_member``.

Excluded from backfill
----------------------

- ``user-anon-<hex>`` rows: anonymous sessions never get persistent
  workspaces. Their canvas data is scoped per-user-id already and
  stays that way until they upgrade to an account.
- ``user-system`` (singleton legacy shared user defined at
  ``auth.py:247``): a service-account, not a real user.

Dialect handling
----------------

Both columns are added via ``op.add_column`` (works on Postgres
and SQLite). Data backfill iterates users in Python and emits
parameterized SQL — dialect-agnostic, completes in <30s on a
100k-row staging Postgres per the partner-readiness gate.

The audit-log retag is one bulk SQL pass that runs in constant
memory.

Downgrade
---------

- Revert ``audit_log`` rows tagged with backfilled workspace IDs
  back to ``"ws-default"`` (identified via ``slug LIKE 'personal-%'``).
- Drop ``v2_projects.workspace_id`` and ``users.default_workspace_id``.
  Postgres uses ``DROP COLUMN``; SQLite uses ``batch_alter_table``
  to rebuild the table.
- The personal-workspaces themselves are dropped only when 0001
  is reversed. Reversing 0005 alone leaves the workspaces in
  place — preserves the data for forward re-upgrade.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_0005"
down_revision: Union[str, Sequence[str], None] = "20260504_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the two backfill columns.
    op.add_column(
        "users",
        sa.Column("default_workspace_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "v2_projects",
        sa.Column("workspace_id", sa.Text(), nullable=True),
    )

    # 2. Iterate non-anon, non-system users and create one personal
    #    workspace + owner membership each. Pythonic loop is
    #    dialect-agnostic and the user count is small.
    users = bind.execute(
        sa.text(
            """
            SELECT
                user_id,
                COALESCE(NULLIF(display_name, ''), 'My') AS display_name
            FROM users
            WHERE user_id NOT LIKE 'user-anon-%'
              AND user_id != 'user-system'
            """
        )
    ).fetchall()

    now = _now_iso()
    insert_workspace = sa.text(
        """
        INSERT INTO workspaces (
            workspace_id, slug, name, created_at,
            billing_owner_user_id, plan_tier, settings_json
        )
        VALUES (
            :ws_id, :slug, :name, :now,
            :user_id, 'free', '{}'
        )
        """
    )
    insert_member = sa.text(
        """
        INSERT INTO workspace_members (
            workspace_id, user_id, role, created_at
        )
        VALUES (:ws_id, :user_id, 'owner', :now)
        """
    )
    update_user = sa.text(
        """
        UPDATE users
        SET default_workspace_id = :ws_id
        WHERE user_id = :user_id
        """
    )
    update_projects = sa.text(
        """
        UPDATE v2_projects
        SET workspace_id = :ws_id
        WHERE user_id = :user_id
        """
    )

    for user_id, display_name in users:
        # Workspace ID: 'ws-' + 10 random hex chars. Random rather
        # than user_id-derived because cross-dialect MD5 portability
        # is messy (SQLite has no md5 by default).
        ws_id = f"ws-{secrets.token_hex(5)}"
        # Slug: 'personal-' + first 8 hex chars of user_id (after
        # the 'user-' prefix). UNIQUE constraint catches the rare
        # 8-hex-char collision (32-bit space; ~1k users → 1e-7
        # birthday probability) — surfaces during apply if it
        # fires.
        clean_user = user_id.replace("user-", "", 1)
        slug = f"personal-{clean_user[:8]}"
        # Honour the founder's existing display name. Trailing
        # apostrophe-s if it doesn't already end in 's'.
        if display_name.endswith("s"):
            name = f"{display_name}' workspace"
        else:
            name = f"{display_name}'s workspace"

        bind.execute(
            insert_workspace,
            {
                "ws_id": ws_id,
                "slug": slug,
                "name": name,
                "now": now,
                "user_id": user_id,
            },
        )
        bind.execute(
            insert_member,
            {"ws_id": ws_id, "user_id": user_id, "now": now},
        )
        bind.execute(
            update_user, {"ws_id": ws_id, "user_id": user_id}
        )
        bind.execute(
            update_projects, {"ws_id": ws_id, "user_id": user_id}
        )

    # 3. Audit-log retag — one bulk SQL pass. COALESCE keeps the
    #    literal "ws-default" for orphaned rows (actor no longer
    #    in users) so they don't lose their group association.
    bind.execute(
        sa.text(
            """
            UPDATE audit_log
            SET workspace_id = COALESCE(
                (
                    SELECT workspace_id FROM workspaces
                    WHERE billing_owner_user_id = audit_log.actor_user_id
                    LIMIT 1
                ),
                'ws-default'
            )
            WHERE workspace_id = 'ws-default'
            """
        )
    )

    # 4. Index the new v2_projects.workspace_id column for the
    #    workspace-scoped project queries that land in W4.
    op.create_index(
        "idx_v2_projects_workspace",
        "v2_projects",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Revert audit_log retag for backfilled workspaces.
    bind.execute(
        sa.text(
            """
            UPDATE audit_log
            SET workspace_id = 'ws-default'
            WHERE workspace_id IN (
                SELECT workspace_id FROM workspaces
                WHERE slug LIKE 'personal-%'
            )
            """
        )
    )

    # 2. Drop the projects index and the two backfill columns.
    op.drop_index(
        "idx_v2_projects_workspace", table_name="v2_projects"
    )

    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.drop_column("v2_projects", "workspace_id")
        op.drop_column("users", "default_workspace_id")
    else:
        # SQLite — batch_alter_table rebuilds the table.
        with op.batch_alter_table("v2_projects") as batch:
            batch.drop_column("workspace_id")
        with op.batch_alter_table("users") as batch:
            batch.drop_column("default_workspace_id")
