"""Markdown-to-project import for Inspira.

Parses a raw markdown document (Notion/Obsidian export, brain dump, outline)
and creates a project with topics, seeded decisions, and an opening note.

Parsing rules
-------------
- YAML frontmatter (``---`` / ``---``) → project metadata; unknown fields
  silently ignored.
- First ``# H1`` → project title (falls back to ``title_override`` or
  "Imported project").
- ``## H2`` headings → one topic each.  The prose block immediately below
  an H2 (before the next heading) becomes a ``context_note`` decision on
  that topic so content is never lost.
- ``### H3`` lines under an H2 → short "decision" entries on that topic.
- Any content before the first ``## H2`` (but after the H1 / frontmatter)
  → ``opening_note`` on the project (stored in metadata).

Topic cap
---------
Topics are capped at 20 to avoid pathologically large canvases.  Content
beyond the cap is silently dropped (the raw markdown is always preserved by
the user, so nothing is permanently lost).

Layout
------
``instantiate_from_markdown`` places topics in a two-row zigzag that matches
the template + kickoff patterns already in ``api.py``.  There is no Python
port of the TS ``computeTopicLayout``/``ensureNoOverlaps``; the frontend runs
its own layout pass after opening the canvas and persists updated positions
via ``api.updateTopic``, so the server-side positions only need to be a
reasonable first approximation.

Pydantic models are defined at module scope — in-function model definitions
cause Pydantic v2 schema-rebuild errors (known codebase bug).
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from .validators import SanitizedStr

# ---------------------------------------------------------------------------
# Pydantic models — module scope (required; in-function models hit a known
# Pydantic v2 schema-rebuild bug in this codebase).
# ---------------------------------------------------------------------------

class MarkdownImportBody(BaseModel):
    """Request body for POST /api/v2/projects/from-markdown."""

    markdown: SanitizedStr
    title: SanitizedStr | None = None


class _ParsedTopic:
    """Intermediate structure for a single parsed H2 section."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.context_note: str = ""   # prose block below the H2
        self.decisions: list[str] = []  # H3 lines under this H2


class ParsedImport:
    """Result of ``parse_markdown``."""

    def __init__(
        self,
        *,
        title: str,
        opening_note: str,
        topics: list[_ParsedTopic],
        metadata: dict[str, Any],
    ) -> None:
        self.title = title
        self.opening_note = opening_note
        self.topics = topics
        self.metadata = metadata


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_TOPICS = 20
_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)

