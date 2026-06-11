"""Add repo_snapshots + connector_sync_runs tables — v4 connector sync state.

Revision ID: 20260504_0004
Revises: 20260504_0003
Create Date: 2026-05-02

Context
-------

Two tables for the W1 polling job (60-min cadence, asyncio task in
``api.py`` lifespan) and the W3 prioritization agent that consumes
the snapshots.

``repo_snapshots`` is a denormalized read-model: one row per
(workspace, provider, repo). The W3 planner reads ``snapshot_json``
whole — we never need diffs at the sync layer. The polling job
replaces the row at each successful sync. Diffing happens at the
planner step, not here.

``connector_sync_runs`` is an audit / observability log: one row
per sync attempt. Drives the design's "Sync failed · last
successful 6 hours ago · Retry →" UI string.

Forward-looking column: parent_run_id
-------------------------------------

``connector_sync_runs.parent_run_id`` is W1-cheap forward setup
for W3. The W3 prioritization-run will spawn child runs (one per
sub-agent / topic per the multi-agent F7-REVISED addendum). Adding
the column now means W3 doesn't need a retrofit migration. NULL
for all W1 sync runs (they're not children of anything).

Schema
------

``repo_snapshots``:

- Composite PK (workspace_id, provider, repo_id). ``repo_id`` is
  the GitHub numeric id as TEXT — stable across renames, unlike
  ``repo_full_name`` which changes if the org or repo is renamed.
- ``visibility`` -- 'public' / 'private' / 'internal'.
- ``snapshot_json`` -- ``{tree_top: [...], open_issues: [...],
  recent_commits: [...]}`` per the W1 sync.py contract.
- ``status`` -- 'fresh' / 'stale' / 'error'. 'stale' surfaces in
  the connectors API when ``last_sync_at`` is older than 2x the
  poll interval.
- Index on (workspace_id, last_sync_at DESC) for the connectors
  API's "give me the workspace's most recent snapshots" query.

``connector_sync_runs``:

- ``run_id`` PK -- ``run-<10 hex>``.
- ``trigger`` -- 'scheduled' / 'manual' / 'install'.
- ``status`` -- 'running' / 'ok' / 'error' / 'rate_limited' /
  'needs_reauth'. Drives the connector-tile error states.
- ``repos_synced`` -- count of repos that completed in this run.
- ``error`` -- nullable text; populated when status != 'ok'.
- Index on (workspace_id, started_at DESC) for "show me this
  workspace's recent runs."
- Partial index on unfinished runs (Postgres) lets the start-of-
  loop reconciler in ``sync_scheduler.py`` quickly find orphaned
  ``running`` rows after a Fly machine restart.

No foreign keys
---------------

Application-layer cleanup (matches #094 + #089). Workspace deletion
in W4-W5 cascades to these tables at the store layer.

Chain
-----

``down_revision = "20260504_0003"``.

Idempotency
-----------

Postgres uses ``CREATE TABLE IF NOT EXISTS``. SQLite goes through
``op.create_table``. SQLite gets full indexes where Postgres uses
partial indexes (older SQLite lacks partial-index support).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_0004"
down_revision: Union[str, Sequence[str], None] = "20260504_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_snapshots (
                workspace_id   TEXT NOT NULL,
                provider       TEXT NOT NULL,
                repo_id        TEXT NOT NULL,
                repo_full_name TEXT NOT NULL,
                default_branch TEXT,
                visibility     TEXT,
                last_sync_at   TEXT NOT NULL,
                snapshot_json  TEXT NOT NULL,
                status         TEXT NOT NULL,
                PRIMARY KEY (workspace_id, provider, repo_id)
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_snap_ws_recent "
            "ON repo_snapshots(workspace_id, last_sync_at DESC)"
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_sync_runs (
                run_id        TEXT PRIMARY KEY,
                workspace_id  TEXT NOT NULL,
                provider      TEXT NOT NULL,
                trigger       TEXT NOT NULL,
                started_at    TEXT NOT NULL,
                finished_at   TEXT,
                status        TEXT NOT NULL,
                repos_synced  INTEGER NOT NULL DEFAULT 0,
                error         TEXT,
                parent_run_id TEXT
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_runs_ws_recent "
            "ON connector_sync_runs(workspace_id, started_at DESC)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_runs_unfinished "
            "ON connector_sync_runs(status) WHERE finished_at IS NULL"
        )
        return

    # SQLite / other backends.
    op.create_table(
        "repo_snapshots",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("repo_id", sa.Text(), nullable=False),
        sa.Column("repo_full_name", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=True),
        sa.Column("visibility", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "provider",
            "repo_id",
            name="pk_repo_snapshots",
        ),
    )
    op.create_index(
        "idx_repo_snap_ws_recent",
        "repo_snapshots",
        ["workspace_id", "last_sync_at"],
        unique=False,
    )
    op.create_table(
        "connector_sync_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "repos_synced",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("parent_run_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_sync_runs_ws_recent",
        "connector_sync_runs",
        ["workspace_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "idx_sync_runs_unfinished",
        "connector_sync_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_sync_runs_unfinished", table_name="connector_sync_runs"
    )
    op.drop_index(
        "idx_sync_runs_ws_recent", table_name="connector_sync_runs"
    )
    op.drop_table("connector_sync_runs")
    op.drop_index("idx_repo_snap_ws_recent", table_name="repo_snapshots")
    op.drop_table("repo_snapshots")
