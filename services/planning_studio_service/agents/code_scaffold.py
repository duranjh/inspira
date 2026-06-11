"""Code-scaffold adapter — artifact writer that produces a runnable
first-draft repository.

Distinct from the prose-artifact adapters (``plan_summary``,
``outline``, ``deduper``) by output shape: this one returns a file
manifest we turn into a zip, not a single markdown document. It shares
the same OpenAI plumbing (circuit breaker + transient-retry) via
``_call_with_toolcall_retry``.

Privacy contract: the adapter receives the project's plan summary,
topic titles, and confirmed decisions. It does NOT receive Q&A turn
bodies or attached-source excerpts — the scaffold synthesizes from
committed-to thinking, not raw inputs.

Sanitization is mandatory here. The LLM occasionally emits:

- Files whose path escapes the project root (leading ``/``, ``..``,
  drive letters).
- Duplicate paths.
- Files with no trailing newline (churns diffs).
- An empty ``files`` array on a bad generation.

The sanitize pass fixes all of that before we persist the manifest or
zip it. Errors surface as ``RuntimeError`` so the API layer can charge
the cost-free "planner_call_failed" path.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .openai_adapter import _call_with_toolcall_retry
from .plan_summary import _build_extra_tool_spec as _build_extra_tool_spec_plan
from .prompts import locale_hint
from .prompts_scaffold import (
    SCAFFOLD_EDIT_PROMPT,
    SCAFFOLD_PROMPT,
    redraft_context_section,
    repo_context_section,
)
from .schemas_scaffold import (
    MAX_EDIT_EXPLANATION_CHARS,
    MAX_FILE_CONTENT_CHARS,
    MAX_FILES_PER_SCAFFOLD,
    SCAFFOLD_TOOL_SPECS,
)


@dataclass(slots=True)
class CodeScaffoldConfig:
    """Tunable config for the scaffold adapter. Safe defaults.

    The model and token ceiling are chosen to be generous: the scaffold
    payload is much larger than a summary (40 files × several hundred
    chars each), and prose quality doesn't matter here — correctness
    and completeness do.
    """

    model: str = "gpt-5-mini"
    # Scaffold generation is the heaviest single call the product
    # makes. 120s is aggressive but we'd rather wait than return a
    # partial file list on a timeout.
    timeout_s: float = 120.0
    max_empty_toolcall_retries: int = 1
    temperature: float | None = None
    # Generous token ceiling — up to 40 files means the serialized
    # tool-call payload can reach the 100k-token range. Billed on
    # actual usage so the ceiling only matters as a cap.
    max_completion_tokens: int = 64_000
    # Medium reasoning — scaffolds need real thought about file
    # layout and import graph, so "low" under-performs here. We pay
    # the cost knowingly; this is a paid-tier feature.
    reasoning_effort: str | None = "medium"
    api_key: str | None = None
    base_url: str | None = None


class CodeScaffoldAdapter:
    """OpenAI-backed adapter for the code-scaffold artifact mode."""

    def __init__(
        self,
        config: CodeScaffoldConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or CodeScaffoldConfig()
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
        summary_markdown: str,
        topics: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        locale: str | None = None,
        model_override: str | None = None,
        timeout_s: float | None = None,
        repo_context: dict[str, Any] | None = None,
        previous_scaffold: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Produce the scaffold manifest.

        Args:
            project_title: project's display title.
            summary_markdown: plan summary prose, shown to the model as
                primary context for what to build.
            topics: list of {topic_id, title, icon, ...}.
            decisions: list of {decision_id, topic_id, statement,
                rationale, status}.
            model_override: pin a specific model for this call (e.g.
                ``gpt-5`` for tier-dispatched FRONTIER/ENTERPRISE on the
                OpenAI fallback path). Defaults to the config's model.
            timeout_s: per-call timeout override.
            repo_context: optional repo metadata fetched via
                ``connectors.github.repo_context.fetch_repo_context``.
                When present, an additional grounding section is
                appended to the system prompt so the model paths +
                imports off real files; when None (no GitHub repo
                connected), the prompt shape is unchanged.
            previous_scaffold: optional ``{path: content}`` dict of the
                project's current scaffold. Wave F.6's "Refresh PR
                with Inspira" passes this so the LLM redraws on top of
                the previous draft (which may include partner edits)
                rather than from scratch. Empty/None preserves the
                legacy first-generation prompt shape.

        Returns the parsed + sanitized tool_call dict matching
        ``SCAFFOLD_SCHEMA``. Raises ``RuntimeError`` on generation
        failure or empty manifest.
        """
        if not project_title or not project_title.strip():
            raise ValueError("project_title is required")

        user_message = _format_scaffold_user_message(
            project_title=project_title,
            summary_markdown=summary_markdown or "",
            topics=topics or [],
            decisions=decisions or [],
        )

        tool_spec = _build_extra_tool_spec_plan  # fallback if not found
        # We have our own registry; use it directly to avoid leaking
        # scaffold-tool metadata into the plan-summary registry.
        spec = SCAFFOLD_TOOL_SPECS["generate_scaffold_manifest"]
        tool_spec = {
            "type": "function",
            "function": {
                "name": "generate_scaffold_manifest",
                "description": spec["description"],
                "parameters": spec["schema"],
                "strict": True,
            },
        }

        scaffold_system_prompt = (
            SCAFFOLD_PROMPT
            + locale_hint(locale)
            + repo_context_section(repo_context)
            + redraft_context_section(previous_scaffold)
        )
        create_kwargs: dict[str, Any] = {
            "model": model_override or self.config.model,
            "messages": [
                {"role": "system", "content": scaffold_system_prompt},
                {"role": "user", "content": user_message},
            ],
            "tools": [tool_spec],
            "tool_choice": {
                "type": "function",
                "function": {"name": "generate_scaffold_manifest"},
            },
            "max_completion_tokens": self.config.max_completion_tokens,
            "timeout": timeout_s if timeout_s is not None else self.config.timeout_s,
        }
        if self.config.temperature is not None:
            create_kwargs["temperature"] = self.config.temperature
        if self.config.reasoning_effort is not None:
            create_kwargs["reasoning_effort"] = self.config.reasoning_effort

        parsed = _call_with_toolcall_retry(
            self.client,
            create_kwargs,
            expected_name="generate_scaffold_manifest",
            max_retries=self.config.max_empty_toolcall_retries,
            breaker_key="scaffold",
        )

        sanitize_scaffold_manifest(parsed)
        return parsed

    def edit(
        self,
        *,
        project_title: str,
        current_files: list[dict[str, Any]],
        user_message: str,
        locale: str | None = None,
        model_override: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Apply a chat-driven edit to an existing scaffold.

        Args:
            project_title: project's display title.
            current_files: the current scaffold's file list as
                ``[{path, content}, ...]``. The model receives this
                inline as context; it re-emits the FULL updated manifest
                rather than diffing.
            user_message: the user's chat-sidebar message describing the
                requested change.
            locale: optional locale hint appended to the system prompt.
            model_override: pin a specific model for this call (e.g. for
                tier-dispatched FRONTIER → gpt-5). Defaults to the
                config's model.
            timeout_s: per-call timeout override.

        Returns the parsed + sanitized tool_call dict matching
        ``SCAFFOLD_EDIT_SCHEMA`` (includes the ``explanation`` field).
        Raises ``RuntimeError`` on generation failure or empty manifest,
        ``ValueError`` on missing inputs.
        """
        if not project_title or not project_title.strip():
            raise ValueError("project_title is required")
        if not user_message or not user_message.strip():
            raise ValueError("user_message is required")

        prepared_user_message = _format_scaffold_edit_user_message(
            project_title=project_title,
            current_files=current_files or [],
            user_message=user_message,
        )

        spec = SCAFFOLD_TOOL_SPECS["edit_scaffold_manifest"]
        tool_spec = {
            "type": "function",
            "function": {
                "name": "edit_scaffold_manifest",
                "description": spec["description"],
                "parameters": spec["schema"],
                "strict": True,
            },
        }

        edit_system_prompt = SCAFFOLD_EDIT_PROMPT + locale_hint(locale)
        create_kwargs: dict[str, Any] = {
            "model": model_override or self.config.model,
            "messages": [
                {"role": "system", "content": edit_system_prompt},
                {"role": "user", "content": prepared_user_message},
            ],
            "tools": [tool_spec],
            "tool_choice": {
                "type": "function",
                "function": {"name": "edit_scaffold_manifest"},
            },
            "max_completion_tokens": self.config.max_completion_tokens,
            "timeout": timeout_s if timeout_s is not None else self.config.timeout_s,
        }
        if self.config.temperature is not None:
            create_kwargs["temperature"] = self.config.temperature
        if self.config.reasoning_effort is not None:
            create_kwargs["reasoning_effort"] = self.config.reasoning_effort

        parsed = _call_with_toolcall_retry(
            self.client,
            create_kwargs,
            expected_name="edit_scaffold_manifest",
            max_retries=self.config.max_empty_toolcall_retries,
            breaker_key="scaffold_edit",
        )

        sanitize_scaffold_manifest(parsed)
        return parsed


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


# Path-safety regex: project-relative POSIX paths only. We allow letters,
# digits, hyphens, underscores, dots, and slashes. Everything else — drive
# letters, backslashes, shell metachars — is rejected at the per-segment
# check below. Keeping this conservative is the whole point.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_safe_path(path: str) -> bool:
    """Return True iff ``path`` is a safe project-relative POSIX path.

    Rejects:
    - Empty strings.
    - Leading slashes (absolute paths).
    - Backslashes (Windows paths; we demand POSIX separators).
    - Drive letters (``C:``, etc.).
    - Any segment that is ``..``, ``.`` alone, empty, or fails the
      per-segment regex.

    Does NOT attempt to resolve symlinks or check filesystem semantics —
    we're never touching the filesystem with these paths, only zipping
    them into a file we hand back to the user.
    """
    if not path or not isinstance(path, str):
        return False
    if path.startswith("/"):
        return False
    if "\\" in path:
        return False
    if re.match(r"^[A-Za-z]:", path):
        return False
    segments = path.split("/")
    for segment in segments:
        if not segment:
            return False  # empty segment means // or trailing slash
        if segment in ("..", "."):
            return False
        if not _SAFE_PATH_SEGMENT.match(segment):
            return False
    return True


def sanitize_scaffold_manifest(parsed: dict[str, Any]) -> None:
    """Normalize the scaffold manifest in-place.

    Rules applied:

    1. Drop files whose ``path`` is unsafe (see ``_is_safe_path``).
    2. Deduplicate by path — first occurrence wins so the model can't
       accidentally overwrite its own file.
    3. Truncate content that exceeds ``MAX_FILE_CONTENT_CHARS`` so a
       hallucinated 100k-char README doesn't balloon the zip.
    4. Cap the total file count at ``MAX_FILES_PER_SCAFFOLD`` — the
       schema already caps this on the model side but the guard is
       defense-in-depth for a stale schema deploy.
    5. Ensure each file ends with exactly one trailing newline.

    Raises ``RuntimeError`` when the post-sanitize file list is empty —
    that's a generation failure, not a user-presentable result.

    Also annotates ``parsed["_sanitize"]`` with counts so the UI and
    tests can surface how many entries were dropped.
    """
    files = parsed.get("files")
    if not isinstance(files, list):
        raise RuntimeError(
            "scaffold generation returned no files (missing 'files' key)",
        )

    dropped_unsafe_paths: list[str] = []
    dropped_duplicate_paths: list[str] = []
    truncated_paths: list[str] = []

    seen_paths: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path", "")
        content = entry.get("content", "")

        if not _is_safe_path(path):
            dropped_unsafe_paths.append(str(path))
            continue
        if path in seen_paths:
            dropped_duplicate_paths.append(path)
            continue
        seen_paths.add(path)

        if not isinstance(content, str):
            content = str(content)
        if len(content) > MAX_FILE_CONTENT_CHARS:
            content = content[:MAX_FILE_CONTENT_CHARS]
            truncated_paths.append(path)
        # One trailing newline — not zero, not many. Preserves diffs and
        # keeps POSIX-tool compat without producing gratuitous blank
        # lines at the end of files.
        content = content.rstrip("\n") + "\n"

        cleaned.append({"path": path, "content": content})

    # Cap the total count after de-dup; if the model packed in 80
    # entries we take the first 40 (the most important ones by the
    # model's emission order, which is usually top-down).
    capped_overflow: list[str] = []
    if len(cleaned) > MAX_FILES_PER_SCAFFOLD:
        capped_overflow = [entry["path"] for entry in cleaned[MAX_FILES_PER_SCAFFOLD:]]
        cleaned = cleaned[:MAX_FILES_PER_SCAFFOLD]

    if not cleaned:
        raise RuntimeError("scaffold generation returned no files")

    parsed["files"] = cleaned
    # Normalize the ancillary fields the schema requires.
    if not isinstance(parsed.get("framework"), str):
        parsed["framework"] = "other"
    if not isinstance(parsed.get("language"), str):
        parsed["language"] = "typescript"
    if not isinstance(parsed.get("readme_preview"), str):
        parsed["readme_preview"] = ""
    if not isinstance(parsed.get("post_install_steps"), list):
        parsed["post_install_steps"] = []
    else:
        parsed["post_install_steps"] = [
            str(s) for s in parsed["post_install_steps"] if isinstance(s, str)
        ]
    if not isinstance(parsed.get("truncation_note"), str):
        parsed["truncation_note"] = ""
    # Edit-mode shape carries an ``explanation`` paragraph for the chat
    # sidebar. Preserve it when present (clamped to the schema cap);
    # generate-mode responses leave it absent.
    if "explanation" in parsed:
        explanation = parsed.get("explanation")
        if not isinstance(explanation, str):
            explanation = ""
        if len(explanation) > MAX_EDIT_EXPLANATION_CHARS:
            explanation = explanation[:MAX_EDIT_EXPLANATION_CHARS]
        parsed["explanation"] = explanation

    # If we had to truncate or cap, merge that into truncation_note so
    # the UI surfaces the reality instead of the model's optimistic
    # "complete".
    sanitize_notes: list[str] = []
    if truncated_paths:
        sanitize_notes.append(
            f"Truncated {len(truncated_paths)} file(s) to the "
            f"{MAX_FILE_CONTENT_CHARS}-char cap.",
        )
    if capped_overflow:
        sanitize_notes.append(
            f"Kept the first {MAX_FILES_PER_SCAFFOLD} files; dropped "
            f"{len(capped_overflow)} additional entries.",
        )
    if sanitize_notes:
        existing = (parsed.get("truncation_note") or "").strip()
        combined = " ".join(sanitize_notes)
        parsed["truncation_note"] = (
            f"{existing} {combined}".strip() if existing else combined
        )

    parsed["_sanitize"] = {
        "dropped_unsafe_paths": dropped_unsafe_paths,
        "dropped_duplicate_paths": dropped_duplicate_paths,
        "truncated_paths": truncated_paths,
        "capped_overflow": capped_overflow,
    }


# ---------------------------------------------------------------------------
# User-message formatter
# ---------------------------------------------------------------------------


def _format_scaffold_user_message(
    *,
    project_title: str,
    summary_markdown: str,
    topics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> str:
    """Render the user-facing message body for the scaffold call.

    Layout is optimized for model comprehension: summary first (the
    richest signal), then topics, then decisions grouped under each
    topic. Mirrors the ``plan_summary`` formatter's structure so the
    model sees a familiar layout.
    """
    decisions_by_topic: dict[str, list[dict[str, Any]]] = {}
    for d in decisions:
        tid = d.get("topic_id") or ""
        decisions_by_topic.setdefault(tid, []).append(d)

    lines: list[str] = []
    lines.append(f"PROJECT: {project_title}")
    lines.append("")

    summary_text = (summary_markdown or "").strip()
    if summary_text:
        lines.append("PLAN SUMMARY:")
        lines.append(summary_text)
        lines.append("")
    else:
        lines.append(
            "(No plan summary on file — infer the shape from the topics "
            "and decisions below.)",
        )
        lines.append("")

    if topics:
        lines.append(f"TOPICS ({len(topics)}):")
        for topic in topics:
            tid = topic.get("topic_id", "")
            title = (topic.get("title") or "(untitled)").strip() or "(untitled)"
            lines.append(f"- {title}")
            tdecs = decisions_by_topic.get(tid, [])
            for d in tdecs:
                if d.get("status") == "retracted":
                    continue
                stmt = (d.get("statement") or "").strip()
                if not stmt:
                    continue
                rationale = (d.get("rationale") or "").strip()
                entry = f"    - {stmt}"
                if rationale:
                    entry += f" (because: {rationale})"
                lines.append(entry)
        lines.append("")
    else:
        lines.append("(No topics on this project yet.)")
        lines.append("")

    lines.append(
        "Design and emit a runnable first-draft repository per the "
        "SCAFFOLD_PROMPT. Return a single generate_scaffold_manifest "
        "tool call.",
    )
    return "\n".join(lines)


def _format_scaffold_edit_user_message(
    *,
    project_title: str,
    current_files: list[dict[str, Any]],
    user_message: str,
) -> str:
    """Render the user-facing message body for the edit call.

    Layout: project line, then the current scaffold inline as a
    ``CURRENT SCAFFOLD:`` block (each file with its full content), then
    the user's chat message as ``USER REQUEST:``. The model has all the
    context it needs to apply the requested change without re-fetching.

    The current_files list is trusted to be already-sanitized (we wrote
    them on the previous call); we don't re-validate paths here, but we
    do clip per-file content to ``MAX_FILE_CONTENT_CHARS`` defensively
    so a buggy caller can't blow past the model's input window.
    """
    lines: list[str] = []
    lines.append(f"PROJECT: {project_title}")
    lines.append("")
    lines.append("CURRENT SCAFFOLD:")
    if current_files:
        for entry in current_files:
            path = (entry.get("path") or "").strip()
            content = entry.get("content") or ""
            if not path:
                continue
            if not isinstance(content, str):
                content = str(content)
            if len(content) > MAX_FILE_CONTENT_CHARS:
                content = content[:MAX_FILE_CONTENT_CHARS]
            lines.append(f"--- {path} ---")
            lines.append(content.rstrip("\n"))
        lines.append("--- end of scaffold ---")
    else:
        lines.append("(empty — no scaffold has been generated yet)")
    lines.append("")
    lines.append("USER REQUEST:")
    lines.append(user_message.strip())
    lines.append("")
    lines.append(
        "Apply the request to the current scaffold and return one "
        "edit_scaffold_manifest tool call with the FULL updated manifest "
        "and a one-paragraph explanation.",
    )
    return "\n".join(lines)


def from_env() -> CodeScaffoldAdapter:
    """Build an adapter using ``OPENAI_API_KEY`` from the environment."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put your OpenAI key in the env "
            "before calling from_env(): export OPENAI_API_KEY=sk-... "
            "(or $env:OPENAI_API_KEY='sk-...' on Windows)",
        )
    return CodeScaffoldAdapter()


