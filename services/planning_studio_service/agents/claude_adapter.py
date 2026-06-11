"""Anthropic Claude adapter for the ``planning_interviewer`` role.

Primary backing for the **frontier** model tier. Same public interface as
``OpenAIPlanningInterviewer`` (``kickoff`` / ``topic_turn``), same returned
dict shapes.

Why a separate adapter:

- Claude's tool-use protocol and response shape differ from OpenAI's, even
  though the product contract (the JSON returned to the caller) is the same.
  Wire-shape translation belongs in the adapter, not the caller.
- Tool definitions take the same JSON Schema but in a flatter envelope:
  ``{name, description, input_schema}`` — no ``function`` wrapper, no
  ``strict`` flag (Claude enforces schemas by default when tool_choice is
  forced).
- System prompts pass via the top-level ``system=`` parameter, not the
  messages list.
- Responses arrive as a list of content blocks; the tool invocation shows
  up as a block with ``type == "tool_use"`` and ``.input`` carrying the
  already-parsed dict (no JSON-string round-trip).

Configuration:

- ``ANTHROPIC_API_KEY`` env var — required. A missing key raises at
  construction so the caller can cleanly fall back to OpenAI with a
  logged warning instead of producing a confusing 401 later.
- ``ANTHROPIC_MODEL`` env var — optional, defaults to
  ``claude-sonnet-4-5-20250929`` (latest Sonnet). Accepts the per-turn
  ``model_override`` parameter on each call so tier routing can stay in
  ``tiers.py`` without mutating the adapter config.

Reuse:

- The post-call sanitize functions (``_sanitize_kickoff_response``,
  ``_sanitize_topic_turn``) are imported from ``openai_adapter`` so
  provider-agnostic post-processing stays in one place. That's the
  "safe default" safety net: a structurally invalid response (unknown
  action, missing required question, bad sibling title reference) gets
  either repaired silently or surfaced as ``RuntimeError`` exactly as
  on the OpenAI path, so the API layer's error handling is uniform.

Scope:

- No circuit breaker here. The OpenAI path already fails fast via its
  breaker; if Claude ALSO fails, we want that exception to propagate
  unchanged so operators can diagnose a dual-provider outage. Wrapping
  the Claude path in its own breaker would hide that signal.
- ``kickoff`` still works but the frontier tier currently routes kickoff
  through OpenAI (TODO: wire Claude kickoff once the live payloads are
  validated in production against the current sanitizer bounds).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .base import PlanningInterviewer
from .openai_adapter import (
    _format_kickoff_user_message,
    _format_topic_turn_user_message,
    _sanitize_kickoff_response,
    _sanitize_topic_turn,
)
from .prompts import (
    BASE_SYSTEM_PROMPT,
    KICKOFF_MODE_PROMPT,
    TOPIC_INTERVIEW_MODE_PROMPT,
    locale_hint,
)
from .schemas import TOOL_SPECS


# Latest publicly available Sonnet at the time the frontier tier went live.
# Bump the string when a newer Sonnet ships; no other adapter changes needed
# as long as the tool-use envelope stays stable.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5-20250929"


@dataclass(slots=True)
class ClaudeConfig:
    """Tunable config for the Claude adapter. Safe defaults.

    Defaults are chosen to mirror the OpenAI adapter where the parameter
    has a direct analog: same completion budget, same 30-second timeout,
    same post-call sanity-check bounds for kickoff topic counts.
    """

    # Read from env at construction if the caller doesn't override.
    model: str | None = None
    timeout_s: float = 30.0
    # Anthropic requires max_tokens on every call. 16384 leaves generous
    # headroom for a full kickoff payload (up to 10 topics + relationships)
    # or a verbose topic_turn with all optional fields populated.
    max_tokens: int = 16384
    api_key: str | None = None
    base_url: str | None = None
    # Soft limits matching the kickoff spec (§3.2 Mode A) for a post-call
    # sanity check. Mirrors OpenAIConfig's kickoff_min/max_topics because
    # the sanitizer we reuse reads these off a config-shaped object.
    kickoff_min_topics: int = 5
    kickoff_max_topics: int = 10


class ClaudePlanningInterviewer(PlanningInterviewer):
    """Planner adapter backed by Anthropic's Messages API.

    Instantiated lazily by the OpenAI adapter when its circuit breaker
    opens. Direct use is fine too — the public interface is identical to
    ``OpenAIPlanningInterviewer`` so tests can swap adapters transparently.
    """

    def __init__(
        self,
        config: ClaudeConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or ClaudeConfig()

        # Resolve API key: explicit config > env. We fail early with a clear
        # message if neither is present. The OpenAI fallback wrapper catches
        # this and surfaces a user-facing "both providers unavailable" error.
        api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — Claude fallback unavailable"
            )

        # Resolve model: explicit config > env override > default.
        if self.config.model is None:
            self.config.model = os.environ.get(
                "ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL,
            )

        # Import lazily so unit tests that mock the client don't require
        # the SDK to be present.
        if client is None:
            try:
                from anthropic import Anthropic  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "The 'anthropic' package is not installed. "
                    "Run: pip install anthropic (or pip install -e services)"
                ) from exc
            kwargs: dict[str, Any] = {"api_key": api_key}
            if self.config.base_url is not None:
                kwargs["base_url"] = self.config.base_url
            client = Anthropic(**kwargs)
        self.client = client

    # ------------------------------------------------------------------
    # Mode A: kickoff
    # ------------------------------------------------------------------
    def kickoff(
        self,
        *,
        user_idea: str,
        attached_sources: list[dict[str, Any]] | None = None,
        locale: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
    ) -> dict[str, Any]:
        if not user_idea or not user_idea.strip():
            raise ValueError("user_idea is required and must be non-empty")

        # P1.4 — sandwich locale_hint at both ends (see openai_adapter
        # for rationale). Empty string when locale is en / unknown so
        # the assembled prompt matches the original shape.
        loc = locale_hint(locale)
        system_prompt = f"{loc}{BASE_SYSTEM_PROMPT}\n\n{KICKOFF_MODE_PROMPT}{loc}"
        user_message = _format_kickoff_user_message(
            user_idea, attached_sources or [],
        )

        parsed = self._call_forced_tool(
            system_prompt=system_prompt,
            user_message=user_message,
            tool_name="kickoff_response",
            model_override=model_override,
            api_key_override=api_key_override,
        )
        _sanitize_kickoff_response(parsed, self.config)
        return parsed

    # ------------------------------------------------------------------
    # Mode B: topic_turn
    # ------------------------------------------------------------------

    def topic_turn(
        self,
        *,
        current_topic: dict[str, Any],
        other_topics: list[dict[str, Any]],
        sources: list[dict[str, Any]] | None = None,
        locale: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        reasoning_effort: str | None = None,  # noqa: ARG002 — accepted for adapter parity, no-op on Claude
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Produce one planner turn via Claude. Matches OpenAI adapter shape.

        ``model_override`` wins over ``self.config.model`` for this call only
        — the tier dispatcher in ``tiers.py`` uses this to pin the frontier
        call to the latest Sonnet without mutating the adapter config.

        ``api_key_override`` activates BYOK: when set, the call uses a
        one-off Anthropic client bound to the user's key. The shared
        ``self.client`` (house account) is never mutated.

        ``reasoning_effort`` is OpenAI-specific (gpt-5 family); Claude has
        a separate ``thinking`` API. Accepted here for adapter-shape parity
        with ``OpenAIPlanningInterviewer.topic_turn`` (callers don't have
        to branch on adapter type) and silently ignored.

        ``timeout_s`` overrides the per-call HTTP timeout when set; falls
        through to ``self.config.timeout_s`` otherwise.
        """
        if not current_topic or not current_topic.get("title"):
            raise ValueError("current_topic is required and must include a title")

        # P1.4 — sandwich locale_hint at both ends (see openai_adapter).
        loc = locale_hint(locale)
        system_prompt = f"{loc}{BASE_SYSTEM_PROMPT}\n\n{TOPIC_INTERVIEW_MODE_PROMPT}{loc}"
        user_message = _format_topic_turn_user_message(
            current_topic, other_topics, sources or [],
        )

        parsed = self._call_forced_tool(
            system_prompt=system_prompt,
            user_message=user_message,
            tool_name="topic_turn",
            model_override=model_override,
            api_key_override=api_key_override,
            timeout_s=timeout_s,
        )
        # Reuses the OpenAI adapter's sanitizer — our safety net. Structural
        # bugs (unknown action, missing required question) raise; minor
        # integrity issues (orphan consistency flags, dangling
        # target_topic_title) get repaired silently.
        _sanitize_topic_turn(parsed, other_topics)
        return parsed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _call_forced_tool(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tool_name: str,
        model_override: str | None = None,
        api_key_override: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Call messages.create with a forced tool and extract the parsed args.

        Claude's tool_choice={"type":"tool","name":...} guarantees the model
        will emit a tool_use block for the named tool. We find that block in
        the response content list and return its already-parsed ``.input``.

        When ``api_key_override`` is set, a per-call Anthropic client is
        built bound to the user's key (BYOK). Otherwise ``self.client``
        (house account) services the call.
        """
        tool_spec = _build_claude_tool_spec(tool_name)

        client = self.client
        if api_key_override:
            client = _build_byok_anthropic_client(self.config, api_key_override)

        response = client.messages.create(
            model=model_override or self.config.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[tool_spec],
            tool_choice={"type": "tool", "name": tool_name},
            max_tokens=self.config.max_tokens,
            timeout=timeout_s if timeout_s is not None else self.config.timeout_s,
        )

        return _extract_tool_use_args(response, expected_name=tool_name)


# ---------------------------------------------------------------------------
# Helpers (module-level for testability).
# ---------------------------------------------------------------------------


def _build_byok_anthropic_client(config: ClaudeConfig, api_key: str) -> Any:
    """Build a one-off Anthropic client bound to a user-supplied key.

    Same contract as the OpenAI BYOK helper — we DON'T mutate
    ``self.client`` (the house-account client) because one user's
    override would leak into every other user's turn. See
    ``openai_adapter._build_byok_openai_client``.
    """
    try:
        from anthropic import Anthropic  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'anthropic' package is not installed."
        ) from exc
    kwargs: dict[str, Any] = {"api_key": api_key}
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url
    return Anthropic(**kwargs)


def _build_claude_tool_spec(tool_name: str) -> dict[str, Any]:
    """Wrap a schema in Claude's tool-definition envelope.

    Anthropic's shape is flatter than OpenAI's — no ``function`` wrapper and
    no ``strict`` flag (Claude enforces schemas on forced tool_choice by
    default). The schema itself is the same JSON Schema as OpenAI's.
    """
    spec = TOOL_SPECS[tool_name]
    return {
        "name": tool_name,
        "description": spec["description"],
        "input_schema": spec["schema"],
    }


def _extract_tool_use_args(response: Any, *, expected_name: str) -> dict[str, Any]:
    """Pull the forced tool_use block off a Claude response.

    Claude responses carry a list of typed content blocks (text, tool_use,
    thinking, etc.). With ``tool_choice`` forcing a specific tool, exactly
    one ``tool_use`` block is expected. We scan for it and return its
    already-parsed ``input`` dict — no JSON string to decode here, unlike
    the OpenAI function-calling path.

    Raises RuntimeError (not the OpenAI-specific ``_EmptyToolCallResponse``)
    if the block is missing. We don't retry on this branch: Claude's forced
    tool_choice is reliable, and a missing tool_use signals a hard problem
    (API shape drift, content-filter intercept, or model refusal) that
    callers should see, not paper over with a retry loop.
    """
    try:
        content_blocks = response.content or []
    except AttributeError as exc:
        raise RuntimeError(
            f"Malformed Claude response — no .content attribute: {response!r}"
        ) from exc

    for block in content_blocks:
        block_type = getattr(block, "type", None)
        if block_type != "tool_use":
            continue
        block_name = getattr(block, "name", None)
        if block_name != expected_name:
            # Forced tool_choice pinned the name, so this would be a drift
            # in the SDK/model — surface it so the bug is visible.
            raise RuntimeError(
                f"Expected tool '{expected_name}', Claude returned "
                f"tool_use for '{block_name}' instead."
            )
        block_input = getattr(block, "input", None)
        if not isinstance(block_input, dict):
            raise RuntimeError(
                f"Tool '{expected_name}' tool_use block has non-dict input: "
                f"{block_input!r}"
            )
        return block_input

    # No tool_use block at all — dump stop_reason / usage for diagnosis.
    stop_reason = getattr(response, "stop_reason", None)
    usage = getattr(response, "usage", None)
    raise RuntimeError(
        f"Expected a tool_use block for '{expected_name}' but got none. "
        f"stop_reason={stop_reason!r}, usage={usage!r}, "
        f"content_blocks={[getattr(b, 'type', '?') for b in content_blocks]!r}."
    )


# Convenience factory for common case: read key from env.
def from_env() -> ClaudePlanningInterviewer:
    """Build a Claude adapter using ``ANTHROPIC_API_KEY`` from the environment."""
    return ClaudePlanningInterviewer()
