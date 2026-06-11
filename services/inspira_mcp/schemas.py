"""Pydantic schemas shared between the MCP server and the OpenAPI Actions surface.

Keeping the schemas in one module means a change to a tool's input or output
shape lands in exactly one place, and both surfaces stay in sync automatically.
The MCP SDK consumes these models as JSON Schema (via
``model_json_schema()``); FastAPI consumes them directly as request/response
bodies on the ``/api/v2/mcp/*`` routes.

Design rules:
- All models use ``model_config = ConfigDict(extra="forbid")`` so an
  unknown key coming in through either surface raises a 422 (FastAPI) or
  an MCP validation error, never a silent drop.
- Optional inputs default to ``None`` and handlers treat them as "use the
  existing value / leave unset". Passing an empty string to a text field
  that's documented as "trim to None" is equivalent to omitting it.
- Output models mirror the shape of the existing ``store`` return values
  with every field typed. A handler that returns a dict from the store
  is wrapped with ``Model.model_validate(dict)`` at the edge.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared output fragments
# ---------------------------------------------------------------------------


class TopicOut(BaseModel):
    """A topic as returned by list_topics / add_topic / update_topic."""

    model_config = ConfigDict(extra="allow")

    topic_id: str
    project_id: str
    title: str
    icon: str
    status: str = "empty"
    order_index: int = 0
    origin: str = "user_manual"
    position_x: float = 0.0
    position_y: float = 0.0
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectSummaryOut(BaseModel):
    """Lightweight project record for list_projects output."""

    project_id: str
    title: str
    updated_at: str
    topic_count: int


class RelationshipOut(BaseModel):
    relationship_id: str
    project_id: str
    source_topic_id: str
    target_topic_id: str
    label: str | None = None
    origin: str = "user_drawn"


class DecisionOut(BaseModel):
    decision_id: str
    topic_id: str
    project_id: str
    statement: str
    rationale: str | None = None
    status: str = "proposed"
    created_at: str


class QnaTurnOut(BaseModel):
    turn_id: str
    topic_id: str
    project_id: str
    role: str
    body: str
    created_at: str


# ---------------------------------------------------------------------------
# 1. create_canvas
# ---------------------------------------------------------------------------


class CreateCanvasInput(BaseModel):
    """Spin up a new v2 canvas from a one-line idea.

    The handler creates an empty v2 project owned by the caller. It does
    NOT invoke the planner / LLM — the MCP client (Claude / ChatGPT) is
    the planner. The handler returns the freshly-minted project id plus
    an empty topic list; the client is expected to follow up with
    ``add_topic`` calls for each topic it wants to seed.

    Callers can pass a ``title`` to override the default (derived from the
    idea). An empty or whitespace-only title falls through to the default.
    """

    model_config = ConfigDict(extra="forbid")

    idea: str = Field(..., min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=200)


class CreateCanvasOutput(BaseModel):
    project_id: str
    title: str
    initial_topic_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. list_projects
# ---------------------------------------------------------------------------


class ListProjectsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListProjectsOutput(BaseModel):
    projects: list[ProjectSummaryOut]


# ---------------------------------------------------------------------------
# 3. list_topics
# ---------------------------------------------------------------------------


class ListTopicsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)


class ListTopicsOutput(BaseModel):
    topics: list[TopicOut]


# ---------------------------------------------------------------------------
# 4. add_topic
# ---------------------------------------------------------------------------


class AddTopicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=400)
    icon: str | None = Field(default=None, max_length=40)
    why: str | None = Field(default=None, max_length=2000)


class AddTopicOutput(BaseModel):
    topic: TopicOut


# ---------------------------------------------------------------------------
# 5. update_topic
# ---------------------------------------------------------------------------


class UpdateTopicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(..., min_length=1)
    title: str | None = Field(default=None, max_length=400)
    icon: str | None = Field(default=None, max_length=40)


class UpdateTopicOutput(BaseModel):
    topic: TopicOut


# ---------------------------------------------------------------------------
# 6. delete_topic
# ---------------------------------------------------------------------------


class DeleteTopicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(..., min_length=1)


class DeleteTopicOutput(BaseModel):
    deleted: bool


# ---------------------------------------------------------------------------
# 7. add_relationship
# ---------------------------------------------------------------------------


class AddRelationshipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    from_topic_id: str = Field(..., min_length=1)
    to_topic_id: str = Field(..., min_length=1)
    label: str | None = Field(default=None, max_length=120)


class AddRelationshipOutput(BaseModel):
    relationship_id: str


# ---------------------------------------------------------------------------
# 8. record_answer
# ---------------------------------------------------------------------------


class RecordAnswerInput(BaseModel):
    """Append a user-visible Q&A exchange onto a topic.

    This is the KEY tool for the MCP surface — the model (Claude / ChatGPT)
    is the planner, so it formulates the question and the user's answer
    both sit in this payload. Inspira persists the pair and does NOT
    invoke its own planner adapter.
    """

    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=4000)
    answer: str = Field(..., min_length=1, max_length=8000)


class RecordAnswerOutput(BaseModel):
    turn_id: str


# ---------------------------------------------------------------------------
# 9. add_decision
# ---------------------------------------------------------------------------


class AddDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(..., min_length=1)
    statement: str = Field(..., min_length=1, max_length=1200)
    rationale: str | None = Field(default=None, max_length=4000)


class AddDecisionOutput(BaseModel):
    decision_id: str


# ---------------------------------------------------------------------------
# 10. get_summary
# ---------------------------------------------------------------------------


class GetSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)


class GetSummaryOutput(BaseModel):
    summary: str
    topic_count: int
    decision_count: int
    last_updated: str | None = None


# ---------------------------------------------------------------------------
# 11. export_markdown
# ---------------------------------------------------------------------------


class ExportMarkdownInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)


class ExportMarkdownOutput(BaseModel):
    markdown: str


# ---------------------------------------------------------------------------
# Canonical tool spec: one entry per operation with MCP + OpenAPI metadata.
# ---------------------------------------------------------------------------


TOOL_SPEC: list[dict[str, Any]] = [
    {
        "name": "create_canvas",
        "description": "Create a new Inspira canvas (project) from a one-line idea.",
        "input": CreateCanvasInput,
        "output": CreateCanvasOutput,
    },
    {
        "name": "list_projects",
        "description": "List the authenticated user's active canvases.",
        "input": ListProjectsInput,
        "output": ListProjectsOutput,
    },
    {
        "name": "list_topics",
        "description": "List all topics on a canvas.",
        "input": ListTopicsInput,
        "output": ListTopicsOutput,
    },
    {
        "name": "add_topic",
        "description": "Add a topic card to a canvas.",
        "input": AddTopicInput,
        "output": AddTopicOutput,
    },
    {
        "name": "update_topic",
        "description": "Rename a topic or change its icon.",
        "input": UpdateTopicInput,
        "output": UpdateTopicOutput,
    },
    {
        "name": "delete_topic",
        "description": "Soft-delete a topic from a canvas.",
        "input": DeleteTopicInput,
        "output": DeleteTopicOutput,
    },
    {
        "name": "add_relationship",
        "description": "Draw a dotted connection between two topics.",
        "input": AddRelationshipInput,
        "output": AddRelationshipOutput,
    },
    {
        "name": "record_answer",
        "description": (
            "Append a Q&A exchange onto a topic. The MCP client supplies "
            "the question it asked and the user's answer — Inspira does "
            "NOT run its own planner. Use this tool to persist the "
            "conversation so it surfaces in the canvas later."
        ),
        "input": RecordAnswerInput,
        "output": RecordAnswerOutput,
    },
    {
        "name": "add_decision",
        "description": "Record a decision on a topic (statement plus optional rationale).",
        "input": AddDecisionInput,
        "output": AddDecisionOutput,
    },
    {
        "name": "get_summary",
        "description": (
            "Summarise a canvas: topic count, decision count, last-updated "
            "timestamp, and the most recent stored summary text if one exists."
        ),
        "input": GetSummaryInput,
        "output": GetSummaryOutput,
    },
    {
        "name": "export_markdown",
        "description": "Return the canvas rendered as a single Markdown document.",
        "input": ExportMarkdownInput,
        "output": ExportMarkdownOutput,
    },
]


def tool_names() -> list[str]:
    return [entry["name"] for entry in TOOL_SPEC]
