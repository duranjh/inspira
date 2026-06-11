"""Comment cascade — regenerates decisions in response to a user comment.

Two phases:

1. **compute_affected_scope** (cheap, no LLM) — returns the set of
   decisions a cascade would touch given the commented decisions, plus a
   ``banner_state`` (none / narrow / wide) for the UI to render.
2. **run_cascade** (BackgroundTask) — for each in-scope decision, calls
   gpt-5-mini to rewrite the statement+rationale informed by the
   commenter's intent, then appends a new ``decision_versions`` row
   and bumps ``decisions.current_version_int``.

Provider rule: cascade is NOT code-gen, so it dispatches to
**gpt-5-mini** (matches F4/F5/F6 conventions, NOT gpt-4o-mini).

Failure isolation: per-decision try/except, mirrors orchestrator's
``return_exceptions=True`` pattern. One failed regen does not block
its siblings; ``diff_summary.failed_count`` reflects the outcome.

Scope algorithm (no LLM):
- ``scope_mode="local"`` → only the commented decisions; ``banner_state="none"``.
- ``scope_mode="cascade"`` → topic-sibling expansion: include every
  non-retracted decision in the same topic(s) as the commented ones,
  plus decisions in topics one hop away via the ``relationships``
  edge list. Decisions deduped; commented-self excluded.

Note: subject-overlap scoping (per the original architecture plan) is
NOT used here because ``decisions.subject`` isn't persisted today —
the orchestrator carries it in-memory only for conflict detection.
Topic + one-hop relationships give a strong-enough cascade signal
for v0; subject-based scope can layer on later behind a flag.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from .. import cascade_store

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


logger = logging.getLogger(__name__)


CASCADE_MODEL = "gpt-5-mini"
CASCADE_REASONING_EFFORT = "low"
CASCADE_MAX_COMPLETION_TOKENS = 4096
CASCADE_TIMEOUT_S = 60.0


# Per-cascade max parallel rewrites. Tunable via env so an over-eager
# cascade can't drown the OpenAI rate limit.
def _max_concurrency() -> int:
    raw = os.environ.get("INSPIRA_CASCADE_MAX_CONCURRENCY", "").strip()
    if not raw:
        return 3
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


# ---------------------------------------------------------------------
# Scope computation
# ---------------------------------------------------------------------


def compute_affected_scope(
    store: "PlanningStudioStore",
    *,
    project_id: str,
    commented_decision_ids: list[str],
    scope_mode: str,
) -> dict[str, Any]:
    """Return the cascade scope without touching the LLM.

    Output shape::

        {
            "decision_ids": [str],   # additional decisions to rewrite
                                     # (excludes the commented ones)
            "topic_ids":    [str],   # topics covered by the affected set
            "count":        int,     # len(decision_ids)
            "banner_state": "none" | "narrow" | "wide",
        }

    Banner thresholds (matching the B3.2 design):
    - ``count == 0`` → ``"none"`` (frontend hides the banner)
    - ``1 <= count <= 3`` → ``"narrow"``
    - ``count >= 4`` → ``"wide"``

    For ``scope_mode="local"`` we short-circuit: count is always 0.
    """
    if scope_mode not in ("local", "cascade"):
        raise ValueError(f"unknown scope_mode: {scope_mode!r}")

    if scope_mode == "local":
        return {
            "decision_ids": [],
            "topic_ids": [],
            "count": 0,
            "banner_state": "none",
        }

    all_decisions = store.list_decisions(project_id=project_id)
    by_id: dict[str, dict[str, Any]] = {d["decision_id"]: d for d in all_decisions}
    commented_set = set(commented_decision_ids)

    # Topics of the commented decisions.
    commented_topics: set[str] = {
        by_id[d_id]["topic_id"] for d_id in commented_set if d_id in by_id
    }

    # One-hop topic expansion via relationships.
    related_topics: set[str] = set(commented_topics)
    if commented_topics:
        rels = store.list_relationships(project_id=project_id)
        for r in rels:
            src = r.get("source_topic_id")
            tgt = r.get("target_topic_id")
            if src in commented_topics and tgt:
                related_topics.add(tgt)
            if tgt in commented_topics and src:
                related_topics.add(src)

    # Decisions in any related topic, excluding the commented set.
    affected: list[dict[str, Any]] = [
        d for d in all_decisions
        if d["topic_id"] in related_topics and d["decision_id"] not in commented_set
    ]

    decision_ids = [d["decision_id"] for d in affected]
    topic_ids = sorted(related_topics)
    count = len(decision_ids)
    if count == 0:
        banner_state = "none"
    elif count <= 3:
        banner_state = "narrow"
    else:
        banner_state = "wide"
    return {
        "decision_ids": decision_ids,
        "topic_ids": topic_ids,
        "count": count,
        "banner_state": banner_state,
    }


# ---------------------------------------------------------------------
# Cost estimate (cheap, no LLM)
# ---------------------------------------------------------------------


def estimate_cost(
    *,
    affected_scope: dict[str, Any],
    commented_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rough cost + time estimate shown to the user before they commit.

    Math: each decision regen is ~$0.0015 on gpt-5-mini (1.5K input
    tokens × $0.15/M + 0.5K output × $0.60/M = ~$0.000525 — round
    up for safety). Time: parallel-bounded by max-concurrency, ~3s
    per call when the model is warm.
    """
    total_decisions = len(affected_scope["decision_ids"]) + len(commented_decisions)
    cost_per = 0.0015
    seconds_per = 3
    concurrency = _max_concurrency()
    return {
        "estimated_cost_usd": round(total_decisions * cost_per, 4),
        "estimated_seconds": max(
            seconds_per,
            (total_decisions * seconds_per + concurrency - 1) // max(concurrency, 1),
        ),
    }


