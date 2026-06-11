"""AI project suggestions — standalone OpenAI call.

Kept out of ``openai_adapter.py`` by design: this is a one-shot "what
would this person find interesting next" call that doesn't belong on
the ``PlanningInterviewer`` contract and shouldn't tangle with the
retry/sanitize plumbing the kickoff/topic_turn path uses.

Privacy contract: the prompt only ever sees project titles, topic
titles, and confirmed decision statements. It NEVER receives Q&A turn
bodies, attachment excerpts, rationale text, or anything else the user
might reasonably consider sensitive.

Returns a structured list suitable for direct rendering on the new-
project screen. Errors are surfaced — the caller decides whether to
degrade gracefully or propagate.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("planning_studio.agents.suggestions")


# Tuning constants. Tweak carefully — the prompt is intentionally strict
# on "adjacent but distinct" to avoid the "5 more novels" overfitting
# failure called out in the product memo.
_MODEL = os.environ.get("INSPIRA_SUGGESTIONS_MODEL", "gpt-4o-mini")
_TIMEOUT_S = 15.0
_MAX_COMPLETION_TOKENS = 4096
# gpt-4o-mini rejects reasoning_effort — set to None to omit the param.
_REASONING_EFFORT: str | None = None

# Cap the number of projects we summarise in the prompt. A returning
# power user might have 30 projects; we don't need all of them to infer
# interests, and shipping 30 serializes a lot of prompt. Most-recent
# first, bounded.
_MAX_PROJECTS_SUMMARISED = 12
_MAX_TOPICS_PER_PROJECT = 8
_MAX_DECISIONS_PER_PROJECT = 6


_SUGGESTIONS_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "project_suggestions",
        "description": (
            "Return 3 to 5 distinct new project ideas this person might "
            "enjoy starting, based on the pattern of their existing "
            "projects. Each idea must be adjacent-but-distinct — not more "
            "of the same thing. Each includes a pasteable kickoff seed."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "suggestions": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "why_this", "example_idea"],
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": (
                                    "Short 1-3 word project title — the "
                                    "kind of thing the user would type on "
                                    "the kickoff screen."
                                ),
                                "minLength": 2,
                                "maxLength": 60,
                            },
                            "why_this": {
                                "type": "string",
                                "description": (
                                    "1-2 sentences grounded in patterns "
                                    "observed across the user's existing "
                                    "projects. Reference the interest "
                                    "signal without copying specific "
                                    "project titles verbatim where "
                                    "avoidable."
                                ),
                                "minLength": 10,
                                "maxLength": 400,
                            },
                            "example_idea": {
                                "type": "string",
                                "description": (
                                    "1-2 sentence kickoff seed the user "
                                    "can paste directly into the kickoff "
                                    "textarea. Concrete, not meta."
                                ),
                                "minLength": 20,
                                "maxLength": 600,
                            },
                        },
                    },
                },
            },
            "required": ["suggestions"],
        },
    },
}


_SYSTEM_PROMPT = """\
You help Inspira users discover what to plan next. You are shown a
compact summary of the user's existing projects — titles, topic labels,
and confirmed decisions only (never Q&A text, never attachments).

From that signal, propose 3 to 5 NEW project ideas this person might
enjoy starting. Ground each suggestion in the pattern you observe
across their portfolio — not in any single existing project. Adjacent
but DISTINCT: if every existing project is a novel, do not suggest
more novels; suggest something in the same creative family (a
screenplay? a narrative podcast? a short-story cycle?) or in an
unrelated domain that the same underlying interests would support.

Never copy an existing project verbatim. Never propose a variant or
sequel of an existing project. Never include the user's own project
titles in the "title" field.

Each suggestion has:
- title: 1-3 words, the kind of thing a user would type on the kickoff
  screen
- why_this: 1-2 sentences tying the idea to the pattern you saw, in
  warm editorial voice. Avoid listing their project titles back; speak
  in terms of the underlying interest.
- example_idea: a 1-2 sentence kickoff seed — concrete and specific —
  the user can paste straight into the kickoff textarea.

