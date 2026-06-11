"""Auto-link adapter — infers relationships for a newly created topic.

When a user drops a new topic onto the canvas composer, we don't want it
landing as an orphan island. This adapter asks the LLM to identify 0-4
genuine connections between the new topic and the existing topics in the
project, then the API layer turns those into persisted relationships.

Design constraints:
- Conservative: only propose relationships with a concrete shared concept.
  A generic "relates to" on every pair would defeat the point.
- Direction-aware: some relationships flow into the new topic ("feeds"),
  others flow outward ("drives"). The schema forces the model to pick.
- Cheap: small prompt, low reasoning_effort, tight max_completion_tokens.
  This runs on EVERY new-topic creation, so cost and latency matter.
- Safe: matches target titles from an explicit allowlist (the existing
  topics). Anything the model hallucinates gets dropped by the caller.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .openai_adapter import _call_with_toolcall_retry


AUTO_LINK_SYSTEM_PROMPT = """\
You identify genuine connections between a newly-added topic and the
existing topics on someone's planning canvas.

Your job: return 0 to 4 relationships. Fewer is better than forcing
loose ones — a canvas with one clear connection beats a canvas with
four generic "relates to" strings.

Each relationship you propose has:
1. `target_topic_title` — EXACT title of an existing topic (copy-paste
   from the list provided; don't paraphrase or capitalize differently).
2. `label` — 1-3 word verb phrase describing the connection. Prefer
   specific verbs ("feeds", "constrains", "depends on", "informs",
   "precedes", "supports", "drives", "shapes"). Avoid "relates to" —
   it's empty calories.
3. `direction` — either `from_new` (the new topic → the target) or
   `to_new` (the target → the new topic). Pick based on which way the
   information or dependency actually flows.

When to skip a connection:
- The two topics share a domain but not a concept (a novel's "Voice"
  and "Setting" are both parts of a novel, but they don't DIRECTLY
  constrain each other — skip).
- The relationship would just repeat what's already implied by being
  in the same project.
- You're reaching. Users hate obvious AI-generated link spam.

Output a `propose_auto_links` tool call with your `relationships` array.
"""


AUTO_LINK_TOOL_NAME = "propose_auto_links"


AUTO_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relationships": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_topic_title": {"type": "string", "minLength": 1},
                    "label": {"type": "string", "minLength": 1, "maxLength": 60},
                    "direction": {"type": "string", "enum": ["from_new", "to_new"]},
                },
                "required": ["target_topic_title", "label", "direction"],
            },
        },
    },
    "required": ["relationships"],
}


@dataclass(slots=True)
class AutoLinkConfig:
    # gpt-4o-mini: auto-link runs after every kickoff/topic_turn —
    # latency adds to perceived slowness. Was gpt-5-mini, switched
    # for the same reason as outline.py.
    model: str = "gpt-4o-mini"
    timeout_s: float = 15.0
    max_completion_tokens: int = 4096
    # gpt-4o-mini rejects reasoning_effort with 400 BadRequest.
    reasoning_effort: str | None = None
    temperature: float | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_retries: int = 1


class AutoLinkAdapter:
    """Lightweight adapter for auto-linking a new topic to existing ones."""

    def __init__(
        self,
        config: AutoLinkConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or AutoLinkConfig()
        if client is None:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "openai package missing. Install via `pip install -e services/`.",
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.config.api_key is not None:
                kwargs["api_key"] = self.config.api_key
            if self.config.base_url is not None:
                kwargs["base_url"] = self.config.base_url
            client = OpenAI(**kwargs)
        self.client = client

    def propose_links(
        self,
        *,
        new_topic: dict[str, Any],
        existing_topics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a sanitized list of relationship proposals.

        Each proposal dict has keys: ``target_topic_title``, ``label``,
        ``direction``. The caller is responsible for resolving titles to
        topic_ids and creating the rows.
        """
        if not existing_topics:
            return []

        user_message = _format_user_message(new_topic, existing_topics)
        tool_spec = {
            "type": "function",
            "function": {
                "name": AUTO_LINK_TOOL_NAME,
                "description": (
                    "Propose 0-4 relationships between the new topic and "
                    "existing topics. Conservative — skip anything loose."
                ),
                "parameters": AUTO_LINK_SCHEMA,
                "strict": True,
            },
        }

        create_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": AUTO_LINK_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "tools": [tool_spec],
            "tool_choice": {
                "type": "function",
                "function": {"name": AUTO_LINK_TOOL_NAME},
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
            expected_name=AUTO_LINK_TOOL_NAME,
            max_retries=self.config.max_retries,
            breaker_key="auto_link",
        )

        return _sanitize_proposals(parsed, existing_topics)


def _format_user_message(
    new_topic: dict[str, Any], existing_topics: list[dict[str, Any]],
) -> str:
    """Build the user-facing message for the auto-link call."""
    new_title = (new_topic.get("title") or "").strip() or "(untitled)"
    new_why = (
        new_topic.get("metadata", {}).get("why_this_topic")
        if isinstance(new_topic.get("metadata"), dict)
        else None
    ) or ""
    existing_descriptions: list[str] = []
    for t in existing_topics:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        why = ""
        metadata = t.get("metadata")
        if isinstance(metadata, dict):
            why = (metadata.get("why_this_topic") or "").strip()
        line = f"- {title}"
        if why:
            line += f" — {why}"
        existing_descriptions.append(line)

    payload = {
        "new_topic": {"title": new_title, "why_this_topic": new_why},
        "existing_topics": existing_descriptions,
    }
    return (
        "The user just added this topic to their canvas:\n\n"
        f"NEW TOPIC: {new_title}\n"
        + (f"WHY: {new_why}\n" if new_why else "")
        + "\nEXISTING TOPICS on the canvas:\n"
        + "\n".join(existing_descriptions)
        + "\n\nPropose 0-4 genuine relationships between the new topic and "
        "the existing ones. Copy target titles exactly. Full JSON payload "
        f"for reference: {json.dumps(payload, ensure_ascii=False)}"
    )


def _sanitize_proposals(
    parsed: dict[str, Any], existing_topics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate + filter the model's output.

    Drops any proposal that references an unknown target title, has a
    blank label, or has an unknown direction. The model is already
    schema-constrained, but this is belt-and-suspenders for the
    title-match guarantee — the caller NEEDS the title to resolve to
    a real topic_id or the create_relationship call will fail.
    """
    raw = parsed.get("relationships") or []
    if not isinstance(raw, list):
        return []
    valid_titles = {t.get("title", "") for t in existing_topics if t.get("title")}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        target = (item.get("target_topic_title") or "").strip()
        label = (item.get("label") or "").strip()
        direction = item.get("direction")
        if target not in valid_titles:
            continue
        if not label:
            continue
        if direction not in {"from_new", "to_new"}:
            continue
        key = (target, direction)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "target_topic_title": target,
            "label": label,
            "direction": direction,
        })
        if len(out) >= 4:
            break
    return out


__all__ = [
    "AutoLinkAdapter",
    "AutoLinkConfig",
    "AUTO_LINK_SYSTEM_PROMPT",
    "AUTO_LINK_SCHEMA",
    "AUTO_LINK_TOOL_NAME",
]

_ = field  # keep import used