# ---------------------------------------------------------------------
# GPT dispatch
# ---------------------------------------------------------------------


SYSTEM_PROMPT = """\
You rewrite one decision on a planning canvas in response to a user's
inline comment.

You receive:
- The decision's current statement and rationale.
- The user's comment about that decision.
- Optionally: related decisions in the same topic (context only — do
  not rewrite them).

You return a JSON object with exactly these keys:
- "statement": the rewritten decision statement (one sentence,
  decisive, present tense).
- "rationale": one short paragraph explaining why this is now the
  decision. Address the user's comment directly when it changes the
  reasoning.
- "change_note": one sentence, plain language, describing what
  changed and why. Shown as a tooltip on the diff badge.

Rules:
- Preserve the decision's identity. The rewrite is a refinement, not
  a different decision. If the comment asks for an unrelated topic,
  acknowledge briefly in change_note and minimally adjust.
- Do NOT escalate scope. If the comment hints at "and also fix X",
  ignore X — that's outside this decision's slot.
- Output ONLY the JSON object. No prose, no fences.
"""


def _build_user_prompt(
    *,
    decision: dict[str, Any],
    comment_text: str | None,
    related: list[dict[str, Any]],
) -> str:
    payload: dict[str, Any] = {
        "decision": {
            "statement": decision.get("statement", ""),
            "rationale": decision.get("rationale", ""),
        },
        "user_comment": comment_text or "",
        "related_decisions_for_context": [
            {
                "statement": r.get("statement", ""),
                "rationale": r.get("rationale", ""),
            }
            for r in related[:5]
        ],
    }
    return (
        "Rewrite the decision below in light of the user's comment.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Return JSON: {\"statement\": ..., \"rationale\": ..., \"change_note\": ...}"
    )


