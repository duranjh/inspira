"""Handlers for the 11 MCP / OpenAPI tool operations.

Every handler:

- Takes ``store: PlanningStudioStore``, ``user_id: str``, and the already-
  validated Pydantic input model.
- Enforces IDOR at the store boundary (``verify_project_ownership`` /
  ``get_topic_with_ownership`` / ``get_*_with_ownership``). A failure
  surfaces as ``ToolError("not_found")`` — callers (both MCP and FastAPI)
  map that to a 404 shape so IDs stay un-enumerable.
- Returns a Pydantic output model (or raises ``ToolError``) — never a raw
  dict. This keeps the serialisation contract tight: changing the output
  shape of a tool means changing its schema in ``schemas.py``, and both
  callers pick the change up automatically.

The handlers live here (not inside the MCP or the OpenAPI modules) because
the two surfaces MUST stay in sync. Both drive this module; if you add a
new tool, you add it here first, then expose it on both sides.
"""
from __future__ import annotations

import logging
from typing import Any

from planning_studio_service.store import PlanningStudioStore

from .markdown_export import project_to_markdown
from .schemas import (
    AddDecisionInput,
    AddDecisionOutput,
    AddRelationshipInput,
    AddRelationshipOutput,
    AddTopicInput,
    AddTopicOutput,
    CreateCanvasInput,
    CreateCanvasOutput,
    DeleteTopicInput,
    DeleteTopicOutput,
    ExportMarkdownInput,
    ExportMarkdownOutput,
    GetSummaryInput,
    GetSummaryOutput,
    ListProjectsInput,
    ListProjectsOutput,
    ListTopicsInput,
    ListTopicsOutput,
    ProjectSummaryOut,
    RecordAnswerInput,
    RecordAnswerOutput,
    TopicOut,
    UpdateTopicInput,
    UpdateTopicOutput,
)

logger = logging.getLogger("inspira_mcp.handlers")


