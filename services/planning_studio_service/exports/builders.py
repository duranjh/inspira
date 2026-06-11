"""Issue-body composition for W2 κ export surface.

Reads the project + topics + decisions for one canvas and projects
them into the markdown body that lands in Linear / GitHub. The
shape mirrors the Decision Summary preview in the design HTML
(``/tmp/inspira-v12/Export Modals.html``):

- Title = project title.
- ``## What this addresses`` from project metadata description.
- ``## Decisions (N)`` bullet list (one per non-retracted decision).
- ``## Trade-offs considered`` only when project metadata provides
  any. We don't synthesize them.
- ``## Source data`` only when ``include_source_feedback`` is True
  and the project has at least one cited feedback item.
- ``Linked from Inspira project →`` line when ``include_canvas_link``
  is True.

The Tasks / sub-issues fan-out is provider-specific and lives in
``linear_send`` / ``github_send`` — not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


PriorityLabel = Literal["P0", "P1", "P2"]


@dataclass(frozen=True)
class IssueBody:
    """Composed issue payload, provider-agnostic."""

    title: str
    body_markdown: str
    topic_titles: list[str]
    priority_label: str | None


def _count_source_feedback(
    store: "PlanningStudioStore", *, project_id: str
) -> int:
    """Count distinct feedback items cited by any decision in this
    project. Drives the ``Source data`` line — exact-zero is a hint
    to omit the section entirely."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT dp.feedback_item_id)
            FROM decision_provenance dp
            JOIN decisions d ON dp.decision_id = d.decision_id
            WHERE d.project_id = ? AND d.status != 'retracted'
            """,
            (project_id,),
        ).fetchone()
    return int(row[0]) if row and row[0] else 0


def build_issue_body(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    include_canvas_link: bool,
    include_source_feedback: bool,
    apply_priority_label: bool,
    priority_label: PriorityLabel,
    canvas_url: str | None,
) -> IssueBody:
    """Compose the full issue payload for one project canvas.

    Raises ``LookupError`` if the project does not exist (caller
    should translate to 404). All other inputs are flags toggling
    optional sections; the body still produces a well-formed
    markdown document with at least a title + decisions.
    """
    project = store._get_v2_project(project_id)
    if project is None:
        raise LookupError(f"project not found: {project_id}")

    title = (project.get("title") or "Untitled project").strip()
    metadata = project.get("metadata") or {}
    description = (metadata.get("description") or "").strip()
    tradeoffs = metadata.get("tradeoffs") or metadata.get("trade_offs") or []
    if not isinstance(tradeoffs, list):
        tradeoffs = []

    topics = store.list_topics(project_id=project_id)
    topic_titles = [
        (t.get("title") or "Untitled topic").strip() for t in topics
    ]

    decisions = store.list_decisions(project_id=project_id)
    decision_statements = [
        (d.get("statement") or "").strip()
        for d in decisions
        if (d.get("statement") or "").strip()
    ]

    sections: list[str] = []
    if description:
        sections.append("## What this addresses\n\n" + description)

    if decision_statements:
        bullets = "\n".join(f"- {s}" for s in decision_statements)
        sections.append(
            f"## Decisions ({len(decision_statements)})\n\n{bullets}"
        )

    if tradeoffs:
        bullets = "\n".join(f"- {str(t).strip()}" for t in tradeoffs if t)
        if bullets:
            sections.append(f"## Trade-offs considered\n\n{bullets}")

    if include_source_feedback:
        cited_count = _count_source_feedback(store, project_id=project_id)
        if cited_count > 0:
            sections.append(
                "## Source data\n\n"
                f"{cited_count} cited feedback item"
                f"{'s' if cited_count != 1 else ''} from Inspira."
            )

    if include_canvas_link and canvas_url:
        sections.append(f"_Linked from Inspira project →_ {canvas_url}")

    body_markdown = "\n\n".join(sections).strip()

    return IssueBody(
        title=title,
        body_markdown=body_markdown,
        topic_titles=topic_titles,
        priority_label=priority_label if apply_priority_label else None,
    )


def github_body_with_tasks(
    body_markdown: str, *, topic_titles: list[str]
) -> str:
    """Append a ``## Tasks`` checkbox section for GitHub Issues.

    GitHub renders ``- [ ] {title}`` as a checkbox per the design's
    tasks-as-checkboxes pattern (no sub-issues on GitHub). Linear
    uses its own sub-issue API instead and ignores this helper.
    """
    if not topic_titles:
        return body_markdown
    tasks = "\n".join(f"- [ ] {t}" for t in topic_titles)
    section = f"## Tasks\n\n{tasks}"
    if body_markdown:
        return f"{body_markdown}\n\n{section}"
    return section