def _call_openai_for_cascade(
    *,
    decision: dict[str, Any],
    comment_text: str | None,
    related: list[dict[str, Any]],
    client: Any | None = None,
) -> dict[str, Any]:
    """Single LLM call to rewrite one decision. Synchronous (use executor to wrap).

    Returns ``{"statement": str, "rationale": str, "change_note": str}``.
    Raises on transport / parse failure — caller wraps in try/except so
    one decision's regen failure does not block its siblings.
    """
    if client is None:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK not installed — cascade cannot run"
            ) from exc
        client = OpenAI(max_retries=0)

    create_kwargs: dict[str, Any] = {
        "model": CASCADE_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(
                    decision=decision,
                    comment_text=comment_text,
                    related=related,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": CASCADE_MAX_COMPLETION_TOKENS,
        "timeout": CASCADE_TIMEOUT_S,
        "reasoning_effort": CASCADE_REASONING_EFFORT,
    }
    response = client.chat.completions.create(**create_kwargs)
    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        raise RuntimeError("cascade_empty_response")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"cascade_invalid_json: {exc}") from exc
    statement = (parsed.get("statement") or "").strip()
    if not statement:
        raise RuntimeError("cascade_missing_statement")
    return {
        "statement": statement,
        "rationale": (parsed.get("rationale") or "").strip() or None,
        "change_note": (parsed.get("change_note") or "").strip() or None,
    }


def is_openai_available() -> bool:
    """True iff the cascade can dispatch to OpenAI right now."""
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


# ---------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------


def _abbreviated_diff(*, old_text: str, new_text: str, max_chars: int = 500) -> str:
    """Unified diff truncated to ``max_chars`` for ``decision_versions.change_note``."""
    diff = "\n".join(
        difflib.unified_diff(
            (old_text or "").splitlines(),
            (new_text or "").splitlines(),
            lineterm="",
            n=2,
        )
    )
    if len(diff) > max_chars:
        return diff[: max_chars - 3] + "..."
    return diff


def _persist_one_regen(
    store: "PlanningStudioStore",
    *,
    decision: dict[str, Any],
    rewrite: dict[str, Any],
    cascade_id: str,
    cascaded_from_decision_ids: list[str],
) -> dict[str, Any]:
    """Append a new decision_versions row + advance decisions.current_version_int.

    Returns the new-version dict for inclusion in cascade status response.
    """
    decision_id = decision["decision_id"]
    # Lazy v1 snapshot — the original is preserved for diff lineage.
    v1_id = cascade_store.ensure_v1_snapshot(store, decision_id=decision_id)
    if v1_id is None:
        raise RuntimeError(f"decision_not_found_for_cascade: {decision_id}")
    current_v = cascade_store.get_latest_version_int(
        store, decision_id=decision_id,
    )
    new_v = current_v + 1
    prior_version = cascade_store.get_decision_version(
        store, decision_id=decision_id, version_int=current_v,
    )
    prior_version_id = prior_version["version_id"] if prior_version else v1_id

    old_text = (
        f"{decision.get('statement') or ''}\n\n{decision.get('rationale') or ''}"
    ).strip()
    new_text = (
        f"{rewrite['statement']}\n\n{rewrite.get('rationale') or ''}"
    ).strip()
    change_note = rewrite.get("change_note") or _abbreviated_diff(
        old_text=old_text, new_text=new_text,
    )

    version_id = cascade_store.insert_decision_version(
        store,
        decision_id=decision_id,
        version_int=new_v,
        statement=rewrite["statement"],
        rationale=rewrite.get("rationale"),
        subject=None,
        prior_version_id=prior_version_id,
        change_note=change_note,
        cascade_id=cascade_id,
        cascaded_from_decision_ids=cascaded_from_decision_ids,
    )
    cascade_store.update_decision_for_cascade(
        store,
        decision_id=decision_id,
        statement=rewrite["statement"],
        rationale=rewrite.get("rationale"),
        current_version_int=new_v,
    )
    return {
        "version_id": version_id,
        "decision_id": decision_id,
        "version_int": new_v,
        "prior_version_int": current_v,
        "statement": rewrite["statement"],
        "rationale": rewrite.get("rationale"),
        "change_note": change_note,
        "is_new_decision": False,
    }


# ---------------------------------------------------------------------
# Cascade orchestration
# ---------------------------------------------------------------------


