"""JSON schema for the code-scaffold tool call.

Kept separate from ``schemas.py`` / ``schemas_extra.py`` for the same
merge-surface reason as the prompts: the scaffold mode ships later and
lives in its own orbit. The schema follows the same strict-mode
conventions used elsewhere in the package:

- ``additionalProperties: False`` at every object level.
- Every property in ``required`` is also in ``properties``.
- No ``nullable`` — optional strings use ``["string", "null"]``.
- Enums are explicit; the model does not freelance on framework or
  language labels.

Sizing
------
The caps in this schema are a deliberate budget on single-shot
generation. We would rather return a partial scaffold than a very long
response the model rushes through:

- ``files`` is capped at 40 entries. A typical runnable starter (README,
  package metadata, entrypoint, one component, one test, config, a
  routes file, ``.gitignore``) lands at 8-14 files; 40 is generous for a
  slightly fuller starter.
- Each file's ``content`` is capped at 40,000 chars (~10k tokens). Real
  entrypoints and READMEs land at 1-3k chars; the cap only bites on
  generated READMEs or docs that pad unnecessarily.
- When the model would have emitted more than fits the cap, it should
  trim low-priority files and populate ``truncation_note`` with a short
  human-readable explanation so the UI can surface "this is a partial
  scaffold".
"""

from __future__ import annotations


# Hard caps referenced by the sanitize pass in ``code_scaffold.py``.
# Exported so tests can assert we honor them. Don't raise these casually
# — the model's single-shot quality drops sharply above ~40k-char files.
MAX_FILES_PER_SCAFFOLD = 40
MAX_FILE_CONTENT_CHARS = 40_000


# Cap on the chat-reply paragraph carried by ``edit_scaffold_manifest``.
# 2000 chars ≈ 500 tokens — enough for a thoughtful explanation of the
# change, short enough to never dominate the chat sidebar.
MAX_EDIT_EXPLANATION_CHARS = 2_000


# ---------------------------------------------------------------------------
# generate_scaffold_manifest — the tool call returned by the scaffold adapter.
# ---------------------------------------------------------------------------
SCAFFOLD_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "framework",
        "language",
        "files",
        "readme_preview",
        "post_install_steps",
        "truncation_note",
    ],
    "properties": {
        "framework": {
            "type": "string",
            "enum": [
                "react-vite",
                "next",
                "fastapi",
                "express",
                "flask",
                "other",
            ],
            "description": (
                "Closest matching framework for the generated starter. "
                "Use 'other' when nothing in the list fits — the UI will "
                "label it by language instead."
            ),
        },
        "language": {
            "type": "string",
            "enum": [
                "typescript",
                "javascript",
                "python",
                "rust",
                "go",
            ],
            "description": (
                "Primary language of the scaffold. A mixed-language project "
                "picks whichever language the entrypoint is in."
            ),
        },
        "files": {
            "type": "array",
            "maxItems": MAX_FILES_PER_SCAFFOLD,
            "description": (
                "All files to include in the zip. Cap of "
                f"{MAX_FILES_PER_SCAFFOLD} entries; if the intended "
                "scaffold is larger, trim low-priority files and populate "
                "truncation_note."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "content"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Project-relative POSIX path. No leading slash, "
                            "no '..' segments, no drive letters. Examples: "
                            "'README.md', 'src/App.tsx', 'tests/test_api.py'."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Exact file contents. UTF-8 text only — binary "
                            "assets are out of scope for scaffolding."
                        ),
                    },
                },
            },
        },
        "readme_preview": {
            "type": "string",
            "description": (
                "First ~500 chars of the README, surfaced by the UI as a "
                "preview before download. May be shorter than 500 chars; "
                "an empty string is allowed if no README was generated."
            ),
        },
        "post_install_steps": {
            "type": "array",
            "maxItems": 8,
            "description": (
                "Ordered shell commands to run after unzipping. Prefer "
                "concrete commands ('pnpm install', 'pnpm dev') over "
                "prose. Empty list is allowed when no setup is needed."
            ),
            "items": {"type": "string"},
        },
        "truncation_note": {
            "type": "string",
            "description": (
                "Short human-readable note when the scaffold was trimmed "
                "to fit the file/char caps. Empty string when the "
                "scaffold is complete as-emitted."
            ),
        },
    },
}


# ---------------------------------------------------------------------------
# edit_scaffold_manifest — chat-driven edit on an existing scaffold.
# ---------------------------------------------------------------------------
# Same shape as ``SCAFFOLD_SCHEMA`` plus a required ``explanation`` field
# carrying the assistant's one-paragraph reply for the chat sidebar.
# The model still emits the FULL set of files (modified + unchanged) —
# we apply edits by replacing the manifest, not diffing — so we don't
# need a separate "changed paths" array; it would just complicate the
# tool call without changing what the API persists.
SCAFFOLD_EDIT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "framework",
        "language",
        "files",
        "readme_preview",
        "post_install_steps",
        "truncation_note",
        "explanation",
    ],
    "properties": {
        **SCAFFOLD_SCHEMA["properties"],
        "explanation": {
            "type": "string",
            "description": (
                "One-paragraph reply for the chat sidebar describing what "
                "the edit changed and any relevant trade-offs. Plain text "
                "with optional inline `code` spans (no block code)."
            ),
        },
    },
}


# Registry shape mirrors ``schemas_extra.EXTRA_TOOL_SPECS`` so the
# adapter can reuse the same OpenAI function-tool envelope builder.
SCAFFOLD_TOOL_SPECS: dict[str, dict] = {
    "generate_scaffold_manifest": {
        "schema": SCAFFOLD_SCHEMA,
        "description": (
            "Design a runnable first-draft repository for the current "
            "project. Return every file's path and content as one "
            "structured tool call."
        ),
    },
    "edit_scaffold_manifest": {
        "schema": SCAFFOLD_EDIT_SCHEMA,
        "description": (
            "Edit an existing scaffold based on the user's chat message. "
            "Re-emit the FULL set of files (modified and unchanged) plus a "
            "one-paragraph 'explanation' for the chat sidebar."
        ),
    },
}