# ---------------------------------------------------------------------------
# Claude-backed sibling — activated for FRONTIER/ENTERPRISE code-gen via the
# artifact endpoint's tier dispatch. Uses the same SCAFFOLD_SCHEMA +
# SCAFFOLD_PROMPT + sanitize_scaffold_manifest as the OpenAI adapter; only
# the wire envelope changes (Anthropic's flat tool spec, system kwarg,
# tool_use response block).
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClaudeCodeScaffoldConfig:
    """Tunable config for the Claude code-scaffold adapter.

    Same defaults as ``CodeScaffoldConfig`` where the parameter has a
    direct analog. Differences:
    - ``model`` resolves at construction from
      ``ANTHROPIC_MODEL`` env or ``DEFAULT_CLAUDE_MODEL`` — set explicitly
      via ``model_override`` per-call to pin Opus 4.7 for the artifact
      endpoint.
    - ``max_tokens`` is required by Anthropic on every call (16384 leaves
      headroom for a 40-file scaffold).
    - No ``reasoning_effort`` (OpenAI-specific; Claude has a separate
      ``thinking`` API not used here).
    """

    model: str | None = None
    timeout_s: float = 120.0
    max_tokens: int = 16384
    api_key: str | None = None
    base_url: str | None = None


class ClaudeCodeScaffoldAdapter:
    """Anthropic-backed adapter for the code-scaffold artifact mode.

    Mirrors :class:`CodeScaffoldAdapter`'s public API
    (``generate(...) -> dict``, ``edit(...) -> dict``) so the artifact
    endpoint can swap providers via ``tier_to_adapter`` without
    branching downstream. Sanitizer, schema, and prompts are shared
    across providers — only the wire envelope changes here.
    """

    def __init__(
        self,
        config: ClaudeCodeScaffoldConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or ClaudeCodeScaffoldConfig()

        api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — Claude code-scaffold unavailable",
            )

        if self.config.model is None:
            # Lazy import — DEFAULT_CLAUDE_MODEL is in claude_adapter
            # module scope; importing it here avoids a circular import
            # if the OpenAI side is loaded standalone.
            from .claude_adapter import DEFAULT_CLAUDE_MODEL
            self.config.model = os.environ.get(
                "ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL,
            )

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

    def generate(
        self,
        *,
        project_title: str,
        summary_markdown: str,
        topics: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        locale: str | None = None,
        model_override: str | None = None,
        timeout_s: float | None = None,
        repo_context: dict[str, Any] | None = None,
        previous_scaffold: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Produce the scaffold manifest via Claude. Same shape as OpenAI sibling.

        ``previous_scaffold`` is the Wave F.6 redraft-reference kwarg —
        when provided, an additional prompt section names every file
        in the project's current scaffold so Claude can preserve
        partner intent while redrawing against fresh main.
        """
        if not project_title or not project_title.strip():
            raise ValueError("project_title is required")

        user_message = _format_scaffold_user_message(
            project_title=project_title,
            summary_markdown=summary_markdown or "",
            topics=topics or [],
            decisions=decisions or [],
        )

        parsed = self._call_forced_tool(
            tool_name="generate_scaffold_manifest",
            system_prompt=(
                SCAFFOLD_PROMPT
                + locale_hint(locale)
                + repo_context_section(repo_context)
                + redraft_context_section(previous_scaffold)
            ),
            user_message=user_message,
            model_override=model_override,
            timeout_s=timeout_s,
        )
        sanitize_scaffold_manifest(parsed)
        return parsed

    def edit(
        self,
        *,
        project_title: str,
        current_files: list[dict[str, Any]],
        user_message: str,
        locale: str | None = None,
        model_override: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Apply a chat-driven edit via Claude. Same shape as OpenAI sibling."""
        if not project_title or not project_title.strip():
            raise ValueError("project_title is required")
        if not user_message or not user_message.strip():
            raise ValueError("user_message is required")

        prepared_user_message = _format_scaffold_edit_user_message(
            project_title=project_title,
            current_files=current_files or [],
            user_message=user_message,
        )

        parsed = self._call_forced_tool(
            tool_name="edit_scaffold_manifest",
            system_prompt=SCAFFOLD_EDIT_PROMPT + locale_hint(locale),
            user_message=prepared_user_message,
            model_override=model_override,
            timeout_s=timeout_s,
        )
        sanitize_scaffold_manifest(parsed)
        return parsed

    def _call_forced_tool(
        self,
        *,
        tool_name: str,
        system_prompt: str,
        user_message: str,
        model_override: str | None,
        timeout_s: float | None,
    ) -> dict[str, Any]:
        """Call ``messages.create`` with a forced scaffold tool + extract args.

        Mirrors :meth:`ClaudePlanningInterviewer._call_forced_tool` but
        sources the tool spec from ``SCAFFOLD_TOOL_SPECS`` (kept separate
        from the interviewer's ``TOOL_SPECS`` registry — see the comment
        in ``schemas_scaffold.py``).
        """
        # Local import to avoid a top-level dependency on claude_adapter.py
        # for environments that only need the OpenAI side.
        from .claude_adapter import _extract_tool_use_args

        spec = SCAFFOLD_TOOL_SPECS[tool_name]
        tool_spec = {
            "name": tool_name,
            "description": spec["description"],
            "input_schema": spec["schema"],
        }

        response = self.client.messages.create(
            model=model_override or self.config.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[tool_spec],
            tool_choice={"type": "tool", "name": tool_name},
            max_tokens=self.config.max_tokens,
            timeout=timeout_s if timeout_s is not None else self.config.timeout_s,
        )
        return _extract_tool_use_args(response, expected_name=tool_name)


def from_env_claude() -> ClaudeCodeScaffoldAdapter:
    """Build a Claude scaffold adapter using ``ANTHROPIC_API_KEY`` from env."""
    return ClaudeCodeScaffoldAdapter()
