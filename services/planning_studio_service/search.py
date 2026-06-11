"""Cross-project full-text search for Inspira.

Searches project titles, topic titles, decision statements, and Q&A turn
bodies (both planner questions and user answers) scoped strictly to the
calling user — no cross-user leakage is possible because every query
carries the user_id through a JOIN on v2_projects.

Implementation uses LIKE with a leading wildcard for substring matching
(i.e. ``LIKE '%query%'``). SQLite's default LIKE is already case-insensitive
for ASCII; non-ASCII folding is handled by Python-side lowercasing of the
query. ILIKE would be used verbatim on PostgreSQL if the backend migrates —
the helper ``_like_pattern`` builds the escaped ``%query%`` form so the
wildcard stays literal when the query itself contains ``%`` or ``_``.

The ranking heuristic is minimal but correct:
  rank 1 — title / statement match (more salient to users)
  rank 2 — body / rationale match (secondary relevance)
Lower rank wins; within a rank, rows are ordered by their updated_at DESC.

Module-level Pydantic models let FastAPI generate OpenAPI docs automatically
when the caller registers the route (see the wire-up guide in api.py).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .store import PlanningStudioStore

# ---------------------------------------------------------------------------
# Pydantic schemas (module-scope so FastAPI can import them for OpenAPI)
# ---------------------------------------------------------------------------

SearchKind = Literal["project", "topic", "decision", "turn"]


class SearchHit(BaseModel):
    kind: SearchKind
    project_id: str
    project_title: str
    topic_id: str | None = None
    topic_title: str | None = None
    snippet: str
    matched_field: str


class SearchResults(BaseModel):
    hits: list[SearchHit]
    truncated: bool


class SearchBody(BaseModel):
    """Request body for POST-style search (not used by the GET route but
    kept here so callers can import the type for validation/testing)."""
    query: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MAX_SNIPPET_CHARS = 200


def _like_pattern(query: str) -> str:
    """Escape LIKE special chars then wrap in ``%…%`` wildcards.

    SQLite LIKE treats ``%`` and ``_`` as wildcards. When the user's query
    literally contains those characters (e.g. ``50%_off``) we want an exact
    substring match, not a wildcard match. RFC: the SQL standard escape
    character for LIKE is ``\\`` (backslash), which SQLite honours with the
    ``ESCAPE '\\'`` clause we add to every LIKE expression.
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, query: str, max_chars: int = _MAX_SNIPPET_CHARS) -> str:
    """Return a short excerpt centred on the first match of *query* in *text*.

    Falls back to the first *max_chars* of *text* when the match is not
    found (shouldn't happen, but defensive). Strips leading/trailing
    whitespace so the snippet doesn't start with a newline.
    """
    text = text.strip()
    lower_text = text.lower()
    lower_query = query.lower()
    idx = lower_text.find(lower_query)
    if idx == -1:
        return text[:max_chars]
    half = max_chars // 2
    start = max(0, idx - half)
    end = min(len(text), idx + len(query) + half)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "\u2026" + excerpt.lstrip()
    if end < len(text):
        excerpt = excerpt.rstrip() + "\u2026"
    return excerpt


