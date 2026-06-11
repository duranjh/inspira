from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ServiceConfig
from .project_state import (
    IllegalTransitionError,
    validate_transition,
)

_log = logging.getLogger(__name__)


class StaleProjectStateError(Exception):
    """Raised by the optimistic-update path when the row's
    ``project_state`` no longer matches the value the caller observed.

    Two admins racing on the same project are the canonical case:
    the loser sees zero rows affected by the WHERE-state-equals-X
    UPDATE and surfaces this exception so the API layer can return
    409 with the current state, prompting the client to refetch.
    """

    def __init__(self, project_id: str, observed: str) -> None:
        self.project_id = project_id
        self.observed = observed
        super().__init__(
            f"project {project_id!r} state changed from {observed!r} "
            "between read and write (concurrent admin?)"
        )


# ---------------------------------------------------------------------------
# Connection adapters — thin wrappers that present a unified API over
# sqlite3 and psycopg3 so the store methods never need to know which backend
# is active.  Key dialect differences handled here (not in callers):
#   • Placeholder style: SQLite uses `?`, Postgres uses `%s`
#   • `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING` (Postgres)
#   • `executescript` (SQLite multi-statement) → per-statement execute loop
# ---------------------------------------------------------------------------

def _rebind(sql: str) -> str:
    """Translate SQLite `?` positional markers to psycopg `%s`."""
    return sql.replace("?", "%s")


