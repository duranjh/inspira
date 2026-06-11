"""JSON-to-project import for Inspira.

The canonical mirror of ``app/src/features/inspira/export.ts :: exportToJson``.
A user downloads ``{slug}-{date}.json`` from an Inspira canvas and can feed
the same blob back to create a fresh project with the same topics,
relationships, and decisions.

What round-trips
----------------
- Project title (optionally overridden by the caller).
- Topics: title, icon, status, order_index, origin, position_x/y, metadata
  (we preserve the whole metadata dict — the exporter is already the one
  that filters, not us).
- Relationships: source/target remapped via the old-id -> new-id map built
  while we insert topics; label, origin, strength preserved. Relationships
  whose endpoints aren't in the blob are silently dropped.
- Decisions: topic_id remapped the same way. We preserve statement,
  rationale, status, proposed_by. ``source_turn_id`` / ``confirmed_by_user_id``
  are dropped — turns are NOT imported (see below) and the confirmer
  belonged to the original owner, not the importer.

What is NOT imported
--------------------
- Q&A turns. The export ships them for archival, but restoring them into
  a fresh project would create rows with a foreign ``user_id`` attribution
  and a topic history the new canvas didn't actually have. If a future
  feature wants turn round-trip, build it on top of this module rather
  than bolting it onto the import path — it needs its own UX.
- Share links, scaffolds, summary versions, consistency flags. Same story
  as duplicate_v2_project: these are artefacts of the source project's
  session, not its authored content.

Validation
----------
``parse_inspira_canvas_v1`` raises ``ValueError`` on:
- missing / wrong ``schema`` tag (anything other than
  ``"inspira.canvas.v1"``),
- missing ``project`` / ``topics`` top-level keys,
- non-list ``topics`` / ``relationships`` / ``decisions``,
- a topic whose ``topic_id`` / ``title`` is missing.

Pydantic models are defined at module scope — in-function model
definitions hit a Pydantic v2 schema-rebuild bug in this codebase (see
the MarkdownImportBody precedent in ``markdown_import.py`` and the
``model_rebuild()`` call in ``api.py``).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .validators import SanitizedStr

# ---------------------------------------------------------------------------
# Pydantic models — module scope (required; in-function models hit a known
# Pydantic v2 schema-rebuild bug in this codebase).
# ---------------------------------------------------------------------------


class JsonImportBody(BaseModel):
    """Request body for POST /api/v2/projects/from-json.

    ``json_blob`` is the whole exported JSON document as a nested dict —
    i.e. what ``JSON.parse`` of the exported file returns. The frontend
    parses before POSTing so we get early validation on the browser side
    and a clean 400 from the server if the payload is malformed.

    ``title`` is an optional override; when omitted the imported project
    re-uses the title from ``json_blob.project.title`` (with a suffix
    marker so the user can tell the import apart from the original on
    the projects list).
    """

    json_blob: dict[str, Any]
    title: SanitizedStr | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_TAG = "inspira.canvas.v1"

# The allowlist of origin strings we accept for topics and relationships.
# The export never emits anything outside this set, but a hand-authored
# blob or a blob from a forked version of the app might — we coerce the
# unknown back to a safe default so a bad field can't surface an invalid
# row that other parts of the system (e.g. cards, filters) can't render.
_VALID_TOPIC_ORIGINS = frozenset(
    {"planner_initial", "planner_proposed", "user_manual"}
)
_VALID_RELATIONSHIP_ORIGINS = frozenset({"planner_inferred", "user_drawn"})
_VALID_TOPIC_STATUS = frozenset({"empty", "in_progress", "fleshed_out"})
_VALID_DECISION_STATUS = frozenset({"proposed", "confirmed", "retracted"})
_VALID_DECISION_PROPOSERS = frozenset({"planner", "user"})


# ---------------------------------------------------------------------------
# Parsed structures
# ---------------------------------------------------------------------------


class _ParsedTopic:
    """Intermediate structure for a single parsed topic."""

    def __init__(
        self,
        *,
        topic_id: str,
        title: str,
        icon: str,
        status: str,
        origin: str,
        order_index: int,
        position_x: float,
        position_y: float,
        metadata: dict[str, Any],
    ) -> None:
        self.topic_id = topic_id
        self.title = title
        self.icon = icon
        self.status = status
        self.origin = origin
        self.order_index = order_index
        self.position_x = position_x
        self.position_y = position_y
        self.metadata = metadata


class _ParsedRelationship:
    """Intermediate structure for a single parsed relationship."""

    def __init__(
        self,
        *,
        source_topic_id: str,
        target_topic_id: str,
        label: str | None,
        origin: str,
        strength: str,
    ) -> None:
        self.source_topic_id = source_topic_id
        self.target_topic_id = target_topic_id
        self.label = label
        self.origin = origin
        self.strength = strength


class _ParsedDecision:
    """Intermediate structure for a single parsed decision."""

    def __init__(
        self,
        *,
        topic_id: str,
        statement: str,
        rationale: str | None,
        status: str,
        proposed_by: str,
    ) -> None:
        self.topic_id = topic_id
        self.statement = statement
        self.rationale = rationale
        self.status = status
        self.proposed_by = proposed_by


class ParsedCanvas:
    """Result of ``parse_inspira_canvas_v1``."""

    def __init__(
        self,
        *,
        title: str,
        project_metadata: dict[str, Any],
        topics: list[_ParsedTopic],
        relationships: list[_ParsedRelationship],
        decisions: list[_ParsedDecision],
    ) -> None:
        self.title = title
        self.project_metadata = project_metadata
        self.topics = topics
        self.relationships = relationships
        self.decisions = decisions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_inspira_canvas_v1(blob: dict[str, Any]) -> ParsedCanvas:
    """Validate an ``inspira.canvas.v1`` blob and return a ``ParsedCanvas``.

    Raises ``ValueError`` for any structural problem so the caller can
    map the failure to a 400. The error messages are intentionally
    short and non-leaky — we're not trying to help an attacker probe
    internal shapes.
    """
    if not isinstance(blob, dict):
        raise ValueError("json_blob must be an object")

    schema = blob.get("schema")
    if schema != SCHEMA_TAG:
        raise ValueError(
            f"unsupported schema: expected {SCHEMA_TAG!r}, got {schema!r}",
        )

    project_blob = blob.get("project")
    if not isinstance(project_blob, dict):
        raise ValueError("missing or invalid 'project' object")

    raw_title = project_blob.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""

    project_metadata_raw = project_blob.get("metadata")
    project_metadata: dict[str, Any] = (
        dict(project_metadata_raw) if isinstance(project_metadata_raw, dict) else {}
    )

    topics_raw = blob.get("topics")
    if not isinstance(topics_raw, list):
        raise ValueError("missing or invalid 'topics' list")

    relationships_raw = blob.get("relationships", [])
    if not isinstance(relationships_raw, list):
        raise ValueError("invalid 'relationships' list")

    decisions_raw = blob.get("decisions", [])
    if not isinstance(decisions_raw, list):
        raise ValueError("invalid 'decisions' list")

    # --- Parse topics -------------------------------------------------------
    topics: list[_ParsedTopic] = []
    for idx, item in enumerate(topics_raw):
        if not isinstance(item, dict):
            raise ValueError(f"topic at index {idx} is not an object")
        topic_id = item.get("topic_id")
        if not isinstance(topic_id, str) or not topic_id:
            raise ValueError(f"topic at index {idx} is missing a topic_id")
        title_val = item.get("title")
        if not isinstance(title_val, str) or not title_val.strip():
            raise ValueError(f"topic at index {idx} is missing a title")

        icon = item.get("icon") if isinstance(item.get("icon"), str) else "flag"
        status = item.get("status")
        if status not in _VALID_TOPIC_STATUS:
            status = "empty"
        origin = item.get("origin")
        if origin not in _VALID_TOPIC_ORIGINS:
            origin = "user_manual"

        # Positions — accept ints or floats, coerce to float. Missing /
        # non-numeric → 0.0. The frontend re-layouts after import anyway,
        # so bad numbers don't wedge anything; we just guarantee the
        # column type is correct.
        position_x = _coerce_float(item.get("position_x"))
        position_y = _coerce_float(item.get("position_y"))

        # ``order_index`` falls back to the list index so imports without
        # explicit ordering still preserve the array order.
        order_raw = item.get("order_index")
        order_index = (
            int(order_raw) if isinstance(order_raw, (int, float)) else idx
        )

        metadata_raw = item.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}

        topics.append(
            _ParsedTopic(
                topic_id=topic_id,
                title=title_val.strip(),
                icon=icon or "flag",
                status=status,
                origin=origin,
                order_index=order_index,
                position_x=position_x,
                position_y=position_y,
                metadata=metadata,
            ),
        )

    # --- Parse relationships ------------------------------------------------
    # We tolerate a relationship whose endpoints aren't in the topics set —
    # we'll drop it at instantiation time rather than erroring out. That
    # keeps importing a slightly-stale blob resilient.
    relationships: list[_ParsedRelationship] = []
    for idx, item in enumerate(relationships_raw):
        if not isinstance(item, dict):
            raise ValueError(f"relationship at index {idx} is not an object")
        src = item.get("source_topic_id")
        tgt = item.get("target_topic_id")
        if not isinstance(src, str) or not isinstance(tgt, str):
            raise ValueError(
                f"relationship at index {idx} is missing source/target topic_id",
            )
        label_raw = item.get("label")
        label = label_raw if isinstance(label_raw, str) else None
        origin = item.get("origin")
        if origin not in _VALID_RELATIONSHIP_ORIGINS:
            origin = "user_drawn"
        strength_raw = item.get("strength")
        strength = (
            strength_raw
            if isinstance(strength_raw, str) and strength_raw
            else "confirmed"
        )
        relationships.append(
            _ParsedRelationship(
                source_topic_id=src,
                target_topic_id=tgt,
                label=label,
                origin=origin,
                strength=strength,
            ),
        )

    # --- Parse decisions ----------------------------------------------------
    decisions: list[_ParsedDecision] = []
    for idx, item in enumerate(decisions_raw):
        if not isinstance(item, dict):
            raise ValueError(f"decision at index {idx} is not an object")
        decision_topic = item.get("topic_id")
        statement = item.get("statement")
        if not isinstance(decision_topic, str) or not decision_topic:
            raise ValueError(
                f"decision at index {idx} is missing a topic_id",
            )
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(
                f"decision at index {idx} is missing a statement",
            )

        rationale_raw = item.get("rationale")
        rationale = rationale_raw if isinstance(rationale_raw, str) else None

        status = item.get("status")
        if status not in _VALID_DECISION_STATUS:
            status = "proposed"

        proposed_by = item.get("proposed_by")
        if proposed_by not in _VALID_DECISION_PROPOSERS:
            proposed_by = "user"

        decisions.append(
            _ParsedDecision(
                topic_id=decision_topic,
                statement=statement.strip(),
                rationale=rationale,
                status=status,
                proposed_by=proposed_by,
            ),
        )

    return ParsedCanvas(
        title=title,
        project_metadata=project_metadata,
        topics=topics,
        relationships=relationships,
        decisions=decisions,
    )


def instantiate_from_json(
    store: Any,
    *,
    user_id: str,
    parsed: ParsedCanvas,
    title_override: str | None = None,
) -> dict[str, Any]:
    """Create a project + topics + relationships + decisions from a ParsedCanvas.

    Returns the project dict (same shape as ``store.create_v2_project``).
    Relationships whose endpoints aren't in the parsed topics list are
    dropped silently; decisions pinned to an unknown topic are likewise
    dropped. Every created row gets a fresh ID; the old-id → new-id map
    is the only thing that keeps the graph stitched together.
    """
    final_title = (
        title_override
        or parsed.title
        or "Imported project"
    ).strip() or "Imported project"

    # Create the project row first.
    project = store.create_v2_project(user_id=user_id, title=final_title)
    project_id = project["project_id"]

    # If the blob carried project-level metadata, persist it. This uses the
    # same direct-SQL approach as ``instantiate_from_markdown`` because the
    # store doesn't expose a top-level project-metadata setter. Non-critical:
    # the project is already on disk, so a failure here at worst drops
    # metadata, not the whole import.
    if parsed.project_metadata:
        import json as _json  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415

        _now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn = store._connect()
            conn.execute(
                "UPDATE v2_projects SET metadata_json = ?, updated_at = ? WHERE project_id = ?",
                (_json.dumps(parsed.project_metadata), _now, project_id),
            )
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001 — non-critical; see docstring.
            pass

    # --- Topics — build old-id → new-id map for relationship / decision wiring.
    topic_id_map: dict[str, str] = {}
    for topic in parsed.topics:
        persisted = store.create_topic(
            project_id=project_id,
            title=topic.title,
            icon=topic.icon,
            position_x=topic.position_x,
            position_y=topic.position_y,
            origin=topic.origin,
            order_index=topic.order_index,
            metadata=topic.metadata,
            user_id=user_id,
        )
        topic_id_map[topic.topic_id] = persisted["topic_id"]
        # Status is not settable via create_topic (always starts "empty"),
        # so we reach for update_topic to honour the imported status.
        if topic.status != "empty":
            store.update_topic(
                persisted["topic_id"],
                user_id=user_id,
                status=topic.status,
            )

    # --- Relationships — drop orphans whose endpoints we didn't import.
    for rel in parsed.relationships:
        new_src = topic_id_map.get(rel.source_topic_id)
        new_tgt = topic_id_map.get(rel.target_topic_id)
        if not new_src or not new_tgt or new_src == new_tgt:
            continue
        store.create_relationship(
            project_id=project_id,
            source_topic_id=new_src,
            target_topic_id=new_tgt,
            label=rel.label,
            origin=rel.origin,
            strength=rel.strength,
            user_id=user_id,
        )

    # --- Decisions — same orphan-drop guard.
    for decision in parsed.decisions:
        new_topic = topic_id_map.get(decision.topic_id)
        if not new_topic:
            continue
        store.create_decision(
            topic_id=new_topic,
            project_id=project_id,
            statement=decision.statement,
            proposed_by=decision.proposed_by,
            rationale=decision.rationale,
            status=decision.status,
            user_id=user_id,
        )

    return project


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> float:
    """Coerce ``value`` to float, returning 0.0 on anything non-numeric.

    Lets us accept ints, floats, numeric strings, and bad data alike
    without the import failing over a position field the layout pass
    will overwrite anyway.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0
