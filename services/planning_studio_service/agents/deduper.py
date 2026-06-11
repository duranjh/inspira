"""Topic deduper adapter — finds semantic duplicates among a project's topics.

Same plumbing pattern as ``plan_summary.py`` / ``outline.py``. The
output is a (possibly empty) list of merge-proposal pairs.

Privacy contract: receives topic ids, titles, and confirmed decisions
only. Q&A bodies and attachments are never sent.

Post-call defense: the adapter trims proposals referencing topic ids
that aren't actually in the input set. That shouldn't happen with
strict JSON mode, but the repair is cheap and keeps the API layer
from ever trying to merge a hallucinated id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .openai_adapter import _call_with_toolcall_retry
from .plan_summary import _build_extra_tool_spec
from .prompts import locale_hint
from .prompts_extra import DEDUPER_PROMPT


@dataclass(slots=True)
class DeduperConfig:
    """Tunable config for the deduper adapter. Safe defaults."""

    # gpt-4o-mini: deduper runs after kickoff to merge near-duplicate
    # topics. Latency adds to perceived kickoff time. Was gpt-5-mini,
    # switched for the same reason as outline.py.
    model: str = "gpt-4o-mini"
    timeout_s: float = 15.0
    max_empty_toolcall_retries: int = 1
    temperature: float | None = None
    # Dedup output is short — a list of pairs. 4096 is plenty.
    max_completion_tokens: int = 4096
    # gpt-4o-mini rejects reasoning_effort with 400 BadRequest.
    reasoning_effort: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class DeduperAdapter:
    """OpenAI-backed adapter for Mode 3 (topic deduper)."""

    def __init__(
        self,
        config: DeduperConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or DeduperConfig()
        if client is None:
            try:
                from openai import OpenAI  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "The 'openai' package is not installed. "
                    "Run: pip install openai (or pip install -e services[dev])"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.config.api_key is not None:
                kwargs["api_key"] = self.config.api_key
            if self.config.base_url is not None:
                kwargs["base_url"] = self.config.base_url
            client = OpenAI(**kwargs)
        self.client = client

    def generate(
        self,
        *,
        topics: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Scan topics for semantic duplicates.

        Args:
            topics: list of {topic_id, title, icon, ...}.
            decisions: list of {decision_id, topic_id, statement, status}.

        Returns the parsed tool_call dict matching ``DEDUPER_SCHEMA``.
        Proposals referencing unknown topic ids are dropped defensively.
        """
        if topics is None:
            topics = []
        if decisions is None:
            decisions = []

        # Short-circuit: fewer than 2 topics means there is nothing to
        # compare. Return an empty result without burning any tokens.
        if len(topics) < 2:
            return {"merge_proposals": [], "_sanitize": {"short_circuit": True}}

        user_message = _format_deduper_user_message(
            topics=topics, decisions=decisions,
        )

        tool_spec = _build_extra_tool_spec("dedupe_response")

        system_prompt = DEDUPER_PROMPT + locale_hint(locale)
        create_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "tools": [tool_spec],
            "tool_choice": {
                "type": "function",
                "function": {"name": "dedupe_response"},
            },
            "max_completion_tokens": self.config.max_completion_tokens,
            "timeout": self.config.timeout_s,
        }
        if self.config.temperature is not None:
            create_kwargs["temperature"] = self.config.temperature
        if self.config.reasoning_effort is not None:
            create_kwargs["reasoning_effort"] = self.config.reasoning_effort

        parsed = _call_with_toolcall_retry(
            self.client,
            create_kwargs,
            expected_name="dedupe_response",
            max_retries=self.config.max_empty_toolcall_retries,
            breaker_key="dedupe",
        )
        _sanitize_deduper_response(parsed, topics)
        return parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_deduper_user_message(
    *,
    topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> str:
    """Render the user-facing message body for the deduper call.

    Lists each topic with its ID (so the model can reference it by
    topic_id in the output) and its confirmed decisions.
    """
    decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
    for d in decisions:
        tid = d.get("topic_id") or ""
        decisions_by_topic.setdefault(tid, []).append(d)

    lines: list[str] = []
    lines.append(
        f"This project has {len(topics)} topic(s). Identify pairs that "
        "genuinely overlap — semantic duplicates on the canvas the user "
        "may not have realised.",
    )
    lines.append("")
    for topic in topics:
        tid = topic.get("topic_id", "")
        title = topic.get("title", "(untitled)")
        lines.append(f"TOPIC id={tid} title={title!r}")
        tdecs = decisions_by_topic.get(tid, [])
        if tdecs:
            for d in tdecs:
                if d.get("status") == "retracted":
                    continue
                stmt = (d.get("statement") or "").strip()
                if not stmt:
                    continue
                lines.append(f"  - {stmt}")
        else:
            lines.append("  (no confirmed decisions)")
        lines.append("")

    lines.append(
        "Return only the pairs that genuinely overlap. An empty list is "
        "a valid answer. Be conservative — false positives are worse than "
        "misses. Return a single dedupe_response tool call.",
    )
    return "\n".join(lines)


def _sanitize_deduper_response(
    parsed: dict[str, Any],
    topics: list[dict[str, Any]],
) -> None:
    """Drop merge proposals referencing topic ids not in the input set.

    Also drops proposals where both ids are the same (a self-merge is
    never sensible) — strict mode doesn't catch that.

    Repair log written to ``parsed["_sanitize"]``.
    """
    known_ids = {t.get("topic_id") for t in topics if t.get("topic_id")}
    proposals = parsed.get("merge_proposals") or []
    clean: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for p in proposals:
        a = p.get("topic_a_id")
        b = p.get("topic_b_id")
        if not a or not b or a == b or a not in known_ids or b not in known_ids:
            dropped.append({
                "topic_a_id": a,
                "topic_b_id": b,
                "reason": "unknown or duplicate topic id",
            })
            continue
        clean.append(p)
    parsed["merge_proposals"] = clean
    parsed["_sanitize"] = {"dropped_proposals": dropped}


def from_env() -> DeduperAdapter:
    """Build an adapter using ``OPENAI_API_KEY`` from the environment."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put your OpenAI key in the env before "
            "calling from_env(): export OPENAI_API_KEY=sk-... "
            "(or $env:OPENAI_API_KEY='sk-...' on Windows)"
        )
    return DeduperAdapter()