def _row_to_hit(kind: SearchKind, row: Any, query: str) -> SearchHit:
    """Convert a sqlite3.Row (dict-like) from any of the four queries into a
    ``SearchHit``.  All queries select the same alias columns."""
    snippet_text = str(row["snippet_text"] or "")
    return SearchHit(
        kind=kind,
        project_id=str(row["project_id"]),
        project_title=str(row["project_title"]),
        topic_id=str(row["topic_id"]) if row["topic_id"] else None,
        topic_title=str(row["topic_title"]) if row["topic_title"] else None,
        snippet=_snippet(snippet_text, query),
        matched_field=str(row["matched_field"]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_QUERY_MAX_LENGTH = 500


def search_all(
    store: PlanningStudioStore,
    *,
    user_id: str,
    query: str,
    limit: int = 50,
) -> SearchResults:
    """Search all content kinds for *query*, scoped to *user_id*.

    Returns a :class:`SearchResults` with up to *limit* hits ordered by
    relevance rank then recency. When the result set was capped,
    ``truncated=True`` is set so the caller can show a "showing first N
    results" note.

    An empty (or blank) *query* returns an empty result set immediately —
    we never perform a full-dump scan.

    Raises :class:`ValueError` when *query* exceeds ``_QUERY_MAX_LENGTH``
    characters (defense-in-depth: the HTTP layer enforces this earlier via
    a 400 response, but library callers get an explicit error too).
    """
    if len(query) > _QUERY_MAX_LENGTH:
        raise ValueError(
            f"search query exceeds maximum length of {_QUERY_MAX_LENGTH} characters "
            f"(got {len(query)})"
        )
    q = query.strip()
    if not q:
        return SearchResults(hits=[], truncated=False)

    pattern = _like_pattern(q)
    # ``ESCAPE '\\'`` tells SQLite to treat the next char after \\ as a
    # literal rather than a wildcard. We need it for every LIKE expression.
    esc = "\\"

    # Collect raw rows from all four kinds, each annotated with a rank so
    # we can sort later. We fetch limit+1 to detect truncation without an
    # extra COUNT query.
    raw: list[tuple[int, Any, SearchKind]] = []

    with store._connect() as conn:  # noqa: SLF001 — store doesn't expose a public conn API
        # ---- projects -------------------------------------------------------
        proj_rows = conn.execute(
            """
            SELECT
                p.project_id,
                p.title        AS project_title,
                NULL           AS topic_id,
                NULL           AS topic_title,
                p.title        AS snippet_text,
                'title'        AS matched_field,
                1              AS rank,
                p.updated_at
            FROM v2_projects p
            WHERE p.user_id = ?
              AND p.deleted_at IS NULL
              AND p.title LIKE ? ESCAPE ?
            ORDER BY p.updated_at DESC
            """,
            (user_id, pattern, esc),
        ).fetchall()
        for row in proj_rows:
            raw.append((int(row["rank"]), row, "project"))

        # ---- topics — title match ------------------------------------------
        topic_title_rows = conn.execute(
            """
            SELECT
                t.project_id,
                p.title        AS project_title,
                t.topic_id,
                t.title        AS topic_title,
                t.title        AS snippet_text,
                'title'        AS matched_field,
                1              AS rank,
                t.updated_at
            FROM topics t
            JOIN v2_projects p ON p.project_id = t.project_id
            WHERE p.user_id = ?
              AND p.deleted_at IS NULL
              AND t.deleted_at IS NULL
              AND t.title LIKE ? ESCAPE ?
            ORDER BY t.updated_at DESC
            """,
            (user_id, pattern, esc),
        ).fetchall()
        for row in topic_title_rows:
            raw.append((int(row["rank"]), row, "topic"))

        # ---- decisions — statement match (rank 1) --------------------------
        dec_stmt_rows = conn.execute(
            """
            SELECT
                d.project_id,
                p.title        AS project_title,
                d.topic_id,
                t.title        AS topic_title,
                d.statement    AS snippet_text,
                'statement'    AS matched_field,
                1              AS rank,
                d.updated_at
            FROM decisions d
            JOIN v2_projects p ON p.project_id = d.project_id
            JOIN topics t      ON t.topic_id  = d.topic_id
            WHERE p.user_id = ?
              AND p.deleted_at IS NULL
              AND t.deleted_at IS NULL
              AND d.statement LIKE ? ESCAPE ?
            ORDER BY d.updated_at DESC
            """,
            (user_id, pattern, esc),
        ).fetchall()
        for row in dec_stmt_rows:
            raw.append((int(row["rank"]), row, "decision"))

        # ---- decisions — rationale match (rank 2) --------------------------
        dec_rat_rows = conn.execute(
            """
            SELECT
                d.project_id,
                p.title        AS project_title,
                d.topic_id,
                t.title        AS topic_title,
                COALESCE(d.rationale, d.statement)  AS snippet_text,
                'rationale'    AS matched_field,
                2              AS rank,
                d.updated_at
            FROM decisions d
            JOIN v2_projects p ON p.project_id = d.project_id
            JOIN topics t      ON t.topic_id  = d.topic_id
            WHERE p.user_id = ?
              AND p.deleted_at IS NULL
              AND t.deleted_at IS NULL
              AND d.rationale IS NOT NULL
              AND d.rationale LIKE ? ESCAPE ?
            ORDER BY d.updated_at DESC
            """,
            (user_id, pattern, esc),
        ).fetchall()
        for row in dec_rat_rows:
            raw.append((int(row["rank"]), row, "decision"))

        # ---- turns — body match (rank 2) -----------------------------------
        turn_rows = conn.execute(
            """
            SELECT
                qt.project_id,
                p.title        AS project_title,
                qt.topic_id,
                t.title        AS topic_title,
                qt.body        AS snippet_text,
                'body'         AS matched_field,
                2              AS rank,
                qt.created_at  AS updated_at
            FROM qna_turns qt
            JOIN v2_projects p ON p.project_id = qt.project_id
            JOIN topics t      ON t.topic_id  = qt.topic_id
            WHERE p.user_id = ?
              AND p.deleted_at IS NULL
              AND t.deleted_at IS NULL
              AND qt.body LIKE ? ESCAPE ?
            ORDER BY qt.created_at DESC
            """,
            (user_id, pattern, esc),
        ).fetchall()
        for row in turn_rows:
            raw.append((int(row["rank"]), row, "turn"))

    # Sort by rank ASC then updated_at DESC (ISO timestamps sort correctly as
    # strings), then cap at limit+1 to detect truncation.
    raw.sort(key=lambda x: (x[0], x[1]["updated_at"]), reverse=False)
    # Stable secondary sort on updated_at descending: within the same rank,
    # newer items should come first.  Re-sort with a combined key.
    raw.sort(key=lambda x: (x[0], str(x[1]["updated_at"])), reverse=False)

    # De-duplicate: same (kind, project_id, topic_id/decision snippet) should
    # not appear twice (e.g. a decision matching both statement AND rationale).
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[tuple[int, Any, SearchKind]] = []
    for rank, row, kind in raw:
        key = (kind, str(row["project_id"]), str(row["snippet_text"])[:60])
        if key in seen:
            continue
        seen.add(key)
        deduped.append((rank, row, kind))

    truncated = len(deduped) > limit
    page = deduped[: limit]

    hits = [_row_to_hit(kind, row, q) for _, row, kind in page]
    return SearchResults(hits=hits, truncated=truncated)