Tone: quietly warm, direct, considered. No emoji. No cheerleading. No
"great idea!" energy.
"""


def build_suggestions_prompt(
    *,
    user_display_name: str,
    projects_payload: list[dict[str, Any]],
) -> str:
    """Render the user-facing message text from the trimmed portfolio.

    ``projects_payload`` is the already-privacy-filtered list produced
    by ``collect_suggestion_signals`` — titles, topic titles, decision
    statements only.
    """
    lines: list[str] = []
    greet = user_display_name.strip() or "The user"
    lines.append(
        f"{greet} has used Inspira for the following projects (most recent "
        "first). Titles, topic labels, and confirmed decisions only — no "
        "Q&A bodies or attachment text.",
    )
    lines.append("")
    for idx, project in enumerate(projects_payload, start=1):
        lines.append(f"Project {idx}: {project['title']}")
        topic_titles = project.get("topic_titles") or []
        if topic_titles:
            lines.append("  Topics:")
            for t in topic_titles:
                lines.append(f"    - {t}")
        decisions = project.get("decisions") or []
        if decisions:
            lines.append("  Confirmed decisions:")
            for d in decisions:
                lines.append(f"    - {d}")
        lines.append("")
    lines.append(
        "Suggest 3 to 5 adjacent-but-distinct project ideas. Return a "
        "single project_suggestions tool call.",
    )
    return "\n".join(lines)


def collect_suggestion_signals(store: Any, *, user_id: str) -> list[dict[str, Any]]:
    """Build the privacy-filtered portfolio summary for a user.

    Returns a list of dicts with only: project title, topic titles,
    confirmed decision statements. No turn bodies, no rationale text,
    no attachment data. Bounded by ``_MAX_PROJECTS_SUMMARISED`` etc.

    Caller (the suggest endpoint) decides what to do when the list is
    shorter than the 2-project minimum.
    """
    projects = store.list_v2_projects(user_id=user_id) or []
    # Most-recent-first. list_v2_projects already orders by updated_at DESC,
    # but re-order defensively in case the method's contract changes.
    projects = projects[:_MAX_PROJECTS_SUMMARISED]

    portfolio: list[dict[str, Any]] = []
    for project in projects:
        project_id = project["project_id"]
        topics = store.list_topics(project_id=project_id, user_id=user_id) or []
        topic_titles = [
            str(t.get("title", "")).strip()
            for t in topics
            if str(t.get("title", "")).strip()
        ][:_MAX_TOPICS_PER_PROJECT]
        all_decisions = store.list_decisions(
            project_id=project_id, user_id=user_id,
        ) or []
        confirmed_statements = [
            str(d.get("statement", "")).strip()
            for d in all_decisions
            if d.get("status") == "confirmed"
            and str(d.get("statement", "")).strip()
        ][:_MAX_DECISIONS_PER_PROJECT]
        portfolio.append({
            "title": str(project.get("title", "")).strip() or "Untitled project",
            "topic_titles": topic_titles,
            "decisions": confirmed_statements,
        })
    return portfolio


def _build_openai_client(api_key: str | None = None) -> Any:
    """Instantiate a fresh OpenAI client.

    We deliberately do NOT reuse the client from openai_adapter.py —
    that module is owned by another agent (no edits allowed). Creating
    one here is cheap; the SDK pools HTTP connections internally.
    """
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — missing dep is a deploy bug
        raise RuntimeError(
            "The 'openai' package is not installed. "
            "Run: pip install openai (or pip install -e services[dev])"
        ) from exc
    kwargs: dict[str, Any] = {}
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if key:
        kwargs["api_key"] = key
    return OpenAI(**kwargs)


def generate_project_suggestions(
    *,
    store: Any,
    user_id: str,
    user_display_name: str = "",
    client: Any = None,
) -> dict[str, Any]:
    """Call OpenAI for fresh project suggestions for this user.

    Returns a dict shaped as::

        {
            "suggestions": [
                {"title": "...", "why_this": "...", "example_idea": "..."},
                ...
            ],
            "usage": {"prompt_tokens": int, "completion_tokens": int},
        }

    The ``usage`` key lets the caller record this into user_usage so the
    daily budget keeps pace with suggestion calls too.

    Raises ``RuntimeError`` on any OpenAI failure. The caller is
    responsible for translating that into an HTTP error.
    """
    projects_payload = collect_suggestion_signals(store, user_id=user_id)

    user_message = build_suggestions_prompt(
        user_display_name=user_display_name,
        projects_payload=projects_payload,
    )

    if client is None:
        client = _build_openai_client()

    create_kwargs: dict[str, Any] = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "tools": [_SUGGESTIONS_TOOL_SPEC],
        "tool_choice": {
            "type": "function",
            "function": {"name": "project_suggestions"},
        },
        "max_completion_tokens": _MAX_COMPLETION_TOKENS,
        "timeout": _TIMEOUT_S,
    }
    if _REASONING_EFFORT:
        create_kwargs["reasoning_effort"] = _REASONING_EFFORT

    try:
        response = client.chat.completions.create(**create_kwargs)
    except Exception as exc:  # noqa: BLE001 — translate to RuntimeError
        raise RuntimeError(
            f"project suggestions call failed: {type(exc).__name__}",
        ) from exc

    suggestions = _parse_suggestions_response(response)
    usage = _extract_usage(response)

    return {"suggestions": suggestions, "usage": usage}


def _parse_suggestions_response(response: Any) -> list[dict[str, Any]]:
    try:
        choice = response.choices[0]
        message = choice.message
        tool_calls = message.tool_calls or []
    except (AttributeError, IndexError) as exc:
        raise RuntimeError(
            f"malformed OpenAI response for suggestions: {response!r}",
        ) from exc

    if not tool_calls:
        raise RuntimeError(
            "suggestions call returned no tool_call despite tool_choice being forced",
        )
    tc = tool_calls[0]
    name = tc.function.name if hasattr(tc, "function") else tc["function"]["name"]
    args_json = tc.function.arguments if hasattr(tc, "function") else tc["function"]["arguments"]
    if name != "project_suggestions":
        raise RuntimeError(
            f"expected 'project_suggestions' tool, model called {name!r}",
        )
    try:
        parsed = json.loads(args_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"project_suggestions returned non-JSON arguments: {args_json!r}",
        ) from exc
    suggestions = parsed.get("suggestions") or []
    # Trim / validate shape defensively. Strict mode should have enforced
    # this already, but a defensive scrub here keeps the endpoint robust
    # if OpenAI's strict-mode semantics ever drift.
    clean: list[dict[str, Any]] = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        why_this = str(s.get("why_this", "")).strip()
        example = str(s.get("example_idea", "")).strip()
        if not title or not why_this or not example:
            continue
        clean.append({
            "title": title,
            "why_this": why_this,
            "example_idea": example,
        })
    return clean


def _extract_usage(response: Any) -> dict[str, int]:
    """Pull prompt/completion tokens off an OpenAI response, best-effort.

    Missing usage fields default to 0 — the caller logs and moves on;
    we don't block suggestion delivery on instrumentation.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    try:
        return {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
        }
    except (TypeError, ValueError):
        return {"prompt_tokens": 0, "completion_tokens": 0}