def _pg_translate(sql: str) -> str:
    """Apply all SQL dialect fixes for a single Postgres statement.

    Handles:
    - `?` → `%s` placeholder conversion (psycopg uses %s)
    - `INSERT OR IGNORE INTO` → `INSERT INTO … ON CONFLICT DO NOTHING`
      (the store uses the standard ON CONFLICT syntax today; this guard
      handles any hand-written SQL that slips back to the SQLite idiom)
    - `rowid` → `ctid`  (used in ORDER BY rowid DESC for tie-breaking;
      ctid is Postgres' physical locator, adequate for tie-breaking ORDER BY)
    """
    # --- placeholder style ---
    sql = _rebind(sql)
    # --- INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING ---
    # Regex matches the keyword pair; we strip "OR IGNORE" then append the
    # Postgres equivalent after the closing VALUES (…) clause.  Since we
    # split on `;` before calling this, each call sees exactly one statement.
    if re.search(r"(?i)\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql):
        sql = re.sub(r"(?i)\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql)
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # --- rowid → ctid ---
    sql = re.sub(r"\browid\b", "ctid", sql, flags=re.IGNORECASE)
    return sql


class _SqliteConnection:
    """Adapter that wraps a sqlite3 connection with the store's expected API."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- context manager ---
    def __enter__(self) -> "_SqliteConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        # Don't close — callers use short-lived connections opened per call.
        self._conn.close()

    # --- delegate ---
    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def executescript(self, sql: str) -> None:
        self._conn.executescript(sql)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    @property
    def row_factory(self) -> Any:
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._conn.row_factory = value


class _PgRow(dict):
    """A dict subclass that also supports integer positional access AND
    value-iteration (matching sqlite3.Row semantics).

    sqlite3 cursors return ``sqlite3.Row`` objects that support both
    ``row["key"]`` and ``row[0]`` (positional), AND iterate over VALUES
    when destructured as ``for a, b, c in rows`` or ``a, b, c = row``.
    psycopg ``dict_row`` returns plain dicts whose default ``__iter__``
    yields KEYS — so a tuple-unpack like
    ``for cluster_id, theme, item_count in rows`` silently binds the
    locals to the literal column names ("cluster_id", "theme", …) and
    callers like ``int(item_count)`` raise ValueError.

    Overriding ``__iter__`` to yield values brings _PgRow in line with
    sqlite3.Row so the same store-level code paths work on both
    backends without per-call adjustments.
    """

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __iter__(self):  # type: ignore[override]
        return iter(self.values())


class _PgCursor:
    """Wraps a psycopg cursor to match sqlite3.Cursor semantics used by the store."""

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def fetchone(self) -> "_PgRow | None":
        row = self._cur.fetchone()
        return _PgRow(row) if row else None

    def fetchall(self) -> list["_PgRow"]:
        rows = self._cur.fetchall()
        return [_PgRow(r) for r in rows]


class _PostgresConnection:
    """Adapter that wraps a psycopg connection with the store's expected API.

    Translates `?` placeholders → `%s` and `INSERT OR IGNORE` →
    `INSERT … ON CONFLICT DO NOTHING` on every execute call so the
    store methods never need to know which backend is active.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # --- context manager ---
    def __enter__(self) -> "_PostgresConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()

    # --- query helpers ---
    def execute(self, sql: str, params: Any = ()) -> "_PgCursor":
        translated = _pg_translate(sql)
        cur = self._conn.cursor()
        cur.execute(translated, params if params else ())
        return _PgCursor(cur)

    def executescript(self, sql: str) -> None:
        """Execute a semicolon-separated multi-statement block on Postgres.

        psycopg3 does not have executescript; we split on `;` and run each
        non-empty statement individually.
        """
        cur = self._conn.cursor()
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                translated = _pg_translate(stmt)
                cur.execute(translated)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _usage_window_is_stale(window_iso: str) -> bool:
    """True if a usage-counter window-start predates the current calendar month UTC.

    Used by the tier_usage + business_plan_usage tables (#080) to apply
    lazy month-boundary reset. Each user has one persisted
    ``window_started_at`` per (tier, feature) row; once the
    user's first turn of a new calendar month runs, the existing
    counter is treated as 0 and the row is overwritten.

    Calendar month, not 30-day rolling: matches the pricing-page copy
    ("monthly limit, resets on the 1st"). UTC, not local — billing
    and usage windows align to UTC across all users.

    Malformed ISO strings count as stale (fail-safe — caller treats
    that as 0 used and overwrites the row on the next increment).
    """
    if not window_iso:
        return True
    try:
        # ``isoformat(timespec="seconds")`` produces "YYYY-MM-DDTHH:MM:SS+00:00"
        # which ``fromisoformat`` parses cleanly on Python 3.11+.
        window_dt = datetime.fromisoformat(window_iso)
    except (ValueError, TypeError):
        return True
    if window_dt.tzinfo is None:
        # Pre-tz-aware writes existed historically; assume UTC so the
        # comparison below has a defined timezone offset.
        window_dt = window_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    current_month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0,
    )
    return window_dt < current_month_start


# Maximum time a Next Steps artifact can sit in ``in_progress`` before the
# in-flight detection treats it as a dead-worker orphan (#089). Covers the
# gpt-5 p99 latency (~60s) plus a generous buffer for network + retries.
# Beyond five minutes, the Background Task is presumed crashed and a fresh
# generation is allowed to start. The orphan row stays for audit.
_NEXT_STEPS_IN_PROGRESS_STALE_SECONDS: int = 300


def _next_steps_in_progress_is_stale(generated_at_iso: str) -> bool:
    """True if an in-progress artifact row is too old to trust.

    Used by ``get_in_flight_document`` to ignore orphan rows left by
    FastAPI BackgroundTask crashes. Malformed timestamps are treated as
    stale (fail-safe — caller falls through to start a fresh
    generation). Name retained from #089/F2 for now; rename to
    ``_in_progress_is_stale`` deferred per #095.
    """
    if not generated_at_iso:
        return True
    try:
        generated_dt = datetime.fromisoformat(generated_at_iso)
    except (ValueError, TypeError):
        return True
    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - generated_dt
    return age.total_seconds() >= _NEXT_STEPS_IN_PROGRESS_STALE_SECONDS


# #094: Allowlist of doc-type slugs accepted by the API + store. The 7 v1
# types each ship as a distinct one-shot async generator — see
# `agents/prompts.py` for the per-type prompt + `agents/schemas.py` for
# the response schema.
VALID_DOC_TYPES: frozenset[str] = frozenset({
    "business_plan",
    "prd",
    "story_outline",
    "event_plan",
    "marketing_plan",
    "research_proposal",
    "course_outline",
})

# #094: Map from project domain (LLM-inferred at kickoff, stored in
# `metadata_json.domain`) to doc_type. Two domains (`career`, `personal`)
# are intentionally unmapped in v1; UI for those falls back to an
# upgrade-CTA-style "no doc type for this project" panel until the
# follow-up issue lands.
DOMAIN_TO_DOC_TYPE: dict[str, str] = {
    "business_plan": "business_plan",
    "software_product": "business_plan",
    "software_feature": "prd",
    "novel": "story_outline",
    "screenplay": "story_outline",
    "event": "event_plan",
    "campaign": "marketing_plan",
    "research": "research_proposal",
    "course": "course_outline",
}


def _row_to_document_dict(row: Any) -> dict[str, Any]:
    """Normalize a ``documents`` row to a plain dict (#094).

    Centralised so all read sites (``get_document``,
    ``get_latest_completed_document``, ``get_in_flight_document``) emit
    the same shape for the API layer. Timestamp columns surface as ISO
    strings; ``content_json`` is left as-is — the API parses it once
    before returning to the FE.
    """
    return {
        "document_id": str(row["document_id"]),
        "project_id": str(row["project_id"]),
        "user_id": str(row["user_id"]),
        "doc_type": str(row["doc_type"]),
        "status": str(row["status"]),
        "content_json": (
            str(row["content_json"]) if row["content_json"] is not None else None
        ),
        "error_message": (
            str(row["error_message"]) if row["error_message"] is not None else None
        ),
        "model_id": str(row["model_id"]),
        "plan_tier": str(row["plan_tier"]),
        "output_tokens_estimate": (
            int(row["output_tokens_estimate"])
            if row["output_tokens_estimate"] is not None
            else None
        ),
        "generated_at": str(row["generated_at"]),
        "completed_at": (
            str(row["completed_at"]) if row["completed_at"] is not None else None
        ),
    }



# Whitelist of topic status values. Mirrors the ``topics.status`` column
# comment in the schema (see CREATE TABLE topics above — "empty |
# in_progress | fleshed_out"). Any other value coming in through the
# HTTP edge must be rejected with a 400 before it reaches the store,
# so the frontend can rely on these three tokens being the only ones
# it ever has to render.
VALID_TOPIC_STATUSES: frozenset[str] = frozenset(
    {"empty", "in_progress", "fleshed_out"},
)


# Allowlist of known system-owned seed project IDs that a non-system caller
# may legitimately claim. Intentionally empty — the pre-auth "anyone can
# claim a system project" escape hatch is closed (audit M6). Once multi-user
# is live this list stays empty; any re-seeding workflow should go through
# a dedicated admin route rather than loosening this gate.
_SYSTEM_SEED_PROJECT_ALLOWLIST: frozenset[str] = frozenset()


# BYOK (Bring Your Own Key) column map. Keys are the provider slugs the
# ``byok`` module accepts; values are the literal ``users`` column names
# the store writes to. Module-level (not a class attribute) because the
# ``@dataclass(slots=True)`` decorator on ``PlanningStudioStore`` rejects
# mutable default values.
_BYOK_COLUMNS: dict[str, str] = {
    "openai": "openai_api_key_encrypted",
    "anthropic": "anthropic_api_key_encrypted",
}


def _today_utc_day() -> str:
    """UTC day stamp in YYYY-MM-DD format. Used by user_usage rollover."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _derive_activity_subject_title(
    *,
    category: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> str:
    """Best-effort human label for an audit_log event.

    Different categories put the "name of the thing" in different JSON
    keys, and some actions (delete) only have ``before``. We try a short
    list of common keys per category in priority order; anything missing
    just yields an empty string and the frontend falls back to a generic
    label.

    This is deliberately conservative — we only pull out a short string
    and refuse to substitute long prose (``description``, ``body``) that
    would wreck the timeline's one-line layout.
    """
    _ = action  # reserved for future action-specific shaping
    sources = [src for src in (after, before) if isinstance(src, dict)]

    # Per-category priority lists. Topic/project use ``title``; decisions
    # use ``statement``; relationships use ``label``; share links have no
    # real title so we fall back to an empty string.
    preferred_keys_by_category = {
        "topic": ("title", "name"),
        "project": ("title", "name"),
        "decision": ("statement", "title"),
        "relationship": ("label",),
        "share": ("url_path",),
        "export": ("format", "filename"),
    }
    for candidate in sources:
        for key in preferred_keys_by_category.get(category, ("title", "name")):
            value = candidate.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    # Clamp absurdly long strings — the timeline shows a
                    # single line per event, not a full essay.
                    return value if len(value) <= 120 else (value[:117] + "...")
    return ""


@dataclass(slots=True)
class PlanningStudioStore:
    config: ServiceConfig
    # Populated by __post_init__; True when DATABASE_URL points at Postgres.
    _is_postgres: bool = field(default=False, init=False, repr=False)
    # Names of any startup column-retrofit migrations that raised. Empty in
    # the happy path; surfaced via `get_failed_migrations()` and the
    # `/api/health` endpoint so on-call gets a loud signal when bootstrap
    # DDL silently degrades the app.
    _failed_migrations: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        db_url = self.config.database_url
        self._is_postgres = (
            db_url.startswith("postgresql://") or db_url.startswith("postgresql+psycopg://")
        )
        if not self._is_postgres:
            # SQLite path: ensure local directories exist.
            self.config.storage_root.mkdir(parents=True, exist_ok=True)
            self.config.sessions_root.mkdir(parents=True, exist_ok=True)
            self.config.artifacts_root.mkdir(parents=True, exist_ok=True)
        else:
            _log.info("[store] Postgres backend detected — skipping inline DDL (alembic owns schema)")
        self._initialize()

    def _connect(self) -> "_SqliteConnection | _PostgresConnection":
        """Return a dialect-appropriate connection wrapper."""
        if self._is_postgres:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(self.config.database_url, row_factory=dict_row)
            # psycopg3 defaults to autocommit=False which is what we want;
            # the `with conn:` protocol commits or rolls back automatically.
            return _PostgresConnection(conn)
        else:
            conn = sqlite3.connect(self.config.db_path)
            conn.row_factory = sqlite3.Row
            return _SqliteConnection(conn)

    def _initialize(self) -> None:
        if self._is_postgres:
            # Alembic is the sole schema owner on Postgres for CREATE TABLE.
            # Running CREATE TABLE IF NOT EXISTS here would be redundant at
            # best and can fail on dialect mismatches (AUTOINCREMENT vs SERIAL,
            # TEXT vs JSONB, etc.).  Skip table creation.
            _log.info("[store] Postgres backend — skipping inline DDL; alembic owns schema")
            # BUT: the ADD COLUMN IF NOT EXISTS retrofits are safe on Postgres
            # (idempotent, zero-risk) and guard us against a "code deployed
            # before migrations were applied" gap. Every one of these
            # retrofits mirrors an Alembic migration; running them as a
            # belt-and-braces bootstrap means the app doesn't 500 every
            # request if alembic upgrade head hasn't been run yet. They
            # no-op when the column already exists.
            # Each retrofit runs inside its own try/except so a single
            # failure (e.g. permissions on one table, a constraint clash
            # on one column) does NOT prevent the others from running.
            # Failures are recorded in `self._failed_migrations` and
            # surfaced via `/api/health` so an on-call responder gets a
            # loud signal instead of a silent degraded state.
            retrofits: list[tuple[str, Any]] = [
                ("ensure_user_id_columns", self._ensure_user_id_columns),
                ("ensure_user_model_tier_column", self._ensure_user_model_tier_column),
                ("ensure_user_byok_columns", self._ensure_user_byok_columns),
                ("ensure_user_verification_columns", self._ensure_user_verification_columns),
                ("ensure_subscription_trial_columns", self._ensure_subscription_trial_columns),
                ("ensure_topics_private_notes_column", self._ensure_topics_private_notes_column),
                ("ensure_v2_projects_shelf_column", self._ensure_v2_projects_shelf_column),
                ("ensure_v2_projects_archived_column", self._ensure_v2_projects_archived_column),
                ("ensure_processed_webhook_events_table", self._ensure_processed_webhook_events_table),
                ("ensure_tier_usage_tables", self._ensure_tier_usage_tables),
                ("ensure_documents_table", self._ensure_documents_table),
            ]
            for name, fn in retrofits:
                try:
                    fn()
                except Exception as exc:  # pragma: no cover — startup safety net
                    self._failed_migrations.add(name)
                    _log.error(
                        "[store] migration %s failed: %s — run `ALTER TABLE ... ADD COLUMN ...` "
                        "manually against the Postgres DB or redeploy after fixing root cause",
                        name,
                        exc,
                        exc_info=True,
                    )
            if self._failed_migrations:
                _log.error(
                    "[store] Postgres column retrofits DEGRADED — failed: %s. "
                    "Health endpoint will report status=degraded until resolved.",
                    sorted(self._failed_migrations),
                )
            else:
                _log.info("[store] Postgres belt-and-braces column retrofits OK")
            # Seeding demo data is safe even on Postgres (idempotent via
            # ON CONFLICT DO NOTHING), but still skip in production.
            import os as _os
            _env = _os.environ.get("ENVIRONMENT", "development").lower()
            _seed_flag = _os.environ.get("INSPIRA_SEED_V1_DEMO", "").strip().lower()
            should_seed = (
                _seed_flag == "true"
                or (_env != "production" and _seed_flag != "false")
            )
            if should_seed:
                self._seed_defaults()
            return
        with self._connect() as connection:
            connection.executescript(
                """
                -- ============================================================
                -- v1 tables (DEPRECATED — kept for backward compatibility with
                -- existing tests. Do not read or write from new code paths.
                -- See docs/architecture/data-model.md.)
                -- ============================================================
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

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
                );

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
                );
                """
            )
            connection.commit()
        self._initialize_v2_schema()
        # Seed only outside production — the bootstrap demo project is
        # handy in dev/tests but shouldn't ship to public
        # users on a real deploy (audit L5). Gate: if ENVIRONMENT=production
        # we skip unless INSPIRA_SEED_V1_DEMO=true is explicitly set.
        import os as _os
        _env = _os.environ.get("ENVIRONMENT", "development").lower()
        _seed_flag = _os.environ.get("INSPIRA_SEED_V1_DEMO", "").strip().lower()
        should_seed = (
            _seed_flag == "true"
            or (_env != "production" and _seed_flag != "false")
        )
        if should_seed:
            self._seed_defaults()

    def _initialize_v2_schema(self) -> None:
        """v2 schema — canvas-first Inspira model.

        Additive alongside the deprecated v1 tables. Every CREATE is IF NOT
        EXISTS, so calling this on an existing DB is safe. Schema source of
        truth: docs/architecture/data-model.md.
        """
        # Users + auth session support. The users table owns a minimum-
        # viable identity record; Google OAuth profile data gets layered on
        # later without a schema change (JSON `metadata_json`). Password
        # hash is nullable so OAuth-only accounts skip it.
        self._initialize_users_schema()
        self._initialize_v2_projects_schema()
        self._ensure_user_id_columns()
        with self._connect() as connection:
            connection.executescript(
                """
                -- Canvas topics. Freeform; every card uses the same schema.
                CREATE TABLE IF NOT EXISTS topics (
                    topic_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    icon TEXT NOT NULL,
                    position_x REAL NOT NULL,
                    position_y REAL NOT NULL,
                    status TEXT NOT NULL,            -- empty | in_progress | fleshed_out
                    order_index INTEGER NOT NULL,
                    origin TEXT NOT NULL,            -- planner_initial | planner_proposed | user_manual
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    -- Short per-topic note authored by the user. VISIBLE ONLY TO
                    -- THE USER — never included in the LLM prompt. Nullable;
                    -- treated as "no note" when NULL or empty string.
                    private_notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_topics_project ON topics(project_id, deleted_at);
                CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(project_id, status);

                -- Dotted relationships between topics.
                CREATE TABLE IF NOT EXISTS relationships (
                    relationship_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source_topic_id TEXT NOT NULL,
                    target_topic_id TEXT NOT NULL,
                    label TEXT,
                    origin TEXT NOT NULL,            -- planner_inferred | user_drawn
                    strength TEXT,                   -- implied | confirmed
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE(project_id, source_topic_id, target_topic_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id),
                    FOREIGN KEY(source_topic_id) REFERENCES topics(topic_id),
                    FOREIGN KEY(target_topic_id) REFERENCES topics(topic_id)
                );
                CREATE INDEX IF NOT EXISTS idx_relationships_project ON relationships(project_id);

                -- Q&A turns. Append-only.
                CREATE TABLE IF NOT EXISTS qna_turns (
                    turn_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,        -- denormalized for query speed
                    role TEXT NOT NULL,              -- planner | user
                    order_index INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    why_this_matters TEXT,
                    action TEXT,                     -- planner only: ask | pressure_test | followup | suggest_close
                    suggested_responses_json TEXT,
                    status TEXT NOT NULL,            -- open | answered | deferred | na
                    parent_turn_id TEXT,
                    attachments_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(topic_id) REFERENCES topics(topic_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qna_topic_order ON qna_turns(topic_id, order_index);
                CREATE INDEX IF NOT EXISTS idx_qna_project_role ON qna_turns(project_id, role);

                -- Decisions attached to topics. Mutable; audit trail via audit_log.
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    rationale TEXT,
                    status TEXT NOT NULL,            -- proposed | confirmed | retracted
                    source_turn_id TEXT,
                    proposed_by TEXT NOT NULL,       -- planner | user
                    confirmed_by_user_id TEXT,
                    current_version_int INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retracted_at TEXT,
                    FOREIGN KEY(topic_id) REFERENCES topics(topic_id),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id),
                    FOREIGN KEY(source_turn_id) REFERENCES qna_turns(turn_id)
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_topic ON decisions(topic_id, status);
                CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);

                -- Append-only decision versions written by the comment-cascade
                -- regenerate flow. v1 is lazy: cascade.py snapshots the current
                -- decisions row state on first cascade, then appends v2.
                CREATE TABLE IF NOT EXISTS decision_versions (
                    version_id                 TEXT PRIMARY KEY,
                    decision_id                TEXT NOT NULL,
                    version_int                INTEGER NOT NULL,
                    statement                  TEXT NOT NULL,
                    rationale                  TEXT,
                    subject                    TEXT,
                    version_hash               TEXT NOT NULL,
                    prior_version_id           TEXT,
                    change_note                TEXT,
                    cascade_id                 TEXT,
                    cascaded_from_decision_ids TEXT,
                    created_at                 TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_versions_unique
                    ON decision_versions(decision_id, version_int);
                CREATE INDEX IF NOT EXISTS idx_decision_versions_latest
                    ON decision_versions(decision_id, version_int DESC);
                CREATE INDEX IF NOT EXISTS idx_decision_versions_cascade
                    ON decision_versions(cascade_id);

                -- Per-cascade run record. Coordinates the BackgroundTask
                -- lifecycle (FE polls GET /regenerate-cascade/{cascade_id}).
                CREATE TABLE IF NOT EXISTS cascade_runs (
                    cascade_id          TEXT PRIMARY KEY,
                    workspace_id        TEXT NOT NULL,
                    project_id          TEXT NOT NULL,
                    triggered_by        TEXT NOT NULL,
                    scope_mode          TEXT NOT NULL,    -- local | cascade
                    status              TEXT NOT NULL,    -- pending | running | complete | failed
                    commented_decisions TEXT NOT NULL,    -- JSON [{decision_id, comment_text}]
                    affected_scope      TEXT,             -- JSON {decision_ids, topic_ids, count, banner_state}
                    diff_summary        TEXT,             -- JSON {updated_count, created_count, failed_count}
                    error               TEXT,
                    started_at          TEXT NOT NULL,
                    completed_at        TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_cascade_runs_project_recent
                    ON cascade_runs(project_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_cascade_runs_workspace
                    ON cascade_runs(workspace_id, started_at DESC);

                -- Wave F.4: inline IDE-style comments on artifact scaffold
                -- code. Anchored to (file_path, line_number,
                -- line_content_hash). Single-level threading via
                -- parent_comment_id. Production schema lives in
                -- alembic/versions/20260513_0001_artifact_comments.py;
                -- duplicated here so the SQLite test bootstrap (which
                -- doesn't run alembic) has the table available.
                CREATE TABLE IF NOT EXISTS v2_artifact_comments (
                    comment_id         TEXT PRIMARY KEY,
                    project_id         TEXT NOT NULL,
                    file_path          TEXT NOT NULL,
                    line_number        INTEGER NOT NULL,
                    line_content_hash  TEXT NOT NULL,
                    category           TEXT NOT NULL
                                       CHECK (category IN
                                         ('question','concern','suggest_fix')),
                    body               TEXT NOT NULL,
                    author_user_id     TEXT NOT NULL,
                    parent_comment_id  TEXT,
                    resolved_at        TEXT,
                    created_at         TEXT NOT NULL,
                    updated_at         TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v2_artifact_comments_project
                    ON v2_artifact_comments(project_id);
                CREATE INDEX IF NOT EXISTS idx_v2_artifact_comments_file
                    ON v2_artifact_comments(project_id, file_path);

                -- Open questions per topic.
                CREATE TABLE IF NOT EXISTS open_questions (
                    question_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,            -- open | answered | deferred | na
                    answer_turn_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(topic_id) REFERENCES topics(topic_id)
                );

                -- Risks and assumptions per topic.
                CREATE TABLE IF NOT EXISTS risks_assumptions (
                    risk_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,              -- risk | assumption
                    text TEXT NOT NULL,
                    severity TEXT,                   -- low | medium | high | critical (risks only)
                    status TEXT NOT NULL,            -- open | resolved | invalidated
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(topic_id) REFERENCES topics(topic_id)
                );

                -- Cross-topic consistency flags. Append-only creation; status mutates.
                CREATE TABLE IF NOT EXISTS consistency_flags (
                    flag_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    topic_a_id TEXT NOT NULL,
                    decision_a_id TEXT,
                    topic_b_id TEXT NOT NULL,
                    decision_b_id TEXT,
                    description TEXT NOT NULL,
                    scope TEXT NOT NULL,             -- within_project | cross_project
                    status TEXT NOT NULL,            -- open | resolved | intentional | dismissed
                    resolved_turn_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_flags_project_status ON consistency_flags(project_id, status);
                CREATE INDEX IF NOT EXISTS idx_flags_topic_a ON consistency_flags(topic_a_id);
                CREATE INDEX IF NOT EXISTS idx_flags_topic_b ON consistency_flags(topic_b_id);

                -- Unified context sources (uploads, URLs, repos).
                CREATE TABLE IF NOT EXISTS context_sources (
                    source_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,              -- upload | url | github_repo | gitlab_repo
                    display_name TEXT NOT NULL,
                    uri TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,            -- active | stale | unreachable
                    added_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sources_project ON context_sources(project_id, status);

                -- Source references: which turns/decisions cite which sources.
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
                );

                -- Plan Summary versions. Append-only.
                CREATE TABLE IF NOT EXISTS summary_versions (
                    version_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    version_hash TEXT NOT NULL,
                    content_markdown TEXT NOT NULL,
                    sections_json TEXT NOT NULL,
                    open_questions_json TEXT,
                    approval_state TEXT NOT NULL,    -- draft | under_review | approved
                    generated_by TEXT NOT NULL,      -- planner_auto | user_edit
                    generated_by_user_id TEXT,
                    version_note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_summary_versions_project
                    ON summary_versions(project_id, created_at DESC);

                -- Approval actions. Append-only. Team-plan-with-approvals-enabled only.
                CREATE TABLE IF NOT EXISTS approval_actions (
                    action_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    summary_version_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,           -- approve | deny | request | cancel
                    comment TEXT,
                    state_before TEXT,
                    state_after TEXT,
                    ip_address TEXT,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(summary_version_id) REFERENCES summary_versions(version_id)
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_project
                    ON approval_actions(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_approvals_version
                    ON approval_actions(summary_version_id);

                -- Comprehensive audit log. Every state change writes here.
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
                );
                CREATE INDEX IF NOT EXISTS idx_audit_workspace
                    ON audit_log(workspace_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_project
                    ON audit_log(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_actor
                    ON audit_log(actor_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_category
                    ON audit_log(category, action);

                -- Per-user daily token accounting. Protects against a single
                -- user burning the entire OpenAI budget (audit M5). Rolled
                -- per UTC day, reset implicitly by using today's date as part
                -- of the primary key. request_count is purely observability.
                CREATE TABLE IF NOT EXISTS user_usage (
                    user_id TEXT NOT NULL,
                    day_utc TEXT NOT NULL,           -- YYYY-MM-DD in UTC
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, day_utc)
                );
                CREATE INDEX IF NOT EXISTS idx_user_usage_day ON user_usage(day_utc);

                -- Per-user cache for AI-generated project suggestions. TTL
                -- enforced in app code (suggestions module) against
                -- generated_at. One row per user; new rows REPLACE.
                CREATE TABLE IF NOT EXISTS suggestions_cache (
                    user_id TEXT PRIMARY KEY,
                    suggestions_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                );

                -- Read-only share links. Each project may have at most one
                -- ACTIVE (non-revoked) link at a time — generating a new one
                -- revokes the prior one. Tokens are stored directly (not
                -- hashed) because they are capability tokens; rotating the
                -- session secret doesn't affect them, and the revoke path
                -- is how we invalidate a leaked link.
                CREATE TABLE IF NOT EXISTS shared_links (
                    token TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_viewed_at TEXT,
                    view_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_shared_links_project
                    ON shared_links(project_id, revoked_at);

                -- Stripe-backed subscription state per user. One row per
                -- user; users without a row are treated as Free tier by the
                -- billing provider. ``plan`` matches a slug in
                -- billing/plans.py (free|pro|team). Stripe IDs are
                -- nullable so the Noop provider can record local
                -- "subscribed" rows without ever hitting Stripe.
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stripe_customer_id TEXT,
                    stripe_subscription_id TEXT,
                    current_period_end TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_subscriptions_customer
                    ON subscriptions(stripe_customer_id);

                -- Stripe webhook idempotency ledger. Stripe retries every
                -- delivery on non-2xx and on missed acks, so the same
                -- ``event.id`` can land here more than once. We record each
                -- event id we have already applied so a duplicate delivery
                -- is a fast no-op rather than re-running the
                -- ``_apply_stripe_event`` side effects (double-charging,
                -- double-flipping a subscription, etc.). Rows are written
                -- AFTER a successful apply -- a failed apply leaves the
                -- table untouched so Stripe's retry will re-attempt the
                -- event next time.
                CREATE TABLE IF NOT EXISTS processed_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT,
                    processed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_processed_webhook_events_processed_at
                    ON processed_webhook_events(processed_at);

                -- User credit balances. One row per user, populated on first
                -- read via credits.ensure_initial_grant(). The ledger of
                -- credit movements lives in credit_transactions — this
                -- table is just the running-sum snapshot kept hot for the
                -- UI meter. Source of truth for disputes is the ledger.
                CREATE TABLE IF NOT EXISTS user_credits (
                    user_id TEXT PRIMARY KEY,
                    balance_credits INTEGER NOT NULL DEFAULT 0 CHECK (balance_credits >= 0),
                    updated_at TEXT NOT NULL
                );

                -- Append-only credit ledger. Every debit and credit lands
                -- here. `delta` is signed: negative = charge, positive =
                -- refund / grant / purchase. `reason` is a short
                -- underscore-cased slug we can filter on for reporting.
                -- `reference_id` is optional — typically the scaffold_id
                -- a charge was for, so a transaction can point back at
                -- the thing it paid for.
                CREATE TABLE IF NOT EXISTS credit_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    reference_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_credit_txns_user
                    ON credit_transactions(user_id, created_at);

                -- Persisted code scaffolds (paid-tier feature). One row
                -- per successful generation; failed generations DO NOT
                -- land here (they're logged in credit_transactions with
                -- reason='scaffold_failed' instead). manifest_json holds
                -- the full file list so zip downloads are repeatable
                -- without re-hitting the LLM.
                CREATE TABLE IF NOT EXISTS scaffolds (
                    scaffold_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    framework TEXT NOT NULL,
                    language TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_scaffolds_project
                    ON scaffolds(project_id, deleted_at);
                CREATE INDEX IF NOT EXISTS idx_scaffolds_user
                    ON scaffolds(user_id, deleted_at);

                -- Wave F.6: "Refresh PR with Inspira" history. Each
                -- POST /refresh-overlay inserts a row in status
                -- 'in_progress' BEFORE the LLM call so a concurrent
                -- second POST surfaces 409 deterministically. The
                -- 3-way diff endpoint resolves back to the precise
                -- pre/post scaffold pair via (previous_scaffold_id,
                -- new_scaffold_id). Mirrors alembic 20260606_0001.
                CREATE TABLE IF NOT EXISTS v2_scaffold_refresh_history (
                    refresh_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    previous_scaffold_id TEXT,
                    new_scaffold_id TEXT,
                    base_main_sha_before TEXT NOT NULL,
                    base_main_sha_after TEXT,
                    preserve_partner_edits INTEGER NOT NULL DEFAULT 1,
                    changed_paths TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'in_progress',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS
                    idx_v2_scaffold_refresh_history_project
                    ON v2_scaffold_refresh_history(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS
                    idx_v2_scaffold_refresh_history_status
                    ON v2_scaffold_refresh_history(project_id, status);

                -- Schema version tracker for future migrations.
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );
                INSERT INTO schema_version (version, applied_at, description)
                VALUES (2, CURRENT_TIMESTAMP, 'v2 canvas-first schema (additive alongside deprecated v1 tables)')
                ON CONFLICT(version) DO NOTHING;
                """
            )
            connection.commit()
        # Retrofit the ``private_notes`` column onto ``topics`` for DBs
        # that predate migration 20260422_0006. The column appears in the
        # CREATE TABLE DDL above, but CREATE TABLE IF NOT EXISTS is a
        # no-op when the table is already present — so pre-existing dev
        # DBs won't pick up the new column without this explicit ALTER.
        self._ensure_topics_private_notes_column()
        # Retrofit email-verification, trial-ending-email guard, and
        # known-device-fingerprint columns on ``users`` (email wave 2026-04).
        self._ensure_user_verification_columns()
        # Retrofit ``users.terms_accepted_at`` for pre-gate DBs. Without
        # this, a dev who boots against an older SQLite file would see a
        # NoSuchColumnError on the first signup.
        self._ensure_user_terms_accepted_column()
        # Retrofit ``subscriptions.{started_at,trial_ends_at}`` for the
        # Switch-to-annual trigger + trial-ending sweeper (email wave
        # 2026-04). Also backfills ``started_at`` from ``created_at`` on
        # pre-existing rows so the 30-day gate runs against real data.
        self._ensure_subscription_trial_columns()
        # Retrofit ``processed_webhook_events`` (Stripe idempotency ledger).
        # Same belt-and-braces pattern: the CREATE TABLE in the inline
        # script above already covers fresh DBs; this call covers SQLite
        # DBs that predate the table being added.
        self._ensure_processed_webhook_events_table()

    def _seed_defaults(self) -> None:
        created_at = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (project_id, title, summary, stage, owner, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO NOTHING
                """,
                (
                    "project-demo-bootstrap",
                    "Demo: planning workspace bootstrap",
                    "A seeded example project: durable sessions and reviewable planning artifacts.",
                    "discovery",
                    "project_manager",
                    json.dumps({"product_surface": "planning-studio", "demo_seed": True}),
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO sessions (session_id, project_id, title, objective, status, transcript_path, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    "session-bootstrap",
                    "project-demo-bootstrap",
                    "Bootstrap planning session",
                    "Stand up save and resume for planning interviews.",
                    "active",
                    None,
                    json.dumps({"mode": "interview", "created_via": "bootstrap"}),
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifacts (artifact_id, project_id, session_id, artifact_type, title, status, artifact_path, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO NOTHING
                """,
                (
                    "artifact-prd-outline",
                    "project-demo-bootstrap",
                    "session-bootstrap",
                    "prd_outline",
                    "Bootstrap PRD outline",
                    "draft",
                    None,
                    json.dumps({"template": "prd-generator"}),
                    created_at,
                    created_at,
                ),
            )
            connection.commit()

    # ---------- Users / auth --------------------------------------------

    def _initialize_users_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    display_name TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    -- Persisted default LLM-tier pick. NULL = "use the plan
                    -- default" (see agents.tiers.resolve_tier_for_user). We
                    -- keep the column as TEXT rather than an ENUM so evolving
                    -- the tier set doesn't require a migration.
                    preferred_model_tier TEXT,
                    -- BYOK (Bring Your Own Key) ciphertext columns. Fernet
                    -- base64 output for the user-pasted OpenAI / Anthropic
                    -- keys. NULL = "not configured". See byok.py for the
                    -- encryption contract. Never read these columns without
                    -- going through ``byok.store`` so decryption errors
                    -- surface as clean RuntimeError instead of binary noise.
                    openai_api_key_encrypted TEXT,
                    anthropic_api_key_encrypted TEXT,
                    -- Terms-of-service acceptance timestamp. NULL for the
                    -- legacy system user and pre-terms-gate accounts;
                    -- populated at signup for every new account going
                    -- forward. ISO-8601 UTC string, matching the rest of
                    -- the timestamps on this table.
                    terms_accepted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

                -- Password reset tokens. We only ever store the SHA-256 hash
                -- of the raw token -- a DB dump therefore cannot mint resets,
                -- and the raw value only ever exists in-memory + inside the
                -- email the legitimate user receives. Tokens are 32 random
                -- bytes (256 bits); the hash is 64 hex characters.
                --
                -- requested_at / expires_at are ISO-8601 UTC strings matching
                -- the project convention (see ``now_timestamp``). A token is
                -- consumable iff used_at is NULL AND expires_at > now.
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_prt_user ON password_reset_tokens(user_id, used_at);

                -- Personal Access Tokens (PATs). External automations
                -- authenticate with ``Authorization: Bearer inspira_pat_<raw>``;
                -- we store only sha256(raw) so a DB dump cannot mint new
                -- tokens. token_id (``tok_<hex>``) is the opaque handle
                -- list/revoke endpoints use. scopes_json is an empty JSON
                -- array on v1 which resolves to full read+write — the
                -- column is already here so scope-narrowing lands without
                -- another migration.
                CREATE TABLE IF NOT EXISTS user_access_tokens (
                    token_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_uat_user ON user_access_tokens(user_id, revoked_at);
                CREATE INDEX IF NOT EXISTS idx_uat_hash ON user_access_tokens(token_hash);

                -- #080: monthly tier-usage counters for the topic_turn cap.
                -- One row per (user_id, tier). Lazy month-boundary reset
                -- handled in the application layer via _usage_window_is_stale.
                CREATE TABLE IF NOT EXISTS tier_usage (
                    user_id TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    output_tokens_used INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, tier)
                );

                -- #080 / #081: monthly business-plan generation counter.
                -- Pro = 1/mo trial, Frontier = 100/mo soft cap.
                CREATE TABLE IF NOT EXISTS business_plan_usage (
                    user_id TEXT NOT NULL PRIMARY KEY,
                    plans_used_this_month INTEGER NOT NULL DEFAULT 0,
                    window_started_at TEXT NOT NULL
                );

                -- #094: Documents — domain-aware doc-type generator. One row
                -- per generation attempt for any of the 7 v1 doc types
                -- (business_plan, prd, story_outline, event_plan,
                -- marketing_plan, research_proposal, course_outline).
                -- One-shot async: status flows in_progress → completed |
                -- failed; content_json is null until completed and carries
                -- the full structured doc once written.
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    doc_type TEXT NOT NULL,         -- VALID_DOC_TYPES allowlist
                    status TEXT NOT NULL,           -- in_progress | completed | failed
                    content_json TEXT,              -- nullable until completed
                    error_message TEXT,             -- nullable; populated on failed
                    model_id TEXT NOT NULL,         -- always 'gpt-5.5' for #094
                    plan_tier TEXT NOT NULL,        -- 'pro' | 'frontier'
                    output_tokens_estimate INTEGER, -- nullable until completed
                    generated_at TEXT NOT NULL,
                    completed_at TEXT               -- nullable until completed/failed
                );
                CREATE INDEX IF NOT EXISTS idx_documents_project_doctype
                    ON documents(project_id, doc_type, generated_at);
                CREATE INDEX IF NOT EXISTS idx_documents_user
                    ON documents(user_id);

                -- v4 B2B pivot: workspaces are the unit of isolation,
                -- billing, and member management. Today the codebase
                -- carries a single-tenant placeholder ("ws-default" at
                -- append_user_audit_event below). The five tables
                -- below are the W1 foundation; the audit-shim swap
                -- happens in W4. Mirrors alembic 20260504_0001..0005.
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
                );
                CREATE INDEX IF NOT EXISTS idx_workspaces_billing_owner
                    ON workspaces(billing_owner_user_id);
                CREATE INDEX IF NOT EXISTS idx_workspaces_active_plan
                    ON workspaces(plan_tier);

                -- workspace_members: composite PK; role is one of
                -- owner|admin|member|viewer (four-role set per the
                -- May-2026 founder lock; per-resource verbs live in
                -- audit_log.action).
                CREATE TABLE IF NOT EXISTS workspace_members (
                    workspace_id TEXT NOT NULL,
                    user_id      TEXT NOT NULL,
                    role         TEXT NOT NULL
                                 CHECK (role IN ('owner','admin','member','viewer')),
                    created_at   TEXT NOT NULL,
                    invited_by   TEXT,
                    PRIMARY KEY (workspace_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ws_members_user
                    ON workspace_members(user_id);

                -- connector_credentials: per-(workspace, provider)
                -- encrypted token store. encrypted_token is Fernet
                -- ciphertext via byok.encrypt_api_key. For GitHub the
                -- column carries the user OAuth token used to verify
                -- install ownership; the actual installation token
                -- is 1-hour ephemeral and re-minted at sync time.
                CREATE TABLE IF NOT EXISTS connector_credentials (
                    workspace_id       TEXT NOT NULL,
                    provider           TEXT NOT NULL,
                    encrypted_token    TEXT NOT NULL,
                    installation_id    TEXT,
                    account_login      TEXT,
                    account_avatar_url TEXT,
                    scopes_json        TEXT NOT NULL DEFAULT '[]',
                    created_at         TEXT NOT NULL,
                    last_refreshed_at  TEXT,
                    status             TEXT NOT NULL DEFAULT 'connected'
                                       CHECK (status IN ('connected','needs_reauth','revoked')),
                    metadata_json      TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (workspace_id, provider)
                );
                CREATE INDEX IF NOT EXISTS idx_conn_creds_attention
                    ON connector_credentials(status);

                -- repo_snapshots: denormalized read-model for the W3
                -- planner agent. snapshot_json carries the whole
                -- {tree_top, open_issues, recent_commits} blob; the
                -- polling job replaces the row at each successful
                -- sync. Diffing happens at the planner step, not here.
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
                );
                CREATE INDEX IF NOT EXISTS idx_repo_snap_ws_recent
                    ON repo_snapshots(workspace_id, last_sync_at);

                -- connector_sync_runs: observability log for the
                -- 60-min polling job. parent_run_id is W1-cheap
                -- forward setup for W3 (orchestrator chains
                -- prioritization runs to triggering sync runs).
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
                );
                CREATE INDEX IF NOT EXISTS idx_sync_runs_ws_recent
                    ON connector_sync_runs(workspace_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_sync_runs_unfinished
                    ON connector_sync_runs(status);

                -- feedback_items: every connector that ingests
                -- partner-supplied feedback (Linear, CSV, Intercom
                -- when it lands, etc.) writes rows here. F5
                -- extends with classification_json + cluster_id
                -- but those are nullable so F4 can ship first.
                -- Idempotency via UNIQUE (workspace_id, content_hash);
                -- content_hash is a sha256 of the canonical content
                -- string for the source.
                CREATE TABLE IF NOT EXISTS feedback_items (
                    item_id          TEXT PRIMARY KEY,
                    workspace_id     TEXT NOT NULL,
                    source           TEXT NOT NULL,
                    external_id      TEXT,
                    content_hash     TEXT NOT NULL,
                    title            TEXT NOT NULL,
                    body             TEXT NOT NULL DEFAULT '',
                    author           TEXT,
                    author_email     TEXT,
                    received_at      TEXT,
                    ingested_at      TEXT NOT NULL,
                    type_hint        TEXT,
                    raw_payload_json TEXT,
                    status           TEXT NOT NULL DEFAULT 'queued'
                                     CHECK (status IN
                                       ('queued','classified','discarded','promoted')),
                    cluster_id       TEXT,
                    embedding_json   TEXT,
                    UNIQUE (workspace_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_items_workspace
                    ON feedback_items(workspace_id, ingested_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_items_status
                    ON feedback_items(status);

                -- feedback_clusters (W2 F5+ embeddings): partner
                -- feedback grouped by similarity. Each cluster
                -- carries a centroid (running average embedding)
                -- so new items can be assigned by cosine distance.
                CREATE TABLE IF NOT EXISTS feedback_clusters (
                    cluster_id     TEXT PRIMARY KEY,
                    workspace_id   TEXT NOT NULL,
                    centroid_json  TEXT NOT NULL,
                    theme          TEXT,
                    item_count     INTEGER NOT NULL DEFAULT 1,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_clusters_workspace
                    ON feedback_clusters(workspace_id, updated_at);

                -- W3 orchestrator tables (F6 + F7-REVISED). See
                -- alembic 20260518_0001 for canonical migration; this
                -- inline DDL keeps the SQLite test path in sync.

                -- prioritization_runs: F6 ROI-scorer output.
                -- One row per /orchestrator/prioritize invocation.
                CREATE TABLE IF NOT EXISTS prioritization_runs (
                    run_id              TEXT PRIMARY KEY,
                    workspace_id        TEXT NOT NULL,
                    triggered_by        TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    started_at          TEXT NOT NULL,
                    completed_at        TEXT,
                    input_snapshot_json TEXT NOT NULL,
                    output_json         TEXT,
                    error               TEXT,
                    orchestrator_run_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_prio_runs_ws_recent
                    ON prioritization_runs(workspace_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_prio_runs_running
                    ON prioritization_runs(status);

                -- orchestrator_runs: F7-REVISED batch coordinator.
                -- UNIQUE (workspace_id, prioritization_run_id) is the
                -- idempotency gate.
                CREATE TABLE IF NOT EXISTS orchestrator_runs (
                    run_id                TEXT PRIMARY KEY,
                    workspace_id          TEXT NOT NULL,
                    prioritization_run_id TEXT NOT NULL,
                    triggered_by          TEXT NOT NULL,
                    top_n                 INTEGER NOT NULL,
                    status                TEXT NOT NULL,
                    started_at            TEXT NOT NULL,
                    completed_at          TEXT,
                    summary_json          TEXT,
                    error                 TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_orch_runs_ws_recent
                    ON orchestrator_runs(workspace_id, started_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orch_runs_idempotency
                    ON orchestrator_runs(workspace_id, prioritization_run_id);

                -- sub_agent_runs: per-theme worker. theme_id references
                -- feedback_clusters.cluster_id semantically (column
                -- name kept as theme_id per spec).
                CREATE TABLE IF NOT EXISTS sub_agent_runs (
                    run_id              TEXT PRIMARY KEY,
                    orchestrator_run_id TEXT NOT NULL,
                    workspace_id        TEXT NOT NULL,
                    theme_id            TEXT NOT NULL,
                    project_id          TEXT,
                    status              TEXT NOT NULL,
                    started_at          TEXT NOT NULL,
                    completed_at        TEXT,
                    decisions_count     INTEGER NOT NULL DEFAULT 0,
                    conflicts_count     INTEGER NOT NULL DEFAULT 0,
                    error               TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_subagent_runs_orch_status
                    ON sub_agent_runs(orchestrator_run_id, status);
                CREATE INDEX IF NOT EXISTS idx_subagent_runs_ws_recent
                    ON sub_agent_runs(workspace_id, started_at);

                -- decision_provenance: many-to-many decision <-> citing
                -- feedback item. weight = uniform 1.0 / len(citations)
                -- in v1 (computed by orchestrator at persistence).
                CREATE TABLE IF NOT EXISTS decision_provenance (
                    decision_id      TEXT NOT NULL,
                    feedback_item_id TEXT NOT NULL,
                    weight           REAL NOT NULL,
                    created_at       TEXT NOT NULL,
                    PRIMARY KEY (decision_id, feedback_item_id)
                );
                CREATE INDEX IF NOT EXISTS idx_decision_provenance_item
                    ON decision_provenance(feedback_item_id);

                -- conflict_resolutions: audit row per moderated conflict
                -- between two sub-agent decisions on the same subject.
                CREATE TABLE IF NOT EXISTS conflict_resolutions (
                    resolution_id          TEXT PRIMARY KEY,
                    orchestrator_run_id    TEXT NOT NULL,
                    decision_a_id          TEXT NOT NULL,
                    decision_b_id          TEXT NOT NULL,
                    subject                TEXT NOT NULL,
                    resolution_text        TEXT NOT NULL,
                    resolution_decision_id TEXT,
                    created_at             TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conflict_resolutions_orch
                    ON conflict_resolutions(orchestrator_run_id);
                """
            )
            connection.commit()
        # Retrofit preferred_model_tier onto users for DBs that predate
        # this column (anything created before migration 20260422_0003).
        # Idempotent: Postgres uses ADD COLUMN IF NOT EXISTS; SQLite
        # catches the duplicate-column error and swallows it.
        self._ensure_user_model_tier_column()
        # Retrofit BYOK (Bring Your Own Key) columns onto users for DBs
        # that predate migration 20260422_0007. Same belt-and-braces
        # pattern as preferred_model_tier.
        self._ensure_user_byok_columns()
        # v4 B2B pivot retrofits for the users-domain side: the
        # default_workspace_id column on users, and the five
        # workspace tables on Postgres (SQLite gets them from the
        # inline executescript above). The v2_projects.workspace_id
        # retrofit lives in _initialize_v2_projects_schema since it
        # needs the v2_projects table to exist first. Mirrors
        # alembic 20260504_0001..0005.
        self._ensure_users_default_workspace_id_column()
        self._ensure_workspace_tables()

    def _ensure_user_model_tier_column(self) -> None:
        """Add ``preferred_model_tier`` to ``users`` when missing.

        Idempotent across both backends. Mirrors
        ``_ensure_v2_projects_shelf_column`` — that pattern is the
        repo-wide convention for retrofitting a new nullable column
        without a full schema reset.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_model_tier TEXT"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN preferred_model_tier TEXT"
                )
                connection.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" in message:
                    return
                raise

    def _ensure_topics_private_notes_column(self) -> None:
        """Add ``private_notes`` to ``topics`` when missing.

        Mirrors ``_ensure_user_model_tier_column``. Nullable TEXT; existing
        rows read as "no note" (both NULL and empty string). Idempotent
        across Postgres (ADD COLUMN IF NOT EXISTS) and SQLite (catch the
        duplicate-column error).
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE topics ADD COLUMN IF NOT EXISTS private_notes TEXT"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE topics ADD COLUMN private_notes TEXT"
                )
                connection.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" in message:
                    return
                raise

    def _ensure_user_byok_columns(self) -> None:
        """Add the BYOK ciphertext columns to ``users`` when missing.

        Two nullable TEXT columns hold Fernet-encrypted API keys:

        - ``openai_api_key_encrypted``
        - ``anthropic_api_key_encrypted``

        Mirrors the other retrofit helpers: Postgres uses
        ``ADD COLUMN IF NOT EXISTS``, SQLite catches the duplicate-column
        error and swallows it so repeated boots are idempotent.
        """
        columns = (
            "openai_api_key_encrypted",
            "anthropic_api_key_encrypted",
        )
        if self._is_postgres:
            with self._connect() as connection:
                for col in columns:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} TEXT"
                    )
                connection.commit()
            return
        with self._connect() as connection:
            for col in columns:
                try:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN {col} TEXT"
                    )
                    connection.commit()
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "duplicate column" in message:
                        continue
                    raise

    def _ensure_subscription_trial_columns(self) -> None:
        """Add ``started_at`` + ``trial_ends_at`` to ``subscriptions``.

        ``started_at`` records when the user first landed on the paid
        plan (seeded from the row's ``created_at`` on migrate); the
        frontend uses it to gate the Pro-monthly → annual offer at 30
        days. ``trial_ends_at`` backs the trial_ending sweeper — it is
        NULL unless Stripe reports the row is trialing.
        """
        columns: tuple[tuple[str, str], ...] = (
            ("started_at", "TEXT"),
            ("trial_ends_at", "TEXT"),
        )
        if self._is_postgres:
            with self._connect() as connection:
                for col, col_type in columns:
                    connection.execute(
                        f"ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    )
                # Backfill `started_at` from `created_at` for pre-existing
                # rows so the Switch-to-annual gate can run against real
                # data right after deploy. Safe to re-run.
                connection.execute(
                    "UPDATE subscriptions SET started_at = created_at "
                    "WHERE started_at IS NULL AND created_at IS NOT NULL"
                )
                connection.commit()
            return
        with self._connect() as connection:
            for col, col_type in columns:
                try:
                    connection.execute(
                        f"ALTER TABLE subscriptions ADD COLUMN {col} {col_type}"
                    )
                    connection.commit()
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "duplicate column" in message:
                        continue
                    raise
            # Same backfill on SQLite. `UPDATE … WHERE … IS NULL` is
            # cheap and idempotent.
            connection.execute(
                "UPDATE subscriptions SET started_at = created_at "
                "WHERE started_at IS NULL AND created_at IS NOT NULL"
            )
            connection.commit()

    def _ensure_user_verification_columns(self) -> None:
        """Add the email-verification / known-device / trial-email columns.

        Four nullable columns on ``users`` support the transactional email
        wave landed 2026-04:

        - ``email_verified_at``: TIMESTAMP — stamped when the user clicks
          their verify link.
        - ``email_verified_token_hash``: TEXT — sha256 of the current
          verification token. Replaced each time we mint a fresh link.
        - ``trial_ending_emailed_at``: TIMESTAMP — one-shot guard so the
          trial-ending sweeper sends at most one notice per trial.
        - ``known_device_fingerprints``: TEXT — JSON array of sha256
          hashes of ``(ip, user_agent)`` pairs we've already emailed a
          new-signin note about. Subsequent logins from the same hash
          stay quiet.

        Mirrors ``_ensure_user_byok_columns`` — loops and swallows the
        duplicate-column error on SQLite. On Postgres, uses
        ``ADD COLUMN IF NOT EXISTS`` which is natively idempotent.
        """
        columns: tuple[tuple[str, str], ...] = (
            ("email_verified_at", "TIMESTAMP"),
            ("email_verified_token_hash", "TEXT"),
            ("trial_ending_emailed_at", "TIMESTAMP"),
            ("known_device_fingerprints", "TEXT"),
        )
        if self._is_postgres:
            with self._connect() as connection:
                for col, col_type in columns:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    )
                connection.commit()
            return
        with self._connect() as connection:
            for col, col_type in columns:
                try:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN {col} {col_type}"
                    )
                    connection.commit()
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "duplicate column" in message:
                        continue
                    raise

    def _ensure_user_terms_accepted_column(self) -> None:
        """Add ``users.terms_accepted_at`` on dev DBs that predate PR 3.

        Mirrors ``_ensure_user_byok_columns`` / ``_ensure_user_verification_columns``.
        The alembic migration ``20260424_0002_add_terms_accepted_at`` is the
        authoritative schema change for production; this retrofit exists so
        a developer booting against an older SQLite file does not hit a
        NoSuchColumn error on first signup.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN terms_accepted_at TEXT"
                )
                connection.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" in message:
                    return
                raise

    def _ensure_processed_webhook_events_table(self) -> None:
        """Create the ``processed_webhook_events`` table when missing.

        Backs the Stripe-webhook idempotency layer added in 2026-04 so a
        retried delivery of the same ``event.id`` is a no-op instead of
        re-running ``_apply_stripe_event`` (which would otherwise double
        upsert the subscriptions row, etc.).

        Mirrors the other retrofit helpers but operates on a whole table
        rather than a single column. Both ``CREATE TABLE IF NOT EXISTS``
        and ``CREATE INDEX IF NOT EXISTS`` are idempotent on both
        backends, so repeated boots are safe.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_webhook_events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT,
                        processed_at TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS "
                    "idx_processed_webhook_events_processed_at "
                    "ON processed_webhook_events(processed_at)"
                )
                connection.commit()
            return
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT,
                    processed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_processed_webhook_events_processed_at "
                "ON processed_webhook_events(processed_at)"
            )
            connection.commit()

    def _ensure_workspace_tables(self) -> None:
        """Create the v4 workspace tables when missing (Postgres only).

        Belt-and-braces retrofit so the workspace tables exist even
        on Postgres deployments that haven't yet run alembic
        migrations 20260504_0001..0005. Idempotent: every CREATE
        is IF NOT EXISTS.

        SQLite path is a no-op because all five tables are already
        created in the inline executescript bootstrap above.
        """
        if not self._is_postgres:
            return
        with self._connect() as connection:
            connection.execute(
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspaces_billing_owner "
                "ON workspaces(billing_owner_user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspaces_active_plan "
                "ON workspaces(plan_tier) WHERE archived_at IS NULL"
            )
            connection.execute(
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ws_members_user "
                "ON workspace_members(user_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_credentials (
                    workspace_id       TEXT NOT NULL,
                    provider           TEXT NOT NULL,
                    encrypted_token    TEXT NOT NULL,
                    installation_id    TEXT,
                    account_login      TEXT,
                    account_avatar_url TEXT,
                    scopes_json        TEXT NOT NULL DEFAULT '[]',
                    created_at         TEXT NOT NULL,
                    last_refreshed_at  TEXT,
                    status             TEXT NOT NULL DEFAULT 'connected'
                                       CHECK (status IN ('connected','needs_reauth','revoked')),
                    metadata_json      TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (workspace_id, provider)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conn_creds_attention "
                "ON connector_credentials(status) WHERE status != 'connected'"
            )
            connection.execute(
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_repo_snap_ws_recent "
                "ON repo_snapshots(workspace_id, last_sync_at DESC)"
            )
            connection.execute(
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_runs_ws_recent "
                "ON connector_sync_runs(workspace_id, started_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_runs_unfinished "
                "ON connector_sync_runs(status) WHERE finished_at IS NULL"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_items (
                    item_id          TEXT PRIMARY KEY,
                    workspace_id     TEXT NOT NULL,
                    source           TEXT NOT NULL,
                    external_id      TEXT,
                    content_hash     TEXT NOT NULL,
                    title            TEXT NOT NULL,
                    body             TEXT NOT NULL DEFAULT '',
                    author           TEXT,
                    author_email     TEXT,
                    received_at      TEXT,
                    ingested_at      TEXT NOT NULL,
                    type_hint        TEXT,
                    raw_payload_json TEXT,
                    status           TEXT NOT NULL DEFAULT 'queued'
                                     CHECK (status IN
                                       ('queued','classified','discarded','promoted')),
                    UNIQUE (workspace_id, content_hash)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_items_workspace "
                "ON feedback_items(workspace_id, ingested_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_items_queued "
                "ON feedback_items(status) WHERE status = 'queued'"
            )
            # Retrofit cluster + embedding columns for DBs that
            # predate migration 0007 (additive ADD COLUMN IF NOT EXISTS).
            connection.execute(
                "ALTER TABLE feedback_items "
                "ADD COLUMN IF NOT EXISTS cluster_id TEXT"
            )
            connection.execute(
                "ALTER TABLE feedback_items "
                "ADD COLUMN IF NOT EXISTS embedding_json TEXT"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_clusters (
                    cluster_id     TEXT PRIMARY KEY,
                    workspace_id   TEXT NOT NULL,
                    centroid_json  TEXT NOT NULL,
                    theme          TEXT,
                    item_count     INTEGER NOT NULL DEFAULT 1,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_clusters_workspace "
                "ON feedback_clusters(workspace_id, updated_at DESC)"
            )
            connection.commit()

    def _ensure_tier_usage_tables(self) -> None:
        """Create ``tier_usage`` + ``business_plan_usage`` when missing (#080).

        Belt-and-braces retrofit so the cap-counter tables exist even
        on Postgres deployments that haven't yet run the
        ``20260428_0001_tier_usage_and_business_plan_caps`` Alembic
        migration. Both ``CREATE TABLE IF NOT EXISTS`` calls are
        idempotent on both backends.

        SQLite path is a no-op because the table is already created
        in the inline executescript bootstrap above.
        """
        if not self._is_postgres:
            return
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tier_usage (
                    user_id TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    output_tokens_used BIGINT NOT NULL DEFAULT 0,
                    window_started_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (user_id, tier)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS business_plan_usage (
                    user_id TEXT NOT NULL PRIMARY KEY,
                    plans_used_this_month INTEGER NOT NULL DEFAULT 0,
                    window_started_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.commit()

    # ---------- Password reset tokens ----------
    def _ensure_users_default_workspace_id_column(self) -> None:
        """Add ``users.default_workspace_id`` when missing (v4 W1).

        Idempotent across both backends. Postgres uses ADD COLUMN
        IF NOT EXISTS; SQLite catches the duplicate-column error.
        Mirrors ``_ensure_user_model_tier_column``.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS default_workspace_id TEXT"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN default_workspace_id TEXT"
                )
                connection.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" in message:
                    return
                raise

    def _ensure_v2_projects_workspace_id_column(self) -> None:
        """Add ``v2_projects.workspace_id`` when missing (v4 W1).

        Idempotent across both backends. Mirrors
        ``_ensure_user_model_tier_column``. Index added in the same
        method since the column without an index slows the
        workspace-scoped project queries that land in W4.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE v2_projects ADD COLUMN IF NOT EXISTS workspace_id TEXT"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_v2_projects_workspace "
                    "ON v2_projects(workspace_id)"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE v2_projects ADD COLUMN workspace_id TEXT"
                )
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" not in message:
                    raise
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_v2_projects_workspace "
                "ON v2_projects(workspace_id)"
            )
            connection.commit()

    def _ensure_documents_table(self) -> None:
        """Create ``documents`` when missing (#094 / Item 3 redesign).

        Belt-and-braces retrofit so the documents table exists even
        on Postgres deployments that haven't yet run
        ``20260429_0003_documents``. Idempotent: each
        ``CREATE TABLE IF NOT EXISTS`` and ``CREATE INDEX IF NOT
        EXISTS`` is safe to re-run.

        SQLite path is a no-op because the table is already created
        in the inline executescript bootstrap (above).
        """
        if not self._is_postgres:
            return
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    doc_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_json TEXT,
                    error_message TEXT,
                    model_id TEXT NOT NULL,
                    plan_tier TEXT NOT NULL,
                    output_tokens_estimate INTEGER,
                    generated_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_documents_project_doctype
                ON documents(project_id, doc_type, generated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_documents_user
                ON documents(user_id)
                """
            )
            connection.commit()

    # TTL on a freshly-minted reset token, in seconds. One hour is the
    # long enough for the legitimate user to notice the email and click,
    # short enough that a leaked-but-unused token rots quickly.
    PASSWORD_RESET_TOKEN_TTL_SECONDS = 3600

    # Cap on the number of concurrently-live reset tokens a single user
    # can hold. New requests beyond this invalidate the older ones so a
    # user pestering "forgot my password" can't grow an unbounded set of
    # active links.
    PASSWORD_RESET_MAX_ACTIVE_TOKENS = 3

    def create_password_reset_token(self, user_id: str) -> str:
        """Mint a fresh reset token for this user and return the RAW token.

        The raw value is what goes into the email. We store only the
        SHA-256 hash. If the user already has ``MAX_ACTIVE_TOKENS`` live
        tokens, the oldest active ones are marked used so this call keeps
        the table bounded.

        Returns the URL-safe raw token (hex-encoded 32 random bytes).
        """
        import hashlib
        import secrets
        from datetime import timedelta

        raw_token = secrets.token_hex(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        requested_at = datetime.now(timezone.utc)
        expires_at = requested_at + timedelta(
            seconds=self.PASSWORD_RESET_TOKEN_TTL_SECONDS,
        )
        requested_at_iso = requested_at.isoformat(timespec="seconds")
        expires_at_iso = expires_at.isoformat(timespec="seconds")

        with self._connect() as connection:
            # Invalidate older live tokens beyond the cap. We pick the ones
            # to kill by requested_at ASC so the newest stay active.
            active_rows = connection.execute(
                """
                SELECT token_hash FROM password_reset_tokens
                WHERE user_id = ? AND used_at IS NULL AND expires_at > ?
                ORDER BY requested_at ASC
                """,
                (user_id, requested_at_iso),
            ).fetchall()
            # After we insert the new one there will be len(active_rows)+1
            # live tokens; trim down so we land at MAX_ACTIVE_TOKENS.
            over = len(active_rows) + 1 - self.PASSWORD_RESET_MAX_ACTIVE_TOKENS
            if over > 0:
                doomed = [row["token_hash"] for row in active_rows[:over]]
                placeholders = ",".join("?" for _ in doomed)
                connection.execute(
                    f"UPDATE password_reset_tokens SET used_at = ? "
                    f"WHERE token_hash IN ({placeholders})",
                    (requested_at_iso, *doomed),
                )
            connection.execute(
                """
                INSERT INTO password_reset_tokens
                    (token_hash, user_id, requested_at, expires_at, used_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (token_hash, user_id, requested_at_iso, expires_at_iso),
            )
            connection.commit()
        return raw_token

    def consume_password_reset_token(self, raw_token: str) -> str | None:
        """Redeem a raw reset token. Returns the ``user_id`` or ``None``.

        ``None`` covers every "don't mint a password change" case: unknown
        token, already used, or expired. Consuming is idempotent only in
        the sense that calling twice with the same token returns
        ``user_id`` then ``None`` -- the row is marked ``used_at`` on the
        first successful redemption.
        """
        import hashlib

        if not raw_token:
            return None
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now_iso = now_timestamp()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, expires_at, used_at
                FROM password_reset_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["used_at"] is not None:
                return None
            if row["expires_at"] <= now_iso:
                return None
            connection.execute(
                "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?",
                (now_iso, token_hash),
            )
            connection.commit()
            return str(row["user_id"])

    def invalidate_user_password_reset_tokens(self, user_id: str) -> int:
        """Mark every active reset token for this user as used.

        Called right after a successful reset so any other links the user
        (or attacker) might hold become dead. Returns the number of tokens
        invalidated -- useful for logging, not required for correctness.
        """
        now_iso = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE password_reset_tokens SET used_at = ?
                WHERE user_id = ? AND used_at IS NULL
                """,
                (now_iso, user_id),
            )
            connection.commit()
            return int(cursor.rowcount or 0)

    def update_user_password(self, user_id: str, new_hash: str) -> bool:
        """Replace the user's password hash. Returns True iff a row matched."""
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (new_hash, now, user_id),
            )
            connection.commit()
            return bool(cursor.rowcount)

    def set_email_verification_token(self, user_id: str, token_hash: str) -> bool:
        """Stash the sha256 of the current verification token.

        Idempotent — a fresh signup OR a resend both just overwrite the
        prior hash. Returns True iff a row matched.

        A ``sqlite3.OperationalError`` ("no such column") can bubble up
        from very-old DBs that haven't picked up the retrofit yet; the
        caller swallows that so signup still completes.
        """
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET email_verified_token_hash = ?, updated_at = ? "
                "WHERE user_id = ?",
                (token_hash, now, user_id),
            )
            connection.commit()
            return bool(cursor.rowcount)

    def observe_device_fingerprint(self, user_id: str, fp_hash: str) -> bool:
        """Record a device fingerprint for a user; return True if new.

        Reads ``known_device_fingerprints`` (JSON array of sha256 hex
        strings), appends the new hash if absent, and writes back. A
        ``True`` return means the caller should fire a ``new_signin``
        email. A ``False`` return means this (ip, user_agent) pair has
        already been seen — stay quiet.

        Best-effort JSON parse: if the column is corrupt or the DB
        doesn't have the column yet, return ``False`` so the caller
        never double-fires and never blocks login.
        """
        import json as _json

        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    "SELECT known_device_fingerprints FROM users WHERE user_id = ?",
                    (user_id,),
                )
                row = cursor.fetchone()
            except sqlite3.OperationalError:
                # Column missing on an older DB — no-op until migrate.
                return False
            if row is None:
                return False
            raw = row[0]
            try:
                known = _json.loads(raw) if raw else []
                if not isinstance(known, list):
                    known = []
            except Exception:  # noqa: BLE001 — treat corrupt JSON as empty
                known = []
            if fp_hash in known:
                return False
            known.append(fp_hash)
            serialized = _json.dumps(known)
            connection.execute(
                "UPDATE users SET known_device_fingerprints = ?, updated_at = ? "
                "WHERE user_id = ?",
                (serialized, now_timestamp(), user_id),
            )
            connection.commit()
            return True

    def list_users_for_trial_ending_sweep(
        self, *, now_iso: str, horizon_iso: str,
    ) -> list[dict[str, Any]]:
        """Return users whose trial ends within ``[now_iso, horizon_iso]``.

        Joins ``users`` and ``subscriptions`` — only rows where the
        subscription has a ``trial_ends_at`` inside the window AND the
        user hasn't already received a ``trial_ending`` email this
        trial (``users.trial_ending_emailed_at IS NULL``).

        Each result dict has: user_id, email, display_name,
        plan (slug), trial_ends_at.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT u.user_id, u.email, u.display_name,
                       s.plan, s.trial_ends_at
                FROM subscriptions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.trial_ends_at IS NOT NULL
                  AND s.trial_ends_at >= ?
                  AND s.trial_ends_at <= ?
                  AND (u.trial_ending_emailed_at IS NULL
                       OR u.trial_ending_emailed_at = '')
                """,
                (now_iso, horizon_iso),
            )
            rows = cursor.fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                # SQLite + psycopg both yield tuple-ish; support both
                # attribute and index access for robustness.
                if hasattr(row, "keys"):
                    out.append(dict(row))
                else:
                    out.append({
                        "user_id": row[0],
                        "email": row[1],
                        "display_name": row[2],
                        "plan": row[3],
                        "trial_ends_at": row[4],
                    })
            return out

    def mark_trial_ending_emailed(self, user_id: str) -> bool:
        """Stamp ``users.trial_ending_emailed_at`` so the sweeper is
        one-shot per trial."""
        now = now_timestamp()
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    "UPDATE users SET trial_ending_emailed_at = ?, updated_at = ? "
                    "WHERE user_id = ?",
                    (now, now, user_id),
                )
                connection.commit()
                return bool(cursor.rowcount)
            except sqlite3.OperationalError:
                return False

    def consume_email_verification_token(self, token_hash: str) -> str | None:
        """Flip ``email_verified_at`` for the user matching this hash.

        Returns the ``user_id`` on success, ``None`` when the hash
        doesn't match any user (already consumed, expired, or forged).
        The hash column is cleared on success so a replay is a no-op.
        """
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT user_id FROM users WHERE email_verified_token_hash = ?",
                (token_hash,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            user_id = row[0]
            connection.execute(
                "UPDATE users SET email_verified_at = ?, "
                "email_verified_token_hash = NULL, updated_at = ? "
                "WHERE user_id = ?",
                (now, now, user_id),
            )
            connection.commit()
            return user_id

    # ---------- Personal Access Tokens (PATs) ---------------------------
    #
    # External automations (Zapier, MCP server, a user's own script)
    # authenticate via ``Authorization: Bearer inspira_pat_<raw>``.  The
    # raw token is shown to the user ONCE in the mint dialog and then
    # discarded -- only the SHA-256 hash lives in the DB.  A DB dump
    # therefore cannot mint tokens; the worst outcome is the attacker
    # learns token_ids / names, which we accept as routinely-visible
    # operational metadata.
    #
    # The ``inspira_pat_`` prefix on the raw value is load-bearing:
    # it's grep-able in logs, obvious in postmortems, and tells the
    # bearer-auth middleware whether a header is a PAT or some other
    # kind of bearer we might support later (OAuth access tokens, etc.)
    # without having to sniff the shape.

    ACCESS_TOKEN_PREFIX = "inspira_pat_"
    # 32 hex = 128 bits of entropy -- plenty for an authentication token.
    # Longer means the copy-once dialog wraps ugly on narrow screens;
    # shorter starts nudging toward guessable.  The ``secrets`` module's
    # CSPRNG is the entropy source (see mint_access_token below).
    _ACCESS_TOKEN_RANDOM_HEX_CHARS = 32

    def mint_access_token(
        self,
        user_id: str,
        name: str,
        scopes: list[str] | None = None,
    ) -> tuple[str, str]:
        """Mint a fresh PAT for ``user_id`` and return ``(token_id, raw_token)``.

        The raw token is the ONLY copy -- we store sha256(raw) and drop
        the plaintext.  Caller is responsible for delivering ``raw_token``
        to the user in a copy-once dialog; it is unrecoverable after this
        function returns.

        Empty ``scopes`` (the default) = full read+write, matching the
        session-cookie grant.  Future scope strings like ``"read:projects"``
        land in the ``scopes_json`` column without another migration.
        """
        import hashlib
        import secrets

        raw_suffix = secrets.token_hex(self._ACCESS_TOKEN_RANDOM_HEX_CHARS // 2)
        # ``token_hex(16)`` yields 32 hex chars.  Keep the maths explicit
        # so a future widening only touches the constant above.
        raw_token = f"{self.ACCESS_TOKEN_PREFIX}{raw_suffix}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token_id = f"tok_{uuid.uuid4().hex[:20]}"
        trimmed_name = (name or "").strip()
        if not trimmed_name:
            raise ValueError("access token name is required")
        scopes_payload = json.dumps(list(scopes or []))
        created_at = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_access_tokens
                  (token_id, token_hash, user_id, name, scopes_json,
                   created_at, last_used_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    token_id,
                    token_hash,
                    user_id,
                    trimmed_name,
                    scopes_payload,
                    created_at,
                ),
            )
            connection.commit()
        return token_id, raw_token

    def list_access_tokens(self, user_id: str) -> list[dict[str, Any]]:
        """Return every PAT this user has minted, newest first.

        Never includes the raw token or its hash -- the list view only
        shows metadata safe to display and act on: name, timestamps,
        and revocation status.  Revoked tokens stay in the list so the
        user can see the audit trail; the UI greys them out.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT token_id, name, scopes_json, created_at,
                       last_used_at, revoked_at
                FROM user_access_tokens
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            row_dict = dict(row)
            try:
                scopes_list = json.loads(row_dict.get("scopes_json") or "[]")
            except (TypeError, ValueError):
                scopes_list = []
            out.append(
                {
                    "token_id": row_dict["token_id"],
                    "name": row_dict["name"],
                    "scopes": list(scopes_list),
                    "created_at": row_dict["created_at"],
                    "last_used_at": row_dict.get("last_used_at"),
                    "revoked_at": row_dict.get("revoked_at"),
                },
            )
        return out

    def resolve_access_token(self, raw_token: str) -> str | None:
        """Look up a raw PAT.  Return the owner's ``user_id`` or ``None``.

        Returns ``None`` for every failure mode -- unknown prefix, unknown
        hash, revoked token -- so bearer-auth callers see a single
        "didn't authenticate" path without leaking which case they hit.
        Updates ``last_used_at`` as a side-effect on success so the
        list-view can show "Last used 3 minutes ago".
        """
        import hashlib

        if not raw_token:
            return None
        # Cheap shape gate: reject anything that isn't an Inspira-origin
        # PAT before doing a DB round-trip.  The prefix is not a secret;
        # it exists to make logs grep-able and to fail fast on obvious
        # non-PAT bearers (OAuth access tokens, JWTs, etc.).
        if not raw_token.startswith(self.ACCESS_TOKEN_PREFIX):
            return None
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now_iso = now_timestamp()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT token_id, user_id, revoked_at
                FROM user_access_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["revoked_at"] is not None:
                return None
            # Touch last_used_at so the list view can surface dormant
            # tokens.  Best-effort -- if this write races with a concurrent
            # revoke the resolver still returns the user_id, which is the
            # behaviour we want (the current request was already authenticated
            # against the pre-revoke state).
            connection.execute(
                "UPDATE user_access_tokens SET last_used_at = ? WHERE token_id = ?",
                (now_iso, row["token_id"]),
            )
            connection.commit()
            return str(row["user_id"])

    def revoke_access_token(self, user_id: str, token_id: str) -> bool:
        """Mark a PAT revoked.  Returns True iff the caller owns it.

        IDOR defence: the WHERE clause pins the row to ``user_id``, so
        user A calling revoke with user B's ``token_id`` matches nothing
        and we return False.  Re-revoking an already-revoked token is a
        no-op returning False (rowcount is 0 because the WHERE
        ``revoked_at IS NULL`` guard excludes it).
        """
        now_iso = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE user_access_tokens SET revoked_at = ?
                WHERE token_id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (now_iso, token_id, user_id),
            )
            connection.commit()
            return bool(cursor.rowcount)

    def update_user_metadata(
        self, user_id: str, metadata: dict[str, Any],
    ) -> None:
        """Overwrite the user's metadata_json blob with ``metadata``.

        Used by the BYOK module to track per-provider ``last_verified_at``
        without requiring a dedicated timestamp table. No-op when the
        user_id doesn't match a row; the caller is responsible for ensuring
        the user exists before relying on this data.
        """
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET metadata_json = ?, updated_at = ? "
                "WHERE user_id = ?",
                (json.dumps(metadata or {}), now, user_id),
            )
            connection.commit()

    def create_user(
        self,
        *,
        email: str,
        password_hash: str | None = None,
        display_name: str = "",
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        terms_accepted_at: "datetime | None" = None,
    ) -> dict[str, Any]:
        uid = user_id or f"user-{uuid.uuid4().hex[:12]}"
        now = now_timestamp()
        # Accepted-at is persisted as ISO-8601 UTC, matching the rest of
        # the timestamp columns on this table. ``None`` writes NULL so the
        # legacy system user and pre-terms-gate anonymous rows read back
        # cleanly as "unknown / not captured".
        accepted_at_str = (
            terms_accepted_at.isoformat()
            if terms_accepted_at is not None
            else None
        )
        payload = {
            "user_id": uid,
            "email": email.lower().strip(),
            "password_hash": password_hash,
            "display_name": display_name or email.split("@", 1)[0],
            "metadata": metadata or {},
            "terms_accepted_at": accepted_at_str,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users
                  (user_id, email, password_hash, display_name, metadata_json, terms_accepted_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (
                    payload["user_id"],
                    payload["email"],
                    payload["password_hash"],
                    payload["display_name"],
                    json.dumps(payload["metadata"]),
                    payload["terms_accepted_at"],
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
            connection.commit()
        return self.get_user_by_id(uid) or payload

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, email, password_hash, display_name, metadata_json, terms_accepted_at, created_at, updated_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["metadata"] = json.loads(out.pop("metadata_json") or "{}")
        return out

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, email, password_hash, display_name, metadata_json, terms_accepted_at, created_at, updated_at FROM users WHERE email = ?",
                (email.lower().strip(),),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["metadata"] = json.loads(out.pop("metadata_json") or "{}")
        return out

    # ---------- User preferences: LLM model tier ------------------------

    def get_preferred_model_tier(self, user_id: str) -> str | None:
        """Return the user's persisted default tier slug, or None.

        ``None`` means "no override set"; the caller should resolve to
        the plan default via ``agents.tiers.default_tier_for_plan``.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT preferred_model_tier FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        value = row["preferred_model_tier"]
        if value in ("", None):
            return None
        return str(value)

    # ---------- Monthly tier-usage counters (#080) -------------------------
    # Cap-counter accessors for the topic_turn output-token cap. Each method
    # applies lazy month-boundary reset: if the persisted ``window_started_at``
    # predates the current calendar-month UTC start, the counter is treated
    # as 0 for reads and reset on the next write.

    def get_tier_usage(
        self, *, user_id: str, tier: str,
    ) -> dict[str, Any]:
        """Return the user's monthly usage counter for a tier.

        Always returns a dict with keys ``tier``, ``output_tokens_used``
        (int), ``window_started_at`` (ISO str). When no row exists OR
        the stored row's window predates the current calendar month,
        returns ``output_tokens_used=0`` with a fresh window stamp
        (without writing — the increment path lazily creates the row).

        Designed for use in the topic_turn cap-check path:
        ``usage = store.get_tier_usage(...); if usage["output_tokens_used"]
        >= cap: ...`` is the canonical check.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT output_tokens_used, window_started_at FROM tier_usage "
                "WHERE user_id = ? AND tier = ?",
                (user_id, tier),
            ).fetchone()
        if row is None:
            return {
                "tier": tier,
                "output_tokens_used": 0,
                "window_started_at": now_timestamp(),
            }
        used = int(row["output_tokens_used"] or 0)
        window_iso = str(row["window_started_at"])
        if _usage_window_is_stale(window_iso):
            return {
                "tier": tier,
                "output_tokens_used": 0,
                "window_started_at": now_timestamp(),
            }
        return {
            "tier": tier,
            "output_tokens_used": used,
            "window_started_at": window_iso,
        }

    def increment_tier_usage(
        self, *, user_id: str, tier: str, tokens: int,
    ) -> None:
        """Add ``tokens`` to the user's monthly counter for ``tier``.

        Lazy month-boundary reset: if the existing row's window
        predates the current calendar month UTC, the counter is set
        to ``tokens`` (replacing the stale value) and the window is
        re-stamped to now. Otherwise ``tokens`` is added to the
        existing counter.

        ``tokens <= 0`` short-circuits without a DB hit.

        Caller (post-LLM-call in v2_topic_turn) reads
        ``usage.completion_tokens`` from the OpenAI response and
        passes it here. Failures here are logged but don't propagate
        — a counter miss is preferable to losing the user's turn.
        """
        if tokens <= 0:
            return
        now_iso = now_timestamp()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT output_tokens_used, window_started_at FROM tier_usage "
                "WHERE user_id = ? AND tier = ?",
                (user_id, tier),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO tier_usage "
                    "(user_id, tier, output_tokens_used, window_started_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, tier, tokens, now_iso),
                )
            else:
                window_iso = str(existing["window_started_at"])
                if _usage_window_is_stale(window_iso):
                    connection.execute(
                        "UPDATE tier_usage SET output_tokens_used = ?, "
                        "window_started_at = ? WHERE user_id = ? AND tier = ?",
                        (tokens, now_iso, user_id, tier),
                    )
                else:
                    connection.execute(
                        "UPDATE tier_usage "
                        "SET output_tokens_used = output_tokens_used + ? "
                        "WHERE user_id = ? AND tier = ?",
                        (tokens, user_id, tier),
                    )
            connection.commit()

    def get_business_plan_usage(self, *, user_id: str) -> dict[str, Any]:
        """Return the user's monthly business-plan generation count.

        Lazy reset semantics mirror ``get_tier_usage``. Returns
        ``{plans_used_this_month: 0, window_started_at: now}`` when no
        row exists or the window has rolled over.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plans_used_this_month, window_started_at "
                "FROM business_plan_usage WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return {
                "plans_used_this_month": 0,
                "window_started_at": now_timestamp(),
            }
        used = int(row["plans_used_this_month"] or 0)
        window_iso = str(row["window_started_at"])
        if _usage_window_is_stale(window_iso):
            return {
                "plans_used_this_month": 0,
                "window_started_at": now_timestamp(),
            }
        return {
            "plans_used_this_month": used,
            "window_started_at": window_iso,
        }

    def increment_business_plan_usage(self, *, user_id: str) -> None:
        """Bump the user's monthly business-plan-generation counter by 1.

        Same lazy-reset semantics as ``increment_tier_usage``. Called
        after a successful business-plan-phase generation completes.
        """
        now_iso = now_timestamp()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT plans_used_this_month, window_started_at "
                "FROM business_plan_usage WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO business_plan_usage "
                    "(user_id, plans_used_this_month, window_started_at) "
                    "VALUES (?, ?, ?)",
                    (user_id, 1, now_iso),
                )
            else:
                window_iso = str(existing["window_started_at"])
                if _usage_window_is_stale(window_iso):
                    connection.execute(
                        "UPDATE business_plan_usage SET plans_used_this_month = ?, "
                        "window_started_at = ? WHERE user_id = ?",
                        (1, now_iso, user_id),
                    )
                else:
                    connection.execute(
                        "UPDATE business_plan_usage "
                        "SET plans_used_this_month = plans_used_this_month + 1 "
                        "WHERE user_id = ?",
                        (user_id,),
                    )
            connection.commit()

    # ---------- Documents (#094 / Item 3 redesign) ----------
    # One row per generation attempt for any of the 7 doc types.
    # Cap accounting reuses the existing business_plan_usage table from
    # #080 (treat it as the document_usage cap counter going forward).

    def create_document_in_progress(
        self,
        *,
        project_id: str,
        user_id: str,
        doc_type: str,
        plan_tier: str,
        model_id: str,
    ) -> str:
        """Insert a fresh ``in_progress`` document row and return its id.

        Caller (the POST /document/generate endpoint) holds the per-project
        advisory lock during this call so two concurrent requests can't
        both reach this path on the same project.

        ``plan_tier`` is the user's plan-derived cap bucket (``"pro"`` for
        Pro accounts, ``"frontier"`` for Frontier); recorded for audit.
        ``model_id`` is always ``"gpt-5.5"`` today but the column future-
        proofs against a model rotation.
        ``doc_type`` must be in VALID_DOC_TYPES; raises if not.
        """
        if doc_type not in VALID_DOC_TYPES:
            raise ValueError(
                f"invalid doc_type: {doc_type!r}; "
                f"expected one of {sorted(VALID_DOC_TYPES)}"
            )
        document_id = f"doc-{uuid.uuid4().hex[:10]}"
        now_iso = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO documents "
                "(document_id, project_id, user_id, doc_type, status, "
                "model_id, plan_tier, generated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    project_id,
                    user_id,
                    doc_type,
                    "in_progress",
                    model_id,
                    plan_tier,
                    now_iso,
                ),
            )
            connection.commit()
        return document_id

    def mark_document_completed(
        self,
        *,
        document_id: str,
        content_json: str,
        output_tokens_estimate: int,
    ) -> None:
        """Flip an ``in_progress`` document to ``completed`` with payload.

        ``content_json`` is the sanitized JSON shape per doc type (see
        ``agents/schemas.py``). ``output_tokens_estimate`` is what gets
        credited against the user's monthly cap via the document-usage
        increment in the calling endpoint.
        """
        now_iso = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents "
                "SET status = ?, content_json = ?, "
                "output_tokens_estimate = ?, completed_at = ? "
                "WHERE document_id = ?",
                ("completed", content_json, output_tokens_estimate, now_iso, document_id),
            )
            connection.commit()

    def mark_document_failed(
        self,
        *,
        document_id: str,
        error_message: str,
    ) -> None:
        """Flip an ``in_progress`` document to ``failed`` with the error.

        ``error_message`` is a short string — the exception's repr
        truncated to a sensible length by the caller. Surfaced to the
        FE via the GET /document/{document_id} poll so the retry CTA
        can describe what went wrong.
        """
        now_iso = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents "
                "SET status = ?, error_message = ?, completed_at = ? "
                "WHERE document_id = ?",
                ("failed", error_message, now_iso, document_id),
            )
            connection.commit()

    def get_document(
        self,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Return one document row by id, or ``None`` if not found.

        Used by the GET /document/{document_id} poll endpoint to let
        the FE drive the in-progress → completed/failed transition.
        Caller is responsible for the ownership check (verifying the
        ``user_id`` on the row matches the requester).
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_id, project_id, user_id, doc_type, status, "
                "content_json, error_message, model_id, plan_tier, "
                "output_tokens_estimate, generated_at, completed_at "
                "FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_document_dict(row)

    def get_latest_completed_document(
        self,
        *,
        project_id: str,
        doc_type: str,
    ) -> dict[str, Any] | None:
        """Latest ``completed`` document for a (project, doc_type), or ``None``.

        Used on tab open to seed the panel with cached prose. Returns
        ``None`` if the project has never had a successful generation
        of this doc_type — the FE then renders the empty state with
        the Generate CTA. Skips ``in_progress`` and ``failed`` rows;
        those surface through ``get_in_flight_document`` instead.
        """
        if doc_type not in VALID_DOC_TYPES:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_id, project_id, user_id, doc_type, status, "
                "content_json, error_message, model_id, plan_tier, "
                "output_tokens_estimate, generated_at, completed_at "
                "FROM documents "
                "WHERE project_id = ? AND doc_type = ? AND status = ? "
                "ORDER BY generated_at DESC LIMIT 1",
                (project_id, doc_type, "completed"),
            ).fetchone()
        if row is None:
            return None
        return _row_to_document_dict(row)

    def get_in_flight_document(
        self,
        *,
        project_id: str,
        doc_type: str,
    ) -> dict[str, Any] | None:
        """Most recent ``in_progress`` document for a (project, doc_type), or ``None``.

        Used by the POST /document/generate endpoint to detect a
        concurrent generation already running (alongside the per-project
        advisory lock — the lock is the authoritative gate, this is a
        belt-and-braces read for surfacing the existing document_id back
        to the second caller so both browser tabs poll the same row).

        Stale guard: if the row's ``generated_at`` is older than 5
        minutes, the FastAPI BackgroundTask is presumed dead (worker
        crash, SIGTERM mid-call) and the row is treated as absent —
        the caller can start a fresh generation. The orphan row stays
        around for audit; a future sweeper can flip it to ``failed``.
        """
        if doc_type not in VALID_DOC_TYPES:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document_id, project_id, user_id, doc_type, status, "
                "content_json, error_message, model_id, plan_tier, "
                "output_tokens_estimate, generated_at, completed_at "
                "FROM documents "
                "WHERE project_id = ? AND doc_type = ? AND status = ? "
                "ORDER BY generated_at DESC LIMIT 1",
                (project_id, doc_type, "in_progress"),
            ).fetchone()
        if row is None:
            return None
        document = _row_to_document_dict(row)
        # Reuse the same 5-minute stale guard the next-steps artifacts
        # path uses (#089 part 1/4).
        if _next_steps_in_progress_is_stale(document["generated_at"]):
            return None
        return document

    def update_document_content_json(
        self,
        *,
        document_id: str,
        content_json: str,
    ) -> dict[str, Any] | None:
        """Replace ``content_json`` on an existing document; return updated row.

        Used by PATCH /document/{document_id}/section/{section_id} for
        user inline edits. **Does NOT** touch ``status`` or
        ``completed_at`` — a user edit on a completed document leaves
        it completed, not "regenerated". Returns the updated row dict
        (so the endpoint can shape the response without a second read)
        or ``None`` if ``document_id`` was not found, so the endpoint
        can 404.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE documents SET content_json = ? WHERE document_id = ?",
                (content_json, document_id),
            )
            if cursor.rowcount == 0:
                return None
            connection.commit()
            row = connection.execute(
                "SELECT document_id, project_id, user_id, doc_type, status, "
                "content_json, error_message, model_id, plan_tier, "
                "output_tokens_estimate, generated_at, completed_at "
                "FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_document_dict(row)

    def set_preferred_model_tier(
        self, user_id: str, tier: str | None,
    ) -> None:
        """Persist the user's default tier slug (or clear it).

        Validates ``tier`` against ``ModelTier`` before writing so a bad
        value never reaches the DB. ``None`` clears the override.
        """
        # Local import to keep the store's import graph unchanged; the
        # agents package doesn't need to load on every store-only path.
        from .agents.tiers import ModelTier  # noqa: PLC0415

        if tier is not None:
            try:
                tier = ModelTier(tier).value
            except ValueError as exc:
                raise ValueError(
                    f"Unknown model tier slug: {tier!r}. "
                    f"Valid: {sorted(t.value for t in ModelTier)}"
                ) from exc
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET preferred_model_tier = ?, updated_at = ? "
                "WHERE user_id = ?",
                (tier, now, user_id),
            )
            connection.commit()

    # ---------- BYOK ciphertext accessors -------------------------------
    # These DELIBERATELY return the raw ciphertext and take raw ciphertext
    # as input. All encryption / decryption happens in ``byok.py``. Keeping
    # the store oblivious to the encryption layer means rotating the
    # Fernet key or swapping to a different symmetric cipher never requires
    # a schema change here.
    #
    # ``provider`` is the literal "openai" or "anthropic" — the column
    # name is derived via the module-level ``_BYOK_COLUMNS`` map below.
    # We validate against a tiny allowlist so a caller can't inject a
    # column name.

    def _byok_column_for(self, provider: str) -> str:
        try:
            return _BYOK_COLUMNS[provider]
        except KeyError as exc:
            raise ValueError(
                f"Unknown BYOK provider: {provider!r}. "
                f"Valid: {sorted(_BYOK_COLUMNS)}"
            ) from exc

    def get_byok_ciphertext(
        self, user_id: str, provider: str,
    ) -> str | None:
        """Return the raw ciphertext for this user + provider, or None.

        ``None`` covers both "user row missing" and "column is NULL". The
        caller (``byok.store.get_user_byok``) decrypts on its way out.
        """
        column = self._byok_column_for(provider)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {column} AS ciphertext FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        value = row["ciphertext"]
        if value in (None, ""):
            return None
        return str(value)

    def set_byok_ciphertext(
        self, user_id: str, provider: str, ciphertext: str | None,
    ) -> None:
        """Persist the raw ciphertext (or clear it with ``None``).

        The caller is responsible for encrypting before this call. Empty
        strings are normalised to NULL so the getter's "configured?"
        check stays a simple ``is not None``.
        """
        column = self._byok_column_for(provider)
        normalised = ciphertext or None
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                f"UPDATE users SET {column} = ?, updated_at = ? "
                f"WHERE user_id = ?",
                (normalised, now, user_id),
            )
            connection.commit()

    # ---------- v2 projects (explicit, per-user) ------------------------

    def _initialize_v2_projects_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v2_projects (
                    project_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    archived_at TEXT,
                    -- v4 B3.3 / B1.1: review state machine + Kanban sort.
                    -- Five-state CHECK is forward-compat for the post-W4
                    -- summary_ready feature. ``priority_order`` NULL means
                    -- "use ROI sort"; non-null is a sticky manual reorder.
                    project_state TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (project_state IN (
                            'pending_review','in_review','approved',
                            'rejected','summary_ready'
                        )),
                    priority_order INTEGER,
                    roi_score INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_v2_projects_user ON v2_projects(user_id);
                -- idx_v2_projects_state_workspace is created in
                -- _ensure_v2_projects_state_columns below — it indexes
                -- workspace_id which is itself a retrofit column added
                -- by _ensure_v2_projects_workspace_id_column. Creating
                -- the index here would fail on a fresh DB because the
                -- column hasn't been added yet at this point in the
                -- schema bootstrap sequence.

                -- Shelves — user-owned named containers for grouping projects
                -- (the novel + its research, the startup + its side experiments).
                -- A project belongs to at most one shelf; shelf_id=NULL means
                -- the project is on the implicit "Unfiled" shelf. Soft-deleted
                -- via deleted_at; deleting a shelf un-shelves its projects
                -- rather than cascading to project deletion.
                CREATE TABLE IF NOT EXISTS shelves (
                    shelf_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_shelves_user ON shelves(user_id, deleted_at);
                """
            )
            connection.commit()
        # Retrofit shelf_id onto v2_projects for DBs that predate this column.
        # Idempotent: the SQLite path catches "duplicate column name" and the
        # Postgres path uses ADD COLUMN IF NOT EXISTS.
        self._ensure_v2_projects_shelf_column()
        # Retrofit archived_at onto v2_projects for DBs that predate
        # migration 20260422_0005. Same pattern as shelf_id above.
        self._ensure_v2_projects_archived_column()
        # Retrofit workspace_id onto v2_projects for the v4 B2B pivot.
        # Same belt-and-braces pattern; mirrors alembic 20260504_0005.
        self._ensure_v2_projects_workspace_id_column()
        # Retrofit project_state + priority_order + roi_score for the
        # v4 Kanban (B1.1) and review state machine (B3.3). Mirrors
        # alembic 20260504_0008. Must run AFTER the workspace_id ensure
        # because the composite index it creates references that column.
        self._ensure_v2_projects_state_columns()
        # Retrofit base_main_sha + last_partner_edit for F.5 staleness
        # (multi-PR drift tracking, #147). Mirrors alembic
        # 20260605_0001. Must run after the workspace_id ensure because
        # the composite index references workspace_id.
        self._ensure_v2_projects_staleness_columns()

    def _ensure_v2_projects_shelf_column(self) -> None:
        """Add ``shelf_id`` to ``v2_projects`` when missing.

        - Postgres: ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` (idempotent natively)
        - SQLite: catches the ``duplicate column`` OperationalError (IF NOT EXISTS
          not supported by SQLite for ALTER TABLE ADD COLUMN)
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE v2_projects ADD COLUMN IF NOT EXISTS shelf_id TEXT"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE v2_projects ADD COLUMN shelf_id TEXT"
                )
                connection.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" in message:
                    return
                raise

    def _ensure_v2_projects_archived_column(self) -> None:
        """Add ``archived_at`` to ``v2_projects`` when missing.

        Mirrors ``_ensure_v2_projects_shelf_column``. The column is a
        nullable ISO-8601 timestamp: NULL means the project is active,
        a stamp means the user archived it (hidden from the default
        projects list, but fully restorable). Deletion still wins —
        ``list_archived_v2_projects`` filters on ``deleted_at IS NULL``
        so a deleted row never surfaces in the archive view either.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE v2_projects ADD COLUMN IF NOT EXISTS archived_at TEXT"
                )
                connection.commit()
            return
        with self._connect() as connection:
            try:
                connection.execute(
                    "ALTER TABLE v2_projects ADD COLUMN archived_at TEXT"
                )
                connection.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column" in message:
                    return
                raise

    def _ensure_v2_projects_state_columns(self) -> None:
        """Add ``project_state`` / ``priority_order`` / ``roi_score`` columns + index.

        Mirrors :meth:`_ensure_v2_projects_shelf_column` for each
        column individually. ``project_state`` carries a CHECK
        constraint on Postgres (the SQLite path enforces the same
        invariant at the application layer via
        ``project_state.validate_transition``).

        The composite index ``idx_v2_projects_state_workspace`` covers
        the Kanban hot path: ``WHERE workspace_id = ? AND project_state = ?``
        ordered by ``priority_order`` (which is also the dominant
        sort term, so the index can satisfy the ORDER BY directly).
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE v2_projects "
                    "ADD COLUMN IF NOT EXISTS project_state TEXT "
                    "NOT NULL DEFAULT 'pending_review' "
                    "CHECK (project_state IN ("
                    "'pending_review','in_review','approved',"
                    "'rejected','summary_ready'))"
                )
                connection.execute(
                    "ALTER TABLE v2_projects "
                    "ADD COLUMN IF NOT EXISTS priority_order INTEGER"
                )
                connection.execute(
                    "ALTER TABLE v2_projects "
                    "ADD COLUMN IF NOT EXISTS roi_score INTEGER"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_v2_projects_state_workspace "
                    "ON v2_projects (workspace_id, project_state, "
                    "priority_order, roi_score DESC, created_at DESC)"
                )
                connection.commit()
            return
        # SQLite path. Each ADD COLUMN catches "duplicate column" so
        # the retrofit is idempotent across reboots. CHECK constraints
        # can't be added retroactively in SQLite — the project_state
        # column gets a default but no DB-level enum guard;
        # validate_transition enforces it at the application layer.
        with self._connect() as connection:
            for stmt, default in (
                (
                    "ALTER TABLE v2_projects ADD COLUMN project_state TEXT "
                    "NOT NULL DEFAULT 'pending_review'",
                    "pending_review",
                ),
                (
                    "ALTER TABLE v2_projects ADD COLUMN priority_order INTEGER",
                    None,
                ),
                (
                    "ALTER TABLE v2_projects ADD COLUMN roi_score INTEGER",
                    None,
                ),
            ):
                try:
                    connection.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
                # ``default`` is informational — left in the tuple so a
                # future reader sees what the column ships with on
                # Postgres without re-reading the SQL string.
                _ = default
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_v2_projects_state_workspace "
                "ON v2_projects (workspace_id, project_state, priority_order)"
            )
            connection.commit()

    def _ensure_v2_projects_staleness_columns(self) -> None:
        """Add ``base_main_sha`` / ``last_partner_edit`` columns + index.

        Mirrors :meth:`_ensure_v2_projects_state_columns`. Both columns
        are nullable: pre-F.5 projects (no recorded base SHA) self-heal
        the next time the partner opens the PR overlay tree, and
        ``last_partner_edit`` is only stamped once a partner actually
        edits a file in the overlay. The composite index covers a
        future "every open PR built against this SHA" sweep when main
        moves; not used in F.5's per-project route, but cheap to land.
        """
        if self._is_postgres:
            with self._connect() as connection:
                connection.execute(
                    "ALTER TABLE v2_projects "
                    "ADD COLUMN IF NOT EXISTS base_main_sha TEXT"
                )
                connection.execute(
                    "ALTER TABLE v2_projects "
                    "ADD COLUMN IF NOT EXISTS last_partner_edit TEXT"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_v2_projects_base_main_sha "
                    "ON v2_projects (workspace_id, base_main_sha)"
                )
                connection.commit()
            return
        with self._connect() as connection:
            for stmt in (
                "ALTER TABLE v2_projects ADD COLUMN base_main_sha TEXT",
                "ALTER TABLE v2_projects ADD COLUMN last_partner_edit TEXT",
            ):
                try:
                    connection.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_v2_projects_base_main_sha "
                "ON v2_projects (workspace_id, base_main_sha)"
            )
            connection.commit()

    def ensure_project(
        self,
        *,
        project_id: str,
        user_id: str,
        title: str = "",
        project_state: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent upsert — creates a v2_projects row if missing.

        Called by the kickoff and create-topic flows so the project exists
        before any topic references it. ``title`` defaults to a placeholder
        derived from the project_id; rename via ``update_v2_project``.

        ``project_state`` is the v4 review-state-machine column. When
        ``None`` (the default), the INSERT lets the column-level DB
        default decide — that's ``'pending_review'`` for the migration
        path. Callers who know what state the row belongs in (kickoff
        wants ``'approved'``, the orchestrator wants ``'pending_review'``)
        should pass it explicitly so the audit trail is unambiguous.
        """
        existing = self._get_v2_project(project_id)
        if existing is not None:
            # Already owned by this user — fine.
            if existing["user_id"] == user_id:
                return existing
            # Owned by the system user. Historically we silently re-assigned
            # it to any caller who knew the ID — that's audit finding M6: a
            # known (or guessed) seed project_id could be hijacked. Close
            # the escape hatch: only allowlisted seed IDs are ever claimable,
            # and the allowlist is intentionally empty. Once multi-user is
            # live the system should never transfer ownership this way;
            # rehoming seed content should go through an explicit admin path.
            if existing["user_id"] == "user-system":
                if user_id == "user-system":
                    return existing
                if project_id in _SYSTEM_SEED_PROJECT_ALLOWLIST:
                    return existing
                raise PermissionError(
                    f"project {project_id} is owned by the system user and "
                    "cannot be claimed by another caller",
                )
            # Owned by a different real user — always refuse.
            raise PermissionError(
                f"project {project_id} is owned by another user",
            )
        now = now_timestamp()
        payload_title = title.strip() or f"Project {project_id[-6:]}"
        with self._connect() as connection:
            if project_state is None:
                connection.execute(
                    """
                    INSERT INTO v2_projects
                      (project_id, user_id, title, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, user_id, payload_title, "{}", now, now),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO v2_projects
                      (project_id, user_id, title, metadata_json, created_at, updated_at, project_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, user_id, payload_title, "{}", now, now,
                        project_state,
                    ),
                )
            connection.commit()
        return self._get_v2_project(project_id) or {
            "project_id": project_id,
            "user_id": user_id,
            "title": payload_title,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }

    def _get_v2_project(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_id, user_id, title, metadata_json, "
                "created_at, updated_at, deleted_at, archived_at, "
                "shelf_id, workspace_id, project_state, priority_order, "
                "roi_score, base_main_sha, last_partner_edit "
                "FROM v2_projects "
                "WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["metadata"] = json.loads(out.pop("metadata_json") or "{}")
        return out

    def verify_project_ownership(
        self, *, project_id: str, user_id: str | None,
    ) -> bool:
        """Return True iff this user owns the named v2 project.

        SECURITY (PR1): ``user_id=None`` now returns False instead of
        True. The previous behavior was a legacy escape-hatch for
        unscoped test paths but it could be reached in production via
        any code path that forgot to pass a real id, silently opening
        every project to whoever called. API-layer routes already pass
        a real user_id; tests should pass a fixture user. This is a
        true tenancy check.
        """
        if user_id is None:
            return False
        existing = self._get_v2_project(project_id)
        if existing is None:
            return False
        return existing["user_id"] == user_id

    def get_topic_with_ownership(
        self, topic_id: str, *, user_id: str | None,
    ) -> dict[str, Any] | None:
        """Fetch a topic, but return None if the caller doesn't own its project."""
        topic = self.get_topic(topic_id)
        if topic is None:
            return None
        if not self.verify_project_ownership(
            project_id=topic["project_id"], user_id=user_id,
        ):
            return None
        return topic

    def get_decision_with_ownership(
        self, decision_id: str, *, user_id: str | None,
    ) -> dict[str, Any] | None:
        decision = self.get_decision(decision_id)
        if decision is None:
            return None
        if not self.verify_project_ownership(
            project_id=decision["project_id"], user_id=user_id,
        ):
            return None
        return decision

    def get_relationship(self, relationship_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT relationship_id, project_id, source_topic_id, target_topic_id,
                       label, origin, strength, created_at, deleted_at
                FROM relationships WHERE relationship_id = ? AND deleted_at IS NULL
                """,
                (relationship_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_relationship_with_ownership(
        self, relationship_id: str, *, user_id: str | None,
    ) -> dict[str, Any] | None:
        rel = self.get_relationship(relationship_id)
        if rel is None:
            return None
        if not self.verify_project_ownership(
            project_id=rel["project_id"], user_id=user_id,
        ):
            return None
        return rel

    def list_v2_projects(
        self, *, user_id: str, include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Active projects owned by this user.

        ``include_archived=False`` (the default) hides rows that have
        ``archived_at`` set — the projects list, homepage suggestions,
        search, dedupe, and every other caller that shows "your live
        projects" goes through this path. Pass ``include_archived=True``
        when you need the full set (e.g. transfer-anonymous flow).
        """
        # SELECT keeps parity with ``_get_v2_project`` and
        # ``list_v2_workspace_projects`` — every list path must expose
        # ``project_state`` (column, not metadata) so the FE Kanban
        # (``useKanbanData.ts``) sees the same state across surfaces.
        # Pre-#148, this list omitted ``project_state`` and the FE
        # fallback ``project.project_state ?? "pending_review"`` made
        # every home-list card appear stuck in pending_review.
        with self._connect() as connection:
            if include_archived:
                rows = connection.execute(
                    """
                    SELECT project_id, user_id, title, metadata_json,
                           created_at, updated_at, archived_at, shelf_id,
                           workspace_id, project_state, priority_order,
                           roi_score, base_main_sha, last_partner_edit
                    FROM v2_projects
                    WHERE user_id = ? AND deleted_at IS NULL
                    ORDER BY updated_at DESC
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT project_id, user_id, title, metadata_json,
                           created_at, updated_at, archived_at, shelf_id,
                           workspace_id, project_state, priority_order,
                           roi_score, base_main_sha, last_partner_edit
                    FROM v2_projects
                    WHERE user_id = ?
                      AND deleted_at IS NULL
                      AND archived_at IS NULL
                    ORDER BY updated_at DESC
                    """,
                    (user_id,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json") or "{}")
            out.append(payload)
        return out

    def list_archived_v2_projects(
        self, *, user_id: str,
    ) -> list[dict[str, Any]]:
        """Only the archived (and not-deleted) projects for this user.

        Ordered most-recently-archived-first so the archive view leads
        with what the user most likely wants to restore. Deleted rows
        are excluded — once a project is soft-deleted it never surfaces
        in any list, archive or otherwise.
        """
        with self._connect() as connection:
            # See ``list_v2_projects`` for the SELECT-column rationale
            # (#148). ``project_state`` must be on the column list so
            # archived cards render with their last known state instead
            # of falling back to pending_review on the FE.
            rows = connection.execute(
                """
                SELECT project_id, user_id, title, metadata_json,
                       created_at, updated_at, archived_at, shelf_id,
                       workspace_id, project_state, priority_order,
                       roi_score
                FROM v2_projects
                WHERE user_id = ?
                  AND deleted_at IS NULL
                  AND archived_at IS NOT NULL
                ORDER BY archived_at DESC
                """,
                (user_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json") or "{}")
            out.append(payload)
        return out

    # -----------------------------------------------------------------------
    # Recently-deleted recovery (soft-delete with a grace window).
    # -----------------------------------------------------------------------
    # ``delete_v2_project`` only stamps ``deleted_at``; the row stays around
    # until the grace window (configurable via INSPIRA_DELETED_PROJECT_GRACE_DAYS,
    # default 30 days) expires. Within that window the user can:
    #   * see the project on the "Recently deleted" page,
    #   * restore it (clears deleted_at, brings it back to active),
    #   * or purge it (hard-delete now, no further recovery).
    # Outside the window the row is unrecoverable: ``list_recently_deleted_v2_projects``
    # lazily hard-deletes anything older than the grace cutoff before returning.
    def list_recently_deleted_v2_projects(
        self, *, user_id: str,
    ) -> list[dict[str, Any]]:
        """Soft-deleted projects still inside the grace window.

        Lazy purge: every call first hard-deletes any of this user's
        soft-deleted projects whose ``deleted_at`` is older than
        ``grace_days``. The remaining rows are returned with the original
        project envelope plus two derived fields:
          * ``deleted_at`` — the original soft-delete timestamp
          * ``days_remaining`` — int, days left in the grace window
            (0 means "expires today", never negative because expired rows
            were just purged above).
        Ordered most-recently-deleted-first so the user's likely target
        is at the top.
        """
        grace_days = int(self.config.deleted_project_grace_days or 30)
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=grace_days)).isoformat(timespec="seconds")
        with self._connect() as connection:
            # Lazy purge: anything past the grace window is gone for good.
            # Cascade by hand — same children that delete_v2_project marked
            # also need hard-removing here. We don't worry about the other
            # child tables (qna_turns, decisions, …) because they all
            # already filter on a deleted_at column that's still NULL for
            # them — they've never been re-stamped, so leaving rows around
            # is harmless aside from a tiny storage leak.
            expired_rows = connection.execute(
                """
                SELECT project_id FROM v2_projects
                WHERE user_id = ?
                  AND deleted_at IS NOT NULL
                  AND deleted_at < ?
                """,
                (user_id, cutoff),
            ).fetchall()
            for row in expired_rows:
                pid = row["project_id"]
                connection.execute(
                    "DELETE FROM topics WHERE project_id = ?", (pid,),
                )
                connection.execute(
                    "DELETE FROM relationships WHERE project_id = ?", (pid,),
                )
                connection.execute(
                    "DELETE FROM v2_projects WHERE project_id = ?", (pid,),
                )
            if expired_rows:
                connection.commit()

            rows = connection.execute(
                """
                SELECT project_id, user_id, title, metadata_json,
                       created_at, updated_at, deleted_at, archived_at, shelf_id
                FROM v2_projects
                WHERE user_id = ?
                  AND deleted_at IS NOT NULL
                  AND deleted_at >= ?
                ORDER BY deleted_at DESC
                """,
                (user_id, cutoff),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json") or "{}")
            deleted_at_str = payload.get("deleted_at") or ""
            try:
                deleted_dt = datetime.fromisoformat(deleted_at_str)
                if deleted_dt.tzinfo is None:
                    deleted_dt = deleted_dt.replace(tzinfo=timezone.utc)
                expires_at = deleted_dt + timedelta(days=grace_days)
                remaining = (expires_at - now).total_seconds() / 86400.0
                payload["days_remaining"] = max(0, int(remaining))
            except (TypeError, ValueError):
                payload["days_remaining"] = 0
            out.append(payload)
        return out

    def restore_v2_project(
        self, *, project_id: str, user_id: str,
    ) -> dict[str, Any] | str | None:
        """Clear ``deleted_at`` if the row is still inside the grace window.

        Returns the restored project on success.
        Returns ``None`` if not found / not owned by user / not soft-deleted.
        Returns the string ``"expired"`` if the grace window has lapsed —
        the route maps that to HTTP 410 Gone.
        """
        grace_days = int(self.config.deleted_project_grace_days or 30)
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=grace_days)).isoformat(timespec="seconds")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT deleted_at FROM v2_projects
                WHERE project_id = ? AND user_id = ? AND deleted_at IS NOT NULL
                """,
                (project_id, user_id),
            ).fetchone()
            if row is None:
                return None
            deleted_at_str = row["deleted_at"]
            if deleted_at_str and deleted_at_str < cutoff:
                # Past grace — treat as gone. Caller (api layer) returns 410.
                return "expired"
            now_ts = now_timestamp()
            connection.execute(
                """
                UPDATE v2_projects
                SET deleted_at = NULL, updated_at = ?
                WHERE project_id = ? AND user_id = ? AND deleted_at IS NOT NULL
                """,
                (now_ts, project_id, user_id),
            )
            # Best-effort cascade-restore of child rows that delete_v2_project
            # cascade-soft-deleted. Only rows whose deleted_at exactly matches
            # the project's deleted_at are restored; rows that were soft-
            # deleted before the project itself stay deleted.
            connection.execute(
                """
                UPDATE topics
                SET deleted_at = NULL, updated_at = ?
                WHERE project_id = ? AND deleted_at = ?
                """,
                (now_ts, project_id, deleted_at_str),
            )
            connection.execute(
                """
                UPDATE relationships
                SET deleted_at = NULL
                WHERE project_id = ? AND deleted_at = ?
                """,
                (project_id, deleted_at_str),
            )
            connection.commit()
        return self._get_v2_project(project_id)

    def purge_v2_project(self, *, project_id: str, user_id: str) -> bool:
        """Hard-delete an already-soft-deleted project owned by ``user_id``.

        Refuses to purge a live (not-soft-deleted) project — purge is a
        terminal action that can only be called from the Recently Deleted
        view. Returns True on success, False otherwise (not found, not
        owned, or not yet soft-deleted).
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT deleted_at FROM v2_projects
                WHERE project_id = ? AND user_id = ?
                """,
                (project_id, user_id),
            ).fetchone()
            if row is None or row["deleted_at"] is None:
                return False
            connection.execute(
                "DELETE FROM topics WHERE project_id = ?", (project_id,),
            )
            connection.execute(
                "DELETE FROM relationships WHERE project_id = ?", (project_id,),
            )
            cur = connection.execute(
                "DELETE FROM v2_projects WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )
            connection.commit()
            return int(getattr(cur, "rowcount", 0) or 0) > 0

    def create_v2_project(
        self,
        *,
        user_id: str,
        title: str,
        project_state: str = "approved",
    ) -> dict[str, Any]:
        """Create a new v2_projects row.

        ``project_state`` defaults to ``'approved'`` because every human-
        driven entry point — kickoff, example seeds, markdown / JSON
        import — produces a project that's already real work in flight,
        not "awaiting AI review". The orchestrator path
        (``agents.orchestrator``) overrides to ``'pending_review'`` so
        AI-generated canvases land in the correct Kanban column. The
        default is intentionally NOT the column's DB default
        (``'pending_review'``) so application code doesn't silently
        misclassify human work; the DB default is a fallback for any
        path that bypasses this method entirely.
        """
        project_id = f"project-{uuid.uuid4().hex[:12]}"
        project = self.ensure_project(
            project_id=project_id,
            user_id=user_id,
            title=title,
            project_state=project_state,
        )
        # A new project changes the signal set that AI suggestions draw
        # from; drop any cached suggestions for this user so the next
        # suggest call regenerates from current state.
        self.invalidate_cached_suggestions(user_id=user_id)
        self._emit_audit_silent(
            user_id=user_id,
            category="project",
            action="create",
            project_id=project_id,
            subject_id=project_id,
            after={"title": title},
        )
        return project

    def transfer_projects_to_user(
        self, *, old_user_id: str, new_user_id: str,
    ) -> int:
        """Reassign every user-scoped row from ``old_user_id`` to ``new_user_id``.

        Used by the anonymous-to-account transfer flow: after a visitor
        signs up, we move their anonymous-session projects (and every
        child row — topics, relationships, Q&A turns, decisions, summaries,
        audit events, sources) onto their new real account. Runs inside a
        single connection so the transfer is atomic — either every row
        moves or none do.

        Returns the number of v2_projects rows moved. Zero is a valid
        response (idempotent re-run after a prior successful transfer).

        Tables covered:
        - ``v2_projects`` — the project records themselves
        - ``shelves`` — user-owned groupings (if any)
        - ``topics``, ``relationships`` — canvas structure
        - ``qna_turns``, ``decisions``, ``summary_versions``,
          ``consistency_flags``, ``audit_events``, ``sources`` — content
        - ``scaffolds`` — generated code artifacts (paid feature, unlikely
          on anon side but moved for completeness)

        NOT covered — intentional:
        - ``user_credits`` / ``credit_transactions`` / ``user_usage`` —
          anonymous credits don't transfer; signup provisions a fresh
          plan allotment and anon spending history is irrelevant.
        - ``users`` — the anon row stays as a historical record; nothing
          references it after transfer.
        """
        if not old_user_id or not new_user_id or old_user_id == new_user_id:
            return 0
        # Per-table moves. Every table here has a ``user_id`` column via
        # ``_ensure_user_id_columns`` (or was created with one in the
        # v2 schema). Wildly different tables share the same WHERE
        # clause because every one is scoped to ``user_id = ?``.
        user_scoped_tables = (
            "topics",
            "relationships",
            "qna_turns",
            "decisions",
            "consistency_flags",
            "summary_versions",
            "audit_events",
            "sources",
            "shelves",
            "scaffolds",
        )
        with self._connect() as connection:
            # v2_projects is the one we return a count for — the rest
            # are side effects. Soft-deleted rows stay with the old
            # user; the spec asks only for live projects to move.
            result = connection.execute(
                "UPDATE v2_projects SET user_id = ? WHERE user_id = ? AND deleted_at IS NULL",
                (new_user_id, old_user_id),
            )
            projects_moved = int(getattr(result, "rowcount", 0) or 0)
            for table in user_scoped_tables:
                try:
                    connection.execute(
                        f"UPDATE {table} SET user_id = ? WHERE user_id = ?",
                        (new_user_id, old_user_id),
                    )
                except sqlite3.OperationalError as exc:
                    # A DB that predates a given table / column (older
                    # deployment that hasn't run the latest schema init,
                    # or a fresh test DB where the user_id column wasn't
                    # retrofitted because the table didn't exist yet)
                    # shouldn't fail the whole transfer. Skip and move on.
                    msg = str(exc).lower()
                    if "no such table" in msg or "no such column" in msg:
                        continue
                    raise
            connection.commit()
        # New projects in the signal set for the recipient — bust any
        # cached homepage suggestions so the next call regenerates.
        self.invalidate_cached_suggestions(user_id=new_user_id)
        return projects_moved

    def update_v2_project(
        self, *, project_id: str, user_id: str, title: str | None = None,
    ) -> dict[str, Any] | None:
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        if existing["user_id"] != user_id:
            return None
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title.strip() or existing["title"]
        if not updates:
            return existing
        updates["updated_at"] = now_timestamp()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [project_id, user_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE v2_projects SET {set_clause} WHERE project_id = ? AND user_id = ?",
                params,
            )
            connection.commit()
        # Rename is the only user-visible variant of update_v2_project right
        # now — emit a 'rename' action so the Activity feed reads cleanly.
        if "title" in updates and updates["title"] != existing.get("title"):
            self._emit_audit_silent(
                user_id=user_id,
                category="project",
                action="rename",
                project_id=project_id,
                subject_id=project_id,
                before={"title": existing.get("title")},
                after={"title": updates["title"]},
            )
        return self._get_v2_project(project_id)

    # ---------- v4 Kanban + state machine (B1.1 / B3.3) ------------------

    def update_v2_project_state(
        self,
        *,
        project_id: str,
        workspace_id: str,
        actor_user_id: str,
        target_state: str,
        note: str | None = None,
        manual: bool = False,
    ) -> dict[str, Any] | None:
        """Transition a project's review state with a tamper-proof audit row.

        Two paths through this method:

        - ``manual=False`` (the ``/transition`` endpoint) — validates
          the move via :func:`project_state.validate_transition`, runs
          an optimistic UPDATE that pins on the previously-observed
          state, and writes a ``category="project_state"
          action="transition"`` audit row. Cross-column moves null the
          ``priority_order`` because manual reorder is per-column.
        - ``manual=True`` (the ``/manual-state-override`` endpoint) —
          bypasses ``validate_transition`` so an admin can recover from
          any state. ``note`` MUST be a non-empty string (the API
          layer 400s on empty); the audit row records ``manual: True``
          + the note so the override is auditable.

        Returns the updated project dict, or ``None`` if the project
        doesn't exist or doesn't belong to ``workspace_id`` (the API
        layer converts to 404 — never leak which case it was).

        Raises:
            IllegalTransitionError: ``manual=False`` and the transition
                is not in ``LEGAL_TRANSITIONS``. The endpoint catches
                this, writes a ``transition_rejected`` audit row, then
                re-raises so the route returns 409.
            StaleProjectStateError: optimistic UPDATE found zero rows
                — another writer changed the state between our read
                and our write. Surfaces as 409 with a refresh hint.
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        # Workspace tenancy: refuse cross-workspace state moves silently.
        # ``workspace_id`` is nullable on legacy rows but the v4 surface
        # only ever calls this method with a real workspace member, so a
        # mismatch is either misuse or an attack — collapse to 404.
        if existing.get("workspace_id") != workspace_id:
            return None
        current_state = existing.get("project_state") or "pending_review"
        if not manual:
            # Raises IllegalTransitionError; caller decides whether to
            # log the rejection to audit. Letting it propagate keeps the
            # store layer thin.
            validate_transition(current_state, target_state)
        cross_column = current_state != target_state
        now = now_timestamp()
        # Optimistic concurrent-write guard. Two admins racing
        # pending_review->in_review must produce one winner; the loser
        # gets zero rows affected and we surface a stale-state error so
        # their UI refetches. WHERE pins on workspace too so a stale
        # transfer between workspaces (impossible today, defensive) can't
        # silently land in the wrong tenant.
        if cross_column:
            sql = (
                "UPDATE v2_projects SET project_state = ?, "
                "priority_order = NULL, updated_at = ? "
                "WHERE project_id = ? AND project_state = ? "
                "AND workspace_id = ? AND deleted_at IS NULL"
            )
            params = (
                target_state, now, project_id, current_state, workspace_id,
            )
        else:
            sql = (
                "UPDATE v2_projects SET project_state = ?, updated_at = ? "
                "WHERE project_id = ? AND project_state = ? "
                "AND workspace_id = ? AND deleted_at IS NULL"
            )
            params = (
                target_state, now, project_id, current_state, workspace_id,
            )
        with self._connect() as connection:
            cursor = connection.execute(sql, params)
            rows_affected = cursor.rowcount
            connection.commit()
        if rows_affected == 0:
            # Re-read so the caller can show the user the live state.
            raise StaleProjectStateError(
                project_id=project_id, observed=current_state,
            )
        action = "manual_override" if manual else "transition"
        before: dict[str, Any] = {"state": current_state}
        after: dict[str, Any] = {"state": target_state, "manual": manual}
        if note:
            after["note"] = note
        try:
            self.append_audit_event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                category="project_state",
                action=action,
                project_id=project_id,
                subject_id=project_id,
                before=before,
                after=after,
            )
        except Exception as exc:  # noqa: BLE001 — audit must not fail the write
            _log.warning(
                "append_audit_event failed (project_state/%s): %s",
                action, exc,
            )
        return self._get_v2_project(project_id)

    def update_v2_project_priority_order(
        self,
        *,
        project_id: str,
        workspace_id: str,
        actor_user_id: str,
        priority_order: int,
    ) -> dict[str, Any] | None:
        """Persist a within-column manual reorder of a Kanban card.

        Same-column drag in the workspace Kanban writes a sparse 1024-
        step int here. NULL means "use ROI sort"; non-null wins. Audit
        row uses ``action="manual_priority"`` so re-orders show up in
        the workspace audit timeline as distinct from state changes.

        Returns the updated project dict, or ``None`` for missing-or-
        cross-workspace rows (the API layer returns 404).
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        if existing.get("workspace_id") != workspace_id:
            return None
        before_order = existing.get("priority_order")
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET priority_order = ?, updated_at = ? "
                "WHERE project_id = ? AND workspace_id = ? "
                "AND deleted_at IS NULL",
                (priority_order, now, project_id, workspace_id),
            )
            connection.commit()
        try:
            self.append_audit_event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                category="project_state",
                action="manual_priority",
                project_id=project_id,
                subject_id=project_id,
                before={"priority_order": before_order},
                after={"priority_order": priority_order},
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "append_audit_event failed (project_state/manual_priority): %s",
                exc,
            )
        return self._get_v2_project(project_id)

    def list_v2_workspace_projects(
        self,
        *,
        workspace_id: str,
        state: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Workspace-scoped projects, sorted for the Kanban board.

        Sort tuple: ``priority_order ASC NULLS LAST, roi_score DESC
        NULLS LAST, created_at DESC``. Manual reorders are sticky;
        new AI-clustered cards (priority_order NULL) land in their
        ROI position so they don't jump a user's manual order.

        ``state`` filter is optional — the Kanban hits this once with
        ``state=None`` to populate all 5 columns in a single round-trip,
        but the same endpoint can serve narrower polls (e.g. the
        "AI thinking" column refresh).

        SQLite doesn't support ``NULLS LAST`` directly; we synthesise
        it with a guard column in the ORDER BY. Postgres handles it
        natively. The dialect split is hidden in the SQL builder
        below so the caller doesn't need to know.
        """
        if self._is_postgres:
            order_clause = (
                "ORDER BY priority_order ASC NULLS LAST, "
                "roi_score DESC NULLS LAST, created_at DESC"
            )
        else:
            # SQLite: emulate NULLS LAST via guard expressions. NULL
            # sorts to position 1 in ascending sort by default; the
            # ``IS NULL`` guard pushes them to position 1 (last) when
            # we want NULLS LAST on ASC. For DESC NULLS LAST, NULL
            # sorts last in descending order natively, so no guard
            # needed on roi_score.
            order_clause = (
                "ORDER BY (priority_order IS NULL) ASC, "
                "priority_order ASC, "
                "roi_score DESC, created_at DESC"
            )
        archived_clause = (
            "" if include_archived else "AND archived_at IS NULL "
        )
        params: list[Any] = [workspace_id]
        state_clause = ""
        if state is not None:
            state_clause = "AND project_state = ? "
            params.append(state)
        sql = (
            "SELECT project_id, user_id, title, metadata_json, "
            "created_at, updated_at, archived_at, shelf_id, workspace_id, "
            "project_state, priority_order, roi_score "
            "FROM v2_projects "
            "WHERE workspace_id = ? "
            "AND deleted_at IS NULL "
            f"{archived_clause}"
            f"{state_clause}"
            f"{order_clause}"
        )
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json") or "{}")
            out.append(payload)
        return out

    def set_project_domain(self, *, project_id: str, domain: str) -> None:
        """Persist the LLM-inferred domain label into the project's metadata_json.

        Called from the kickoff route after the planner adapter returns so that
        subsequent routes (e.g. the scaffold generator) can gate on domain
        without re-running the LLM. The write is a JSON-level merge: existing
        metadata keys are preserved, only ``domain`` is set/overwritten.
        Idempotent — safe to call multiple times (e.g. on re-kickoff).
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return
        metadata = dict(existing.get("metadata") or {})
        metadata["domain"] = domain
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET metadata_json = ?, updated_at = ? WHERE project_id = ?",
                (json.dumps(metadata), now, project_id),
            )
            connection.commit()

    # ---------- Wave F.5 staleness columns ---------------------------------
    # ``base_main_sha`` snapshots the main-branch SHA the PR overlay was
    # drafted against; ``last_partner_edit`` tracks the most recent edit
    # for context in the staleness response. The setters are paired with
    # the columns added in 20260605_0001_v2_projects_staleness_columns.py.

    def set_project_base_main_sha(
        self, *, project_id: str, base_main_sha: str,
    ) -> dict[str, Any] | None:
        """Record the main SHA this project's overlay was drafted against.

        **Idempotent on the column** — only writes when the row's current
        ``base_main_sha`` is NULL. Once set, subsequent calls are no-ops.
        This preserves the original snapshot SHA across all later
        ``build_overlay_tree`` runs (which would otherwise overwrite the
        baseline every time main moves and silently mark the overlay as
        "fresh against current main" forever).

        Returns the refreshed project row, or ``None`` if the project
        doesn't exist. The returned dict reflects the post-UPDATE state
        — so callers can read back the actual SHA on the row (which may
        be a previously-recorded value, not the one passed in).
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        if existing.get("base_main_sha"):
            # Already snapshot — no-op, return current state untouched.
            return existing
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects "
                "SET base_main_sha = ?, updated_at = ? "
                "WHERE project_id = ? AND base_main_sha IS NULL",
                (base_main_sha, now, project_id),
            )
            connection.commit()
        return self._get_v2_project(project_id)

    def reset_project_base_main_sha(
        self, *, project_id: str, base_main_sha: str,
    ) -> dict[str, Any] | None:
        """Unconditionally overwrite the recorded baseline SHA.

        Distinct from ``set_project_base_main_sha`` (which is
        write-only-when-NULL to preserve the original snapshot across
        overlay rebuilds). Wave F.6's refresh path calls this AFTER a
        successful redraft so the staleness compute path treats the
        post-refresh main SHA as the new baseline — otherwise the
        original snapshot would persist and the banner would never
        clear.

        Returns the refreshed project row, or ``None`` if the project
        doesn't exist.
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects "
                "SET base_main_sha = ?, updated_at = ? "
                "WHERE project_id = ?",
                (base_main_sha, now, project_id),
            )
            connection.commit()
        return self._get_v2_project(project_id)

    def set_project_last_partner_edit(
        self, *, project_id: str, ts: str | None = None,
    ) -> dict[str, Any] | None:
        """Stamp the most recent partner-edit timestamp on the project.

        Used by the staleness response so the UI can show "last edited
        N minutes ago" alongside the drift indicator. Pass ``ts=None``
        (default) to use the current timestamp; tests pass an explicit
        value for determinism.

        Returns the refreshed project row, or ``None`` if the project
        doesn't exist.
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        stamp = ts if ts is not None else now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects "
                "SET last_partner_edit = ?, updated_at = ? "
                "WHERE project_id = ?",
                (stamp, stamp, project_id),
            )
            connection.commit()
        return self._get_v2_project(project_id)

    # ---------- Artifact viewer overlay -----------------------------------
    # The artifact viewer uses ``metadata_json.artifact`` as a chat-history
    # overlay on top of the existing scaffolds table — files are read
    # from the scaffold row by ``latest_scaffold_id``; the overlay just
    # tracks which scaffold is currently displayed and the chat messages.

    def get_v2_project_artifact(
        self, *, project_id: str,
    ) -> dict[str, Any] | None:
        """Return the artifact overlay for the project, or ``None``.

        The overlay shape is:
        ``{version, latest_scaffold_id, model_used, messages: [{role, body, ts}]}``.
        Returns ``None`` when no artifact has been generated yet (so the
        artifact GET endpoint can answer 404).
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        metadata = existing.get("metadata") or {}
        artifact = metadata.get("artifact")
        if not isinstance(artifact, dict):
            return None
        return artifact

    def set_v2_project_artifact(
        self, *, project_id: str, artifact: dict[str, Any],
    ) -> None:
        """Persist the artifact overlay. Other metadata keys preserved.

        Mirrors ``set_project_domain`` — JSON-level merge so
        ``state``, ``orchestrator_run_id``, etc. survive each
        artifact write.
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return
        metadata = dict(existing.get("metadata") or {})
        metadata["artifact"] = artifact
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET metadata_json = ?, updated_at = ? "
                "WHERE project_id = ?",
                (json.dumps(metadata), now, project_id),
            )
            connection.commit()

    def append_artifact_chat_turn(
        self, *, project_id: str, role: str, body: str,
    ) -> dict[str, Any] | None:
        """Append one chat message to ``metadata.artifact.messages``.

        Returns the updated artifact dict, or ``None`` if the project
        has no artifact yet (caller should 409 in that case rather than
        write a half-formed artifact). The artifact dict carries
        ``messages`` as a list of ``{role, body, ts}`` entries; we
        append timestamped at write time.
        """
        existing = self._get_v2_project(project_id)
        if existing is None:
            return None
        metadata = dict(existing.get("metadata") or {})
        artifact = metadata.get("artifact")
        if not isinstance(artifact, dict):
            return None
        artifact = dict(artifact)
        messages = list(artifact.get("messages") or [])
        messages.append({"role": role, "body": body, "ts": now_timestamp()})
        artifact["messages"] = messages
        metadata["artifact"] = artifact
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET metadata_json = ?, updated_at = ? "
                "WHERE project_id = ?",
                (json.dumps(metadata), now, project_id),
            )
            connection.commit()
        return artifact

    # ---------- Artifact comments (Wave F.4) -----------------------------
    #
    # Inline IDE-style comments on generated scaffold code. Anchored to
    # ``(file_path, line_number, line_content_hash)`` so a saved comment
    # survives minor edits to the surrounding code; ``line_content_hash``
    # is a SHA-256 over the line's raw UTF-8 bytes, truncated to 16 hex
    # chars. The FE recomputes the current line's hash at render time and
    # marks the chip "stale" on mismatch — never auto-migrates.
    #
    # Threading is single-level v1: replies all carry the top-level
    # ``parent_comment_id``. FE renders as a flat reply list under the
    # parent.

    @staticmethod
    def _hash_artifact_comment_line(line_content: str) -> str:
        """Stable line-anchor hash: SHA-256 over UTF-8 bytes, first 16 hex."""
        return hashlib.sha256(line_content.encode("utf-8")).hexdigest()[:16]

    def create_artifact_comment(
        self,
        *,
        project_id: str,
        file_path: str,
        line_number: int,
        line_content: str,
        category: str,
        body: str,
        author_user_id: str,
        parent_comment_id: str | None = None,
    ) -> dict[str, Any]:
        if category not in ("question", "concern", "suggest_fix"):
            raise ValueError(
                f"invalid artifact comment category: {category!r}"
            )
        comment_id = f"comment-{uuid.uuid4().hex[:10]}"
        line_content_hash = self._hash_artifact_comment_line(line_content)
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_artifact_comments (
                    comment_id, project_id, file_path, line_number,
                    line_content_hash, category, body, author_user_id,
                    parent_comment_id, resolved_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    comment_id, project_id, file_path, line_number,
                    line_content_hash, category, body, author_user_id,
                    parent_comment_id, now, now,
                ),
            )
            connection.commit()
        return {
            "comment_id": comment_id,
            "project_id": project_id,
            "file_path": file_path,
            "line_number": line_number,
            "line_content_hash": line_content_hash,
            "category": category,
            "body": body,
            "author_user_id": author_user_id,
            "parent_comment_id": parent_comment_id,
            "resolved_at": None,
            "created_at": now,
            "updated_at": now,
        }

    def list_artifact_comments(
        self,
        project_id: str,
        *,
        include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT comment_id, project_id, file_path, line_number, "
            "line_content_hash, category, body, author_user_id, "
            "parent_comment_id, resolved_at, created_at, updated_at "
            "FROM v2_artifact_comments WHERE project_id = ?"
        )
        if not include_resolved:
            query += " AND resolved_at IS NULL"
        query += " ORDER BY file_path, line_number, created_at"
        with self._connect() as connection:
            rows = connection.execute(query, (project_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_artifact_comment(
        self, comment_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT comment_id, project_id, file_path, line_number,
                       line_content_hash, category, body, author_user_id,
                       parent_comment_id, resolved_at, created_at, updated_at
                FROM v2_artifact_comments WHERE comment_id = ?
                """,
                (comment_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_artifact_comment(
        self,
        comment_id: str,
        *,
        actor_user_id: str,
        actor_is_admin: bool = False,
        body: str | None = None,
        resolved: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update body and/or resolved-state on an artifact comment.

        Body edits are gated to the comment author (or a workspace
        admin); resolve toggles are allowed for any workspace member
        — the router enforces membership before getting here.

        Raises ``PermissionError`` when a non-owner non-admin tries to
        edit the body. Returns the updated comment dict, or ``None`` if
        the comment doesn't exist.
        """
        existing = self.get_artifact_comment(comment_id)
        if existing is None:
            return None
        sets: list[str] = []
        params: list[Any] = []
        if body is not None:
            if (
                existing["author_user_id"] != actor_user_id
                and not actor_is_admin
            ):
                raise PermissionError(
                    "only the comment author or a workspace admin "
                    "can edit a comment's body"
                )
            sets.append("body = ?")
            params.append(body)
        if resolved is not None:
            if resolved:
                # Idempotent: if already resolved, keep the original
                # timestamp so "Resolved 3 days ago" stays stable.
                sets.append(
                    "resolved_at = COALESCE(resolved_at, ?)"
                )
                params.append(now_timestamp())
            else:
                sets.append("resolved_at = NULL")
        if not sets:
            return existing
        sets.append("updated_at = ?")
        params.append(now_timestamp())
        params.append(comment_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE v2_artifact_comments SET {', '.join(sets)} "
                "WHERE comment_id = ?",
                tuple(params),
            )
            connection.commit()
        return self.get_artifact_comment(comment_id)

    def delete_v2_project(self, *, project_id: str, user_id: str) -> bool:
        """Soft-delete: also cascade-soft-deletes the topics / relationships."""
        existing = self._get_v2_project(project_id)
        now = now_timestamp()
        with self._connect() as connection:
            cur = connection.execute(
                "UPDATE v2_projects SET deleted_at = ?, updated_at = ? WHERE project_id = ? AND user_id = ? AND deleted_at IS NULL",
                (now, now, project_id, user_id),
            )
            if cur.rowcount == 0:
                connection.commit()
                return False
            connection.execute(
                "UPDATE topics SET deleted_at = ?, updated_at = ? WHERE project_id = ? AND deleted_at IS NULL",
                (now, now, project_id),
            )
            connection.execute(
                "UPDATE relationships SET deleted_at = ? WHERE project_id = ? AND deleted_at IS NULL",
                (now, project_id),
            )
            connection.commit()
        self._emit_audit_silent(
            user_id=user_id,
            category="project",
            action="delete",
            project_id=project_id,
            subject_id=project_id,
            before={"title": (existing or {}).get("title")},
        )
        return True

    def bulk_delete_v2_projects(
        self, *, project_ids: list[str], user_id: str,
    ) -> int:
        """Soft-delete a batch of v2_projects + cascade their topics
        and relationships. Returns the count actually removed.

        Each id is checked individually so cross-tenant ids are
        silently skipped (the WHERE includes user_id) — same
        IDOR hygiene as the single-project delete. Best-effort: a
        per-id failure does not abort the batch.
        """
        if not project_ids:
            return 0
        deleted = 0
        for pid in project_ids:
            if self.delete_v2_project(project_id=pid, user_id=user_id):
                deleted += 1
        return deleted

    def archive_v2_project(
        self, *, project_id: str, user_id: str,
    ) -> dict[str, Any] | None:
        """Stamp ``archived_at`` on this user's project; return the updated row.

        Returns ``None`` when the project doesn't exist, is already
        deleted, or belongs to a different user — the callsite 404s on
        all three to keep project IDs un-enumerable (IDOR hygiene,
        matching the rest of the v2 mutation surface). Re-archiving an
        already-archived row re-stamps the timestamp and is harmless.

        Unlike ``delete_v2_project``, archiving does NOT cascade to
        child rows. Topics, relationships, Q&A turns, and decisions all
        stay as they were — archive is a view-level filter, not a
        destructive operation.

        Share tokens ARE revoked though: keeping a public share link
        live on an archived project contradicts the user's "hide this
        from the world" intent, and QA flagged this as a privacy bug
        (a link pasted into a chat thread stayed live after the owner
        archived the project). Both UPDATEs run inside the same
        connection so either both land or neither does; a reader hitting
        the shared route during the transaction sees a consistent state.
        Unarchiving deliberately does NOT re-activate the token — the
        user can mint a fresh link if they want to re-share, same as
        after an explicit revoke.
        """
        now = now_timestamp()
        with self._connect() as connection:
            cur = connection.execute(
                """
                UPDATE v2_projects
                SET archived_at = ?, updated_at = ?
                WHERE project_id = ? AND user_id = ? AND deleted_at IS NULL
                """,
                (now, now, project_id, user_id),
            )
            affected = int(getattr(cur, "rowcount", 0) or 0)
            if affected > 0:
                # Revoke every active share token on the project inside the
                # same transaction. The ``revoked_at IS NULL`` predicate
                # makes this idempotent — previously-revoked tokens stay
                # put, and a re-archive pass is a no-op on sharing.
                connection.execute(
                    "UPDATE shared_links SET revoked_at = ? "
                    "WHERE project_id = ? AND revoked_at IS NULL",
                    (now, project_id),
                )
            connection.commit()
        if affected == 0:
            return None
        project = self._get_v2_project(project_id)
        self._emit_audit_silent(
            user_id=user_id,
            category="project",
            action="archive",
            project_id=project_id,
            subject_id=project_id,
            after={"title": (project or {}).get("title")},
        )
        return project

    def unarchive_v2_project(
        self, *, project_id: str, user_id: str,
    ) -> dict[str, Any] | None:
        """Clear ``archived_at`` on this user's project; return the updated row.

        Symmetric to ``archive_v2_project``. Unarchiving an already-
        active project is a no-op that still returns the row (so the
        frontend can refresh card state idempotently after a stale
        request).
        """
        now = now_timestamp()
        with self._connect() as connection:
            cur = connection.execute(
                """
                UPDATE v2_projects
                SET archived_at = NULL, updated_at = ?
                WHERE project_id = ? AND user_id = ? AND deleted_at IS NULL
                """,
                (now, project_id, user_id),
            )
            affected = int(getattr(cur, "rowcount", 0) or 0)
            connection.commit()
        if affected == 0:
            return None
        project = self._get_v2_project(project_id)
        self._emit_audit_silent(
            user_id=user_id,
            category="project",
            action="unarchive",
            project_id=project_id,
            subject_id=project_id,
            after={"title": (project or {}).get("title")},
        )
        return project

    def duplicate_v2_project(
        self, *, source_project_id: str, user_id: str,
    ) -> dict[str, Any] | None:
        """Deep-clone a project owned by ``user_id``.

        Copies, in a single connection transaction:
          * the v2_projects row (new id, title suffixed with " (copy)")
          * every active topic (positions, origins, metadata preserved;
            new topic_ids, old→new map remembered for ref rewiring)
          * every active relationship (with mapped source/target topic ids)
          * every decision, open_question, risk/assumption (with mapped topic_id)
          * every qna_turn (with mapped topic_id; parent_turn_id rewired
            to the new turn id when the parent is part of the copy)

        Intentionally NOT copied:
          * shelf_id — the duplicate starts on the implicit "Unfiled" shelf.
            A user who duplicates a project is usually comparing-and-diverging
            rather than cloning-into-the-same-group, and shelving is cheap
            to redo.
          * shared_links — capability tokens are scoped to the original; a
            new project must mint its own token through the share flow.
            Carrying tokens across would silently leak read access.
          * consistency_flags, summary_versions, audit_log, context_sources,
            source_references, scaffolds — these are artefacts of the
            original project's history / session rather than its authored
            content. They don't belong in a fresh copy that the user will
            iterate on independently.

        Soft-deleted topics / relationships / etc. in the source are NOT
        carried over — only active rows land in the duplicate. The deep
        copy preserves ``origin`` values (planner_initial vs user_manual
        vs planner_inferred vs user_drawn) because the author's provenance
        is their own record; the duplicate is a fork of their work, not
        a fresh blank canvas.

        IDOR: the caller MUST pass ``user_id``. If the source project
        does not exist OR is not owned by ``user_id``, returns None (so
        the HTTP layer can surface a uniform 404 without leaking
        project-exists information to someone probing IDs).
        """
        source = self._get_v2_project(source_project_id)
        if source is None or source.get("user_id") != user_id:
            return None

        new_project_id = f"project-{uuid.uuid4().hex[:12]}"
        now = now_timestamp()
        original_title = source.get("title") or "Untitled project"
        new_title = f"{original_title} (copy)"
        new_metadata_json = json.dumps(source.get("metadata") or {})

        with self._connect() as connection:
            # 1. Insert the new project row. shelf_id is deliberately NULL
            #    (the duplicate starts on Unfiled, see docstring).
            connection.execute(
                """
                INSERT INTO v2_projects
                  (project_id, user_id, title, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_project_id, user_id, new_title, new_metadata_json, now, now),
            )

            # 2. Topics — build old→new id map for downstream rewiring.
            topic_rows = connection.execute(
                """
                SELECT topic_id, title, icon, position_x, position_y, status,
                       order_index, origin, metadata_json, created_at, updated_at
                FROM topics
                WHERE project_id = ? AND deleted_at IS NULL
                """,
                (source_project_id,),
            ).fetchall()
            topic_id_map: dict[str, str] = {}
            for row in topic_rows:
                old_topic_id = row["topic_id"]
                new_topic_id = f"topic-{uuid.uuid4().hex[:10]}"
                topic_id_map[old_topic_id] = new_topic_id
                connection.execute(
                    """
                    INSERT INTO topics
                      (topic_id, project_id, title, icon, position_x, position_y,
                       status, order_index, origin, metadata_json,
                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_topic_id,
                        new_project_id,
                        row["title"],
                        row["icon"],
                        row["position_x"],
                        row["position_y"],
                        row["status"],
                        row["order_index"],
                        row["origin"],
                        row["metadata_json"] or "{}",
                        now,
                        now,
                    ),
                )

            # 3. Relationships — keep only those whose BOTH endpoints were
            #    copied (an orphan pointing at a soft-deleted topic is
            #    already inconsistent in the source; don't propagate it).
            rel_rows = connection.execute(
                """
                SELECT source_topic_id, target_topic_id, label, origin, strength
                FROM relationships
                WHERE project_id = ? AND deleted_at IS NULL
                """,
                (source_project_id,),
            ).fetchall()
            for row in rel_rows:
                src_new = topic_id_map.get(row["source_topic_id"])
                tgt_new = topic_id_map.get(row["target_topic_id"])
                if not src_new or not tgt_new:
                    continue
                connection.execute(
                    """
                    INSERT INTO relationships
                      (relationship_id, project_id, source_topic_id, target_topic_id,
                       label, origin, strength, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"rel-{uuid.uuid4().hex[:10]}",
                        new_project_id,
                        src_new,
                        tgt_new,
                        row["label"],
                        row["origin"],
                        row["strength"],
                        now,
                    ),
                )

            # 4a. Decisions (excluding retracted — they're soft-deleted).
            decision_rows = connection.execute(
                """
                SELECT decision_id, topic_id, statement, rationale, status,
                       source_turn_id, proposed_by, confirmed_by_user_id
                FROM decisions
                WHERE project_id = ? AND status != 'retracted'
                """,
                (source_project_id,),
            ).fetchall()
            for row in decision_rows:
                new_topic_id = topic_id_map.get(row["topic_id"])
                if not new_topic_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO decisions
                      (decision_id, topic_id, project_id, statement, rationale,
                       status, source_turn_id, proposed_by, confirmed_by_user_id,
                       created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"dec-{uuid.uuid4().hex[:10]}",
                        new_topic_id,
                        new_project_id,
                        row["statement"],
                        row["rationale"],
                        row["status"],
                        # source_turn_id refers to a turn_id in the SOURCE
                        # project. We rewire it to the new turn_id below if
                        # it's part of the copy; for now drop the pointer so
                        # we don't leak cross-project references.
                        None,
                        row["proposed_by"],
                        row["confirmed_by_user_id"],
                        now,
                        now,
                    ),
                )

            # 4b. Open questions — defensive copy (table is declared but
            #     no production write path targets it yet; keep the copy
            #     flow correct so future callers inherit the behaviour).
            try:
                oq_rows = connection.execute(
                    """
                    SELECT topic_id, text, status, answer_turn_id
                    FROM open_questions
                    WHERE project_id = ?
                    """,
                    (source_project_id,),
                ).fetchall()
            except Exception:
                oq_rows = []
            for row in oq_rows:
                new_topic_id = topic_id_map.get(row["topic_id"])
                if not new_topic_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO open_questions
                      (question_id, topic_id, project_id, text, status,
                       answer_turn_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"oq-{uuid.uuid4().hex[:10]}",
                        new_topic_id,
                        new_project_id,
                        row["text"],
                        row["status"],
                        None,  # answer_turn_id would reference a source turn
                        now,
                        now,
                    ),
                )

            # 4c. Risks / assumptions — same defensive-copy rationale as 4b.
            try:
                ra_rows = connection.execute(
                    """
                    SELECT topic_id, kind, text, severity, status
                    FROM risks_assumptions
                    WHERE project_id = ?
                    """,
                    (source_project_id,),
                ).fetchall()
            except Exception:
                ra_rows = []
            for row in ra_rows:
                new_topic_id = topic_id_map.get(row["topic_id"])
                if not new_topic_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO risks_assumptions
                      (risk_id, topic_id, project_id, kind, text, severity,
                       status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"risk-{uuid.uuid4().hex[:10]}",
                        new_topic_id,
                        new_project_id,
                        row["kind"],
                        row["text"],
                        row["severity"],
                        row["status"],
                        now,
                        now,
                    ),
                )

            # 5. Q&A turns. Copy in order_index order so the parent link can
            #    rewire to a new turn id that already exists in the target.
            #    Cached per-turn flags (suggested_responses, attachments) are
            #    preserved; bookkeeping that the duplicate shouldn't inherit
            #    (embeddings / checkpoints) lives on topics.metadata, which
            #    the topic-copy step already excluded if present — and this
            #    table doesn't store embeddings/checkpoints directly anyway.
            turn_rows = connection.execute(
                """
                SELECT turn_id, topic_id, role, order_index, body,
                       why_this_matters, action, suggested_responses_json,
                       status, parent_turn_id, attachments_json, created_at
                FROM qna_turns
                WHERE project_id = ?
                ORDER BY topic_id, order_index
                """,
                (source_project_id,),
            ).fetchall()
            turn_id_map: dict[str, str] = {}
            for row in turn_rows:
                new_topic_id = topic_id_map.get(row["topic_id"])
                if not new_topic_id:
                    continue
                old_turn_id = row["turn_id"]
                new_turn_id = f"turn-{uuid.uuid4().hex[:10]}"
                turn_id_map[old_turn_id] = new_turn_id
                parent_old = row["parent_turn_id"]
                parent_new = turn_id_map.get(parent_old) if parent_old else None
                connection.execute(
                    """
                    INSERT INTO qna_turns
                      (turn_id, topic_id, project_id, role, order_index, body,
                       why_this_matters, action, suggested_responses_json,
                       status, parent_turn_id, attachments_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_turn_id,
                        new_topic_id,
                        new_project_id,
                        row["role"],
                        row["order_index"],
                        row["body"],
                        row["why_this_matters"],
                        row["action"],
                        row["suggested_responses_json"],
                        row["status"],
                        parent_new,
                        row["attachments_json"],
                        now,
                    ),
                )

            connection.commit()

        # Bust cached homepage suggestions — new project, new signal set.
        self.invalidate_cached_suggestions(user_id=user_id)

        duplicated = self._get_v2_project(new_project_id)
        return duplicated

    # ---------- Shelves --------------------------------------------------
    #
    # A shelf is a user-owned named container for grouping related projects.
    # A project can sit on at most one shelf; ``shelf_id=NULL`` on the
    # project row means the project is on the implicit "Unfiled" shelf
    # (which is never materialised as an actual row). Shelves are soft-
    # deleted — delete un-shelves the member projects rather than cascading
    # to project deletion.

    def _row_to_shelf(self, row: Any) -> dict[str, Any]:
        return dict(row)

    def _get_shelf(self, shelf_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT shelf_id, user_id, name, sort_order,
                       created_at, updated_at, deleted_at
                FROM shelves
                WHERE shelf_id = ? AND deleted_at IS NULL
                """,
                (shelf_id,),
            ).fetchone()
        return self._row_to_shelf(row) if row else None

    def list_shelves(self, *, user_id: str) -> list[dict[str, Any]]:
        """Active shelves owned by this user, with project_count derived.

        Ordered by sort_order ascending, then name. project_count reflects
        only active (non-soft-deleted) projects — a shelf full of deleted
        projects renders as empty, not as a ghost count.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.shelf_id, s.user_id, s.name, s.sort_order,
                       s.created_at, s.updated_at,
                       COUNT(p.project_id) AS project_count
                FROM shelves AS s
                LEFT JOIN v2_projects AS p
                    ON p.shelf_id = s.shelf_id
                    AND p.user_id = s.user_id
                    AND p.deleted_at IS NULL
                WHERE s.user_id = ? AND s.deleted_at IS NULL
                GROUP BY s.shelf_id
                ORDER BY s.sort_order ASC, s.name ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_shelf(self, *, user_id: str, name: str) -> dict[str, Any]:
        """Create a new shelf for this user. Returns the new shelf dict.

        Caller is responsible for validating ``name`` length / non-empty —
        the store accepts whatever it's given. ``sort_order`` defaults to
        the highest-current + 1 so a new shelf lands at the end of the
        user's shelf list.
        """
        shelf_id = f"shelf-{uuid.uuid4().hex[:12]}"
        now = now_timestamp()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order "
                "FROM shelves WHERE user_id = ? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()
            sort_order = int(row["next_order"]) if row else 0
            connection.execute(
                """
                INSERT INTO shelves
                  (shelf_id, user_id, name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (shelf_id, user_id, name, sort_order, now, now),
            )
            connection.commit()
        return {
            "shelf_id": shelf_id,
            "user_id": user_id,
            "name": name,
            "sort_order": sort_order,
            "created_at": now,
            "updated_at": now,
            "project_count": 0,
        }

    def update_shelf(
        self,
        *,
        shelf_id: str,
        user_id: str,
        name: str | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any] | None:
        """Rename / reorder a shelf. Returns the updated shelf dict or None.

        Returns None when the shelf doesn't exist OR when it belongs to
        another user — callers should not distinguish those cases (route
        layer maps None to 404 and keeps user IDs un-enumerable).
        """
        existing = self._get_shelf(shelf_id)
        if existing is None or existing["user_id"] != user_id:
            return None
        updates: dict[str, Any] = {}
        if name is not None:
            trimmed = name.strip()
            if trimmed:
                updates["name"] = trimmed
        if sort_order is not None:
            updates["sort_order"] = int(sort_order)
        if not updates:
            return existing
        updates["updated_at"] = now_timestamp()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [shelf_id, user_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE shelves SET {set_clause} "
                "WHERE shelf_id = ? AND user_id = ? AND deleted_at IS NULL",
                params,
            )
            connection.commit()
        return self._get_shelf(shelf_id)

    def delete_shelf(self, *, shelf_id: str, user_id: str) -> bool:
        """Soft-delete a shelf; unshelve its member projects.

        Returns True on success, False when the shelf is absent or not
        owned. Member projects have their ``shelf_id`` reset to NULL so
        they fall onto the implicit "Unfiled" shelf — we never delete
        projects as a side effect.
        """
        existing = self._get_shelf(shelf_id)
        if existing is None or existing["user_id"] != user_id:
            return False
        now = now_timestamp()
        with self._connect() as connection:
            # Un-shelve member projects first — doing this before the
            # shelf's soft-delete keeps the FK-less "shelf_id" pointer
            # self-consistent at every point in time.
            connection.execute(
                "UPDATE v2_projects SET shelf_id = NULL, updated_at = ? "
                "WHERE shelf_id = ? AND user_id = ? AND deleted_at IS NULL",
                (now, shelf_id, user_id),
            )
            cursor = connection.execute(
                "UPDATE shelves SET deleted_at = ?, updated_at = ? "
                "WHERE shelf_id = ? AND user_id = ? AND deleted_at IS NULL",
                (now, now, shelf_id, user_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def move_project_to_shelf(
        self,
        *,
        project_id: str,
        user_id: str,
        shelf_id: str | None,
    ) -> dict[str, Any] | None:
        """Assign ``project_id`` to ``shelf_id`` (or None to un-shelve).

        Ownership-checked on BOTH the project and the shelf: a user can
        only move their own projects onto their own shelves. Returns the
        updated project dict on success, or None when either ownership
        check fails (the route maps None to 404).
        """
        project = self._get_v2_project(project_id)
        if project is None or project["user_id"] != user_id:
            return None
        if shelf_id is not None:
            shelf = self._get_shelf(shelf_id)
            if shelf is None or shelf["user_id"] != user_id:
                return None
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET shelf_id = ?, updated_at = ? "
                "WHERE project_id = ? AND user_id = ? AND deleted_at IS NULL",
                (shelf_id, now, project_id, user_id),
            )
            connection.commit()
        return self._get_v2_project(project_id)

    # ---------- user_id column retrofit (idempotent) --------------------

    def _ensure_user_id_columns(self) -> None:
        """Add ``user_id`` columns to v2 tables when missing.

        - Postgres: ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` per table.
        - SQLite: ``ALTER TABLE ADD COLUMN`` errors on duplicate; we catch
          and ignore ``duplicate column`` / ``no such table`` per table.
        """
        tables_with_user = (
            "topics",
            "relationships",
            "qna_turns",
            "decisions",
            "consistency_flags",
            "summary_versions",
            "audit_events",
            "sources",
        )
        if self._is_postgres:
            # ``ALTER TABLE IF EXISTS`` matches the alembic baseline so the two
            # stay in lock-step. The retrofit list intentionally names two
            # tables that are never actually created (``audit_events``,
            # ``sources``) — without IF EXISTS those relations abort the
            # transaction on Postgres and none of the real tables get the
            # ``user_id`` column, which previously caused a
            # health-degraded incident in production.
            with self._connect() as connection:
                for table in tables_with_user:
                    connection.execute(
                        f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS"
                        f" user_id TEXT NOT NULL DEFAULT 'user-system'",
                    )
                connection.commit()
            return
        with self._connect() as connection:
            for table in tables_with_user:
                try:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT 'user-system'",
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower() and "no such table" not in str(exc).lower():
                        raise
            connection.commit()

    def get_failed_migrations(self) -> set[str]:
        """Names of any startup column-retrofit migrations that raised.

        Empty in the happy path. When non-empty, the API health endpoint
        flips its `status` to `"degraded"` and surfaces this set so an
        operator can see which retrofits to run manually.
        """
        return set(self._failed_migrations)

    def health(self) -> dict[str, Any]:
        failed = self.get_failed_migrations()
        return {
            "status": "degraded" if failed else "ok",
            "failed_migrations": sorted(failed),
            "storage_root": str(self.config.storage_root),
            "db_path": str(self.config.db_path),
            "sessions_root": str(self.config.sessions_root),
            "artifacts_root": str(self.config.artifacts_root),
            "generated_at": now_timestamp(),
        }

    def list_projects(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        # user_id reserved for v1.1 when the v1 `projects` table gains the
        # column. For now the v1 surface stays global and returns the seed.
        _ = user_id
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_id, title, summary, stage, owner, metadata_json, created_at, updated_at FROM projects ORDER BY created_at"
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json"))
            items.append(payload)
        return items

    def list_sessions(
        self, project_id: str | None = None, *, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = user_id
        query = "SELECT session_id, project_id, title, objective, status, transcript_path, metadata_json, created_at, updated_at FROM sessions"
        params: tuple[Any, ...] = ()
        if project_id:
            query += " WHERE project_id = ?"
            params = (project_id,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json"))
            items.append(payload)
        return items

    def list_artifacts(
        self, project_id: str | None = None, *, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = user_id
        query = "SELECT artifact_id, project_id, session_id, artifact_type, title, status, artifact_path, metadata_json, created_at, updated_at FROM artifacts"
        params: tuple[Any, ...] = ()
        if project_id:
            query += " WHERE project_id = ?"
            params = (project_id,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json"))
            items.append(payload)
        return items

    def create_session(
        self,
        *,
        project_id: str,
        title: str,
        objective: str,
        mode: str = "interview",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        created_at = now_timestamp()
        session_id = f"session-{uuid.uuid4().hex[:10]}"
        transcript_path = self.config.sessions_root / f"{session_id}.md"
        transcript_path.write_text(f"# {title}\n\nObjective: {objective}\n", encoding="utf-8")
        payload = {
            "session_id": session_id,
            "project_id": project_id,
            "title": title,
            "objective": objective,
            "status": "active",
            "transcript_path": str(transcript_path),
            "metadata": {"mode": mode, "created_via": "api"},
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (session_id, project_id, title, objective, status, transcript_path, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["session_id"],
                    payload["project_id"],
                    payload["title"],
                    payload["objective"],
                    payload["status"],
                    payload["transcript_path"],
                    json.dumps(payload["metadata"]),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
            connection.commit()
        return payload

    # ================================================================
    # v2 CRUD — Inspira canvas-first model.
    # These operate on the v2 tables declared in _initialize_v2_schema.
    # Pure CRUD stubs: they persist rows and return dicts. Higher-level
    # domain logic (planner integration, consistency-check dispatch,
    # summary regeneration triggers) lives in the app layer, not here.
    # Schema source of truth: docs/architecture/data-model.md.
    # ================================================================

    # ---------- Topics ----------

    # Color coding on topics: users can tag a topic with one of a small
    # fixed palette to visually group related topics on the canvas. The
    # slug is stored under ``metadata_json["color"]`` (no new column — the
    # JSON blob is already flexible and this keeps the schema stable) and
    # surfaced as a top-level ``color`` field on every topic dict handed
    # back to the API layer. ``None`` / absent means "no color set", which
    # the frontend renders as the default ink border.
    TOPIC_COLOR_ALLOWLIST = frozenset({"sage", "rust", "gold", "ink", "paper"})

    @staticmethod
    def _with_topic_color(topic: dict[str, Any]) -> dict[str, Any]:
        """Populate the top-level ``color`` field on a topic dict.

        Reads ``metadata["color"]`` and promotes it to ``topic["color"]``
        so callers never need to dig into the metadata blob. A missing or
        non-allowlisted value is normalized to ``None`` so clients see a
        consistent shape. Mutates the passed dict and returns it for
        convenience.
        """
        metadata = topic.get("metadata") or {}
        raw = metadata.get("color")
        topic["color"] = (
            raw if isinstance(raw, str) and raw in PlanningStudioStore.TOPIC_COLOR_ALLOWLIST
            else None
        )
        return topic

    def create_topic(
        self,
        *,
        project_id: str,
        title: str,
        icon: str,
        position_x: float = 0.0,
        position_y: float = 0.0,
        origin: str = "user_manual",
        order_index: int = 0,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        topic_id = f"topic-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        payload = {
            "topic_id": topic_id,
            "project_id": project_id,
            "title": title,
            "icon": icon,
            "position_x": position_x,
            "position_y": position_y,
            "status": "empty",
            "order_index": order_index,
            "origin": origin,
            "metadata": metadata or {},
            # New topics start with no private note (NULL). Populated only
            # by the /private-notes endpoint; never by planner flows.
            "private_notes": None,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO topics (topic_id, project_id, title, icon, position_x, position_y,
                                     status, order_index, origin, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["topic_id"], payload["project_id"], payload["title"], payload["icon"],
                    payload["position_x"], payload["position_y"], payload["status"],
                    payload["order_index"], payload["origin"], json.dumps(payload["metadata"]),
                    payload["created_at"], payload["updated_at"],
                ),
            )
            connection.commit()
        if user_id:
            self._emit_audit_silent(
                user_id=user_id,
                category="topic",
                action="create",
                project_id=project_id,
                subject_id=topic_id,
                after={"title": title, "icon": icon},
            )
        return self._with_topic_color(payload)

    def list_topics(
        self, *, project_id: str, include_deleted: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = user_id
        query = (
            "SELECT topic_id, project_id, title, icon, position_x, position_y, status, "
            "order_index, origin, metadata_json, private_notes, created_at, updated_at, deleted_at "
            "FROM topics WHERE project_id = ?"
        )
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        query += " ORDER BY order_index, created_at"
        with self._connect() as connection:
            rows = connection.execute(query, (project_id,)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            items.append(self._with_topic_color(item))
        return items

    def update_topic(
        self, topic_id: str, *, user_id: str | None = None, **fields: Any,
    ) -> dict[str, Any] | None:
        _ = user_id
        allowed = {
            "title", "icon", "position_x", "position_y", "status",
            "order_index", "metadata_json", "deleted_at",
        }
        if "metadata" in fields:
            fields["metadata_json"] = json.dumps(fields.pop("metadata"))
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_topic(topic_id)
        updates["updated_at"] = now_timestamp()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE topics SET {set_clause} WHERE topic_id = ?",
                (*updates.values(), topic_id),
            )
            connection.commit()
        return self.get_topic(topic_id)

    def get_topic(self, topic_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
        _ = user_id
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT topic_id, project_id, title, icon, position_x, position_y, status,
                       order_index, origin, metadata_json, private_notes, created_at, updated_at, deleted_at
                FROM topics WHERE topic_id = ?
                """,
                (topic_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return self._with_topic_color(item)

    def update_topic_checkpoints(
        self,
        topic_id: str,
        user_id: str | None,
        checkpoints: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Persist checkpoint list into topic.metadata.checkpoints.

        ``checkpoints`` is the full merged list (not a delta). Each entry
        is ``{id, question, status, answered_in_turn_id?}``. The metadata
        blob is read, updated, and written back atomically within the
        connection context so concurrent writes don't clobber each other.

        Returns the updated topic row (with ``metadata`` key populated),
        or None if the topic doesn't exist.
        """
        topic = self.get_topic(topic_id)
        if topic is None:
            return None
        meta = topic.get("metadata") or {}
        meta["checkpoints"] = checkpoints
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE topics SET metadata_json = ?, updated_at = ? WHERE topic_id = ?",
                (json.dumps(meta), now, topic_id),
            )
            connection.commit()
        return self.get_topic(topic_id)

    def update_topic_private_notes(
        self,
        topic_id: str,
        user_id: str | None,
        notes: str | None,
    ) -> dict[str, Any] | None:
        """Persist private_notes on a topic — user-only, never sent to the LLM.

        IDOR check: ownership is verified against ``user_id``. A caller who
        does not own the topic's project sees ``None`` (same shape as a
        missing topic) so this cannot be used to probe for topic_ids that
        belong to other users.

        Normalization:
        - ``notes is None`` → store NULL (clears the note).
        - ``notes == ""``   → store NULL (empty string means "clear").
        - Any other string  → stored verbatim; callers are free to enforce
          length bounds at the HTTP edge.

        Returns the updated topic row, or None if the topic doesn't exist
        or the caller isn't the owner.
        """
        # Load-through ownership check. ``get_topic_with_ownership`` returns
        # None for both "doesn't exist" and "wrong owner" — the endpoint
        # layer maps both to 404 so we never leak which case hit.
        topic = self.get_topic_with_ownership(topic_id, user_id=user_id)
        if topic is None:
            return None
        # Empty string == cleared. Anything else is stored verbatim.
        normalized: str | None = notes if notes else None
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE topics SET private_notes = ?, updated_at = ? WHERE topic_id = ?",
                (normalized, now, topic_id),
            )
            connection.commit()
        return self.get_topic(topic_id)

    def duplicate_topic(
        self, topic_id: str, *, user_id: str,
    ) -> dict[str, Any] | None:
        """Create a sibling topic from ``topic_id`` with " (copy)" suffix.

        Unlike ``duplicate_v2_project``, this is a SHALLOW duplication:
        only a new topic row is written. No relationships, decisions,
        Q&A turns, or private notes are carried over — the new topic
        starts as a fresh empty sibling sitting offset +40px/+40px from
        the source's position. The offset is baked in here so the new
        card doesn't render directly on top of the source (it would
        still be bumped by the canvas overlap resolver, but paying the
        extra eye-jumps is unnecessary when we can place it sensibly
        up-front).

        IDOR: the caller MUST pass ``user_id``. If the source topic
        does not exist OR belongs to a project not owned by ``user_id``,
        returns None so the HTTP layer can surface a uniform 404.

        Returns the newly-created topic row (same shape as
        ``create_topic`` and ``get_topic``) or None.
        """
        source = self.get_topic_with_ownership(topic_id, user_id=user_id)
        if source is None:
            return None
        duplicate = self.create_topic(
            project_id=source["project_id"],
            title=f"{source['title']} (copy)",
            icon=source["icon"],
            position_x=float(source["position_x"]) + 40.0,
            position_y=float(source["position_y"]) + 40.0,
            # Order-index is a secondary sort key; leave at 0 so the copy
            # lands adjacent to other user-manual topics on list queries.
            order_index=0,
            # The copy is explicitly a user-initiated fork — use the
            # user_manual origin regardless of the source's provenance.
            origin="user_manual",
            user_id=user_id,
        )
        return duplicate

    def update_topic_color(
        self,
        topic_id: str,
        user_id: str | None,
        color: str | None,
    ) -> dict[str, Any] | None:
        """Persist a color tag on a topic's ``metadata_json["color"]`` field.

        ``color`` must be either ``None`` (clears the color) or one of the
        allowlisted slugs in ``TOPIC_COLOR_ALLOWLIST``. Any other value
        raises ``ValueError`` — callers at the HTTP edge translate that to
        a 400.

        IDOR: uses ``get_topic_with_ownership`` so a caller who doesn't own
        the topic's project sees ``None`` — same shape as a missing topic —
        and the route layer maps both to 404.

        The metadata dict is read, mutated, and written back inside a single
        connection context so a concurrent write can't silently clobber the
        color (or the reverse — a concurrent checkpoint update can't lose
        the color we just set). When ``color`` is ``None`` we delete the
        ``color`` key from metadata rather than storing ``null`` so the
        blob stays minimal.
        """
        if color is not None and color not in self.TOPIC_COLOR_ALLOWLIST:
            raise ValueError(
                f"invalid topic color {color!r}; allowed: "
                f"{sorted(self.TOPIC_COLOR_ALLOWLIST)}",
            )
        topic = self.get_topic_with_ownership(topic_id, user_id=user_id)
        if topic is None:
            return None
        meta = topic.get("metadata") or {}
        if color is None:
            meta.pop("color", None)
        else:
            meta["color"] = color
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE topics SET metadata_json = ?, updated_at = ? WHERE topic_id = ?",
                (json.dumps(meta), now, topic_id),
            )
            connection.commit()
        return self.get_topic(topic_id)

    # ---------- Relationships ----------

    def create_relationship(  # noqa: D401
        self,
        *,
        project_id: str,
        source_topic_id: str,
        target_topic_id: str,
        label: str | None = None,
        origin: str = "user_drawn",
        strength: str = "confirmed",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        relationship_id = f"rel-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        payload = {
            "relationship_id": relationship_id,
            "project_id": project_id,
            "source_topic_id": source_topic_id,
            "target_topic_id": target_topic_id,
            "label": label,
            "origin": origin,
            "strength": strength,
            "created_at": now,
            "deleted_at": None,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relationships
                    (relationship_id, project_id, source_topic_id, target_topic_id,
                     label, origin, strength, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, source_topic_id, target_topic_id) DO NOTHING
                """,
                (
                    relationship_id, project_id, source_topic_id, target_topic_id,
                    label, origin, strength, now,
                ),
            )
            connection.commit()
        if user_id:
            self._emit_audit_silent(
                user_id=user_id,
                category="relationship",
                action="create",
                project_id=project_id,
                subject_id=relationship_id,
                after={"label": label, "source_topic_id": source_topic_id,
                       "target_topic_id": target_topic_id},
            )
        return payload

    def delete_topic(self, topic_id: str, *, user_id: str | None = None) -> bool:
        """Soft-delete a topic and cascade-soft-delete its relationships.

        Topic's Q&A turns, decisions, open questions, risks/assumptions,
        and source references stay in the DB for audit — they're simply
        orphaned. Listings exclude them via the topic's deleted_at
        filter. Hard-delete (actual row removal) happens in a later pass
        against the grace-window purge job.

        Returns True if the topic was active and was soft-deleted;
        False if the topic didn't exist or was already deleted.
        """
        # Snapshot title + project_id before the soft-delete for the
        # audit record. Safe to ignore if the row is already gone —
        # we return False anyway.
        existing = self.get_topic(topic_id) if user_id else None
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE topics SET deleted_at = ?, updated_at = ? "
                "WHERE topic_id = ? AND deleted_at IS NULL",
                (now, now, topic_id),
            )
            if cursor.rowcount == 0:
                connection.commit()
                return False
            # Cascade: soft-delete any relationship touching this topic.
            connection.execute(
                "UPDATE relationships SET deleted_at = ? "
                "WHERE (source_topic_id = ? OR target_topic_id = ?) "
                "AND deleted_at IS NULL",
                (now, topic_id, topic_id),
            )
            connection.commit()
        if user_id and existing:
            self._emit_audit_silent(
                user_id=user_id,
                category="topic",
                action="delete",
                project_id=existing.get("project_id"),
                subject_id=topic_id,
                before={"title": existing.get("title")},
            )
        return True

    def update_relationship_label(
        self,
        *,
        relationship_id: str,
        user_id: str | None,
        label: str | None,
    ) -> dict[str, Any] | None:
        """Persist a new label on an existing relationship.

        ``label`` may be ``None`` (clears the label so the edge renders
        unlabeled) or a non-empty string (replaces the existing label).
        Empty strings are normalized to ``None`` at the route layer.

        Returns the updated relationship row, or ``None`` if the
        relationship doesn't exist, is already soft-deleted, or
        belongs to a project the caller doesn't own. The route
        layer maps ``None`` to a 404.

        Emits a ``relationship_relabeled`` audit-log entry on success
        capturing the before+after label.
        """
        # Read existing row + project_id for the ownership check + audit.
        with self._connect() as c:
            row = c.execute(
                "SELECT project_id, label, source_topic_id, target_topic_id "
                "FROM relationships "
                "WHERE relationship_id = ? AND deleted_at IS NULL",
                (relationship_id,),
            ).fetchone()
            existing = dict(row) if row else None
        if existing is None:
            return None
        if user_id and not self.verify_project_ownership(
            project_id=existing["project_id"], user_id=user_id,
        ):
            return None
        # No-op short-circuit: same label as before. Skip the write
        # AND the audit row so a UI-driven cancel-then-resave doesn't
        # spam the audit log with identical entries.
        if existing.get("label") == label:
            return self.get_relationship(relationship_id)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE relationships SET label = ? "
                "WHERE relationship_id = ? AND deleted_at IS NULL",
                (label, relationship_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                # Race: row was soft-deleted between our SELECT and UPDATE.
                return None
        if user_id:
            self._emit_audit_silent(
                user_id=user_id,
                category="relationship",
                action="relabel",
                project_id=existing["project_id"],
                subject_id=relationship_id,
                before={"label": existing.get("label")},
                after={"label": label},
            )
        return self.get_relationship(relationship_id)

    def delete_relationship(
        self, relationship_id: str, *, user_id: str | None = None,
    ) -> bool:
        """Soft-delete a relationship (sets deleted_at).

        Returns True when a row was updated, False when no matching
        active relationship was found.
        """
        existing = None
        if user_id:
            with self._connect() as c:
                row = c.execute(
                    "SELECT project_id, label, source_topic_id, target_topic_id "
                    "FROM relationships WHERE relationship_id = ?",
                    (relationship_id,),
                ).fetchone()
                existing = dict(row) if row else None
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE relationships SET deleted_at = ? "
                "WHERE relationship_id = ? AND deleted_at IS NULL",
                (now, relationship_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                return False
        if user_id and existing:
            self._emit_audit_silent(
                user_id=user_id,
                category="relationship",
                action="delete",
                project_id=existing.get("project_id"),
                subject_id=relationship_id,
                before={"label": existing.get("label")},
            )
        return True

    def list_relationships(
        self, *, project_id: str, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = user_id
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT relationship_id, project_id, source_topic_id, target_topic_id,
                       label, origin, strength, created_at, deleted_at
                FROM relationships
                WHERE project_id = ? AND deleted_at IS NULL
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------- Q&A turns ----------

    def append_qna_turn(  # noqa: D401
        self,
        *,
        topic_id: str,
        project_id: str,
        role: str,
        body: str,
        status: str = "answered",
        why_this_matters: str | None = None,
        action: str | None = None,
        suggested_responses: list[dict[str, str]] | None = None,
        parent_turn_id: str | None = None,
        attachments: list[str] | None = None,
        user_id: str | None = None,  # noqa: ARG002 — reserved for scoping
    ) -> dict[str, Any]:
        turn_id = f"turn-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        with self._connect() as connection:
            next_order = connection.execute(
                "SELECT COALESCE(MAX(order_index), -1) + 1 FROM qna_turns WHERE topic_id = ?",
                (topic_id,),
            ).fetchone()[0]
            payload = {
                "turn_id": turn_id,
                "topic_id": topic_id,
                "project_id": project_id,
                "role": role,
                "order_index": next_order,
                "body": body,
                "why_this_matters": why_this_matters,
                "action": action,
                "suggested_responses": suggested_responses or [],
                "status": status,
                "parent_turn_id": parent_turn_id,
                "attachments": attachments or [],
                "created_at": now,
            }
            connection.execute(
                """
                INSERT INTO qna_turns (turn_id, topic_id, project_id, role, order_index, body,
                                        why_this_matters, action, suggested_responses_json,
                                        status, parent_turn_id, attachments_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id, topic_id, project_id, role, next_order, body,
                    why_this_matters, action, json.dumps(payload["suggested_responses"]),
                    status, parent_turn_id, json.dumps(payload["attachments"]), now,
                ),
            )
            connection.commit()
        return payload

    def list_qna_turns(
        self, *, topic_id: str, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = user_id
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_id, topic_id, project_id, role, order_index, body,
                       why_this_matters, action, suggested_responses_json, status,
                       parent_turn_id, attachments_json, created_at
                FROM qna_turns WHERE topic_id = ?
                ORDER BY order_index
                """,
                (topic_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["suggested_responses"] = json.loads(item.pop("suggested_responses_json") or "[]")
            item["attachments"] = json.loads(item.pop("attachments_json") or "[]")
            items.append(item)
        return items

    # ---------- Decisions ----------

    def create_decision(  # noqa: D401
        self,
        *,
        topic_id: str,
        project_id: str,
        statement: str,
        proposed_by: str,
        rationale: str | None = None,
        source_turn_id: str | None = None,
        status: str = "proposed",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        decision_id = f"dec-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        payload = {
            "decision_id": decision_id,
            "topic_id": topic_id,
            "project_id": project_id,
            "statement": statement,
            "rationale": rationale,
            "status": status,
            "source_turn_id": source_turn_id,
            "proposed_by": proposed_by,
            "confirmed_by_user_id": None,
            "created_at": now,
            "updated_at": now,
            "retracted_at": None,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decisions (decision_id, topic_id, project_id, statement, rationale,
                                        status, source_turn_id, proposed_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id, topic_id, project_id, statement, rationale,
                    status, source_turn_id, proposed_by, now, now,
                ),
            )
            connection.commit()
        if user_id:
            self._emit_audit_silent(
                user_id=user_id,
                category="decision",
                action="create",
                project_id=project_id,
                subject_id=decision_id,
                after={"statement": statement, "topic_id": topic_id},
            )
        return payload

    def confirm_decision(self, decision_id: str, *, user_id: str) -> dict[str, Any] | None:
        """Confirm a decision. Returns the updated row, or None if the
        caller doesn't own the decision's project (IDOR-safe)."""
        # Pre-flight ownership check. Without this, any signed-in user
        # could confirm any decision_id they could guess.
        existing = self.get_decision(decision_id)
        if existing is None:
            return None
        if not self.verify_project_ownership(
            project_id=existing["project_id"], user_id=user_id,
        ):
            return None
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE decisions SET status = 'confirmed', confirmed_by_user_id = ?, updated_at = ?
                WHERE decision_id = ?
                """,
                (user_id, now, decision_id),
            )
            connection.commit()
        return self.get_decision(decision_id)

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Trust-required primitive — no ownership filter.

        Public callers MUST go through ``get_decision_with_ownership``
        instead; this raw fetch exists for in-store flows that perform
        their own ownership check (``confirm_decision``,
        ``delete_decision``) immediately after.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT decision_id, topic_id, project_id, statement, rationale, status,
                       source_turn_id, proposed_by, confirmed_by_user_id, created_at,
                       updated_at, retracted_at
                FROM decisions WHERE decision_id = ?
                """,
                (decision_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_decisions(
        self, *, project_id: str, topic_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = user_id
        # Retracted decisions are excluded — they're the equivalent of a
        # soft delete. The row stays for audit, but the UI never sees it.
        query = (
            "SELECT decision_id, topic_id, project_id, statement, rationale, status, "
            "source_turn_id, proposed_by, confirmed_by_user_id, created_at, updated_at, retracted_at "
            "FROM decisions WHERE project_id = ? AND status != 'retracted'"
        )
        params: tuple[Any, ...] = (project_id,)
        if topic_id:
            query += " AND topic_id = ?"
            params = (project_id, topic_id)
        query += " ORDER BY topic_id, created_at"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def delete_decision(
        self, decision_id: str, *, user_id: str | None = None,
    ) -> bool:
        """Soft-delete a decision by setting status='retracted'.

        The row stays in the DB for audit; ``list_decisions`` filters it
        out. Returns True when an active decision was found and updated,
        False when it was missing, already retracted, OR the caller
        doesn't own the project (IDOR-safe).

        ``user_id=None`` is reserved for in-process / migration use; in
        that mode the ownership check is skipped. Production routes
        always pass a real user_id via the authed dependency.
        """
        existing = self.get_decision(decision_id)
        if existing is None:
            return False
        # IDOR pre-flight: when a user_id is supplied, refuse the delete
        # unless the caller owns the decision's project. Without this,
        # any signed-in user could retract any decision_id they could
        # guess.
        if user_id is not None and not self.verify_project_ownership(
            project_id=existing.get("project_id"), user_id=user_id,
        ):
            return False
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE decisions SET status = 'retracted', retracted_at = ?, "
                "updated_at = ? WHERE decision_id = ? AND status != 'retracted'",
                (now, now, decision_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                return False
        if user_id:
            self._emit_audit_silent(
                user_id=user_id,
                category="decision",
                action="delete",
                project_id=existing.get("project_id"),
                subject_id=decision_id,
                before={"statement": existing.get("statement")},
            )
        return True

    # ---------- Consistency flags ----------

    def create_consistency_flag(
        self,
        *,
        project_id: str,
        topic_a_id: str,
        topic_b_id: str,
        description: str,
        scope: str = "within_project",
        decision_a_id: str | None = None,
        decision_b_id: str | None = None,
    ) -> dict[str, Any]:
        flag_id = f"flag-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        payload = {
            "flag_id": flag_id,
            "project_id": project_id,
            "topic_a_id": topic_a_id,
            "decision_a_id": decision_a_id,
            "topic_b_id": topic_b_id,
            "decision_b_id": decision_b_id,
            "description": description,
            "scope": scope,
            "status": "open",
            "resolved_turn_id": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO consistency_flags (flag_id, project_id, topic_a_id, decision_a_id,
                                                topic_b_id, decision_b_id, description, scope,
                                                status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    flag_id, project_id, topic_a_id, decision_a_id,
                    topic_b_id, decision_b_id, description, scope, now, now,
                ),
            )
            connection.commit()
        return payload

    def resolve_consistency_flag(
        self,
        flag_id: str,
        *,
        resolution: str,
        resolved_turn_id: str | None = None,
    ) -> dict[str, Any] | None:
        if resolution not in ("resolved", "intentional", "dismissed"):
            raise ValueError(f"invalid resolution: {resolution}")
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE consistency_flags SET status = ?, resolved_turn_id = ?, updated_at = ?
                WHERE flag_id = ?
                """,
                (resolution, resolved_turn_id, now, flag_id),
            )
            connection.commit()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM consistency_flags WHERE flag_id = ?", (flag_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_consistency_flags(
        self,
        *,
        project_id: str,
        status: str = "open",
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT flag_id, project_id, topic_a_id, decision_a_id, topic_b_id,
                       decision_b_id, description, scope, status, resolved_turn_id,
                       created_at, updated_at
                FROM consistency_flags
                WHERE project_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (project_id, status),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------- Summary versions ----------

    def append_summary_version(
        self,
        *,
        project_id: str,
        content_markdown: str,
        sections: list[dict[str, Any]],
        generated_by: str,
        version_hash: str,
        open_questions: list[str] | None = None,
        approval_state: str = "draft",
        generated_by_user_id: str | None = None,
        version_note: str | None = None,
    ) -> dict[str, Any]:
        version_id = f"sum-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        payload = {
            "version_id": version_id,
            "project_id": project_id,
            "version_hash": version_hash,
            "content_markdown": content_markdown,
            "sections": sections,
            "open_questions": open_questions or [],
            "approval_state": approval_state,
            "generated_by": generated_by,
            "generated_by_user_id": generated_by_user_id,
            "version_note": version_note,
            "created_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO summary_versions (version_id, project_id, version_hash, content_markdown,
                                               sections_json, open_questions_json, approval_state,
                                               generated_by, generated_by_user_id, version_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id, project_id, version_hash, content_markdown,
                    json.dumps(sections), json.dumps(open_questions or []), approval_state,
                    generated_by, generated_by_user_id, version_note, now,
                ),
            )
            connection.commit()
        return payload

    def latest_summary_version(self, *, project_id: str) -> dict[str, Any] | None:
        # Secondary sort by rowid DESC to break created_at ties deterministically
        # (now_timestamp is second-precision; two appends within the same second
        # must still return the later one).
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT version_id, project_id, version_hash, content_markdown, sections_json,
                       open_questions_json, approval_state, generated_by, generated_by_user_id,
                       version_note, created_at
                FROM summary_versions WHERE project_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["sections"] = json.loads(item.pop("sections_json") or "[]")
        item["open_questions"] = json.loads(item.pop("open_questions_json") or "[]")
        return item

    # ---------- Audit log ----------

    # Every mutation that should show up in the user-facing Activity feed
    # calls this helper. We swallow DB errors silently because audit
    # writes are a nice-to-have — they must NEVER break the actual
    # mutation. The Activity Timeline view filters to user-visible
    # categories (topic, relationship, decision, project, share, export)
    # so internal categories pass through the append but never render.
    def _emit_audit_silent(
        self,
        *,
        user_id: str,
        category: str,
        action: str,
        project_id: str | None = None,
        subject_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        try:
            # The workspace concept is single-tenant for now — every user
            # gets "ws-default". When (if) we add real workspaces, this is
            # the one seam to swap.
            self.append_audit_event(
                workspace_id="ws-default",
                actor_user_id=user_id,
                category=category,
                action=action,
                project_id=project_id,
                subject_id=subject_id,
                before=before,
                after=after,
            )
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "append_audit_event failed (%s/%s): %s", category, action, e,
            )

    def append_audit_event(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        category: str,
        action: str,
        project_id: str | None = None,
        subject_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip_address: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        event_id = f"evt-{uuid.uuid4().hex[:10]}"
        now = now_timestamp()
        payload = {
            "event_id": event_id,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "actor_user_id": actor_user_id,
            "category": category,
            "action": action,
            "subject_id": subject_id,
            "before": before,
            "after": after,
            "ip_address": ip_address,
            "session_id": session_id,
            "created_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log (event_id, workspace_id, project_id, actor_user_id,
                                        category, action, subject_id, before_json, after_json,
                                        ip_address, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, workspace_id, project_id, actor_user_id,
                    category, action, subject_id,
                    json.dumps(before) if before is not None else None,
                    json.dumps(after) if after is not None else None,
                    ip_address, session_id, now,
                ),
            )
            connection.commit()
        return payload

    # Categories we expose to the user-facing Activity Timeline. Internal
    # / system categories (``system``, ``admin``, ``auth``, etc.) are
    # deliberately excluded — the user only cares about things that
    # happened to their canvas, not housekeeping.
    _ACTIVITY_VISIBLE_CATEGORIES: tuple[str, ...] = (
        "topic",
        "relationship",
        "decision",
        "project",
        "share",
        "export",
    )

    def list_project_activity(
        self,
        *,
        project_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Activity-timeline feed for a project.

        Reads ``audit_log`` rows for the given project, newest first, and
        returns a shape ready for the frontend timeline. The caller MUST
        own the project — cross-user access raises ``PermissionError``
        (the API layer converts this to a 404 so we don't leak IDs).

        ``categories`` is filtered to the user-visible set (topic,
        relationship, decision, project, share, export); anything else
        (system, auth, admin) is dropped server-side.

        Return shape::

            {
                "events": [
                    {
                        "event_id": "evt-...",
                        "category": "topic",
                        "action": "create",
                        "subject_id": "topic-...",
                        "subject_title": "Venue",          # derived from after_json
                        "created_at": "2026-04-21T10:02:33Z",
                        "actor_display_name": "Alice",      # joined from users
                    },
                    ...
                ],
                "has_more": bool,
            }

        Pagination uses limit+1 fetching: we ask for ``limit+1`` rows,
        return at most ``limit``, and set ``has_more`` based on whether
        the extra row showed up. Offset is a plain numeric skip — no
        cursor, since the audit_log rows are immutable and ordered by
        ``created_at DESC`` with a project_id index already in place.
        """
        # IDOR gate — the route-layer helper also enforces this, but we
        # belt-and-braces it here so direct store callers can't bypass.
        if not self.verify_project_ownership(
            project_id=project_id, user_id=user_id,
        ):
            raise PermissionError("project not found or not owned by user")

        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))

        placeholders = ",".join("?" for _ in self._ACTIVITY_VISIBLE_CATEGORIES)
        # Secondary sort on ``rowid`` guarantees a deterministic order when
        # multiple events share the same ``created_at`` timestamp (seconds
        # precision; burst-insert scenarios collapse otherwise). The
        # Postgres translator rewrites ``rowid`` → ``ctid`` for us.
        query = (
            "SELECT a.event_id, a.category, a.action, a.subject_id, "
            "       a.before_json, a.after_json, a.created_at, "
            "       a.actor_user_id, u.display_name AS actor_display_name "
            "FROM audit_log a "
            "LEFT JOIN users u ON u.user_id = a.actor_user_id "
            f"WHERE a.project_id = ? AND a.category IN ({placeholders}) "
            "ORDER BY a.created_at DESC, a.rowid DESC "
            "LIMIT ? OFFSET ?"
        )
        params = (
            project_id,
            *self._ACTIVITY_VISIBLE_CATEGORIES,
            limit + 1,
            offset,
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            before = item.pop("before_json", None)
            after = item.pop("after_json", None)
            try:
                before_obj = json.loads(before) if before else None
            except (ValueError, TypeError):
                before_obj = None
            try:
                after_obj = json.loads(after) if after else None
            except (ValueError, TypeError):
                after_obj = None
            subject_title = _derive_activity_subject_title(
                category=item.get("category") or "",
                action=item.get("action") or "",
                before=before_obj,
                after=after_obj,
            )
            events.append({
                "event_id": item.get("event_id"),
                "category": item.get("category"),
                "action": item.get("action"),
                "subject_id": item.get("subject_id"),
                "subject_title": subject_title,
                "created_at": item.get("created_at"),
                "actor_display_name": item.get("actor_display_name") or "",
            })

        return {"events": events, "has_more": has_more}

    # ---------- User usage (per-user daily token budget) ----------------

    def get_usage_today(self, *, user_id: str) -> dict[str, int]:
        """Return today's UTC usage totals for this user.

        Missing rows yield zeros — the user hasn't made any billed requests
        yet today. Use the output to gate further calls against a per-user
        daily budget.
        """
        day = _today_utc_day()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT tokens_in, tokens_out, request_count
                FROM user_usage
                WHERE user_id = ? AND day_utc = ?
                """,
                (user_id, day),
            ).fetchone()
        if row is None:
            return {"tokens_in": 0, "tokens_out": 0, "request_count": 0}
        return {
            "tokens_in": int(row["tokens_in"] or 0),
            "tokens_out": int(row["tokens_out"] or 0),
            "request_count": int(row["request_count"] or 0),
        }

    def record_usage(
        self, *, user_id: str, tokens_in: int, tokens_out: int,
    ) -> dict[str, int]:
        """Upsert today's user_usage row, adding the deltas in.

        Called after each successful LLM turn. Negative deltas are clamped
        to zero — recording an "estimate" we're not confident about must
        never decrease the visible usage.
        """
        tokens_in = max(0, int(tokens_in or 0))
        tokens_out = max(0, int(tokens_out or 0))
        day = _today_utc_day()
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_usage
                  (user_id, day_utc, tokens_in, tokens_out, request_count, updated_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id, day_utc) DO UPDATE SET
                  tokens_in = user_usage.tokens_in + excluded.tokens_in,
                  tokens_out = user_usage.tokens_out + excluded.tokens_out,
                  request_count = user_usage.request_count + 1,
                  updated_at = excluded.updated_at
                """,
                (user_id, day, tokens_in, tokens_out, now),
            )
            connection.commit()
        return self.get_usage_today(user_id=user_id)

    # ---------- Suggestions cache (AI project suggestions) --------------

    def get_cached_suggestions(
        self, *, user_id: str,
    ) -> dict[str, Any] | None:
        """Return the cached suggestions blob for a user, or None.

        TTL is enforced by the caller (the suggestions module) — this is
        a dumb KV store. Shape of the returned dict:
          {"suggestions": [...], "generated_at": "..."}
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT suggestions_json, generated_at
                FROM suggestions_cache
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            suggestions = json.loads(row["suggestions_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            suggestions = []
        return {
            "suggestions": suggestions,
            "generated_at": row["generated_at"],
        }

    def save_cached_suggestions(
        self,
        *,
        user_id: str,
        suggestions: list[dict[str, Any]],
    ) -> None:
        """Overwrite the cached suggestions for this user with fresh data."""
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO suggestions_cache (user_id, suggestions_json, generated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  suggestions_json = excluded.suggestions_json,
                  generated_at = excluded.generated_at
                """,
                (user_id, json.dumps(suggestions), now),
            )
            connection.commit()

    def invalidate_cached_suggestions(self, *, user_id: str) -> None:
        """Drop any cached suggestions for this user.

        Called when the user's project set changes (create/delete) so the
        next suggest call reflects new signals rather than stale ones.
        """
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM suggestions_cache WHERE user_id = ?",
                (user_id,),
            )
            connection.commit()

    # ---------- Credits: balance + transaction ledger -------------------
    #
    # Consumed by ``credits.py`` (module-level helpers) and by the
    # ``/api/v2/credits`` routes. The ``credits`` module is where the
    # business rules live — seeding by plan tier, charge semantics, refund
    # bookkeeping. This store layer only owns SQL.
    #
    # Balance is kept hot in ``user_credits.balance_credits`` as a running
    # total; every change is ALSO written to ``credit_transactions`` so
    # the ledger remains the source of truth. Dispute resolution sums the
    # ledger rather than trusting the snapshot.

    def get_user_credits(self, *, user_id: str) -> dict[str, Any] | None:
        """Return the raw user_credits row for this user, or None."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, balance_credits, updated_at "
                "FROM user_credits WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def seed_user_credits(
        self,
        *,
        user_id: str,
        balance_credits: int,
        reason: str,
    ) -> dict[str, Any]:
        """Create the initial user_credits row AND the grant transaction.

        No-op if the row already exists — idempotent on concurrent first
        reads. Returns the resulting row. The companion ledger entry
        lands in credit_transactions with the given reason so the user
        can see "seed: initial_grant_free" in their history.
        """
        now = now_timestamp()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT balance_credits FROM user_credits WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "user_id": user_id,
                    "balance_credits": int(existing["balance_credits"]),
                    "updated_at": now,
                }
            connection.execute(
                "INSERT INTO user_credits (user_id, balance_credits, updated_at) "
                "VALUES (?, ?, ?)",
                (user_id, int(balance_credits), now),
            )
            if balance_credits > 0:
                txn_id = f"txn-{uuid.uuid4().hex[:12]}"
                connection.execute(
                    "INSERT INTO credit_transactions "
                    "(transaction_id, user_id, delta, reason, reference_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (txn_id, user_id, int(balance_credits), reason, None, now),
                )
            connection.commit()
        return {
            "user_id": user_id,
            "balance_credits": int(balance_credits),
            "updated_at": now,
        }

    def adjust_user_credits(
        self,
        *,
        user_id: str,
        delta: int,
        reason: str,
        reference_id: str | None = None,
    ) -> tuple[int, str]:
        """Apply a signed delta to the user's balance + append a ledger row.

        ``delta`` is signed: negative for charges, positive for refunds /
        grants / purchases. Returns ``(new_balance, transaction_id)``.

        TOCTOU protection: ``user_credits.balance_credits`` carries a
        ``CHECK (balance_credits >= 0)`` constraint (added in migration
        20260422_0001).  Concurrent debits that race past the application-level
        balance check raise ``sqlite3.IntegrityError``, which the HTTP layer
        (``credits.charge``) converts to a 402 Payment Required.  Callers of
        this method should catch ``sqlite3.IntegrityError`` and treat it as
        insufficient balance.

        For zero-delta calls (e.g. logging a ``scaffold_failed`` event without
        charging), the constraint is never triggered.
        """
        now = now_timestamp()
        txn_id = f"txn-{uuid.uuid4().hex[:12]}"
        with self._connect() as connection:
            # Ensure the row exists so the UPDATE lands. If it doesn't
            # (caller bypassed ensure_initial_grant) we insert at zero
            # first so the UPDATE has a target.
            connection.execute(
                "INSERT INTO user_credits (user_id, balance_credits, updated_at) "
                "VALUES (?, 0, ?) "
                "ON CONFLICT(user_id) DO NOTHING",
                (user_id, now),
            )
            connection.execute(
                "UPDATE user_credits "
                "SET balance_credits = balance_credits + ?, updated_at = ? "
                "WHERE user_id = ?",
                (int(delta), now, user_id),
            )
            connection.execute(
                "INSERT INTO credit_transactions "
                "(transaction_id, user_id, delta, reason, reference_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (txn_id, user_id, int(delta), reason, reference_id, now),
            )
            row = connection.execute(
                "SELECT balance_credits FROM user_credits WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            connection.commit()
        new_balance = int(row["balance_credits"]) if row else 0
        return new_balance, txn_id

    def list_credit_transactions(
        self, *, user_id: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Most-recent-first ledger rows for a user."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT transaction_id, user_id, delta, reason, reference_id, created_at "
                "FROM credit_transactions "
                "WHERE user_id = ? "
                "ORDER BY created_at DESC, transaction_id DESC "
                "LIMIT ?",
                (user_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]


    # ---------- Scaffolds ----------------------------------------------

    def create_scaffold(
        self,
        *,
        project_id: str,
        user_id: str,
        framework: str,
        language: str,
        manifest_json: str,
    ) -> dict[str, Any]:
        """Persist a generated scaffold. Returns the full row."""
        scaffold_id = f"scaf-{uuid.uuid4().hex[:12]}"
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scaffolds "
                "(scaffold_id, project_id, user_id, framework, language, "
                " manifest_json, created_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    scaffold_id, project_id, user_id, framework, language,
                    manifest_json, now,
                ),
            )
            connection.commit()
        return {
            "scaffold_id": scaffold_id,
            "project_id": project_id,
            "user_id": user_id,
            "framework": framework,
            "language": language,
            "manifest_json": manifest_json,
            "created_at": now,
            "deleted_at": None,
        }

    def get_scaffold(
        self, *, scaffold_id: str, user_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a scaffold row, ownership-checked.

        Returns None for missing OR cross-user — we don't distinguish so
        the API can't enumerate scaffold IDs across tenants.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT scaffold_id, project_id, user_id, framework, "
                "       language, manifest_json, created_at, deleted_at "
                "FROM scaffolds "
                "WHERE scaffold_id = ? AND user_id = ? "
                "      AND deleted_at IS NULL",
                (scaffold_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def add_scaffold_file(
        self,
        *,
        scaffold_id: str,
        user_id: str,
        path: str,
        content: str = "",
    ) -> str | None:
        """Add a new file to a scaffold's manifest.

        Returns "ok" on success, "exists" if a file with that path
        already lives in the manifest, None if the scaffold doesn't
        exist (or is owned by another user).
        """
        import json as _json  # noqa: PLC0415

        with self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM scaffolds "
                "WHERE scaffold_id = ? AND user_id = ? "
                "      AND deleted_at IS NULL",
                (scaffold_id, user_id),
            ).fetchone()
            if row is None:
                return None
            try:
                manifest = _json.loads(row["manifest_json"] or "{}")
            except (TypeError, ValueError):
                return None
            files = manifest.get("files") or []
            for entry in files:
                if isinstance(entry, dict) and entry.get("path") == path:
                    return "exists"
            files.append({"path": path, "content": content})
            manifest["files"] = files
            connection.execute(
                "UPDATE scaffolds SET manifest_json = ? "
                "WHERE scaffold_id = ? AND user_id = ?",
                (_json.dumps(manifest), scaffold_id, user_id),
            )
            connection.commit()
        return "ok"

    def rename_scaffold_file(
        self,
        *,
        scaffold_id: str,
        user_id: str,
        old_path: str,
        new_path: str,
    ) -> str | None:
        """Rename one file inside a scaffold's manifest.

        Returns "ok" / "not_found" / "exists" / None (scaffold missing).
        """
        import json as _json  # noqa: PLC0415

        with self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM scaffolds "
                "WHERE scaffold_id = ? AND user_id = ? "
                "      AND deleted_at IS NULL",
                (scaffold_id, user_id),
            ).fetchone()
            if row is None:
                return None
            try:
                manifest = _json.loads(row["manifest_json"] or "{}")
            except (TypeError, ValueError):
                return None
            files = manifest.get("files") or []
            target = None
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                if entry.get("path") == old_path:
                    target = entry
                if entry.get("path") == new_path:
                    return "exists"
            if target is None:
                return "not_found"
            target["path"] = new_path
            manifest["files"] = files
            connection.execute(
                "UPDATE scaffolds SET manifest_json = ? "
                "WHERE scaffold_id = ? AND user_id = ?",
                (_json.dumps(manifest), scaffold_id, user_id),
            )
            connection.commit()
        return "ok"

    def delete_scaffold_file(
        self,
        *,
        scaffold_id: str,
        user_id: str,
        path: str,
    ) -> bool:
        """Drop one file from a scaffold's manifest.

        Returns True on success, False if the scaffold or path is
        missing (idempotent — caller can ignore False).
        """
        import json as _json  # noqa: PLC0415

        with self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM scaffolds "
                "WHERE scaffold_id = ? AND user_id = ? "
                "      AND deleted_at IS NULL",
                (scaffold_id, user_id),
            ).fetchone()
            if row is None:
                return False
            try:
                manifest = _json.loads(row["manifest_json"] or "{}")
            except (TypeError, ValueError):
                return False
            files = manifest.get("files") or []
            kept = [
                e for e in files
                if not (isinstance(e, dict) and e.get("path") == path)
            ]
            if len(kept) == len(files):
                return False
            manifest["files"] = kept
            connection.execute(
                "UPDATE scaffolds SET manifest_json = ? "
                "WHERE scaffold_id = ? AND user_id = ?",
                (_json.dumps(manifest), scaffold_id, user_id),
            )
            connection.commit()
        return True

    def update_scaffold_file_content(
        self,
        *,
        scaffold_id: str,
        user_id: str,
        path: str,
        content: str,
    ) -> bool:
        """Patch one file's content inside a scaffold's manifest_json.

        Returns True on success, False if the scaffold doesn't exist
        (or is owned by another user) or if `path` isn't in the manifest.

        Used by the artifact viewer's autosave path: the user types
        in the Code-tab textarea and the FE debounce-PATCHes here so
        edits survive a page reload + show up in the Preview embed
        on next open.
        """
        import json as _json  # noqa: PLC0415

        with self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM scaffolds "
                "WHERE scaffold_id = ? AND user_id = ? "
                "      AND deleted_at IS NULL",
                (scaffold_id, user_id),
            ).fetchone()
            if row is None:
                return False
            try:
                manifest = _json.loads(row["manifest_json"] or "{}")
            except (TypeError, ValueError):
                return False
            files = manifest.get("files") or []
            patched = False
            for entry in files:
                if isinstance(entry, dict) and entry.get("path") == path:
                    # Wave F.6 — snapshot the pre-edit content as
                    # original_content on the FIRST partner edit so a
                    # later Refresh PR can render a 3-way diff (base /
                    # partner_edit / ai_redraft). Mirrors the
                    # write-once discipline of set_project_base_main_sha:
                    # subsequent edits do NOT overwrite original_content
                    # — the captured baseline is the AI-as-generated
                    # state, not "previous keystroke".
                    if (
                        entry.get("original_content") is None
                        and entry.get("content") != content
                    ):
                        entry["original_content"] = entry.get("content")
                    entry["content"] = content
                    patched = True
                    break
            if not patched:
                return False
            manifest["files"] = files
            connection.execute(
                "UPDATE scaffolds SET manifest_json = ? "
                "WHERE scaffold_id = ? AND user_id = ?",
                (_json.dumps(manifest), scaffold_id, user_id),
            )
            connection.commit()
        return True

    # ---------- Scaffold refresh history (Wave F.6) -----------------------
    # Tracks each "Refresh PR with Inspira" run so the FE 3-way diff
    # endpoint can resolve back to the precise pre/post scaffold pair
    # and so a second concurrent POST surfaces 409 instead of racing.

    def create_scaffold_refresh_history(
        self,
        *,
        project_id: str,
        base_main_sha_before: str,
        previous_scaffold_id: str | None,
        preserve_partner_edits: bool = True,
    ) -> dict[str, Any]:
        """Insert a row with status='in_progress' at the start of refresh.

        Returns the inserted row dict (refresh_id + initial state). The
        caller updates the row to status='completed' / 'failed' once the
        adapter call resolves; 'resolved' is set later when the partner
        submits per-file decisions.
        """
        refresh_id = f"refr-{uuid.uuid4().hex[:12]}"
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO v2_scaffold_refresh_history "
                "(refresh_id, project_id, previous_scaffold_id, "
                " new_scaffold_id, base_main_sha_before, "
                " base_main_sha_after, preserve_partner_edits, "
                " changed_paths, status, created_at, resolved_at) "
                "VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, "
                "        'in_progress', ?, NULL)",
                (
                    refresh_id, project_id, previous_scaffold_id,
                    base_main_sha_before, int(preserve_partner_edits),
                    json.dumps([]), now,
                ),
            )
            connection.commit()
        return {
            "refresh_id": refresh_id,
            "project_id": project_id,
            "previous_scaffold_id": previous_scaffold_id,
            "new_scaffold_id": None,
            "base_main_sha_before": base_main_sha_before,
            "base_main_sha_after": None,
            "preserve_partner_edits": preserve_partner_edits,
            "changed_paths": [],
            "status": "in_progress",
            "created_at": now,
            "resolved_at": None,
        }

    def update_scaffold_refresh_history(
        self,
        *,
        refresh_id: str,
        status: str | None = None,
        new_scaffold_id: str | None = None,
        base_main_sha_after: str | None = None,
        changed_paths: list[str] | None = None,
        resolved_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch a refresh_history row. Returns the post-UPDATE row.

        Only non-None kwargs are written. ``resolved_at`` is set
        automatically when status transitions to 'resolved' if not
        passed explicitly.
        """
        existing = self.get_scaffold_refresh_history(refresh_id=refresh_id)
        if existing is None:
            return None
        sets: list[str] = []
        values: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            values.append(status)
            if status == "resolved" and resolved_at is None:
                resolved_at = now_timestamp()
        if new_scaffold_id is not None:
            sets.append("new_scaffold_id = ?")
            values.append(new_scaffold_id)
        if base_main_sha_after is not None:
            sets.append("base_main_sha_after = ?")
            values.append(base_main_sha_after)
        if changed_paths is not None:
            sets.append("changed_paths = ?")
            values.append(json.dumps(changed_paths))
        if resolved_at is not None:
            sets.append("resolved_at = ?")
            values.append(resolved_at)
        if not sets:
            return existing
        values.append(refresh_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE v2_scaffold_refresh_history "
                f"SET {', '.join(sets)} WHERE refresh_id = ?",
                tuple(values),
            )
            connection.commit()
        return self.get_scaffold_refresh_history(refresh_id=refresh_id)

    def get_scaffold_refresh_history(
        self, *, refresh_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one refresh row by id, or ``None``."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT refresh_id, project_id, previous_scaffold_id, "
                "       new_scaffold_id, base_main_sha_before, "
                "       base_main_sha_after, preserve_partner_edits, "
                "       changed_paths, status, created_at, resolved_at "
                "FROM v2_scaffold_refresh_history "
                "WHERE refresh_id = ?",
                (refresh_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["preserve_partner_edits"] = bool(result["preserve_partner_edits"])
        try:
            result["changed_paths"] = json.loads(result["changed_paths"] or "[]")
        except (TypeError, ValueError):
            result["changed_paths"] = []
        return result

    def get_in_progress_refresh_for_project(
        self, *, project_id: str,
    ) -> dict[str, Any] | None:
        """Return an unfinished refresh row, or ``None``.

        Used by the POST /refresh-overlay route to 409 a second
        concurrent kickoff. ``in_progress`` is the only blocking status
        — completed / failed / resolved rows are historical.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT refresh_id, project_id, previous_scaffold_id, "
                "       new_scaffold_id, base_main_sha_before, "
                "       base_main_sha_after, preserve_partner_edits, "
                "       changed_paths, status, created_at, resolved_at "
                "FROM v2_scaffold_refresh_history "
                "WHERE project_id = ? AND status = 'in_progress' "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["preserve_partner_edits"] = bool(result["preserve_partner_edits"])
        try:
            result["changed_paths"] = json.loads(result["changed_paths"] or "[]")
        except (TypeError, ValueError):
            result["changed_paths"] = []
        return result

    def list_scaffolds_for_project(
        self, *, project_id: str, user_id: str,
    ) -> list[dict[str, Any]]:
        """Scaffolds owned by this user for this project, most-recent first.

        Intentionally does NOT return the full manifest_json — large
        scaffolds would bloat the list response. Callers that need the
        full payload fetch the single row via get_scaffold.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT scaffold_id, project_id, user_id, framework, "
                "       language, created_at "
                "FROM scaffolds "
                "WHERE project_id = ? AND user_id = ? "
                "      AND deleted_at IS NULL "
                "ORDER BY created_at DESC",
                (project_id, user_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- Shared read-only links ----------------------------------

    def create_share_link(
        self, *, project_id: str, user_id: str,
    ) -> dict[str, Any]:
        """Mint a new read-only share token for this project.

        Revokes any existing live link for the project first — a project
        has at most one active link at a time. Returns a dict with the
        new ``token`` and the canonical ``url_path`` (``/s/<token>``);
        the frontend prefixes its own origin to form the full URL.
        """
        import secrets
        import base64

        now = now_timestamp()
        raw = secrets.token_bytes(24)
        token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        with self._connect() as connection:
            # Revoke any live link on this project — a new link always wins.
            connection.execute(
                "UPDATE shared_links SET revoked_at = ? "
                "WHERE project_id = ? AND revoked_at IS NULL",
                (now, project_id),
            )
            connection.execute(
                """
                INSERT INTO shared_links
                    (token, project_id, created_by_user_id, created_at,
                     revoked_at, last_viewed_at, view_count)
                VALUES (?, ?, ?, ?, NULL, NULL, 0)
                """,
                (token, project_id, user_id, now),
            )
            connection.commit()
        self._emit_audit_silent(
            user_id=user_id,
            category="share",
            action="create",
            project_id=project_id,
            subject_id=token,
        )
        return {
            "token": token,
            "project_id": project_id,
            "created_by_user_id": user_id,
            "created_at": now,
            "revoked_at": None,
            "last_viewed_at": None,
            "view_count": 0,
            "url_path": f"/s/{token}",
        }

    def get_share_link_by_token(
        self, token: str,
    ) -> dict[str, Any] | None:
        """Return the share-link row for this token, or None if unknown."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT token, project_id, created_by_user_id, created_at,
                       revoked_at, last_viewed_at, view_count
                FROM shared_links WHERE token = ?
                """,
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def get_active_share_link(
        self, *, project_id: str, user_id: str,
    ) -> dict[str, Any] | None:
        """Return the currently-active (non-revoked) link for this project.

        Gated on ownership: a user can only see links for projects they
        own. Returns None when no active link exists OR when the caller
        doesn't own the project.
        """
        if not self.verify_project_ownership(
            project_id=project_id, user_id=user_id,
        ):
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT token, project_id, created_by_user_id, created_at,
                       revoked_at, last_viewed_at, view_count
                FROM shared_links
                WHERE project_id = ? AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["url_path"] = f"/s/{out['token']}"
        return out

    def revoke_share_link(
        self, *, project_id: str, user_id: str,
    ) -> bool:
        """Revoke the active link on a project. Returns True when a row changed.

        Ownership-gated: a non-owner gets False (no row changes). A
        project with no active link also gets False.
        """
        if not self.verify_project_ownership(
            project_id=project_id, user_id=user_id,
        ):
            return False
        now = now_timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE shared_links SET revoked_at = ? "
                "WHERE project_id = ? AND revoked_at IS NULL",
                (now, project_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                return False
        self._emit_audit_silent(
            user_id=user_id,
            category="share",
            action="revoke",
            project_id=project_id,
        )
        return True

    def touch_share_link(self, token: str) -> None:
        """Record a view on this token — updates last_viewed_at + view_count.

        Best-effort — errors are swallowed so view-tracking never blocks a
        successful shared fetch. Only updates if the token is live (not
        revoked); revoked tokens don't accrue views.
        """
        now = now_timestamp()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE shared_links
                    SET last_viewed_at = ?, view_count = view_count + 1
                    WHERE token = ? AND revoked_at IS NULL
                    """,
                    (now, token),
                )
                connection.commit()
        except Exception:  # noqa: BLE001 — sqlite3.Error or psycopg.Error; metrics are cosmetic
            # View tracking is best-effort; never fail the fetch on either backend.
            pass

    # ---------- Subscriptions (Stripe-backed billing) -------------------
    #
    # One row per user. Missing rows mean "user is on Free tier" — the
    # billing provider treats absence as the default plan so the UI can
    # render unconditionally. The row gets written either by the Stripe
    # webhook handler (real billing) or by the Noop provider's local
    # ``record_local_subscription`` helper (dev/tests).

    def get_subscription(self, *, user_id: str) -> dict[str, Any] | None:
        """Return the raw subscriptions row for this user, or None."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, plan, status, stripe_customer_id,
                       stripe_subscription_id, current_period_end,
                       created_at, updated_at
                FROM subscriptions
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_subscription(
        self,
        *,
        user_id: str,
        plan: str,
        status: str,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        current_period_end: str | None = None,
    ) -> dict[str, Any]:
        """Create or update the subscription row for ``user_id``.

        Stripe IDs default to None so the Noop provider can write rows
        without ever touching Stripe. When the real provider fills them in
        later (via a checkout-session-completed webhook) the ON CONFLICT
        branch updates them in place.
        """
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO subscriptions
                  (user_id, plan, status, stripe_customer_id,
                   stripe_subscription_id, current_period_end,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  plan = excluded.plan,
                  status = excluded.status,
                  stripe_customer_id =
                      COALESCE(excluded.stripe_customer_id, subscriptions.stripe_customer_id),
                  stripe_subscription_id =
                      COALESCE(excluded.stripe_subscription_id, subscriptions.stripe_subscription_id),
                  current_period_end =
                      COALESCE(excluded.current_period_end, subscriptions.current_period_end),
                  updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    plan,
                    status,
                    stripe_customer_id,
                    stripe_subscription_id,
                    current_period_end,
                    now,
                    now,
                ),
            )
            connection.commit()
        row = self.get_subscription(user_id=user_id)
        # The row is written transactionally above, so it must exist here.
        return row or {
            "user_id": user_id,
            "plan": plan,
            "status": status,
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "current_period_end": current_period_end,
            "created_at": now,
            "updated_at": now,
        }

    def find_user_by_stripe_customer_id(
        self, *, stripe_customer_id: str | None,
    ) -> str | None:
        """Reverse lookup for webhook handlers.

        Stripe sends ``customer.subscription.*`` events keyed on the
        Stripe customer id, not our user id. This mapping lets the
        webhook attribute the change to the right local row. Returns
        None when the customer id is unknown (first-time webhook before
        any local state, or a spurious event).
        """
        if not stripe_customer_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM subscriptions WHERE stripe_customer_id = ?",
                (stripe_customer_id,),
            ).fetchone()
        return row["user_id"] if row else None

    # ---------- Stripe webhook idempotency ------------------------------
    # Stripe retries every webhook delivery on a non-2xx response and on
    # missed acks (their docs guarantee at-least-once delivery for ~3
    # days). Each ``event.id`` (e.g. ``evt_1OabcXYZ``) is unique per
    # event, so we record the ids we have already applied and short-
    # circuit duplicates before they hit ``_apply_stripe_event`` a
    # second time. The trio below is the full surface used by
    # ``StripeBillingProvider.handle_webhook``.

    def is_webhook_event_processed(self, *, event_id: str) -> bool:
        """Return True iff we have already applied this Stripe event.

        Empty / falsy ``event_id`` returns False so a malformed event
        (Stripe should never send one) still falls through to the apply
        path -- the apply path itself logs and ignores unknown shapes.
        """
        if not event_id:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM processed_webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return row is not None

    def mark_webhook_event_processed(
        self, *, event_id: str, event_type: str | None,
    ) -> None:
        """Record ``event_id`` as applied so a retry is a no-op.

        Called AFTER a successful ``_apply_stripe_event`` -- a failed
        apply must NOT mark the event so Stripe's next retry actually
        re-runs it. Uses ``ON CONFLICT DO NOTHING`` so a (theoretical)
        race between two parallel deliveries of the same event resolves
        cleanly: whichever one inserted first wins, the other is a
        harmless no-op.
        """
        if not event_id:
            return
        now = now_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO processed_webhook_events
                  (event_id, event_type, processed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (event_id, event_type, now),
            )
            connection.commit()

    def purge_old_webhook_events(self, *, older_than_days: int = 30) -> int:
        """Delete idempotency rows older than ``older_than_days`` days.

        Stripe stops retrying ~3 days after a delivery, so 30 days is a
        generous safety margin while keeping the table from growing
        unboundedly. Returns the number of rows deleted (best-effort:
        SQLite + Postgres both honour ``rowcount`` for plain DELETE).
        Safe to call from a background sweeper or skip entirely.
        """
        from datetime import timedelta

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM processed_webhook_events WHERE processed_at < ?",
                (cutoff,),
            )
            connection.commit()
            try:
                return int(cursor.rowcount or 0)
            except Exception:  # pragma: no cover -- backend quirk
                return 0