# Known project metadata fields we accept from frontmatter.  Any key not in
# this set is silently dropped.
_KNOWN_METADATA_KEYS = frozenset(
    {"description", "domain", "tags", "author", "source", "created", "updated"}
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_markdown(raw: str) -> ParsedImport:
    """Parse a raw markdown string into a ``ParsedImport`` structure.

    Raises ``ValueError`` if ``raw`` is empty or whitespace-only.
    """
    if not raw or not raw.strip():
        raise ValueError("markdown is empty")

    # --- Strip and parse frontmatter ----------------------------------------
    metadata: dict[str, Any] = {}
    body = raw
    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        body = raw[fm_match.end():]
        metadata = _parse_frontmatter(fm_match.group(1))

    # --- Extract H1 (project title) -----------------------------------------
    h1_match = _H1_RE.search(body)
    h1_title = h1_match.group(1).strip() if h1_match else ""

    # --- Split into H2-delimited sections ------------------------------------
    # Find all H2 positions so we can slice the body between them.
    h2_positions: list[tuple[int, str]] = [
        (m.start(), m.group(1).strip())
        for m in _H2_RE.finditer(body)
    ]

    # Content before the first H2 (but after any H1) → opening_note.
    if h2_positions:
        pre_h2 = body[: h2_positions[0][0]]
    else:
        pre_h2 = body
    opening_note = _extract_opening_note(pre_h2, h1_title)

    # --- Parse each H2 section (capped at _MAX_TOPICS) -----------------------
    topics: list[_ParsedTopic] = []
    for idx, (h2_start, h2_title) in enumerate(h2_positions[:_MAX_TOPICS]):
        # The section body runs from just after the H2 line to the next H2
        # (or end of document).
        section_end = (
            h2_positions[idx + 1][0]
            if idx + 1 < len(h2_positions)
            else len(body)
        )
        # Find end of the H2 heading line itself.
        h2_line_end = body.index("\n", h2_start) + 1 if "\n" in body[h2_start:] else len(body)
        section_body = body[h2_line_end:section_end]

        topic = _ParsedTopic(h2_title)

        # H3 headings under this H2 → decision statements
        h3_matches = list(_H3_RE.finditer(section_body))
        for m in h3_matches:
            topic.decisions.append(m.group(1).strip())

        # Prose in the section (everything that is NOT an H3 line or the H2
        # line itself).  We strip H3 lines out then clean up blank runs.
        section_prose = _H3_RE.sub("", section_body).strip()
        if section_prose:
            topic.context_note = section_prose

        topics.append(topic)

    return ParsedImport(
        title=h1_title,
        opening_note=opening_note,
        topics=topics,
        metadata=metadata,
    )


def instantiate_from_markdown(
    store: Any,
    *,
    user_id: str,
    parsed: ParsedImport,
    title_override: str | None = None,
) -> dict[str, Any]:
    """Create a project, topics, and seed decisions from a ``ParsedImport``.

    Returns the created project dict (same shape as ``store.create_v2_project``).
    Seed decisions are created as a side-effect and are NOT included in the
    return value — the caller can fetch them via ``store.list_decisions`` if
    needed.
    """
    # Resolve final title
    final_title = (
        title_override
        or parsed.title
        or "Imported project"
    ).strip() or "Imported project"

    # Build project metadata from frontmatter + opening note
    project_metadata: dict[str, Any] = {k: v for k, v in parsed.metadata.items()}
    if parsed.opening_note:
        project_metadata["opening_note"] = parsed.opening_note

    # Create the project row
    project = store.create_v2_project(user_id=user_id, title=final_title)
    project_id = project["project_id"]

    # Store opening_note in project metadata if present
    if project_metadata:
        import json as _json
        import sqlite3 as _sqlite3
        from datetime import datetime, timezone

        _now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn = store._connect()
            conn.execute(
                "UPDATE v2_projects SET metadata_json = ?, updated_at = ? WHERE project_id = ?",
                (_json.dumps(project_metadata), _now, project_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # Non-critical; project is already created

    # Layout: two-row zigzag (same pattern as templates + kickoff in api.py)
    x_step, y_rows = 440, [0, 320]

    persisted_topics: list[dict[str, Any]] = []

    for idx, topic in enumerate(parsed.topics):
        # Choose a readable icon based on order
        icon = _pick_icon(idx)

        persisted = store.create_topic(
            project_id=project_id,
            title=topic.title,
            icon=icon,
            position_x=float((idx // len(y_rows)) * x_step),
            position_y=float(y_rows[idx % len(y_rows)]),
            origin="planner_initial",
            order_index=idx,
            metadata={"why_this_topic": "Imported from markdown."},
            user_id=user_id,
        )
        persisted_topics.append(persisted)
        topic_id = persisted["topic_id"]

        # Seed context_note as a decision so prose content isn't lost
        if topic.context_note:
            store.create_decision(
                topic_id=topic_id,
                project_id=project_id,
                statement=topic.context_note[:2000],  # guard against runaway prose
                proposed_by="planner",
                rationale="Context imported from markdown.",
                status="proposed",
                user_id=user_id,
            )

        # H3 lines → individual decisions
        for decision_text in topic.decisions:
            if decision_text:
                store.create_decision(
                    topic_id=topic_id,
                    project_id=project_id,
                    statement=decision_text[:500],
                    proposed_by="planner",
                    rationale=None,
                    status="proposed",
                    user_id=user_id,
                )

    return project


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(raw_yaml: str) -> dict[str, Any]:
    """Parse YAML frontmatter into a dict, dropping unknown keys.

    We avoid pulling in a full YAML library; frontmatter in Notion/Obsidian
    exports consists almost entirely of simple ``key: value`` pairs.  We
    handle that case directly; multi-line values and complex types are
    treated as raw strings.
    """
    result: dict[str, Any] = {}
    for line in raw_yaml.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if key in _KNOWN_METADATA_KEYS:
                result[key] = value
    return result


def _extract_opening_note(pre_h2: str, h1_title: str) -> str:
    """Return the prose that precedes the first H2, minus the H1 line itself."""
    # Remove the H1 line
    note = _H1_RE.sub("", pre_h2).strip()
    # Drop any remaining heading lines (e.g. a stray H1 duplicate)
    note = re.sub(r"^#{1,6}\s+.+$", "", note, flags=re.MULTILINE).strip()
    return note


# Icon pool — cycles through a small set of readable canvas icons.
_ICON_POOL = [
    "flag", "map-pin", "chart", "heart", "clock",
    "star", "tag", "file", "link", "bolt",
]


def _pick_icon(idx: int) -> str:
    return _ICON_POOL[idx % len(_ICON_POOL)]
