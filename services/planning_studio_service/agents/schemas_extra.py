"""JSON Schema dicts for the three auxiliary tool outputs.

Kept separate from ``schemas.py`` for the same merge-conflict reason as
``prompts_extra.py``. These schemas follow the same strict-mode
conventions as the core schemas:

- ``additionalProperties: False`` at every object level.
- Every property in ``required`` and in ``properties``.
- ``["string", "null"]`` for optional fields, not ``nullable: true``.
- Enums are explicit; the model does not freelance.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# plan_summary — a cohesive narrative document for the whole project.
# ---------------------------------------------------------------------------
PLAN_SUMMARY_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary_markdown",
        "suggested_title",
        "domain_framing",
    ],
    "properties": {
        "summary_markdown": {
            "type": "string",
            "description": (
                "600-1200 word cohesive narrative summary. Prose, not "
                "bullets. Leads with a one-sentence 'what this project is "
                "about', weaves major decisions naturally, closes with a "
                "paragraph of live open questions."
            ),
        },
        "suggested_title": {
            "type": "string",
            "description": (
                "A 2-6 word title for the summary document. Prefer the "
                "user's own language where possible."
            ),
        },
        "domain_framing": {
            "type": "string",
            "description": (
                "Short phrase naming the domain voice written in — e.g. "
                "'novelist's brief', 'campaign memo', 'research framing', "
                "'product one-pager'. Used by the UI to label the artifact."
            ),
        },
    },
}


# ---------------------------------------------------------------------------
# outline_response — hierarchical artifact outline.
# ---------------------------------------------------------------------------
OUTLINE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "artifact_kind",
        "suggested_title",
        "sections",
    ],
    "properties": {
        "artifact_kind": {
            "type": "string",
            "enum": [
                "chapter_outline",
                "deck_outline",
                "report_outline",
                "brief_outline",
                "course_outline",
                "screenplay_outline",
                "other",
            ],
            "description": (
                "Closest match for the outline type. Default to 'other' "
                "when nothing fits cleanly."
            ),
        },
        "suggested_title": {
            "type": "string",
            "description": "A 2-6 word title for the outline.",
        },
        "sections": {
            "type": "array",
            "description": (
                "Top-level sections (I, II, III...). Each carries its own "
                "subsections and sub-subsections."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "roman_numeral",
                    "title",
                    "note",
                    "subsections",
                ],
                "properties": {
                    "roman_numeral": {
                        "type": "string",
                        "description": "Roman numeral label (I, II, III...).",
                    },
                    "title": {
                        "type": "string",
                        "description": "Section title — noun phrase, not a command.",
                    },
                    "note": {
                        "type": "string",
                        "description": "One sentence describing what the section covers.",
                    },
                    "subsections": {
                        "type": "array",
                        "description": "Second-level (A, B, C...).",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "letter",
                                "title",
                                "note",
                                "sub_subsections",
                            ],
                            "properties": {
                                "letter": {
                                    "type": "string",
                                    "description": "Letter label (A, B, C...).",
                                },
                                "title": {"type": "string"},
                                "note": {
                                    "type": "string",
                                    "description": "One sentence describing the subsection.",
                                },
                                "sub_subsections": {
                                    "type": "array",
                                    "description": "Third-level (1, 2, 3...).",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["number", "title", "note"],
                                        "properties": {
                                            "number": {
                                                "type": "string",
                                                "description": "Number label (1, 2, 3...).",
                                            },
                                            "title": {"type": "string"},
                                            "note": {
                                                "type": "string",
                                                "description": (
                                                    "One sentence describing the "
                                                    "sub-subsection."
                                                ),
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# dedupe_response — semantic-duplicate merge proposals.
# ---------------------------------------------------------------------------
DEDUPER_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["merge_proposals"],
    "properties": {
        "merge_proposals": {
            "type": "array",
            "description": (
                "Pairs of topics that genuinely overlap. May be empty — an "
                "empty list is a valid answer meaning 'no real duplicates'."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "topic_a_id",
                    "topic_b_id",
                    "overlap_reason",
                    "suggested_merged_title",
                    "suggested_action",
                ],
                "properties": {
                    "topic_a_id": {"type": "string"},
                    "topic_b_id": {"type": "string"},
                    "overlap_reason": {
                        "type": "string",
                        "description": (
                            "1-2 sentences naming what's the same. Must "
                            "cite specific decisions or title fragments — "
                            "generic 'both about marketing' is not enough."
                        ),
                    },
                    "suggested_merged_title": {
                        "type": "string",
                        "description": "1-3 word title that cleanly combines both.",
                    },
                    "suggested_action": {
                        "type": "string",
                        "enum": ["merge", "keep_both_but_note"],
                        "description": (
                            "'merge' when the two topics collapse into a "
                            "single cleaner topic. 'keep_both_but_note' for "
                            "related-but-distinct concepts the user should "
                            "be aware of."
                        ),
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Tool registry — same shape as ``schemas.TOOL_SPECS``. Kept separate so
# the two registries don't collide. Adapters import from whichever is
# relevant to their mode.
# ---------------------------------------------------------------------------
EXTRA_TOOL_SPECS: dict[str, dict] = {
    "plan_summary": {
        "schema": PLAN_SUMMARY_SCHEMA,
        "description": (
            "Produce a 600-1200 word cohesive narrative summary of the "
            "entire project — executive-brief style, not a bullet list."
        ),
    },
    "outline_response": {
        "schema": OUTLINE_SCHEMA,
        "description": (
            "Produce a hierarchical outline for a user-chosen artifact "
            "type (chapter outline, pitch deck, research report, etc.)."
        ),
    },
    "dedupe_response": {
        "schema": DEDUPER_SCHEMA,
        "description": (
            "Identify topics that semantically overlap within a project. "
            "Returns merge-proposal pairs; may be empty."
        ),
    },
}
