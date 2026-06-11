"""F7-REVISED — orchestrator coordinator (W3).

Top-level coordinator for autonomous canvas generation. Reads a
completed ``prioritization_runs`` row, spawns one sub-agent per
top-N theme via ``asyncio.gather``, persists the sub-agents'
topics + decisions + provenance, runs cross-sub-agent conflict
detection + a moderation pass, generates a Decision Summary,
creates one canvas (``v2_projects`` row) per theme, and emits
SSE events for the live UI to consume.

Design rules (from the W3 plan):

- **Sub-agents do not write to the database directly.** They
  return a structured dict; the orchestrator owns persistence
  so idempotency, conflict ordering, and failure isolation live
  in one place.
- **Concurrency is gated by ``asyncio.Semaphore``** (default 3).
  Bigger ``top_n`` values still produce parallel work but don't
  hammer org-level RPM caps. Tunable via
  ``INSPIRA_ORCHESTRATOR_MAX_CONCURRENCY``.
- **Sub-agent failure isolation:** ``asyncio.gather(return_exceptions=True)``
  + per-theme try/except. One sub-agent's bug never blocks its
  siblings. The Decision Summary calls out failed themes
  explicitly; the corresponding canvas is created with
  ``metadata.state="generation_failed"`` so partners can see
  exactly which theme didn't complete.
- **``decision_summary.ready`` strictly precedes
  ``orchestrator.completed``.** The orchestrator awaits the
  Summary task before emitting completion; on total failure it
  emits ``error`` instead.
- **Event-queue cleanup is the router's job.** The orchestrator
  emits to whatever queue it's handed; lifecycle of the queue
  (registration in ``app.state.orchestrator_event_queues`` and
  cleanup in a finally block) lives in the router.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from ..feedback_items import store as fi_store
from ..locks import try_project_advisory_lock
from ..orchestrator_store import (
    complete_orchestrator_run,
    complete_sub_agent_run,
    create_sub_agent_run,
    get_prioritization_run,
    list_conflict_resolutions,
    list_sub_agent_runs,
    record_conflict_resolution,
    record_decision_provenance,
)
from ..store import now_timestamp
from . import conflict_detector, sub_agent
from .code_scaffold import CodeScaffoldAdapter

if TYPE_CHECKING:
    from ..store import PlanningStudioStore

logger = logging.getLogger(__name__)


MODERATION_MODEL = "gpt-5"

DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_TOP_N = 5
MAX_TOP_N = 10

# Strong refs for fire-and-forget pre-warm tasks (#184). asyncio.create_task
# only holds a weakref to the wrapped coroutine; without an external strong
# ref, Python's GC can collect the task mid-flight. add_done_callback
# discards on completion.
_pre_warm_tasks: "set[asyncio.Task[None]]" = set()

# Bound concurrent pre-warms so a top_n=5 orchestrator run doesn't fire 5
# simultaneous gpt-5-mini scaffolds at OpenAI's per-org RPM budget.
_PRE_WARM_MAX_CONCURRENCY = 2
_pre_warm_semaphore: asyncio.Semaphore | None = None


def _get_pre_warm_semaphore() -> asyncio.Semaphore:
    """Lazy-init: Semaphore() needs a running event loop on older Pythons."""
    global _pre_warm_semaphore
    if _pre_warm_semaphore is None:
        _pre_warm_semaphore = asyncio.Semaphore(_PRE_WARM_MAX_CONCURRENCY)
    return _pre_warm_semaphore


def _max_concurrency() -> int:
    """Read the concurrency cap from env, clamped to a sane range.

    Bigger values risk org-level RPM trips on OpenAI; smaller values
    serialize the work and slow large runs. Default 3 hits the sweet spot
    for top_n=5.
    """
    raw = os.environ.get("INSPIRA_ORCHESTRATOR_MAX_CONCURRENCY", "").strip()
    if not raw:
        return DEFAULT_MAX_CONCURRENCY
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MAX_CONCURRENCY
    return max(1, min(10, n))


# ---------------------------------------------------------------------
# SSE event helper
# ---------------------------------------------------------------------


async def _emit(
    event_queue: "asyncio.Queue[tuple[str, dict[str, Any]]] | None",
    event: str,
    payload: dict[str, Any],
) -> None:
    """Push a (event, payload) tuple onto the queue, no-op if no subscriber.

    The queue type is just a 2-tuple so the SSE endpoint can format the
    frame however it likes (``sse.format_sse`` does the wire format).
    Putting ``None`` on the queue isn't supported — callers should
    pass a real Queue or omit the arg.
    """
    if event_queue is None:
        return
    try:
        await event_queue.put((event, payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestrator event queue put failed: %s", exc)


# ---------------------------------------------------------------------
# Workspace billing-owner lookup (canvas user_id)
# ---------------------------------------------------------------------


def _resolve_workspace_owner(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
) -> str:
    """Return the workspace's billing_owner_user_id (used as canvas user_id).

    Falls back to ``"user-system"`` if the workspace row is missing —
    that lets tests run against an in-memory store without seeding a
    real workspace. In production the billing_owner is always set.
    """
    with store._connect() as connection:
        row = connection.execute(
            "SELECT billing_owner_user_id FROM workspaces "
            "WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    if row is None or not row[0]:
        return "user-system"
    return str(row[0])


# ---------------------------------------------------------------------
# Canvas creation (one v2_projects row per theme)
# ---------------------------------------------------------------------


def _create_canvas(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    user_id: str,
    title: str,
    state: str,
    orchestrator_run_id: str,
    theme_id: str,
    target_project_id: str | None = None,
) -> str:
    """Create a v2_projects row + set workspace_id + write the state metadata.

    When ``target_project_id`` is provided, the orchestrator UPDATES
    that existing row instead of creating a new one. This is the
    auto-promote path — CSV-import already created shells per cluster
    in `In queue`; clicking a tile triggers /start-canvas which spawns
    the orchestrator with the existing project_id so topics + decisions
    write back into the same project. Without this, every spawn would
    create a duplicate v2_project for the same cluster.

    The metadata JSON carries:
    - ``state`` — ``pending_review`` (success path) or ``generation_failed``.
      A follow-up migration replaces this with a real enum column; for
      now, reading via ``metadata.state`` is the contract.
    - ``orchestrator_run_id`` — back-pointer to this batch.
    - ``theme_id`` — the cluster_id this canvas was generated for.
    - ``autonomous`` — True so the inbox view can filter on it.
    - ``cluster_id`` + ``auto_promoted`` (preserved from the shell row
      when reusing an existing project).

    Returns the project_id (new or existing).
    """
    metadata = {
        "state": state,
        "orchestrator_run_id": orchestrator_run_id,
        "theme_id": theme_id,
        "autonomous": True,
    }

    if target_project_id:
        # Reuse existing v2_project (auto-promoted shell). Merge the
        # caller's metadata over whatever the shell had (cluster_id +
        # auto_promoted + dominant_category) so we don't lose triage
        # info downstream.
        existing = store._get_v2_project(target_project_id)
        if existing is not None:
            # `_get_v2_project` pre-parses metadata_json into
            # existing["metadata"] — read the dict directly.
            raw_md = existing.get("metadata")
            shell_md = dict(raw_md) if isinstance(raw_md, dict) else {}
            shell_md.update(metadata)
            # Keep ai_review_in_progress True while sub-agents draft —
            # start_canvas_route stamps it; the success/failure paths in
            # _run_sub_agent_for_theme clear it. Project_state stays
            # pending_review so columnFor (useKanbanData.ts) routes the
            # card to "AI thinking" via the metadata flag, NOT to
            # "review" via the state column.
            shell_md["ai_review_in_progress"] = True
            with store._connect() as connection:
                connection.execute(
                    "UPDATE v2_projects "
                    "SET workspace_id = ?, metadata_json = ?, "
                    "    project_state = ?, title = ?, updated_at = ? "
                    "WHERE project_id = ?",
                    (
                        workspace_id,
                        json.dumps(shell_md),
                        "pending_review",
                        title or existing.get("title") or f"Theme {theme_id}",
                        now_timestamp(),
                        target_project_id,
                    ),
                )
                connection.commit()
            return target_project_id
        # Target doesn't exist (race / stale id) — fall through to
        # create-new so the orchestrator still has somewhere to land.

    # The state-machine column landed in a W3 follow-up
    # (alembic 20260518_0002). Orchestrator-generated rows are
    # explicitly ``pending_review`` because they're awaiting human
    # review on the workspace Kanban, not yet shipping. The
    # ``state`` key inside metadata_json is preserved for
    # back-compat with any rows that pre-dated the column.
    project = store.create_v2_project(
        user_id=user_id, title=title, project_state="pending_review",
    )
    project_id = project["project_id"]
    with store._connect() as connection:
        # Set workspace_id (the column was retrofitted for v4 — see
        # _ensure_v2_projects_workspace_id_column in store.py).
        connection.execute(
            "UPDATE v2_projects SET workspace_id = ?, metadata_json = ? "
            "WHERE project_id = ?",
            (workspace_id, json.dumps(metadata), project_id),
        )
        connection.commit()
    return project_id


def _persist_topics_and_decisions(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    user_id: str,
    sub_agent_output: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persist a sub-agent's topics + decisions to the canvas.

    Returns ``(persisted_topics, persisted_decisions)``. Each persisted
    decision dict includes ``decision_id`` (the new DB id), the original
    sub-agent ``subject``, and the original ``cited_feedback_item_ids``
    so the orchestrator can run conflict detection + provenance against
    real DB ids.
    """
    topics_in = sub_agent_output.get("topics") or []
    decisions_in = sub_agent_output.get("decisions") or []
    persisted_topics: list[dict[str, Any]] = []
    # Lay topics out in a 2-row grid (matches v2_kickoff's pattern).
    x_step, y_rows = 440, [0, 320]
    for idx, t in enumerate(topics_in):
        topic_row = store.create_topic(
            project_id=project_id,
            title=t["title"],
            icon=t["icon"],
            position_x=float((idx // len(y_rows)) * x_step),
            position_y=float(y_rows[idx % len(y_rows)]),
            origin="orchestrator",
            order_index=idx,
            metadata={"why_this_topic": t.get("why_this_topic")},
            user_id=user_id,
        )
        persisted_topics.append(topic_row)

    persisted_decisions: list[dict[str, Any]] = []
    for d in decisions_in:
        topic_index = int(d.get("topic_index", 0))
        if topic_index < 0 or topic_index >= len(persisted_topics):
            continue
        topic_id = persisted_topics[topic_index]["topic_id"]
        decision_row = store.create_decision(
            topic_id=topic_id,
            project_id=project_id,
            statement=d["statement"],
            proposed_by="orchestrator",
            rationale=d.get("rationale"),
            source_turn_id=None,
            status="proposed",
            user_id=user_id,
        )
        # Persist provenance.
        cited = d.get("cited_feedback_item_ids") or []
        if cited:
            record_decision_provenance(
                store,
                decision_id=decision_row["decision_id"],
                cited_feedback_item_ids=cited,
            )
        persisted_decisions.append(
            {
                **decision_row,
                "subject": d.get("subject", "_unspecified"),
                "cited_feedback_item_ids": cited,
                "topic_id": topic_id,
            }
        )
    return persisted_topics, persisted_decisions


# ---------------------------------------------------------------------
# Sub-agent runner (async wrapper around sync LLM call)
# ---------------------------------------------------------------------


async def _run_sub_agent_for_theme(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    orchestrator_run_id: str,
    user_id: str,
    theme: dict[str, Any],
    semaphore: asyncio.Semaphore,
    event_queue: "asyncio.Queue[tuple[str, dict[str, Any]]] | None",
) -> dict[str, Any]:
    """Run one sub-agent end-to-end. Returns a result dict for the orchestrator.

    Result shape (always):

    - ``sub_agent_run_id``: id of the sub_agent_runs row
    - ``theme_id``: cluster id
    - ``project_id``: v2_projects.project_id (set even on failure)
    - ``status``: ``"completed"`` or ``"error"``
    - ``decisions``: list of persisted decision dicts (empty on error)
    - ``error``: error string when status=='error', else None

    The semaphore caps how many sub-agents run their LLM calls at the
    same time; we acquire BEFORE the LLM call and release after, so
    DB writes / event emits are unconstrained.
    """
    cluster_id = theme["cluster_id"]
    theme_label = theme.get("suggested_theme_label") or f"Theme {cluster_id}"
    rationale = theme.get("rationale") or ""
    # When `target_project_id` is set on the theme (passed in via the
    # /start-canvas endpoint), the orchestrator writes back into that
    # existing v2_projects row instead of minting a new one. This is
    # the auto-promote → click → AI-thinking → topics-populate flow.
    target_project_id = theme.get("target_project_id")
    # Product decision: when partners drag a card back into
    # In Progress with the "Have Inspira rerun" toggle on, they type a
    # required correction note. start-canvas threads it through here.
    # Sub-agent's user prompt gets a PARTNER_CORRECTION fence so the
    # redraft actually responds to the partner's pushback.
    correction_note = (theme.get("correction_note") or "").strip()
    sub_agent_run_id = create_sub_agent_run(
        store,
        workspace_id=workspace_id,
        orchestrator_run_id=orchestrator_run_id,
        theme_id=cluster_id,
    )
    await _emit(
        event_queue,
        "sub_agent.started",
        {
            "sub_agent_run_id": sub_agent_run_id,
            "theme_id": cluster_id,
            "project_id": target_project_id,
        },
    )
    # Pre-create the canvas so the SSE event has a project_id to attach.
    project_id = _create_canvas(
        store,
        workspace_id=workspace_id,
        user_id=user_id,
        title=theme_label,
        state="generating",
        orchestrator_run_id=orchestrator_run_id,
        theme_id=cluster_id,
        target_project_id=target_project_id,
    )
    # Read items for the cluster (workspace-scoped).
    items_pydantic = fi_store.list_items(
        store,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        limit=200,
    )
    items_for_llm = [
        {
            "item_id": it.item_id,
            "title": it.title,
            "body": it.body,
            "type_hint": it.type_hint or "noise",
        }
        for it in items_pydantic
    ]
    # Run sub-agent inside the semaphore so concurrent LLM calls are bounded.
    loop = asyncio.get_running_loop()
    async with semaphore:
        try:
            sa_output = await loop.run_in_executor(
                None,
                lambda: sub_agent.extract_topics_and_decisions_for_theme(
                    cluster_id=cluster_id,
                    theme_label=theme_label,
                    rationale=rationale,
                    items=items_for_llm,
                    correction_note=correction_note,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "sub_agent[%s] unexpected exception", cluster_id
            )
            sa_output = {
                "topics": [], "decisions": [],
                "errors": [f"sub_agent_uncaught: {type(exc).__name__}"],
            }
    if not sa_output.get("topics"):
        # Failure path: empty topics → mark error, set canvas to failed.
        error_text = ", ".join(sa_output.get("errors") or ["empty_output"])
        complete_sub_agent_run(
            store,
            workspace_id=workspace_id,
            sub_agent_run_id=sub_agent_run_id,
            project_id=project_id,
            decisions_count=0,
            conflicts_count=0,
            error=error_text,
        )
        # Update canvas state. Read-modify-write to preserve shell keys
        # (cluster_id, auto_promoted, dominant_category) and to clear
        # ai_review_in_progress so the card returns to "In queue"
        # instead of stranding in "AI thinking".
        with store._connect() as connection:
            existing = store._get_v2_project(project_id)
            raw_md = existing.get("metadata") if existing else None
            md_out = dict(raw_md) if isinstance(raw_md, dict) else {}
            md_out.update(
                {
                    "state": "generation_failed",
                    "orchestrator_run_id": orchestrator_run_id,
                    "theme_id": cluster_id,
                    "autonomous": True,
                    "error": error_text,
                    "ai_review_in_progress": False,
                }
            )
            connection.execute(
                "UPDATE v2_projects SET metadata_json = ? "
                "WHERE project_id = ?",
                (json.dumps(md_out), project_id),
            )
            connection.commit()
        await _emit(
            event_queue,
            "sub_agent.failed",
            {
                "sub_agent_run_id": sub_agent_run_id,
                "theme_id": cluster_id,
                "project_id": project_id,
                "error": error_text,
            },
        )
        return {
            "sub_agent_run_id": sub_agent_run_id,
            "theme_id": cluster_id,
            "project_id": project_id,
            "status": "error",
            "decisions": [],
            "error": error_text,
        }
    # Success path: persist topics + decisions, emit per-topic / per-decision events.
    persisted_topics, persisted_decisions = _persist_topics_and_decisions(
        store,
        project_id=project_id,
        user_id=user_id,
        sub_agent_output=sa_output,
    )
    for idx, t in enumerate(persisted_topics):
        await _emit(
            event_queue,
            "topic.drafted",
            {
                "sub_agent_run_id": sub_agent_run_id,
                "theme_id": cluster_id,
                "topic": {
                    "title": t["title"],
                    "icon": t["icon"],
                    "why_this_topic": (t.get("metadata") or {}).get(
                        "why_this_topic"
                    ),
                },
                "topic_index": idx,
            },
        )
    for d in persisted_decisions:
        provenance = [
            {
                "feedback_item_id": cid,
                "weight": 1.0 / len(d["cited_feedback_item_ids"]),
            }
            for cid in d["cited_feedback_item_ids"]
        ]
        # Resolve topic_index from topic_id (best-effort; index = position
        # in persisted_topics list).
        topic_index = next(
            (
                i for i, t in enumerate(persisted_topics)
                if t["topic_id"] == d["topic_id"]
            ),
            0,
        )
        await _emit(
            event_queue,
            "decision.drafted",
            {
                "sub_agent_run_id": sub_agent_run_id,
                "theme_id": cluster_id,
                "topic_index": topic_index,
                "decision": {
                    "decision_id": d["decision_id"],
                    "statement": d["statement"],
                    "rationale": d.get("rationale"),
                    "subject": d["subject"],
                },
                "provenance": provenance,
            },
        )
    # Flip canvas state to pending_review now that topics/decisions are
    # present. Read-modify-write preserves shell keys (cluster_id,
    # auto_promoted, dominant_category) and clears
    # ai_review_in_progress so the card moves "AI thinking" → "In
    # review" (project_state column flip below routes it).
    with store._connect() as connection:
        existing = store._get_v2_project(project_id)
        raw_md = existing.get("metadata") if existing else None
        md_out = dict(raw_md) if isinstance(raw_md, dict) else {}
        md_out.update(
            {
                "state": "pending_review",
                "orchestrator_run_id": orchestrator_run_id,
                "theme_id": cluster_id,
                "autonomous": True,
                "ai_review_in_progress": False,
            }
        )
        connection.execute(
            "UPDATE v2_projects SET metadata_json = ?, project_state = ? "
            "WHERE project_id = ?",
            (json.dumps(md_out), "in_review", project_id),
        )
        connection.commit()
    decisions_count = len(persisted_decisions)
    complete_sub_agent_run(
        store,
        workspace_id=workspace_id,
        sub_agent_run_id=sub_agent_run_id,
        project_id=project_id,
        decisions_count=decisions_count,
        conflicts_count=0,  # updated by orchestrator after detection
        error=None,
    )
    await _emit(
        event_queue,
        "sub_agent.completed",
        {
            "sub_agent_run_id": sub_agent_run_id,
            "theme_id": cluster_id,
            "project_id": project_id,
            "topics_count": len(persisted_topics),
            "decisions_count": decisions_count,
            "conflicts_count": 0,
        },
    )
    # Annotate decisions with sub_agent_run_id so the conflict detector
    # can carry the attribution into resolution rows.
    for d in persisted_decisions:
        d["sub_agent_run_id"] = sub_agent_run_id
        d["theme_id"] = cluster_id
    return {
        "sub_agent_run_id": sub_agent_run_id,
        "theme_id": cluster_id,
        "project_id": project_id,
        "status": "completed",
        "decisions": persisted_decisions,
        "error": None,
    }


# ---------------------------------------------------------------------
# Conflict moderation
# ---------------------------------------------------------------------


def _moderate_conflict(
    *,
    decision_a: dict[str, Any],
    decision_b: dict[str, Any],
    subject: str,
) -> str:
    """Ask GPT-5 for a canonical resolution of two conflicting decisions.

    Returns a short text resolution. Failures fall back to a deterministic
    "team to reconcile" placeholder so the moderation never blocks the
    orchestrator. The resolution does NOT spawn a new ``decisions`` row
    in v1; it's recorded as ``conflict_resolutions.resolution_text``.

    Provider rule: Claude is only used for code generation on
    Frontier/Enterprise; conflict moderation is non-code-gen so it
    always uses OpenAI regardless of tier.
    """
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return (
            f"Both options on '{subject}' need team review. "
            "Set up a follow-up to reconcile."
        )
    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(max_retries=0)
        prompt = (
            f"Two product decisions disagree on the subject '{subject}'.\n\n"
            f"Option A: {decision_a.get('statement')}\n"
            f"Reason: {decision_a.get('rationale') or '(none)'}\n\n"
            f"Option B: {decision_b.get('statement')}\n"
            f"Reason: {decision_b.get('rationale') or '(none)'}\n\n"
            "In 2-3 sentences, propose a CANONICAL resolution that the "
            "team should adopt. Cite both originating options briefly. "
            "Don't hedge — pick the stronger option or propose a hybrid. "
            "Plain prose, no JSON."
        )
        response = client.chat.completions.create(
            model=MODERATION_MODEL,
            max_completion_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            reasoning_effort="low",
            timeout=DEFAULT_TIMEOUT_S,
        )
        text = (response.choices[0].message.content or "").strip()
        return text or (
            f"Resolution unavailable for '{subject}' — team to reconcile."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("conflict moderation failed: %s", exc)
        return (
            f"Resolution unavailable for '{subject}' "
            f"({type(exc).__name__}); team to reconcile."
        )


# ---------------------------------------------------------------------
# Decision Summary
# ---------------------------------------------------------------------


def _generate_decision_summary(
    *,
    sub_agent_outcomes: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce the cross-run Decision Summary (B2.5).

    Shape::

        {
            "themes": [{"theme_id", "project_id", "status",
                        "decisions_count", "highlights": [str, ...]}],
            "failed_themes": [{"theme_id", "error"}],
            "conflicts": [{"subject", "resolution_text",
                           "decision_a_id", "decision_b_id"}],
            "headline": str,
        }

    Deterministic aggregation of sub-agent outcomes — no LLM call.
    The output is short but always lands so the SSE
    ``decision_summary.ready`` event always fires (the orchestrator
    contract requires it). A future enhancement could route the
    summary through GPT-5 for richer narrative; tracked as a
    follow-up rather than implemented in this swap.
    """
    themes_section: list[dict[str, Any]] = []
    failed_themes: list[dict[str, Any]] = []
    for outcome in sub_agent_outcomes:
        if outcome["status"] == "error":
            failed_themes.append(
                {
                    "theme_id": outcome["theme_id"],
                    "error": outcome.get("error") or "(no detail)",
                }
            )
            continue
        themes_section.append(
            {
                "theme_id": outcome["theme_id"],
                "project_id": outcome["project_id"],
                "status": outcome["status"],
                "decisions_count": len(outcome["decisions"]),
                "highlights": [
                    d["statement"] for d in outcome["decisions"][:3]
                ],
            }
        )
    conflicts_section = [
        {
            "subject": c.get("subject"),
            "resolution_text": c.get("resolution_text"),
            "decision_a_id": c.get("decision_a_id"),
            "decision_b_id": c.get("decision_b_id"),
        }
        for c in conflicts
    ]
    headline = (
        f"{len(themes_section)} canvas(es) generated; "
        f"{len(failed_themes)} theme(s) failed; "
        f"{len(conflicts_section)} conflict(s) resolved."
    )
    return {
        "themes": themes_section,
        "failed_themes": failed_themes,
        "conflicts": conflicts_section,
        "headline": headline,
    }


# ---------------------------------------------------------------------
# Orchestrator entrypoint
# ---------------------------------------------------------------------


async def run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    orchestrator_run_id: str,
    prioritization_run_id: str,
    top_n: int = DEFAULT_TOP_N,
    event_queue: "asyncio.Queue[tuple[str, dict[str, Any]]] | None" = None,
    pre_warm_artifacts: bool = False,
) -> None:
    """End-to-end orchestrator run.

    Reads the prioritization output, spawns top-N sub-agents in parallel
    (semaphore-bounded), persists their topics/decisions, runs conflict
    detection + moderation, generates the Decision Summary, and emits
    SSE events to ``event_queue`` if present.

    On total failure (e.g., prioritization run not found), emits an
    ``error`` terminal event instead of ``orchestrator.completed``.

    Caller (router) is responsible for queue lifecycle — registering
    in ``app.state.orchestrator_event_queues`` before this runs and
    popping after.

    When ``pre_warm_artifacts=True`` (router-spawned production path), fires
    a fire-and-forget ``_pre_warm_artifact_async`` task per completed
    outcome after ``orchestrator.completed`` is emitted (#184).
    """
    user_id = _resolve_workspace_owner(store, workspace_id=workspace_id)
    summary: dict[str, Any] | None = None
    error_text: str | None = None
    # Init outcomes at top so the finally-block hook can reference it
    # safely even on the empty-themes early-return path.
    outcomes: list[dict[str, Any]] = []
    try:
        prio = get_prioritization_run(
            store,
            workspace_id=workspace_id,
            run_id=prioritization_run_id,
        )
        if prio is None or prio["status"] != "completed":
            raise RuntimeError(
                f"prioritization_run {prioritization_run_id} "
                f"not found or not completed"
            )
        themes_all = (prio.get("output") or {}).get("themes") or []
        themes = sorted(themes_all, key=lambda t: t.get("rank", 999))[:top_n]
        await _emit(
            event_queue,
            "run.started",
            {
                "run_id": orchestrator_run_id,
                "top_n": len(themes),
                "theme_ids": [t["cluster_id"] for t in themes],
            },
        )
        if not themes:
            # No work to do — still emit a (trivial) Decision Summary
            # so the SSE contract holds.
            summary = _generate_decision_summary(
                sub_agent_outcomes=[], conflicts=[],
            )
            await _emit(
                event_queue,
                "decision_summary.ready",
                {"summary_json": summary},
            )
            return
        semaphore = asyncio.Semaphore(_max_concurrency())
        tasks = [
            _run_sub_agent_for_theme(
                store,
                workspace_id=workspace_id,
                orchestrator_run_id=orchestrator_run_id,
                user_id=user_id,
                theme=theme,
                semaphore=semaphore,
                event_queue=event_queue,
            )
            for theme in themes
        ]
        outcomes_raw = await asyncio.gather(*tasks, return_exceptions=True)
        for theme, raw in zip(themes, outcomes_raw):
            if isinstance(raw, BaseException):
                logger.exception(
                    "sub_agent task crashed for theme %s",
                    theme.get("cluster_id"),
                )
                outcomes.append(
                    {
                        "sub_agent_run_id": "",
                        "theme_id": theme["cluster_id"],
                        "project_id": None,
                        "status": "error",
                        "decisions": [],
                        "error": f"task_crashed: {type(raw).__name__}",
                    }
                )
            else:
                outcomes.append(raw)
        # Conflict detection across all successful sub-agents.
        all_decisions = [
            d
            for o in outcomes
            if o["status"] == "completed"
            for d in o["decisions"]
        ]
        candidates = conflict_detector.find_conflict_candidates(all_decisions)
        # Emit + moderate each candidate.
        decisions_by_id = {d["decision_id"]: d for d in all_decisions}
        resolved: list[dict[str, Any]] = []
        for c in candidates:
            await _emit(
                event_queue,
                "conflict.detected",
                {
                    "decision_a_id": c["decision_a_id"],
                    "decision_b_id": c["decision_b_id"],
                    "subject": c["subject"],
                },
            )
            d_a = decisions_by_id.get(c["decision_a_id"], {})
            d_b = decisions_by_id.get(c["decision_b_id"], {})
            loop = asyncio.get_running_loop()
            resolution_text = await loop.run_in_executor(
                None,
                lambda da=d_a, db=d_b, subj=c["subject"]: _moderate_conflict(
                    decision_a=da, decision_b=db, subject=subj,
                ),
            )
            resolution_id = record_conflict_resolution(
                store,
                orchestrator_run_id=orchestrator_run_id,
                decision_a_id=c["decision_a_id"],
                decision_b_id=c["decision_b_id"],
                subject=c["subject"],
                resolution_text=resolution_text,
                resolution_decision_id=None,
            )
            resolved.append(
                {
                    "resolution_id": resolution_id,
                    "decision_a_id": c["decision_a_id"],
                    "decision_b_id": c["decision_b_id"],
                    "subject": c["subject"],
                    "resolution_text": resolution_text,
                    "resolution_decision_id": None,
                }
            )
            await _emit(
                event_queue,
                "conflict.resolved",
                {
                    "resolution_id": resolution_id,
                    "resolution_decision_id": None,
                    "resolution_text": resolution_text,
                },
            )
        # Decision Summary — awaited before terminal event.
        summary = _generate_decision_summary(
            sub_agent_outcomes=outcomes, conflicts=resolved,
        )
        await _emit(
            event_queue,
            "decision_summary.ready",
            {"summary_json": summary},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "orchestrator.run failed (orchestrator_run_id=%s)",
            orchestrator_run_id,
        )
        error_text = f"{type(exc).__name__}: {exc}"
    finally:
        # Always mark terminal in DB.
        try:
            if error_text is None and summary is not None:
                complete_orchestrator_run(
                    store,
                    workspace_id=workspace_id,
                    run_id=orchestrator_run_id,
                    summary=summary,
                )
                await _emit(
                    event_queue,
                    "orchestrator.completed",
                    {"run_id": orchestrator_run_id},
                )
                # Fire-and-forget pre-warm of the code artifact per
                # completed project (#184). The FE auto-fires GET first;
                # if the scaffold is persisted by the time the partner
                # clicks Code, they get an instant render instead of the
                # 60-120s synchronous OpenAI call.
                if pre_warm_artifacts and outcomes:
                    for outcome in outcomes:
                        if (
                            outcome.get("status") == "completed"
                            and outcome.get("project_id")
                        ):
                            task = asyncio.create_task(
                                _pre_warm_artifact_async(
                                    store,
                                    outcome["project_id"],
                                    workspace_id,
                                ),
                                name=(
                                    f"pre-warm-{outcome['project_id']}"
                                ),
                            )
                            _pre_warm_tasks.add(task)
                            task.add_done_callback(
                                _pre_warm_tasks.discard,
                            )
            else:
                final_error = error_text or "orchestrator_failed_no_summary"
                complete_orchestrator_run(
                    store,
                    workspace_id=workspace_id,
                    run_id=orchestrator_run_id,
                    error=final_error,
                )
                await _emit(
                    event_queue,
                    "error",
                    {
                        "code": "orchestrator_failed",
                        "message": final_error,
                    },
                )
        except Exception:  # noqa: BLE001
            logger.exception(
                "orchestrator failed to write terminal state "
                "(orchestrator_run_id=%s)",
                orchestrator_run_id,
            )


# ---------------------------------------------------------------------
# Replay helper for late SSE subscribers
# ---------------------------------------------------------------------


def build_replay_events(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    orchestrator_run_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Reconstruct a trimmed event sequence for a completed run.

    Late-connecting clients (the run already finished, so the live
    queue is gone) get a minimal replay that captures terminal state
    only — no per-topic / per-decision noise. Per the W3 plan: "Late
    mounts get the run summary in O(top_n + conflict_count) events
    instead of the full live stream."

    Replay shape:
    1. ``run.started`` (one)
    2. ``sub_agent.completed`` or ``sub_agent.failed`` per row
    3. ``conflict.resolved`` per row
    4. ``decision_summary.ready`` (if summary present)
    5. ``orchestrator.completed`` or ``error`` (terminal)
    """
    from ..orchestrator_store import get_orchestrator_run  # noqa: PLC0415

    orch = get_orchestrator_run(
        store,
        workspace_id=workspace_id,
        run_id=orchestrator_run_id,
    )
    if orch is None:
        return [(
            "error",
            {
                "code": "orchestrator_run_not_found",
                "message": orchestrator_run_id,
            },
        )]
    events: list[tuple[str, dict[str, Any]]] = []
    sub_rows = list_sub_agent_runs(
        store,
        workspace_id=workspace_id,
        orchestrator_run_id=orchestrator_run_id,
    )
    events.append(
        (
            "run.started",
            {
                "run_id": orch["run_id"],
                "top_n": orch["top_n"],
                "theme_ids": [s["theme_id"] for s in sub_rows],
            },
        )
    )
    for s in sub_rows:
        if s["status"] == "error":
            events.append(
                (
                    "sub_agent.failed",
                    {
                        "sub_agent_run_id": s["sub_agent_run_id"],
                        "theme_id": s["theme_id"],
                        "project_id": s["project_id"],
                        "error": s.get("error"),
                    },
                )
            )
        else:
            events.append(
                (
                    "sub_agent.completed",
                    {
                        "sub_agent_run_id": s["sub_agent_run_id"],
                        "theme_id": s["theme_id"],
                        "project_id": s["project_id"],
                        "topics_count": None,  # not persisted on the row
                        "decisions_count": s["decisions_count"],
                        "conflicts_count": s["conflicts_count"],
                    },
                )
            )
    conflicts = list_conflict_resolutions(
        store, orchestrator_run_id=orchestrator_run_id,
    )
    for c in conflicts:
        events.append(
            (
                "conflict.resolved",
                {
                    "resolution_id": c["resolution_id"],
                    "resolution_decision_id": c["resolution_decision_id"],
                    "resolution_text": c["resolution_text"],
                },
            )
        )
    if orch.get("summary"):
        events.append(
            ("decision_summary.ready", {"summary_json": orch["summary"]})
        )
    if orch["status"] == "completed":
        events.append(
            ("orchestrator.completed", {"run_id": orch["run_id"]})
        )
    else:
        events.append(
            (
                "error",
                {
                    "code": "orchestrator_failed",
                    "message": orch.get("error") or "(no detail)",
                },
            )
        )
    return events


# ---------------------------------------------------------------------
# Artifact pre-warm (#184)
# ---------------------------------------------------------------------


async def _pre_warm_artifact_async(
    store: "PlanningStudioStore",
    project_id: str,
    workspace_id: str,
) -> None:
    """Async wrapper — runs the sync OpenAI call in a thread under a
    module-level semaphore so a top_n=5 run doesn't fire 5 simultaneous
    scaffold gens at OpenAI's per-org RPM budget.
    """
    async with _get_pre_warm_semaphore():
        try:
            await asyncio.to_thread(
                _pre_warm_artifact, store, project_id, workspace_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "artifact pre-warm failed (project_id=%s)", project_id,
            )


def _pre_warm_artifact(
    store: "PlanningStudioStore",
    project_id: str,
    workspace_id: str,
) -> None:
    """Sync scaffold generation + persist; mirrors v2_artifact_generate_stream.

    Idempotent against existing artifacts (early-return) and against
    concurrent pre-warms on the same project (advisory lock). Failures
    are logged and swallowed — they don't block orchestrator completion,
    they just leave the FE click to fall through to the existing
    synchronous gen path (no regression).
    """
    try:
        # Skip if an artifact already exists.
        if store.get_v2_project_artifact(project_id=project_id) is not None:
            return

        with try_project_advisory_lock(store, project_id) as acquired:
            if not acquired:
                return
            # Re-check under the lock (another holder may have persisted
            # between the existence check + acquire).
            if (
                store.get_v2_project_artifact(project_id=project_id)
                is not None
            ):
                return

            project_row = store._get_v2_project(project_id)  # noqa: SLF001
            if project_row is None:
                # Project deleted between orchestrator success + pre-warm.
                return
            project_title = project_row.get("title") or "Untitled"

            user_id = _resolve_workspace_owner(
                store, workspace_id=workspace_id,
            )
            topics = store.list_topics(
                project_id=project_id, user_id=user_id,
            )
            decisions = store.list_decisions(
                project_id=project_id, user_id=user_id,
            )
            try:
                summary_row = store.latest_summary_version(
                    project_id=project_id,
                )
            except Exception:  # noqa: BLE001
                summary_row = None
            summary_markdown = ""
            if summary_row and isinstance(summary_row, dict):
                summary_markdown = (
                    summary_row.get("content_markdown") or ""
                ).strip()

            adapter = CodeScaffoldAdapter()
            manifest = adapter.generate(
                project_title=project_title,
                summary_markdown=summary_markdown,
                topics=topics,
                decisions=decisions,
                locale=None,
                model_override=None,
            )

            row = store.create_scaffold(
                project_id=project_id,
                user_id=user_id,
                framework=str(manifest.get("framework") or "other"),
                language=str(manifest.get("language") or "typescript"),
                manifest_json=json.dumps(manifest),
            )
            files = manifest.get("files") or []
            file_count = len(files)
            framework = str(manifest.get("framework") or "other")
            opener = (
                f"I drafted a {framework} scaffold with {file_count} "
                "file(s). Open any file on the left, or ask me to "
                "tweak it via the chat."
            )
            artifact_overlay = {
                "version": 1,
                "latest_scaffold_id": row["scaffold_id"],
                "model_used": None,
                "messages": [
                    {
                        "role": "assistant",
                        "body": opener,
                        "ts": row["created_at"],
                    },
                ],
            }
            store.set_v2_project_artifact(
                project_id=project_id, artifact=artifact_overlay,
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "artifact pre-warm failed (project_id=%s)", project_id,
        )