async def run_cascade(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    project_id: str,
    cascade_id: str,
    user_id: str,
    scope_mode: str,
    commented_decisions: list[dict[str, Any]],
    openai_client: Any | None = None,
) -> None:
    """Top-level cascade entry point. Drives one cascade_runs row to terminal.

    ``commented_decisions`` shape: ``[{decision_id, comment_text}]``.
    Marks the cascade ``running`` → ``complete`` (or ``failed``) by the
    end. Per-decision regen failures do NOT bubble; they're recorded in
    ``diff_summary.failed_count`` and the cascade still completes.
    """
    cascade_store.update_cascade_status(
        store,
        workspace_id=workspace_id,
        cascade_id=cascade_id,
        status="running",
    )

    if not is_openai_available():
        cascade_store.update_cascade_status(
            store,
            workspace_id=workspace_id,
            cascade_id=cascade_id,
            status="failed",
            error="cascade_unavailable: OPENAI_API_KEY not configured",
        )
        return

    try:
        affected_scope = compute_affected_scope(
            store,
            project_id=project_id,
            commented_decision_ids=[c["decision_id"] for c in commented_decisions],
            scope_mode=scope_mode,
        )

        # Build the work list: commented decisions FIRST (they're the
        # primary edit), then affected siblings (cascade-mode only).
        all_decisions = store.list_decisions(project_id=project_id)
        decisions_by_id: dict[str, dict[str, Any]] = {
            d["decision_id"]: d for d in all_decisions
        }
        comment_by_decision_id: dict[str, str] = {
            c["decision_id"]: c.get("comment_text", "")
            for c in commented_decisions
        }
        commented_in_order = [
            decisions_by_id[c["decision_id"]]
            for c in commented_decisions
            if c["decision_id"] in decisions_by_id
        ]
        affected_in_order = [
            decisions_by_id[d_id]
            for d_id in affected_scope["decision_ids"]
            if d_id in decisions_by_id
        ]
        work: list[tuple[dict[str, Any], str | None]] = (
            [(d, comment_by_decision_id.get(d["decision_id"])) for d in commented_in_order]
            + [(d, None) for d in affected_in_order]  # affected get cascade context, no direct comment
        )

        # Cascade-context sample — give the LLM a small slice of related
        # decisions for situational awareness when rewriting affected ones.
        cascade_context = [
            {"statement": d.get("statement"), "rationale": d.get("rationale")}
            for d in commented_in_order
        ]

        cascaded_from_ids = [c["decision_id"] for c in commented_decisions]
        semaphore = asyncio.Semaphore(_max_concurrency())
        loop = asyncio.get_running_loop()

        async def _worker(decision: dict[str, Any], comment_text: str | None) -> dict[str, Any]:
            async with semaphore:
                related = (
                    [] if comment_text is not None else cascade_context
                )
                try:
                    rewrite = await loop.run_in_executor(
                        None,
                        lambda: _call_openai_for_cascade(
                            decision=decision,
                            comment_text=comment_text,
                            related=related,
                            client=openai_client,
                        ),
                    )
                    new_version = _persist_one_regen(
                        store,
                        decision=decision,
                        rewrite=rewrite,
                        cascade_id=cascade_id,
                        cascaded_from_decision_ids=cascaded_from_ids,
                    )
                    return {"ok": True, "version": new_version}
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "[cascade %s] regen failed for decision=%s",
                        cascade_id, decision.get("decision_id"),
                    )
                    return {
                        "ok": False,
                        "decision_id": decision.get("decision_id"),
                        "error": f"{type(exc).__name__}: {exc}",
                    }

        results = await asyncio.gather(
            *[_worker(d, c) for d, c in work],
            return_exceptions=False,  # workers swallow their own exceptions
        )

        new_versions = [r["version"] for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        diff_summary = {
            "updated_count": len(new_versions),
            "created_count": 0,  # MVP doesn't invent new decisions
            "failed_count": len(failed),
        }

        cascade_store.update_cascade_status(
            store,
            workspace_id=workspace_id,
            cascade_id=cascade_id,
            status="complete",
            affected_scope={
                **affected_scope,
                "new_decision_versions": new_versions,
                "failed_decisions": failed,
            },
            diff_summary=diff_summary,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[cascade %s] catch-all failure", cascade_id,
        )
        cascade_store.update_cascade_status(
            store,
            workspace_id=workspace_id,
            cascade_id=cascade_id,
            status="failed",
            error=f"unexpected_failure: {type(exc).__name__}: {exc}",
        )