class ToolError(Exception):
    """Raised when a handler cannot satisfy the request.

    The ``status`` attribute maps cleanly to an HTTP code (FastAPI) and
    an MCP error reply. The ``reason`` is a short machine-friendly slug
    — callers surface it in the error body, humans read the
    ``str(error)`` for the sentence.
    """

    def __init__(self, reason: str, *, status: int = 400, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.status = status


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _topic_out(raw: dict[str, Any]) -> TopicOut:
    """Coerce a store topic dict into the TopicOut model."""
    return TopicOut.model_validate(raw)


def _owned_topic_or_404(
    store: PlanningStudioStore, *, topic_id: str, user_id: str,
) -> dict[str, Any]:
    topic = store.get_topic_with_ownership(topic_id, user_id=user_id)
    if topic is None:
        raise ToolError("topic_not_found", status=404)
    return topic


def _owned_project_or_404(
    store: PlanningStudioStore, *, project_id: str, user_id: str,
) -> None:
    if not store.verify_project_ownership(project_id=project_id, user_id=user_id):
        raise ToolError("project_not_found", status=404)


# ---------------------------------------------------------------------------
# 1. create_canvas
# ---------------------------------------------------------------------------


def create_canvas(
    store: PlanningStudioStore, user_id: str, payload: CreateCanvasInput,
) -> CreateCanvasOutput:
    """Create an empty canvas owned by ``user_id``.

    The canvas starts with zero topics. The MCP client (Claude / ChatGPT)
    is expected to call ``add_topic`` once per topic it wants to seed.
    We don't invoke the planner adapter here — the whole point of this
    surface is that the model on the other end IS the planner.
    """
    idea = payload.idea.strip()
    if not idea:
        raise ToolError("idea_required")
    title = (payload.title or "").strip() or idea[:120]
    project = store.create_v2_project(user_id=user_id, title=title)
    return CreateCanvasOutput(
        project_id=project["project_id"],
        title=project["title"],
        initial_topic_ids=[],
    )


# ---------------------------------------------------------------------------
# 2. list_projects
# ---------------------------------------------------------------------------


def list_projects(
    store: PlanningStudioStore, user_id: str, _payload: ListProjectsInput,
) -> ListProjectsOutput:
    """Active canvases owned by the caller, most-recent first."""
    raw_projects = store.list_v2_projects(user_id=user_id)
    summaries: list[ProjectSummaryOut] = []
    for project in raw_projects:
        topics = store.list_topics(
            project_id=project["project_id"], user_id=user_id,
        )
        summaries.append(
            ProjectSummaryOut(
                project_id=project["project_id"],
                title=project["title"],
                updated_at=project["updated_at"],
                topic_count=len(topics),
            )
        )
    return ListProjectsOutput(projects=summaries)


# ---------------------------------------------------------------------------
# 3. list_topics
# ---------------------------------------------------------------------------


def list_topics(
    store: PlanningStudioStore, user_id: str, payload: ListTopicsInput,
) -> ListTopicsOutput:
    _owned_project_or_404(store, project_id=payload.project_id, user_id=user_id)
    raw = store.list_topics(project_id=payload.project_id, user_id=user_id)
    return ListTopicsOutput(topics=[_topic_out(t) for t in raw])


# ---------------------------------------------------------------------------
# 4. add_topic
# ---------------------------------------------------------------------------


def add_topic(
    store: PlanningStudioStore, user_id: str, payload: AddTopicInput,
) -> AddTopicOutput:
    _owned_project_or_404(store, project_id=payload.project_id, user_id=user_id)
    title = payload.title.strip()
    if not title:
        raise ToolError("title_required")
    icon = (payload.icon or "").strip() or "flag"
    metadata: dict[str, Any] = {}
    if payload.why and payload.why.strip():
        metadata["why_this_topic"] = payload.why.strip()
    topic = store.create_topic(
        project_id=payload.project_id,
        title=title,
        icon=icon,
        origin="user_manual",
        metadata=metadata or None,
        user_id=user_id,
    )
    return AddTopicOutput(topic=_topic_out(topic))


# ---------------------------------------------------------------------------
# 5. update_topic
# ---------------------------------------------------------------------------


def update_topic(
    store: PlanningStudioStore, user_id: str, payload: UpdateTopicInput,
) -> UpdateTopicOutput:
    _owned_topic_or_404(store, topic_id=payload.topic_id, user_id=user_id)
    fields: dict[str, Any] = {}
    if payload.title is not None:
        new_title = payload.title.strip()
        if not new_title:
            raise ToolError("title_required")
        fields["title"] = new_title
    if payload.icon is not None:
        new_icon = payload.icon.strip()
        if new_icon:
            fields["icon"] = new_icon
    if not fields:
        # No-op update: return the topic unchanged rather than 400.
        # The client may legitimately call with no fields set after
        # diffing against a stale copy; keeping this idempotent is
        # more forgiving than forcing them to skip the call.
        current = store.get_topic(payload.topic_id, user_id=user_id)
        assert current is not None  # ownership check above guarantees presence
        return UpdateTopicOutput(topic=_topic_out(current))
    updated = store.update_topic(payload.topic_id, user_id=user_id, **fields)
    if updated is None:
        raise ToolError("topic_not_found", status=404)
    return UpdateTopicOutput(topic=_topic_out(updated))


# ---------------------------------------------------------------------------
# 6. delete_topic
# ---------------------------------------------------------------------------


def delete_topic(
    store: PlanningStudioStore, user_id: str, payload: DeleteTopicInput,
) -> DeleteTopicOutput:
    _owned_topic_or_404(store, topic_id=payload.topic_id, user_id=user_id)
    deleted = store.delete_topic(payload.topic_id, user_id=user_id)
    return DeleteTopicOutput(deleted=deleted)


# ---------------------------------------------------------------------------
# 7. add_relationship
# ---------------------------------------------------------------------------


def add_relationship(
    store: PlanningStudioStore, user_id: str, payload: AddRelationshipInput,
) -> AddRelationshipOutput:
    _owned_project_or_404(store, project_id=payload.project_id, user_id=user_id)
    # Both endpoints of the relationship must be topics the caller owns
    # inside THIS project — reject cross-project edges even if both topics
    # belong to the caller (that would otherwise be a subtle way to
    # smuggle a topic from project A into project B's graph).
    src = _owned_topic_or_404(store, topic_id=payload.from_topic_id, user_id=user_id)
    tgt = _owned_topic_or_404(store, topic_id=payload.to_topic_id, user_id=user_id)
    if src["project_id"] != payload.project_id or tgt["project_id"] != payload.project_id:
        raise ToolError("topic_not_found", status=404)
    if payload.from_topic_id == payload.to_topic_id:
        raise ToolError("self_relationship")
    rel = store.create_relationship(
        project_id=payload.project_id,
        source_topic_id=payload.from_topic_id,
        target_topic_id=payload.to_topic_id,
        label=(payload.label or "").strip() or None,
        origin="user_drawn",
        user_id=user_id,
    )
    return AddRelationshipOutput(relationship_id=rel["relationship_id"])


# ---------------------------------------------------------------------------
# 8. record_answer
# ---------------------------------------------------------------------------


def record_answer(
    store: PlanningStudioStore, user_id: str, payload: RecordAnswerInput,
) -> RecordAnswerOutput:
    """Persist a planner/user Q&A pair onto a topic.

    The MCP client is the planner — the question was generated by Claude
    or ChatGPT and the answer is what the user typed back at them. We
    append two rows to ``qna_turns``: one with role=planner, one with
    role=user. The user turn's turn_id is returned because that's the
    row downstream code (history, summary regeneration) reads as "the
    user's last contribution".
    """
    topic = _owned_topic_or_404(store, topic_id=payload.topic_id, user_id=user_id)
    question = payload.question.strip()
    answer = payload.answer.strip()
    if not question:
        raise ToolError("question_required")
    if not answer:
        raise ToolError("answer_required")
    planner_turn = store.append_qna_turn(
        topic_id=payload.topic_id,
        project_id=topic["project_id"],
        role="planner",
        body=question,
        status="answered",
        user_id=user_id,
    )
    user_turn = store.append_qna_turn(
        topic_id=payload.topic_id,
        project_id=topic["project_id"],
        role="user",
        body=answer,
        status="answered",
        parent_turn_id=planner_turn["turn_id"],
        user_id=user_id,
    )
    # Mark the topic as in_progress on first Q&A exchange; leave it at
    # fleshed_out if the client has already pushed it that far. We do
    # NOT downgrade.
    if topic.get("status") == "empty":
        store.update_topic(payload.topic_id, user_id=user_id, status="in_progress")
    return RecordAnswerOutput(turn_id=user_turn["turn_id"])


# ---------------------------------------------------------------------------
# 9. add_decision
# ---------------------------------------------------------------------------


def add_decision(
    store: PlanningStudioStore, user_id: str, payload: AddDecisionInput,
) -> AddDecisionOutput:
    topic = _owned_topic_or_404(store, topic_id=payload.topic_id, user_id=user_id)
    statement = payload.statement.strip()
    if not statement:
        raise ToolError("statement_required")
    rationale = (payload.rationale or "").strip() or None
    # proposed_by="user" reflects the real chain of custody: Inspira's
    # planner didn't author this; the MCP client pushed it at our
    # direction. The decision's audit trail records "user" so downstream
    # consumers (UI, exports) don't misattribute it to our in-app model.
    decision = store.create_decision(
        topic_id=payload.topic_id,
        project_id=topic["project_id"],
        statement=statement,
        rationale=rationale,
        proposed_by="user",
        status="confirmed",
        user_id=user_id,
    )
    return AddDecisionOutput(decision_id=decision["decision_id"])


# ---------------------------------------------------------------------------
# 10. get_summary
# ---------------------------------------------------------------------------


def get_summary(
    store: PlanningStudioStore, user_id: str, payload: GetSummaryInput,
) -> GetSummaryOutput:
    _owned_project_or_404(store, project_id=payload.project_id, user_id=user_id)
    topics = store.list_topics(project_id=payload.project_id, user_id=user_id)
    decisions = store.list_decisions(project_id=payload.project_id, user_id=user_id)
    latest = store.latest_summary_version(project_id=payload.project_id)
    summary_text = ""
    last_updated: str | None = None
    if latest is not None:
        summary_text = str(latest.get("content_markdown") or "").strip()
        last_updated = str(latest.get("created_at") or "") or None
    if not last_updated and topics:
        # Fall back to the most recent topic update when no summary version
        # has been materialised yet. Better signal than "never" for the
        # MCP client's next-turn reasoning.
        last_updated = max(
            (str(t.get("updated_at") or "") for t in topics),
            default=None,
        )
    return GetSummaryOutput(
        summary=summary_text,
        topic_count=len(topics),
        decision_count=len(decisions),
        last_updated=last_updated,
    )


# ---------------------------------------------------------------------------
# 11. export_markdown
# ---------------------------------------------------------------------------


def export_markdown(
    store: PlanningStudioStore, user_id: str, payload: ExportMarkdownInput,
) -> ExportMarkdownOutput:
    _owned_project_or_404(store, project_id=payload.project_id, user_id=user_id)
    project = store._get_v2_project(payload.project_id)
    # Guarded by _owned_project_or_404 above; if we got here the project
    # exists and belongs to the caller. The None case is unreachable but
    # we handle it defensively so the type-checker doesn't complain.
    assert project is not None
    topics = store.list_topics(project_id=payload.project_id, user_id=user_id)
    relationships = store.list_relationships(
        project_id=payload.project_id, user_id=user_id,
    )
    decisions = store.list_decisions(
        project_id=payload.project_id, user_id=user_id,
    )
    decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions:
        decisions_by_topic.setdefault(decision["topic_id"], []).append(decision)
    turns_by_topic: dict[str, list[dict[str, Any]]] = {}
    for topic in topics:
        turns_by_topic[topic["topic_id"]] = store.list_qna_turns(
            topic_id=topic["topic_id"], user_id=user_id,
        )
    markdown = project_to_markdown(
        project_title=str(project.get("title") or ""),
        topics=topics,
        relationships=relationships,
        decisions_by_topic_id=decisions_by_topic,
        turns_by_topic_id=turns_by_topic,
    )
    return ExportMarkdownOutput(markdown=markdown)


# ---------------------------------------------------------------------------
# Public dispatch table — used by both server.py and the FastAPI routes.
# ---------------------------------------------------------------------------


HANDLERS: dict[str, Any] = {
    "create_canvas": create_canvas,
    "list_projects": list_projects,
    "list_topics": list_topics,
    "add_topic": add_topic,
    "update_topic": update_topic,
    "delete_topic": delete_topic,
    "add_relationship": add_relationship,
    "record_answer": record_answer,
    "add_decision": add_decision,
    "get_summary": get_summary,
    "export_markdown": export_markdown,
}
