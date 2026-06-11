"""Plan Summary adapter — artifact writer that produces a cohesive narrative
document for an entire Inspira project.

Distinct from ``openai_adapter.OpenAIPlanningInterviewer`` by design:

- The interviewer is asking questions; this adapter is synthesizing a
  stand-alone document. The voices are related but not identical.
- The interviewer carries a kickoff/topic_turn retry + sanitize pipeline
  specific to those tool shapes. This adapter reuses the same circuit
  breaker + transient-retry plumbing via ``_call_with_toolcall_retry``
  but doesn't need the per-mode sanitize helpers.

Privacy contract: the prompt receives titles, confirmed decisions, and
a sample of Q&A turn bodies (enough to show the texture of the
thinking). It does NOT receive attached-source excerpts or raw
attachment bytes — the summary runs on the structured thinking the
user already committed to, not the raw inputs they fed in.

Errors surface as ``RuntimeError`` so the API layer can translate them
into the same generic ``planner_call_failed`` response used by the
primary modes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .openai_adapter import _build_openai_tool_spec as _core_build_tool_spec
from .openai_adapter import _call_with_toolcall_retry
from .prompts import locale_hint
from .prompts_extra import PLAN_SUMMARY_PROMPT
from .schemas_extra import EXTRA_TOOL_SPECS


# Cap on how many Q&A turns we include per topic. The summary benefits
# from seeing enough texture to write naturally, but the whole transcript
# is both expensive and noisy. 2 per topic (most-recent-first) is enough
# for the model to hear the voice without drowning in chatter.
_MAX_TURNS_PER_TOPIC = 2


@dataclass(slots=True)
class PlanSummaryConfig:
    """Tunable config for the plan-summary adapter. Safe defaults."""

    # gpt-4o-mini for prose summary: same quality on narrative output,
    # ~5x faster than gpt-5-mini. Was gpt-5-mini, switched for the
    # same reason as outline.py.
    model: str = "gpt-4o-mini"
    timeout_s: float = 20.0  # Prose output, but under 5s on gpt-4o-mini.
    max_empty_toolcall_retries: int = 1
    temperature: float | None = None
    # Generous for a long prose payload — 800 words is roughly 1000-1200
    # tokens. Billed on actual usage so the ceiling is safe.
    max_completion_tokens: int = 16384
    # gpt-4o-mini rejects reasoning_effort with 400 BadRequest.
    reasoning_effort: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class PlanSummaryAdapter:
    """OpenAI-backed adapter for Mode 1 (plan summary)."""

    def __init__(
        self,
        config: PlanSummaryConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or PlanSummaryConfig()
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
        project_title: str,
        topics: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        sample_turns: list[dict[str, Any]],
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Produce the narrative summary.

        Args:
            project_title: Project's display title.
            topics: list of {topic_id, title, icon, ...}.
            decisions: list of {decision_id, topic_id, statement,
                rationale, status}.
            sample_turns: list of {turn_id, topic_id, role, body, ...}
                capped per topic by the caller or internally.

        Returns the parsed tool_call dict matching ``PLAN_SUMMARY_SCHEMA``.
        """
        if not project_title or not project_title.strip():
            raise ValueError("project_title is required")

        user_message = _format_summary_user_message(
            project_title=project_title,
            topics=topics or [],
            decisions=decisions or [],
            sample_turns=sample_turns or [],
        )

        tool_spec = _build_extra_tool_spec("plan_summary")

        system_prompt = PLAN_SUMMARY_PROMPT + locale_hint(locale)
        create_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "tools": [tool_spec],
            "tool_choice": {"type": "function", "function": {"name": "plan_summary"}},
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
            expected_name="plan_summary",
            max_retries=self.config.max_empty_toolcall_retries,
            breaker_key="plan_summary",
        )
        return parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_extra_tool_spec(tool_name: str) -> dict[str, Any]:
    """Wrap an extra-tool schema in OpenAI's function-tool envelope.

    Mirrors ``openai_adapter._build_openai_tool_spec`` but reads from
    ``EXTRA_TOOL_SPECS`` so we don't mutate the core registry.
    """
    spec = EXTRA_TOOL_SPECS[tool_name]
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": spec["description"],
            "parameters": spec["schema"],
            "strict": True,
        },
    }


def _format_summary_user_message(
    *,
    project_title: str,
    topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    sample_turns: list[dict[str, Any]],
) -> str:
    """Render the user-facing message body for the summary call.

    Groups content by topic so the model can hear the shape of each
    area before synthesizing the whole. Sample turns are limited to
    bodies only — no attachments, no rationale side-channels.
    """
    # Index decisions by topic_id so we can interleave them with each topic.
    decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
    for d in decisions:
        tid = d.get("topic_id") or ""
        decisions_by_topic.setdefault(tid, []).append(d)

    turns_by_topic: dict[str, list[dict[str, Any]]] = {}
    for t in sample_turns:
        tid = t.get("topic_id") or ""
        turns_by_topic.setdefault(tid, []).append(t)

    lines: list[str] = []
    lines.append(f"PROJECT: {project_title}")
    lines.append("")

    if not topics:
        lines.append("(No topics on this project yet.)")
        lines.append("")
    else:
        lines.append(f"This project has {len(topics)} topic(s) on its canvas.")
        lines.append("")

    for topic in topics:
        tid = topic.get("topic_id", "")
        title = topic.get("title", "(untitled)")
        icon = topic.get("icon", "?")
        lines.append(f"TOPIC: {title} [{icon}]")

        tdecs = decisions_by_topic.get(tid, [])
        if tdecs:
            lines.append("  Confirmed decisions:")
            for d in tdecs:
                if d.get("status") == "retracted":
                    continue
                stmt = (d.get("statement") or "").strip()
                if not stmt:
                    continue
                rationale = (d.get("rationale") or "").strip()
                line = f"    - {stmt}"
                if rationale:
                    line += f" (because: {rationale})"
                lines.append(line)
        else:
            lines.append("  (No decisions captured yet.)")

        tturns = turns_by_topic.get(tid, [])
        # Cap per topic so large projects stay within prompt budget.
        tturns = tturns[:_MAX_TURNS_PER_TOPIC]
        if tturns:
            lines.append("  Sample Q&A turns (for voice/texture):")
            for t in tturns:
                role = (t.get("role") or "?").upper()
                body = (t.get("body") or "").strip()
                if not body:
                    continue
                lines.append(f"    [{role}] {body}")
        lines.append("")

    lines.append(
        "Produce the narrative summary per the PLAN_SUMMARY instructions. "
        "600-1200 words of cohesive prose. Return a single plan_summary "
        "tool call.",
    )
    return "\n".join(lines)


def from_env() -> PlanSummaryAdapter:
    """Build an adapter using ``OPENAI_API_KEY`` from the environment."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put your OpenAI key in the env before "
            "calling from_env(): $env:OPENAI_API_KEY = 'sk-...'"
        )
    return PlanSummaryAdapter()
