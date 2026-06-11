"""LLM-backed feedback classifier (W2 F5 follow-up).

The rule-based classifier in ``classify.py`` is conservative —
content without explicit lexical signals lands in ``noise``. For
real partner data (Intercom exports, App Store reviews, sales-call
notes) where partners don't supply ``type_hint``, recall on
rule-based is ~50%. This module routes to GPT-5-mini for
higher-recall classification at low cost.

Cost & latency
--------------

GPT-5-mini at low per-token cost × ~ 80 tokens/item including
prompt is well under $0.05 per 200-row CSV import. Latency: a
batch of 20 items typically returns in under a second. A 200-row
import → 10 batched calls → a few seconds. Acceptable for the
paste-feedback flow ("paste → see results in <10s").

When this becomes a bottleneck (50K+ rows or shifting to a slower
model with seconds-per-call), promote to the async queue pattern
the F5 store-doc references.

Feature flag
------------

``INSPIRA_LLM_CLASSIFIER=1`` enables the LLM path. When unset (or
when ``OPENAI_API_KEY`` is missing), classify routes back to
the rule-based fallback silently. No partner-visible behaviour
change.

Fallback
--------

Any LLM error (auth, rate-limit, parse failure, unexpected schema)
falls back to the rule-based classifier per-item. The classifier
output is treated as best-effort enrichment, never a hard
dependency. The unit tests pin this fallback path.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable

from .classify import ALLOWED_HINT_VALUES, FeedbackCategory, classify

logger = logging.getLogger(__name__)


# Default model — gpt-5-mini is the cheapest + fastest option in
# the GPT-5 family that holds up on classification tasks. Provider
# rule: non-code-gen LLM features always go to OpenAI regardless of tier.
DEFAULT_MODEL = "gpt-5-mini"

# Batch size — context allows much higher, but smaller batches
# keep the response shape predictable + give per-batch latency a
# tighter bound. 20 items × ~80 tokens = 1.6K tokens input +
# ~200 tokens output per call; well inside any limit.
DEFAULT_BATCH_SIZE = 20

# Per-call timeout (seconds). Budget guard: if the LLM hangs for
# more than this, fall back to rule-based for the whole batch.
DEFAULT_TIMEOUT_S = 12.0


@dataclass(frozen=True)
class ItemForClassify:
    """One item to classify. Carries enough context for the LLM
    but no PII or DB row internals."""

    title: str
    body: str = ""


def is_llm_enabled() -> bool:
    """Env-gate: ``INSPIRA_LLM_CLASSIFIER=1`` + a real API key.

    Returns False when either the flag is off or the key is
    missing — both states route to the rule-based fallback.
    """
    if os.environ.get("INSPIRA_LLM_CLASSIFIER", "").strip() != "1":
        return False
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


_CATEGORY_LIST_LITERAL = ", ".join(sorted(ALLOWED_HINT_VALUES))


def _build_prompt(items: list[ItemForClassify]) -> str:
    """Render the user-side prompt. The system prompt is a fixed
    classifier rubric; this varies per batch.
    """
    lines = [
        "Classify each feedback item below into exactly ONE category from this list:",
        f"  {_CATEGORY_LIST_LITERAL}",
        "",
        "Rubric:",
        "  bug       — something is broken / fails / crashes / produces wrong output.",
        "  feature   — request to add something the product doesn't currently do.",
        "  complaint — frustration about UX, performance, or pricing — not a bug.",
        "  praise    — positive sentiment with no actionable request.",
        "  question  — how-to / clarification / support question.",
        "  noise     — too short, off-topic, spam, or unclassifiable.",
        "",
        "Return a JSON object with one key: ``categories`` — an array of the same",
        f"length as the input ({len(items)}), in the same order, where each",
        "element is one of the category strings exactly. No prose, no commentary.",
        "",
        "Items:",
    ]
    for idx, item in enumerate(items):
        # Truncate excessively long bodies — saves tokens, has no
        # impact on classification accuracy.
        title = (item.title or "").strip()[:200]
        body = (item.body or "").strip()[:600]
        lines.append(f"[{idx}] title: {title!r}")
        if body:
            lines.append(f"    body: {body!r}")
    return "\n".join(lines)


def _system_prompt() -> str:
    return (
        "You are a feedback-triage classifier. You output strict JSON "
        "matching the schema requested by the caller. You never add prose, "
        "explanation, or commentary outside the JSON object."
    )


def _parse_response(text: str, expected_count: int) -> list[FeedbackCategory] | None:
    """Parse the LLM response; return None on any failure.

    With ``response_format={"type": "json_object"}`` the model is
    required to emit a single JSON object, but we defensive-parse
    anyway: strip code fences if present, parse, validate the
    array length + each entry's membership.
    """
    cleaned = text.strip()
    # Strip ```json fences as defense-in-depth — json_object mode
    # should not emit them, but cheap to handle if it does.
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("categories")
    if not isinstance(raw, list) or len(raw) != expected_count:
        return None
    out: list[FeedbackCategory] = []
    for entry in raw:
        if not isinstance(entry, str):
            return None
        normalized = entry.strip().lower()
        if normalized not in ALLOWED_HINT_VALUES:
            return None
        out.append(normalized)  # type: ignore[arg-type]
    return out


def _rule_fallback_batch(
    items: list[ItemForClassify],
) -> list[FeedbackCategory]:
    return [classify(title=it.title, body=it.body) for it in items]


def classify_batch(
    items: list[ItemForClassify],
    *,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[FeedbackCategory]:
    """Classify a batch of items via GPT-5-mini.

    Returns one category per item, in the same order. On any
    error (auth, rate-limit, parse) falls back to per-item rule-
    based classification — the result is always a valid list of
    the requested length.

    Caller can inject ``client`` for tests; otherwise lazily
    constructs an ``openai.OpenAI()`` with ``max_retries=0`` so the
    breaker logic in this module owns retry policy (mirrors the
    pattern in ``openai_adapter.py`` after issue #096).
    """
    if not items:
        return []

    if client is None:
        try:
            from openai import OpenAI  # noqa: PLC0415

            client = OpenAI(max_retries=0)
        except Exception:  # noqa: BLE001
            logger.warning(
                "llm_classify: openai SDK not importable; falling back",
                exc_info=True,
            )
            return _rule_fallback_batch(items)

    try:
        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=1024,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _build_prompt(items)},
            ],
            response_format={"type": "json_object"},
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "llm_classify: API call failed (%s); falling back to rule-based",
            exc,
        )
        return _rule_fallback_batch(items)

    try:
        text = response.choices[0].message.content or ""
    except Exception:  # noqa: BLE001
        return _rule_fallback_batch(items)

    parsed = _parse_response(text, expected_count=len(items))
    if parsed is None:
        logger.info(
            "llm_classify: response parse failed; falling back to rule-based",
        )
        return _rule_fallback_batch(items)
    return parsed


def classify_chunked(
    items: list[ItemForClassify],
    *,
    chunk_size: int = DEFAULT_BATCH_SIZE,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
) -> list[FeedbackCategory]:
    """Split a long list into batches and call classify_batch on each.

    The single-call ``classify_batch`` keeps response-parsing
    predictable; this wrapper handles the "200-row CSV import"
    case by issuing 10 sequential calls of 20 each.
    """
    if not items:
        return []
    out: list[FeedbackCategory] = []
    for start in range(0, len(items), chunk_size):
        chunk = items[start : start + chunk_size]
        out.extend(
            classify_batch(chunk, client=client, model=model)
        )
    return out


def classify_items_with_fallback(
    items: Iterable[ItemForClassify],
) -> list[FeedbackCategory]:
    """Top-level entry point used by the CSV import + Linear sync
    paths. Routes to LLM when enabled; otherwise rule-based.

    Always returns one category per input, in the same order.
    """
    items_list = list(items)
    if not items_list:
        return []
    if is_llm_enabled():
        return classify_chunked(items_list)
    return _rule_fallback_batch(items_list)
