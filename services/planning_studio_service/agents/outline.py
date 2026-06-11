"""Outline adapter — produces a structured hierarchical outline for a
user-chosen artifact type.

Shares plumbing with ``plan_summary.py`` (same circuit breaker, same
retry policy) but is its own adapter because the input shape and the
output schema are different: outline takes an ``artifact_type`` string,
outline returns a tree of sections/subsections/sub-subsections.

Privacy contract: receives project title, topics, and confirmed
decisions only — no Q&A turn bodies, no attachment data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .openai_adapter import _call_with_toolcall_retry
from .plan_summary import _build_extra_tool_spec
from .prompts import locale_hint
from .prompts_extra import OUTLINE_PROMPT


@dataclass(slots=True)
class OutlineConfig:
    """Tunable config for the outline adapter. Safe defaults."""

    # gpt-4o-mini for outline: typical 2-4s, p99 ~10s. Switched from
    # gpt-5-mini (a reasoning model) which routinely spent 30-60s per
    # call on the production prompt and tripped the kickoff timeout.
    # Outlines are structure-heavy but not reasoning-heavy — list 4-8
    # topics per a JSON schema. The reasoning premium isn't earning
    # its latency cost here.
    model: str = "gpt-4o-mini"
    timeout_s: float = 15.0
    max_empty_toolcall_retries: int = 1
    temperature: float | None = None
    # gpt-4o-mini caps output at 16384 tokens. Plenty of margin for a
    # long chapter outline; you're billed on actual usage so the
    # ceiling is safe.
    max_completion_tokens: int = 16384
    # gpt-4o-mini is not a reasoning model — pass None so the
    # `reasoning_effort` param is omitted (gpt-4o-mini rejects it
    # with 400 BadRequest if sent).
    reasoning_effort: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class OutlineAdapter:
    """OpenAI-backed adapter for Mode 2 (outline generator)."""

    def __init__(
        self,
        config: OutlineConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or OutlineConfig()
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
        artifact_type: str,
        topics: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Produce the hierarchical outline.

        Args:
            project_title: project display title.
            artifact_type: free-text — treated as authoritative. E.g.
                "Chapter outline", "Pitch deck outline".
            topics: list of topic dicts.
            decisions: list of decision dicts.

        Returns the parsed tool_call dict matching ``OUTLINE_SCHEMA``.
        """
        if not project_title or not project_title.strip():
            raise ValueError("project_title is required")
        if not artifact_type or not artifact_type.strip():
            raise ValueError("artifact_type is required")

        user_message = _format_outline_user_message(
            project_title=project_title,
            artifact_type=artifact_type,
            topics=topics or [],
            decisions=decisions or [],
        )

        tool_spec = _build_extra_tool_spec("outline_response")

        system_prompt = OUTLINE_PROMPT + locale_hint(locale)
        create_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "tools": [tool_spec],
            "tool_choice": {
                "type": "function",
                "function": {"name": "outline_response"},
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
            expected_name="outline_response",
            max_retries=self.config.max_empty_toolcall_retries,
            breaker_key="outline",
        )
        return parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_outline_user_message(
    *,
    project_title: str,
    artifact_type: str,
    topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> str:
    """Render the user-facing message body for the outline call."""
    decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
    for d in decisions:
        tid = d.get("topic_id") or ""
        decisions_by_topic.setdefault(tid, []).append(d)

    lines: list[str] = []
    lines.append(f"PROJECT: {project_title}")
    lines.append(f"ARTIFACT TYPE REQUESTED: {artifact_type}")
    lines.append("")

    if not topics:
        lines.append("(No topics on this project yet — propose a scaffold outline for the artifact type from first principles.)")
        lines.append("")
    else:
        lines.append("Topics on this project with their confirmed decisions:")
        lines.append("")

    for topic in topics:
        tid = topic.get("topic_id", "")
        title = topic.get("title", "(untitled)")
        lines.append(f"- {title}")
        tdecs = decisions_by_topic.get(tid, [])
        for d in tdecs:
            if d.get("status") == "retracted":
                continue
            stmt = (d.get("statement") or "").strip()
            if not stmt:
                continue
            lines.append(f"    - {stmt}")

    if topics:
        lines.append("")
    lines.append(
        "Produce the outline per the OUTLINE instructions. Use the "
        "artifact-type conventions (chapters for a novel, slides for a "
        "deck, IMRAD for a research report). Propose what SHOULD be in "
        "a good artifact of this type — don't just recite the decisions. "
        "Return a single outline_response tool call.",
    )
    return "\n".join(lines)


def from_env() -> OutlineAdapter:
    """Build an adapter using ``OPENAI_API_KEY`` from the environment."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put your OpenAI key in the env before "
            "calling from_env(): $env:OPENAI_API_KEY = 'sk-...'"
        )
    return OutlineAdapter()
