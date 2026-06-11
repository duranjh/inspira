"""F7-REVISED — per-theme sub-agent worker (W3).

One sub-agent per top-priority theme. Reads the cluster's
feedback items + the theme's priority rationale, asks GPT-5 for
a structured set of topics + decisions citing source items,
returns the result as a Python dict shape (the ORCHESTRATOR
persists; sub-agents never touch the DB directly — this keeps
idempotency, conflict ordering, and failure isolation in one
place).

Provider rule: sub-agent extraction is non-code-gen so it always
uses OpenAI regardless of tier.

Retry policy
------------

One retry with 1.5s backoff on transient errors:
  - ``APIConnectionError``
  - ``APITimeoutError``
  - ``APIStatusError`` with status_code >= 500
  - ``RateLimitError``

Permanent errors (validation, schema, 4xx other than 429) fail
immediately — no retry. The caller (orchestrator) treats a final
failure as ``status='error'`` for that theme and proceeds with the
remaining sub-agents.

Output shape
------------

::

    {
        "topics": [
            {"title": str, "icon": str, "why_this_topic": str}
        ],
        "decisions": [
            {
                "topic_index": int,                  # position in topics
                "statement": str,
                "rationale": str,
                "subject": str,                      # short noun phrase
                                                     # for conflict detection
                "cited_feedback_item_ids": [str],
            }
        ],
        "errors": [str],                             # non-fatal warnings
    }

The ``subject`` field is the conflict-detector's join key —
two decisions with the same subject across sub-agents are a
conflict candidate (the conflict_detector then asks the LLM
to disambiguate stance vs rationale).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


SUB_AGENT_MODEL = "gpt-5"
DEFAULT_TIMEOUT_S = 30.0
RETRY_BACKOFF_S = 1.5

# Curated icon set — must match the canvas's accepted icon list so
# the orchestrator can persist topics via store.create_topic without
# the canvas-router sanitizer downgrading them.
ALLOWED_ICONS = (
    "lightbulb", "wrench", "shield", "compass", "map",
    "flag", "heart", "star", "target", "tools",
    "users", "chart", "bug", "rocket",
)


SUB_AGENT_SYSTEM_PROMPT = """You are a sub-agent for one product theme.

You receive:
1. A cluster of customer feedback items (titles + bodies + IDs).
2. A theme label and a priority rationale (why this theme is high-ROI to address).

Your job is to output:

A) TOPICS — 3-6 short topic cards covering the work needed to address
   this theme. Each topic is one focused area (e.g., "Crash detection",
   "Logging strategy", "Customer comms"). Title, icon (from the allowed
   list), and a 1-sentence why_this_topic.

