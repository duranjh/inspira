"""Baseline schema — v1 + v2 + auth tables.

Revision ID: 20260421_0001
Revises:
Create Date: 2026-04-21

Captures the schema that ``planning_studio_service.store`` creates through
``_initialize`` / ``_initialize_v2_schema`` / ``_initialize_users_schema`` /
``_initialize_v2_projects_schema`` / ``_ensure_user_id_columns``. Every
``CREATE TABLE`` and ``CREATE INDEX`` uses ``IF NOT EXISTS`` so running this
migration against a database that was already bootstrapped by store.py is a
no-op instead of an error — going forward, alembic owns schema changes and
store.py's initialise methods will be retired.

The ``ALTER TABLE ... ADD COLUMN user_id`` statements are wrapped in a
per-table catch identical to the one in store.py: SQLite reports
"duplicate column" when the column already exists; Postgres uses
``ADD COLUMN IF NOT EXISTS`` directly. The migration picks the right form
for the active dialect.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260421_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Tables with a ``user_id`` column that is retrofitted onto v2 rows. The list
# mirrors ``PlanningStudioStore._ensure_user_id_columns`` in store.py — kept
# in sync deliberately. Entries whose table is not actually created by this
# migration (``audit_events``, ``sources``) are included because store.py's
# retrofit method tolerates "no such table" errors; alembic skips them too.
# ---------------------------------------------------------------------------
_USER_ID_TABLES: tuple[str, ...] = (
    "topics",
    "relationships",
    "qna_turns",
    "decisions",
    "consistency_flags",
    "summary_versions",
    "audit_events",
    "sources",
)


# ---------------------------------------------------------------------------
# CREATE TABLE statements. Keep column order and types byte-identical to
# ``store.py`` so a fresh alembic-bootstrapped DB matches a store.py-
# bootstrapped DB exactly.
# ---------------------------------------------------------------------------
_CREATE_STATEMENTS: tuple[str, ...] = (
    # --- v1 (deprecated but live) --------------------------------------
    """
    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        stage TEXT NOT NULL,
        owner TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        status TEXT NOT NULL,
        transcript_path TEXT,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        session_id TEXT,
        artifact_type TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        artifact_path TEXT,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(project_id),
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    # --- Users (auth) ---------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        display_name TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # --- v2 explicit projects table ------------------------------------
    """
    CREATE TABLE IF NOT EXISTS v2_projects (
        project_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )
    """,
    # --- v2 canvas-first tables ----------------------------------------
    """
    CREATE TABLE IF NOT EXISTS topics (
        topic_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        icon TEXT NOT NULL,
        position_x REAL NOT NULL,
        position_y REAL NOT NULL,
        status TEXT NOT NULL,
        order_index INTEGER NOT NULL,
        origin TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS relationships (
        relationship_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_topic_id TEXT NOT NULL,
        target_topic_id TEXT NOT NULL,
        label TEXT,
        origin TEXT NOT NULL,
        strength TEXT,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        UNIQUE(project_id, source_topic_id, target_topic_id),
        FOREIGN KEY(project_id) REFERENCES projects(project_id),
        FOREIGN KEY(source_topic_id) REFERENCES topics(topic_id),
        FOREIGN KEY(target_topic_id) REFERENCES topics(topic_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS qna_turns (
        turn_id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        role TEXT NOT NULL,
        order_index INTEGER NOT NULL,
        body TEXT NOT NULL,
        why_this_matters TEXT,
        action TEXT,
        suggested_responses_json TEXT,
        status TEXT NOT NULL,
        parent_turn_id TEXT,
        attachments_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(topic_id) REFERENCES topics(topic_id),
        FOREIGN KEY(project_id) REFERENCES projects(project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        statement TEXT NOT NULL,
        rationale TEXT,
        status TEXT NOT NULL,
        source_turn_id TEXT,
        proposed_by TEXT NOT NULL,
        confirmed_by_user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        retracted_at TEXT,
        FOREIGN KEY(topic_id) REFERENCES topics(topic_id),
        FOREIGN KEY(project_id) REFERENCES projects(project_id),
        FOREIGN KEY(source_turn_id) REFERENCES qna_turns(turn_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS open_questions (
        question_id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL,
        answer_turn_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(topic_id) REFERENCES topics(topic_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risks_assumptions (
        risk_id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        severity TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(topic_id) REFERENCES topics(topic_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consistency_flags (
        flag_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        topic_a_id TEXT NOT NULL,
        decision_a_id TEXT,
        topic_b_id TEXT NOT NULL,
        decision_b_id TEXT,
        description TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL,
        resolved_turn_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS context_sources (
        source_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        display_name TEXT NOT NULL,
        uri TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        added_by_user_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_references (
        reference_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        topic_id TEXT,
        turn_id TEXT,
        decision_id TEXT,
        citation_detail TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(source_id) REFERENCES context_sources(source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_versions (
        version_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        version_hash TEXT NOT NULL,
        content_markdown TEXT NOT NULL,
        sections_json TEXT NOT NULL,
        open_questions_json TEXT,
        approval_state TEXT NOT NULL,
        generated_by TEXT NOT NULL,
        generated_by_user_id TEXT,
        version_note TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_actions (
        action_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        summary_version_id TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        comment TEXT,
        state_before TEXT,
        state_after TEXT,
        ip_address TEXT,
        session_id TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(summary_version_id) REFERENCES summary_versions(version_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        event_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        project_id TEXT,
        actor_user_id TEXT NOT NULL,
        category TEXT NOT NULL,
        action TEXT NOT NULL,
        subject_id TEXT,
        before_json TEXT,
        after_json TEXT,
        ip_address TEXT,
        session_id TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL
    )
    """,
)


