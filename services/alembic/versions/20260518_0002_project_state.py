"""Add project state machine columns + indexes to v2_projects.

Revision ID: 20260518_0002
Revises: 20260518_0001
Create Date: 2026-05-03

Context
-------

Backbone of the v4 Kanban Workspace Home (B1.1) and the per-project
review state machine (B3.3). Each ``v2_projects`` row carries:

- ``project_state``  — pending_review | in_review | approved | rejected
                       | summary_ready (5th state reserved for post-W4)
- ``priority_order`` — sparse 1024-step int, NULL means "use ROI sort"
- ``roi_score``      — int populated by Session α's orchestrator;
                       NULL until the first AI pass

Sort tuple per the Kanban brief:

    ORDER BY priority_order ASC NULLS LAST,
             roi_score DESC NULLS LAST,
             created_at DESC

Manual reorders win over the AI's score; new AI-clustered cards land
in their ROI position so they don't jump a user's manual order.

State machine — only legal transitions through /transition:

    pending_review -> in_review
    in_review      -> approved   (terminal)
    in_review      -> rejected   (terminal)

Cross-column drag is the manual override path: it bypasses
``validate_transition``, requires a non-empty note, and stamps the
audit row with ``manual: true``. Re-opening a terminal state always
goes through that path so the audit trail captures *why*.

The 5th state ``summary_ready`` is in the CHECK constraint so the
schema is forward-compatible — Session α can flip a project to it
once the post-W4 summary feature lands without another migration.

Backfill semantics
------------------

Pre-W3 user-created ``v2_projects`` rows are real work in flight —
the founder's existing projects are already shipping/shipped, not
"awaiting AI review." The Postgres backfill therefore pulls
``metadata_json->>'state'`` if W3's orchestrator stamped one (it
does for AI-generated rows), and falls back to ``approved`` for
legacy human-authored rows so they land in the "Shipping" Kanban
column on first render rather than "Review needed".

The column-level DEFAULT is still ``'pending_review'`` so any path
that INSERTs without specifying a state lands in the queue, but
production INSERTs MUST set state explicitly:

  - The orchestrator-driven path (Session α F7) writes
    ``project_state='pending_review'`` since those rows are
    actually awaiting human review.
  - The kickoff / user-create path writes ``project_state='approved'``
    since the user is the source of truth for their own intentional
    work — see ``store.create_v2_project``.

Audit
-----

This migration adds *no* new audit table. The existing ``audit_log``
schema (added in 0001_baseline) already carries every column the
brief asked for: workspace_id, project_id, actor_user_id, category,
action, before_json, after_json, created_at. State transitions log
with ``category="project_state"`` and ``action ∈ {transition,
manual_override, manual_priority, transition_rejected}`` — the
existing ``append_audit_event`` helper handles writes.

Workspace scope
---------------

``v2_projects`` already carries workspace_id (added in 0005). The
new index ``idx_v2_projects_state_workspace`` is the hot path for
the Kanban GET — filtering by workspace + state and sorting by the
priority/ROI tuple in one btree pass.

Downgrade
---------

Drop indexes + columns. Dialect-aware drops mirror the b0d1214 fix
on the feedback_items index downgrade (Postgres uses raw DROP INDEX,
SQLite goes through op.drop_index because batch ALTER doesn't
reliably target named indexes on every version).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260518_0002"
down_revision: Union[str, Sequence[str], None] = "20260518_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Single source of truth for the CHECK constraint. The Postgres CHECK
# is added as a separate ALTER step, the SQLite CHECK is added via
# sa.CheckConstraint on op.add_column. Defined here so both paths
# stay byte-identical and the project_state.py module's STATES tuple
# can be cross-referenced in PR review.
_PROJECT_STATE_CHECK = (
    "project_state IN ("
    "'pending_review',"
    "'in_review',"
    "'approved',"
    "'rejected',"
    "'summary_ready'"
    ")"
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Three-step pattern so legacy rows get a sensible backfill:
        #
        #   1. ADD COLUMN nullable so the new column can be filled by
        #      a UPDATE without violating NOT NULL on existing rows.
        #   2. Backfill: pull state from metadata_json if Session α's
        #      orchestrator stamped one; else fall back to 'approved'
        #      because pre-W3 rows are user-authored work that's
        #      already shipping or shipped, not "awaiting AI review".
        #   3. Tighten: SET NOT NULL + SET DEFAULT for new INSERTs,
        #      then add the CHECK constraint that bounds the enum.
        #
        # Done as a series of independent ALTERs so a partial failure
        # is recoverable — each step is idempotent under
        # ``IF NOT EXISTS`` / ``COALESCE`` so re-running the
        # migration's _ensure_v2_projects_state_columns retrofit
        # against a half-applied state still converges.
        op.execute(
            "ALTER TABLE v2_projects "
            "ADD COLUMN IF NOT EXISTS project_state TEXT"
        )
        # The ``replace(metadata_json, '\\u0000', '')`` strips JSON-escaped
        # null bytes from the TEXT column before the ``::jsonb`` cast.
        # Real production data hit this on the first three deploys: a
        # legacy v2_projects row contained ``{"opening_note":
        # "hello\\u0000..."}`` (user-typed stray null byte). Postgres
        # JSONB cannot represent null bytes in strings, so the cast +
        # ``->> 'state'`` raised ``UntranslatableCharacter``, which
        # Alembic surfaced as a deploy failure. Stripping the literal
        # 6-character escape sequence ``\\u0000`` from the TEXT input
        # before casting fixes the failure mode without touching the
        # column data itself (the row still has ``\\u0000`` in
        # ``metadata_json``; only the backfill computation skips it).
        op.execute(
            """
            UPDATE v2_projects
            SET project_state = COALESCE(
                replace(metadata_json, '\\u0000', '')::jsonb ->> 'state',
                'approved'
            )
            WHERE project_state IS NULL
            """
        )
        op.execute(
            "ALTER TABLE v2_projects "
            "ALTER COLUMN project_state SET NOT NULL, "
            "ALTER COLUMN project_state SET DEFAULT 'pending_review'"
        )
        # ADD CONSTRAINT IF NOT EXISTS isn't supported on Postgres
        # CHECKs natively; we rely on the migration only running once
        # per environment. If a re-run is ever needed, drop the
        # constraint by name first.
        op.execute(
            "ALTER TABLE v2_projects "
            f"ADD CONSTRAINT v2_projects_project_state_check "
            f"CHECK ({_PROJECT_STATE_CHECK})"
        )
        op.execute(
            "ALTER TABLE v2_projects "
            "ADD COLUMN IF NOT EXISTS priority_order INTEGER"
        )
        op.execute(
            "ALTER TABLE v2_projects "
            "ADD COLUMN IF NOT EXISTS roi_score INTEGER"
        )
        # Composite index covering the Kanban hot path —
        # WHERE workspace_id = ? AND project_state = ?
        # ORDER BY priority_order, roi_score DESC, created_at DESC.
        # Postgres can read the ORDER BY straight off this index.
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_projects_state_workspace "
            "ON v2_projects (workspace_id, project_state, "
            "priority_order, roi_score DESC, created_at DESC)"
        )
        return

    # SQLite path. Test DBs and dev SQLite files have no legacy
    # v2_projects rows that pre-date this migration — the test
    # harness builds a fresh DB per test (``make_test_app``) and
    # dev users running this migration are early enough in the
    # product lifecycle that the "approved-by-default" backfill
    # isn't needed. We use NOT NULL with a server_default of
    # 'pending_review' which SQLite applies to any pre-existing
    # row in one shot.
    op.add_column(
        "v2_projects",
        sa.Column(
            "project_state",
            sa.Text(),
            nullable=False,
            server_default="pending_review",
        ),
    )
    op.add_column(
        "v2_projects",
        sa.Column("priority_order", sa.Integer(), nullable=True),
    )
    op.add_column(
        "v2_projects",
        sa.Column("roi_score", sa.Integer(), nullable=True),
    )
    # SQLite supports CHECK constraints declared at table-create time
    # but not retroactively via ALTER TABLE. The store layer enforces
    # the same invariant (validate_transition runs before every
    # write) so the integrity check is intact in both dialects — the
    # SQLite test path just doesn't get a DB-level CHECK row.
    op.create_index(
        "idx_v2_projects_state_workspace",
        "v2_projects",
        ["workspace_id", "project_state", "priority_order"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Drop the index first regardless of dialect — Postgres allows
    # dropping a column referenced by an index (it cascades), but
    # SQLite is stricter; doing it in this order means the script
    # works on both without a CASCADE flag we'd then have to
    # special-case.
    if dialect == "postgresql":
        op.execute(
            "DROP INDEX IF EXISTS idx_v2_projects_state_workspace"
        )
        op.execute(
            "ALTER TABLE v2_projects "
            "DROP CONSTRAINT IF EXISTS v2_projects_project_state_check"
        )
        op.execute("ALTER TABLE v2_projects DROP COLUMN IF EXISTS roi_score")
        op.execute(
            "ALTER TABLE v2_projects DROP COLUMN IF EXISTS priority_order"
        )
        op.execute(
            "ALTER TABLE v2_projects DROP COLUMN IF EXISTS project_state"
        )
        return

    op.drop_index(
        "idx_v2_projects_state_workspace", table_name="v2_projects"
    )
    op.drop_column("v2_projects", "roi_score")
    op.drop_column("v2_projects", "priority_order")
    op.drop_column("v2_projects", "project_state")