B) DECISIONS — for each topic, 1-3 specific decisions someone could
   confirm or reject. Each decision has:
   - topic_index: position in your topics array (0-based)
   - statement: ONE concrete decision (e.g., "Switch error reporting
     from console.log to Sentry")
   - rationale: ONE sentence explaining why
   - subject: a SHORT noun-phrase tag for the decision's subject
     (e.g., "error_reporting", "auth_provider", "sync_cadence").
     Subjects join across sub-agents for conflict detection — keep them
     consistent and concrete.
   - cited_feedback_item_ids: the IDs of feedback items in the cluster
     that drove this decision. ALWAYS include at least one ID when an
     item motivated the decision; empty list only for general-purpose
     decisions (e.g., setup steps).

Rules:
- Output STRICT JSON. No prose before or after.
- Topic count: 3-6. Decision count: 1-3 per topic, 4-12 total.
- Keep statements actionable (verb-led). Avoid hedging language.
- ONLY use icon names from the provided list.
"""


def _build_user_prompt(
    *,
    theme_label: str,
    rationale: str,
    items: list[dict[str, Any]],
    correction_note: str = "",
) -> str:
    """Render the per-theme prompt body. Items are wrapped in XML fences.

    The XML wrapping is the same prompt-injection defense as the
    rest of the agents/ module (see prompts.py). User-supplied
    content (titles, bodies) NEVER lives outside the fences.

    ``correction_note`` (product decision): when a partner
    drags a card back into "In Progress" via the Kanban override
    dialog with the "Have Inspira rerun" toggle on, they're required
    to type a note explaining what was wrong / what to improve. That
    note flows through start-canvas → metadata.correction_note →
    synthetic theme → here as a PARTNER CORRECTION fence so the
    sub-agent's redraft actually responds to the partner's pushback.
    Empty string → no fence emitted, behaves exactly as before.
    """
    item_lines: list[str] = []
    for it in items:
        title = (it.get("title") or "").replace("</ITEM>", "")
        body = (it.get("body") or "").replace("</ITEM>", "")
        item_id = it.get("item_id") or ""
        type_hint = it.get("type_hint") or "noise"
        item_lines.append(
            f'<ITEM id="{item_id}" category="{type_hint}">\n'
            f"  TITLE: {title}\n"
            f"  BODY: {body}\n"
            f"</ITEM>"
        )
    items_block = "\n".join(item_lines) or "(no items)"
    icon_list = ", ".join(ALLOWED_ICONS)
    correction_block = ""
    if correction_note.strip():
        # Defang the closing fence the same way item bodies are.
        safe = correction_note.strip().replace("</PARTNER_CORRECTION>", "")
        correction_block = (
            "PARTNER CORRECTION (the user is asking you to redo this; "
            "weight this above your prior draft):\n"
            f"<PARTNER_CORRECTION>\n{safe}\n</PARTNER_CORRECTION>\n\n"
        )
    return (
        f"THEME LABEL: {theme_label}\n\n"
        f"PRIORITY RATIONALE: {rationale}\n\n"
        f"{correction_block}"
        f"ALLOWED ICONS: {icon_list}\n\n"
        f"FEEDBACK ITEMS IN CLUSTER:\n{items_block}\n\n"
        'Output JSON: {"topics": [...], "decisions": [...]}'
    )


def _classify_exception(exc: BaseException) -> str:
    """Return ``"transient"`` or ``"permanent"`` for retry policy.

    The OpenAI SDK exposes typed HTTP-level errors with stable class
    names (APIConnectionError, APITimeoutError, APIStatusError,
    RateLimitError). We classify by class + status_code where
    present. Anything we can't identify is treated as permanent
    (fail-fast on unknowns rather than burn an extra retry on a
    malformed call).
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if name in ("APIConnectionError", "APITimeoutError"):
        return "transient"
    if name == "APIStatusError" and isinstance(status, int) and status >= 500:
        return "transient"
    if name == "RateLimitError":
        return "transient"
    return "permanent"


def _strip_json_fences(raw: str) -> str:
    """Trim Markdown code fences as defense-in-depth.

    With ``response_format={"type": "json_object"}`` the model
    should never emit fences, but we strip them anyway for safety.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        # ```json\n...\n```  -> middle
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return raw


def _validate_and_clean(
    parsed: Any,
    *,
    valid_item_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Validate + sanitize the LLM output. Returns (topics, decisions, errors).

    Drops malformed entries with a non-fatal entry in errors. Coerces
    icons outside the allowed list to "lightbulb" (sane default).
    Filters cited_feedback_item_ids to only include real cluster IDs
    (LLM hallucinations get dropped, not retained).
    """
    errors: list[str] = []
    topics_raw = parsed.get("topics") if isinstance(parsed, dict) else None
    decisions_raw = (
        parsed.get("decisions") if isinstance(parsed, dict) else None
    )
    if not isinstance(topics_raw, list) or not topics_raw:
        return ([], [], ["sub_agent_no_topics"])
    if not isinstance(decisions_raw, list):
        decisions_raw = []
    topics: list[dict[str, Any]] = []
    for t in topics_raw:
        if not isinstance(t, dict):
            errors.append("topic_not_dict")
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            errors.append("topic_missing_title")
            continue
        icon = str(t.get("icon") or "").strip()
        if icon not in ALLOWED_ICONS:
            icon = "lightbulb"
        why = str(t.get("why_this_topic") or "").strip()
        topics.append(
            {"title": title, "icon": icon, "why_this_topic": why}
        )
    if not topics:
        return ([], [], errors + ["sub_agent_all_topics_invalid"])
    decisions: list[dict[str, Any]] = []
    for d in decisions_raw:
        if not isinstance(d, dict):
            errors.append("decision_not_dict")
            continue
        try:
            topic_index = int(d.get("topic_index"))
        except (TypeError, ValueError):
            errors.append("decision_topic_index_missing")
            continue
        if topic_index < 0 or topic_index >= len(topics):
            errors.append("decision_topic_index_oob")
            continue
        statement = str(d.get("statement") or "").strip()
        if not statement:
            errors.append("decision_missing_statement")
            continue
        rationale = str(d.get("rationale") or "").strip()
        subject = str(d.get("subject") or "").strip().lower()
        if not subject:
            # No subject means conflict detection can't join — record but
            # keep the decision (still useful, just not conflict-checked).
            subject = "_unspecified"
        raw_cites = d.get("cited_feedback_item_ids") or []
        if not isinstance(raw_cites, list):
            raw_cites = []
        cited = [
            str(c).strip()
            for c in raw_cites
            if isinstance(c, (str, int))
            and str(c).strip() in valid_item_ids
        ]
        decisions.append(
            {
                "topic_index": topic_index,
                "statement": statement,
                "rationale": rationale,
                "subject": subject,
                "cited_feedback_item_ids": cited,
            }
        )
    return (topics, decisions, errors)


def _call_openai(
    *,
    theme_label: str,
    rationale: str,
    items: list[dict[str, Any]],
    timeout_s: float,
    correction_note: str = "",
) -> str:
    """One OpenAI call returning the response's combined text.

    Imports the SDK lazily so the module imports without a hard
    dependency in test environments that mock openai via
    ``sys.modules`` patching. Uses ``response_format=json_object``
    so the model is required to emit a single JSON object.

    ``max_completion_tokens=8192`` + ``reasoning_effort="low"``:
    gpt-5 family models count internal reasoning tokens against
    the completion budget; default reasoning eats the budget on
    moderately-large prompts and emits empty content. Low effort
    keeps the output side complete for the typical 3-6 topics ×
    1-3 decisions shape (mirrors the F6 fix in prioritization.py).
    """
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(max_retries=0)
    user_prompt = _build_user_prompt(
        theme_label=theme_label,
        rationale=rationale,
        items=items,
        correction_note=correction_note,
    )
    response = client.chat.completions.create(
        model=SUB_AGENT_MODEL,
        max_completion_tokens=8192,
        messages=[
            {"role": "system", "content": SUB_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        reasoning_effort="low",
        timeout=timeout_s,
    )
    return response.choices[0].message.content or ""


def is_openai_available() -> bool:
    """Env gate. When False, ``extract_topics_and_decisions_for_theme``
    returns empty topics + a single ``"sub_agent_disabled"`` error so
    the caller can still record the sub_agent_run row as failed.
    """
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def extract_topics_and_decisions_for_theme(
    *,
    cluster_id: str,
    theme_label: str,
    rationale: str,
    items: list[dict[str, Any]],
    timeout_s: float = DEFAULT_TIMEOUT_S,
    sleep_for_test: float = RETRY_BACKOFF_S,
    correction_note: str = "",
) -> dict[str, Any]:
    """Synchronously run one sub-agent. Returns the structured dict.

    Args:
        cluster_id: just for log context — sub-agents are stateless.
        theme_label: short label of the theme (from F6 output).
        rationale: F6's per-theme rationale.
        items: list of dicts with item_id / title / body / type_hint.
        timeout_s: per-call LLM timeout.
        sleep_for_test: backoff between retries; tests pass 0.0 to
            avoid wall-clock latency.

    Returns:
        ``{"topics": [...], "decisions": [...], "errors": [...]}``.

    Never raises — all failure modes return empty topics/decisions
    with an explanatory entry in ``errors``. The orchestrator
    interprets empty topics as "this theme failed; mark sub_agent_run
    status='error'".
    """
    valid_item_ids = {str(it.get("item_id")) for it in items if it.get("item_id")}
    if not is_openai_available():
        return {
            "topics": [], "decisions": [],
            "errors": ["sub_agent_disabled_no_api_key"],
        }
    last_exc_name: str | None = None
    raw_text: str | None = None
    for attempt in range(2):  # initial + one retry
        try:
            raw_text = _call_openai(
                theme_label=theme_label,
                rationale=rationale,
                items=items,
                timeout_s=timeout_s,
                correction_note=correction_note,
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_exc_name = type(exc).__name__
            kind = _classify_exception(exc)
            logger.warning(
                "sub_agent[%s] attempt=%d %s exc=%s msg=%s",
                cluster_id, attempt, kind, last_exc_name, exc,
            )
            if kind == "permanent" or attempt >= 1:
                return {
                    "topics": [], "decisions": [],
                    "errors": [f"sub_agent_llm_failed: {last_exc_name}"],
                }
            time.sleep(sleep_for_test)
    if raw_text is None:
        return {
            "topics": [], "decisions": [],
            "errors": [f"sub_agent_llm_failed: {last_exc_name or 'unknown'}"],
        }
    cleaned = _strip_json_fences(raw_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("sub_agent[%s] json parse failed: %s", cluster_id, exc)
        return {
            "topics": [], "decisions": [],
            "errors": [f"sub_agent_json_parse: {exc}"],
        }
    topics, decisions, errors = _validate_and_clean(
        parsed, valid_item_ids=valid_item_ids,
    )
    return {"topics": topics, "decisions": decisions, "errors": errors}