_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_v2_projects_user ON v2_projects(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_topics_project ON topics(project_id, deleted_at)",
    "CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_relationships_project ON relationships(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_qna_topic_order ON qna_turns(topic_id, order_index)",
    "CREATE INDEX IF NOT EXISTS idx_qna_project_role ON qna_turns(project_id, role)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_topic ON decisions(topic_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_flags_project_status ON consistency_flags(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_flags_topic_a ON consistency_flags(topic_a_id)",
    "CREATE INDEX IF NOT EXISTS idx_flags_topic_b ON consistency_flags(topic_b_id)",
    "CREATE INDEX IF NOT EXISTS idx_sources_project ON context_sources(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_summary_versions_project ON summary_versions(project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_project ON approval_actions(project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_version ON approval_actions(summary_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_workspace ON audit_log(workspace_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_log(project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category, action)",
)


def _add_user_id_columns() -> None:
    """Add ``user_id`` columns to v2 tables when missing.

    Mirrors ``PlanningStudioStore._ensure_user_id_columns``. Each ``ALTER
    TABLE`` is issued inside its own try/except: SQLite raises
    ``OperationalError`` with ``"duplicate column"`` when the column is
    already there, and ``"no such table"`` when the table does not exist
    (the retrofit list in store.py names two tables that are never
    actually created — we keep them in the list for parity). Postgres
    supports ``ADD COLUMN IF NOT EXISTS`` natively, so we branch on the
    active dialect.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    for table in _USER_ID_TABLES:
        if dialect == "postgresql":
            op.execute(
                sa.text(
                    f"ALTER TABLE IF EXISTS {table} "
                    "ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'user-system'"
                )
            )
            continue

        # SQLite (and anything else): try, tolerate the two expected failures.
        try:
            op.execute(
                sa.text(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN user_id TEXT NOT NULL DEFAULT 'user-system'"
                )
            )
        except Exception as exc:  # pragma: no cover — error-path guard
            message = str(exc).lower()
            if "duplicate column" in message or "no such table" in message:
                continue
            raise


def upgrade() -> None:
    for ddl in _CREATE_STATEMENTS:
        op.execute(sa.text(ddl.strip()))
    for index_ddl in _INDEX_STATEMENTS:
        op.execute(sa.text(index_ddl))
    _add_user_id_columns()


# Tables are dropped in reverse FK-dependency order so downgrade works on
# Postgres too (SQLite is permissive about FK ordering at drop time, but
# Postgres is not). Indexes are removed implicitly with their tables.
_DROP_ORDER: tuple[str, ...] = (
    "schema_version",
    "audit_log",
    "approval_actions",
    "summary_versions",
    "source_references",
    "context_sources",
    "consistency_flags",
    "risks_assumptions",
    "open_questions",
    "decisions",
    "qna_turns",
    "relationships",
    "topics",
    "v2_projects",
    "users",
    "artifacts",
    "sessions",
    "projects",
)


def downgrade() -> None:
    for table in _DROP_ORDER:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
