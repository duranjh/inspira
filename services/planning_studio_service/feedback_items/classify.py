"""Rule-based feedback classifier (W2 F5).

Maps a feedback item to one of:
  bug | feature | complaint | praise | question | noise

Strategy: lexical signals on title + body. Cheap, deterministic,
no API calls. The W3 LLM-backed classifier will replace this
with a Haiku/gpt-5-mini call when budgets and partner volume
justify it; until then, the keyword approach gets the demo flow
working without infra cost.

Behaviour notes:
- The user-supplied ``type_hint`` (from CSV imports or future
  source-side category fields) is ALWAYS preferred over the
  classifier when present. The classifier never overwrites a
  partner-provided category.
- Very short content (< 6 chars after trim) defaults to ``noise``.
  Catches the "?" / "test" / "ok" rows in the CSV fixture.
- Classification is intentionally one-label, not multi-label.
  Multi-label clustering is the F5+ embedding-based dedupe job;
  this function returns the single best fit.
"""
from __future__ import annotations

import re
from typing import Literal

FeedbackCategory = Literal[
    "bug", "feature", "complaint", "praise", "question", "noise"
]

ALLOWED_HINT_VALUES = {
    "bug",
    "feature",
    "complaint",
    "praise",
    "question",
    "noise",
}

_NOISE_THRESHOLD = 6  # chars

# Order matters: first match wins. Bug/error keywords come early
# because "broken on Safari" trumps "Safari" being mentioned in a
# complaint about typography.
_BUG_PATTERNS = (
    r"\bcrash(es|ed|ing)?\b",
    r"\bbroken?\b",
    r"\b(?:not|won['’]?t|doesn['’]?t|cannot|can['’]?t)\s+(work|load|save|open|render|display|submit)",
    r"\b(?:throws?|threw)\s+(?:an\s+)?error\b",
    r"\bfreezes?\b",
    r"\bspinner\s+forever\b",
    r"\bblank\s+screen\b",
    r"\b500\b|\b502\b|\b503\b",
    r"\blost\s+my\s+work\b",
    r"\bbug\b",
    r"\bduplicates?\b.*\b(rows?|items?|entries)\b",
)

_FEATURE_PATTERNS = (
    # Polite "can we add X" — fires before the bare question shape
    # below. Requires "we" / "you" / "i" + a "?" so it's a request,
    # not a yes/no question.
    r"\bcan\s+(?:we|you|i)\s+(?:add|enable|allow|get|have|use|see)\b",
    r"\bplease\s+(?:add|consider|make|allow|support)\b",
    r"\bwould\s+be\s+(?:great|huge|nice|amazing)\b",
    r"\bfeature\s+request\b",
    # Imperative "add X" at the start of the title.
    r"^add\s+\w+",
    r"^enable\s+\w+",
    r"^allow\s+\w+",
    r"\bshould\s+(?:have|support|allow|remember)\b",
    r"\bneed\s+(?:a|an|the)\s+\w+",
)

_COMPLAINT_PATTERNS = (
    r"\btoo\s+(slow|expensive|complicated|noisy|many|few|hard|confusing)\b",
    r"\bhate\s+the\b",
    r"\bjanky\b",
    r"\bconfusing\b",
    r"\boverwhelming\b",
    r"\bregret\b",
    r"\bpapercut\b",
    r"\bzero\s+docs\b",
    r"\bonboarding.*rough\b",
)

_PRAISE_PATTERNS = (
    r"\blove\s+the\b",
    r"\bgame\s+changer\b",
    r"\bchef['’]?s\s+kiss\b",
    r"\bdeserves?\s+a\s+raise\b",
    r"\bthanks\s+for\s+shipping\b",
    r"\bbest\s+(?:in|i['’]?ve)\b",
    r"\btake\s+my\s+money\b",
    r"\bbutter[- ]?smooth\b",
    r"\bso\s+much\s+better\b",
)

_QUESTION_PATTERNS = (
    r"^\s*how\s+do\s+i\b",
    r"^\s*(can|could|will|when|where|is|does|do)\b.+\?$",
    r"\bcan\s+(?:i|the\s+\w+)\s+\w+",
)


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def classify(
    *,
    title: str,
    body: str = "",
    hint: str | None = None,
) -> FeedbackCategory:
    """Return the best-fit category for a feedback item.

    ``hint`` (e.g., the CSV ``type_hint`` column) wins when it's a
    recognized category. Otherwise the classifier picks based on
    lexical signal in ``title`` + ``body``.
    """
    if hint:
        normalized = hint.strip().lower()
        if normalized in ALLOWED_HINT_VALUES:
            return normalized  # type: ignore[return-value]

    haystack_full = f"{title}\n{body}".strip()
    cleaned = re.sub(r"\s+", " ", haystack_full).strip()

    if len(cleaned) < _NOISE_THRESHOLD:
        return "noise"

    # The check order matters:
    # - bug wins everything (most actionable)
    # - feature wins question because "Can we add X?" is a polite
    #   feature request, not a how-to question. The feature
    #   patterns are tighter than the bare question shape.
    # - question wins praise/complaint because "How do I X?" is a
    #   support ticket, not a vibe.
    if _matches_any(_BUG_PATTERNS, cleaned):
        return "bug"
    if _matches_any(_FEATURE_PATTERNS, cleaned):
        return "feature"
    if _matches_any(_QUESTION_PATTERNS, cleaned):
        return "question"
    if _matches_any(_PRAISE_PATTERNS, cleaned):
        return "praise"
    if _matches_any(_COMPLAINT_PATTERNS, cleaned):
        return "complaint"

    # No signal — call it noise rather than misfiling. Partners
    # would rather see "we couldn't classify this" than a wrong
    # bucket.
    return "noise"
