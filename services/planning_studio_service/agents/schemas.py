"""JSON Schema dicts for the ``planning_interviewer`` tool outputs.

These are the exact wire contracts each mode returns. OpenAI's function
calling + strict JSON mode enforces these at decode time, so a conformant
response is guaranteed if the API call succeeds. The adapter layer does one
final ``json.loads`` + schema-minimum check just in case.

Source of truth (human-readable): ``docs/product/agent/planning-interviewer-prompt.md``
§4. Keep this file in sync.

Notes on strict mode:

- OpenAI strict mode requires every property in ``required`` and every
  property declared in ``properties``. Use ``"type": ["string", "null"]``
  (not ``"nullable": true``) for optional fields; the model then emits an
  explicit null.
- ``additionalProperties`` must be false at every object level.
- Enum constraints must be explicit; the model will not freelance.
"""

from __future__ import annotations

from .prompts import CURATED_ICONS, DOMAIN_ENUM


# ---------------------------------------------------------------------------
# kickoff_response — map a vague idea into 5–10 topic cards + relationships.
# ---------------------------------------------------------------------------
KICKOFF_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "domain",
        "domain_confidence",
        "opening_card",
        "topics",
        "relationships",
        "suggested_first_topic",
        "clarifying_question_if_too_vague",
    ],
    "properties": {
        "domain": {
            "type": "string",
            "enum": list(DOMAIN_ENUM),
            "description": "Closest domain match. If ambiguous, prefer the more generic choice.",
        },
        "domain_confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "opening_card": {
            "type": "object",
            "additionalProperties": False,
            "required": ["body"],
            "properties": {
                "body": {
                    "type": "string",
                    "description": "One-to-two sentences introducing the map and recommending the first topic. No more.",
                },
            },
        },
        "topics": {
            "type": "array",
            "minItems": 0,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                # OpenAI's strict schema requires every property to also
                # appear in `required`. The planner returns an empty
                # array for q_and_a when a topic is too vague to commit
                # to specific decisions — that's the documented escape
                # hatch in the prompt. Frontend treats empty q_and_a as
                # "use the legacy on-demand topic_turn flow."
                "required": ["title", "icon", "why_this_topic", "q_and_a"],
                "properties": {
                    "title": {"type": "string", "description": "1–3 words, serif display."},
                    "icon": {"type": "string", "enum": list(CURATED_ICONS)},
                    "why_this_topic": {
                        "type": "string",
                        "description": "One sentence.",
                    },
                    # B1 (YC v4) — pre-populated Q&A per topic. The
                    # planner generates 2-3 short interview turns up
                    # front so a reviewer clicking a topic sees the
                    # AI's best-guess thinking immediately, not an
                    # empty composer. Empty array = legacy projects
                    # / a planner that opted to defer; the frontend
                    # renders the existing Q&A composer in that case.
                    "q_and_a": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["question", "answer", "decision"],
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": (
                                        "The question a thoughtful "
                                        "interviewer would ask about "
                                        "this topic. Concrete, anchored "
                                        "to the user's specifics. 1 "
                                        "sentence."
                                    ),
                                },
                                "answer": {
                                    "type": "string",
                                    "description": (
                                        "The AI's best-guess answer "
                                        "based on the user's input + "
                                        "any sources. 1-3 sentences. "
                                        "Concrete, not vague — pick a "
                                        "lane the human can edit if "
                                        "wrong."
                                    ),
                                },
                                "decision": {
                                    "type": "string",
                                    "description": (
                                        "The decision captured from "
                                        "this Q&A — the load-bearing "
                                        "outcome the user can later "
                                        "approve or modify. 1 sentence."
                                    ),
                                },
                            },
                        },
                    },
                },
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from_topic_title", "to_topic_title", "label"],
                "properties": {
                    "from_topic_title": {"type": "string"},
                    "to_topic_title": {"type": "string"},
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "REQUIRED short verb phrase describing the relationship. "
                            "1–3 words. Examples: 'blocks', 'supports', 'informs', "
                            "'precedes', 'depends on', 'drives', 'limits', 'shapes'. "
                            "Never empty — every dotted line the user sees must read as "
                            "a concrete dependency, not a generic association."
                        ),
                    },
                },
            },
        },
        "suggested_first_topic": {
            "type": "string",
            "description": "Title of the topic to open first. Empty string when clarifying_question_if_too_vague is set.",
        },
        "clarifying_question_if_too_vague": {
            "type": ["string", "null"],
            "description": "Set when the idea is too vague to map (<30 words, no concrete entities). Null otherwise.",
        },
    },
}


# ---------------------------------------------------------------------------
# topic_turn — one planner turn inside a topic's Q&A thread.
# ---------------------------------------------------------------------------
TOPIC_TURN_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "question",
        "why_this_matters",
        "suggested_responses",
        "proposed_decisions",
        "consistency_flags",
        "new_topic_proposal",
        "topic_deletion_suggestion",
        "close_recommendation_reason",
        "conflict_resolution",
        "planned_checkpoints",
        "checkpoint_updates",
    ],
    "properties": {
        "action": {
            "type": "string",
            "enum": ["ask", "pressure_test", "followup", "suggest_close", "resolve_conflict"],
        },
        "question": {
            "type": ["string", "null"],
            "description": "Required unless action == 'suggest_close'.",
        },
        "why_this_matters": {
            "type": ["string", "null"],
        },
        "suggested_responses": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "intent"],
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Full-sentence user-voiced answer, ≤20 words.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Internal tag, e.g. 'conservative', 'ambitious', 'defer'.",
                    },
                },
            },
        },
        "proposed_decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "rationale", "extracted_from_turn_id", "target_topic_title"],
                "properties": {
                    "statement": {"type": "string"},
                    "rationale": {"type": ["string", "null"]},
                    "extracted_from_turn_id": {
                        "type": "string",
                        "description": "The user turn this was extracted from.",
                    },
                    "target_topic_title": {
                        "type": ["string", "null"],
                        "description": (
                            "If this decision clearly belongs to a DIFFERENT existing topic "
                            "on the canvas, set this to that topic's exact title. "
                            "Null or absent means keep on the current topic."
                        ),
                    },
                },
            },
        },
        "consistency_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["other_topic_title", "other_decision_id", "description"],
                "properties": {
                    "other_topic_title": {"type": "string"},
                    "other_decision_id": {"type": "string"},
                    "description": {
                        "type": "string",
                        "description": "One-sentence description of the conflict.",
                    },
                },
            },
        },
        "new_topic_proposal": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["title", "icon", "why", "source_turn_id"],
            "properties": {
                "title": {"type": "string"},
                "icon": {"type": "string", "enum": list(CURATED_ICONS)},
                "why": {"type": "string"},
                "source_turn_id": {"type": "string"},
            },
        },
        "topic_deletion_suggestion": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": [
                "target_topic_id",
                "target_topic_title",
                "reason",
                "superseded_by_decision",
            ],
            "properties": {
                "target_topic_id": {"type": "string"},
                "target_topic_title": {"type": "string"},
                "reason": {"type": "string"},
                "superseded_by_decision": {"type": ["string", "null"]},
            },
        },
        "close_recommendation_reason": {
            "type": ["string", "null"],
            "description": "Set when action == 'suggest_close'. Null otherwise.",
        },
        "conflict_resolution": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "description": (
                "Required when action == 'resolve_conflict'. "
                "Identifies both sides of the contradiction so the UI can surface them."
            ),
            "required": [
                "conflicting_decision_id",
                "conflicting_topic_title",
                "current_statement_summary",
                "previous_statement_summary",
            ],
            "properties": {
                "conflicting_decision_id": {
                    "type": "string",
                    "description": "decision_id of the earlier decision that contradicts the current one.",
                },
                "conflicting_topic_title": {
                    "type": "string",
                    "description": "Title of the topic that owns the conflicting decision.",
                },
                "current_statement_summary": {
                    "type": "string",
                    "description": "Short summary (under 20 words) of what the user just said.",
                },
                "previous_statement_summary": {
                    "type": "string",
                    "description": "Short summary (under 20 words) of the earlier conflicting decision.",
                },
            },
        },
        "planned_checkpoints": {
            "type": ["array", "null"],
            "description": (
                "Only populated on the first turn of a topic (when there are no prior turns). "
                "Null on all subsequent turns. Emit 4-7 short questions the planner intends to "
                "cover to consider this topic fleshed out."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "question"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short slug, e.g. 'price_point'. lowercase, underscores.",
                    },
                    "question": {
                        "type": "string",
                        "description": "Human-readable short question, under 15 words.",
                    },
                },
            },
        },
        "checkpoint_updates": {
            "type": ["array", "null"],
            "description": (
                "Null on the first turn. On subsequent turns, emit ONLY checkpoints whose "
                "status changed this turn. Omit unchanged ones."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "status"],
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "partial", "answered"],
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# composer_routing — route a free-text composer input to the right surface.
# ---------------------------------------------------------------------------
COMPOSER_ROUTING_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["confidence", "primary_route", "alternate_routes", "clarifying_question"],
    "properties": {
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "primary_route": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "target_topic_id", "new_topic_proposal", "payload"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "add_to_topic",
                        "new_topic",
                        "capture_decision",
                        "capture_open_question",
                        "ask_clarifying",
                    ],
                },
                "target_topic_id": {"type": ["string", "null"]},
                "new_topic_proposal": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["title", "icon", "why"],
                    "properties": {
                        "title": {"type": "string"},
                        "icon": {"type": "string", "enum": list(CURATED_ICONS)},
                        "why": {"type": "string"},
                    },
                },
                "payload": {
                    "type": "string",
                    "description": "The text as it will land in the target surface.",
                },
            },
        },
        "alternate_routes": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "target_topic_id", "label"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "add_to_topic",
                            "new_topic",
                            "capture_decision",
                            "capture_open_question",
                        ],
                    },
                    "target_topic_id": {"type": ["string", "null"]},
                    "label": {
                        "type": "string",
                        "description": "Human-readable alternative, shown to the user as a chip.",
                    },
                },
            },
        },
        "clarifying_question": {
            "type": ["string", "null"],
            "description": "Set when primary_route.kind == 'ask_clarifying'.",
        },
    },
}


# ---------------------------------------------------------------------------
# summary_synthesis — regenerate or update the Plan Summary.
# ---------------------------------------------------------------------------
SUMMARY_SYNTHESIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sections", "open_questions_summary", "version_note"],
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "header",
                    "body_markdown",
                    "cited_topic_titles",
                    "cited_source_ids",
                    "preserved_user_text",
                ],
                "properties": {
                    "header": {
                        "type": "string",
                        "description": "Serif-display noun phrase, 1–4 words.",
                    },
                    "body_markdown": {
                        "type": "string",
                        "description": "Prose, not bullets (unless structural).",
                    },
                    "cited_topic_titles": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "cited_source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "preserved_user_text": {
                        "type": ["string", "null"],
                        "description": "Verbatim from prior version. Null if this is new.",
                    },
                },
            },
        },
        "open_questions_summary": {
            "type": "array",
            "items": {"type": "string"},
        },
        "version_note": {
            "type": "string",
            "description": "One-sentence note on what changed vs the prior version.",
        },
    },
}


# ---------------------------------------------------------------------------
# propagation_preview — show downstream effects of a decision edit.
# ---------------------------------------------------------------------------
PROPAGATION_PREVIEW_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["affected_topics", "affected_summary_sections"],
    "properties": {
        "affected_topics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic_title", "affected_decision_id", "change_description"],
                "properties": {
                    "topic_title": {"type": "string"},
                    "affected_decision_id": {"type": ["string", "null"]},
                    "change_description": {"type": "string"},
                },
            },
        },
        "affected_summary_sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["section_header", "change_description"],
                "properties": {
                    "section_header": {"type": "string"},
                    "change_description": {"type": "string"},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# extract_themes_response — cluster pasted customer feedback into 3-5 themes.
# (YC v4 — B3 paste-feedback flow. Each theme becomes one auto-generated
# project on the workspace home; the kickoff for each project rides through
# the existing /api/v2/projects/{id}/kickoff path.)
# ---------------------------------------------------------------------------
EXTRACT_THEMES_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["themes"],
    "properties": {
        "themes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "summary", "source_indices"],
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "2-5 word feature title — PM-readable, sounds like "
                            "a roadmap entry. Examples: 'SSO for enterprise', "
                            "'Dashboard performance on large datasets', "
                            "'Mobile sign-up flow'."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "1-2 sentences. What the underlying issue is + "
                            "why it matters to ship. Concrete > vague."
                        ),
                    },
                    "source_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "0-based indices of feedback items that ground "
                            "this theme. Drop items that don't cluster well "
                            "rather than forcing them into a theme."
                        ),
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Document response schemas — one envelope per doc type for the 7-doc-type
# generator (#094 / Item 3 / Commit 3). All 7 schemas share the same section
# item base; they differ only in (minItems, maxItems) for the sections array
# and the canonical section_id list (enumerated in each mode prompt and
# enforced by the per-doc sanitizer in openai_adapter.py).
#
# All 7 schemas are pinned to ``gpt-5.5`` regardless of user tier. The
# OpenAI adapter exposes 7 explicit grep-able methods
# (``business_plan()`` … ``course_outline()``) that delegate to a shared
# ``_generate_document`` engine.
# ---------------------------------------------------------------------------

# Shared base: every section item across all 7 doc types has this exact shape.
# Sanitizer (per doc type) clamps prose to 3000 chars per section, escapes raw
# HTML outside fenced code, drops ghost cited_topics, clamps key_points to 0–5
# (NOT min 3 — cover/references/etc. legitimately have no key takeaways), and
# trims title to 80 chars.
_DOC_SECTION_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["section_id", "title", "prose_markdown", "key_points", "cited_topics"],
    "properties": {
        "section_id": {
            "type": "string",
            "description": (
                "Canonical slug from DOCUMENT_CANONICAL_SECTIONS for this doc "
                "type. Sanitizer drops sections with unknown ids and reorders "
                "the cleaned set to canonical sequence."
            ),
        },
        "title": {
            "type": "string",
            "description": (
                "Human-readable section title. Sanitizer trims to 80 chars."
            ),
        },
        "prose_markdown": {
            "type": "string",
            "description": (
                "Section body as plain markdown. Headings, lists, and tables "
                "are fine; raw HTML is escaped by the sanitizer. Sanitizer "
                "caps each section's prose at 3000 characters at the nearest "
                "paragraph boundary."
            ),
        },
        "key_points": {
            "type": "array",
            "minItems": 0,
            "maxItems": 5,
            "items": {
                "type": "string",
                "description": (
                    "One short take-away the reader would underline. ≤12 words. "
                    "Sanitizer caps each at 120 chars."
                ),
            },
            "description": (
                "0–5 short take-aways for this section. Cover, references, and "
                "appendix-style sections legitimately have 0 — do not invent "
                "filler."
            ),
        },
        "cited_topics": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "string",
                "description": (
                    "Exact topic title from the input this section pulls from. "
                    "Sanitizer drops ghost citations (case-insensitive match)."
                ),
            },
            "description": (
                "0–8 topic titles the section pulled from. Never invent a "
                "topic that isn't in the project."
            ),
        },
    },
}


def _build_doc_response_schema(min_items: int, max_items: int) -> dict:
    """Build a doc-type envelope schema given (minItems, maxItems)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["sections"],
        "properties": {
            "sections": {
                "type": "array",
                "minItems": min_items,
                "maxItems": max_items,
                "items": _DOC_SECTION_ITEM_SCHEMA,
            },
        },
    }


# 7 envelope schemas — one per doc type. minItems/maxItems differ.
# Business Plan / PRD / Story Outline / Marketing Plan / Research Proposal:
# strict (minItems == maxItems). Event Plan + Course Outline: ranged (some
# canonical sections are conditional on input signal — Event Plan omits
# marketing_ticketing + sponsorship for weddings; Course Outline omits
# tech_requirements + support_community for self-paced/marketplace courses).
BUSINESS_PLAN_RESPONSE_SCHEMA: dict = _build_doc_response_schema(14, 14)
PRD_RESPONSE_SCHEMA: dict = _build_doc_response_schema(13, 13)
STORY_OUTLINE_RESPONSE_SCHEMA: dict = _build_doc_response_schema(9, 9)
EVENT_PLAN_RESPONSE_SCHEMA: dict = _build_doc_response_schema(9, 11)
MARKETING_PLAN_RESPONSE_SCHEMA: dict = _build_doc_response_schema(12, 12)
RESEARCH_PROPOSAL_RESPONSE_SCHEMA: dict = _build_doc_response_schema(10, 10)
COURSE_OUTLINE_RESPONSE_SCHEMA: dict = _build_doc_response_schema(11, 13)


# Canonical section_id slugs in order, per doc type. Used by:
# - prompts.py to enumerate section_ids in each mode prompt.
# - openai_adapter.py user-message formatter to remind the LLM of the order.
# - openai_adapter.py sanitizers to validate section_ids and reorder.
DOCUMENT_CANONICAL_SECTIONS: dict[str, list[str]] = {
    "business_plan": [
        "cover",
        "executive_summary",
        "mission",
        "problem",
        "solution",
        "why_now",
        "market",
        "traction",
        "business_model",
        "competition",
        "gtm",
        "team",
        "financials",
        "risk",
    ],
    "prd": [
        "tldr",
        "problem",
        "customer",
        "goals_non_goals",
        "functional_requirements",
        "non_functional_requirements",
        "out_of_scope",
        "user_stories",
        "success_metrics",
        "open_questions",
        "risks",
        "timeline",
        "references",
    ],
    "story_outline": [
        "logline",
        "genre_audience",
        "theme",
        "characters",
        "world",
        "beat_skeleton",
        "subplots",
        "scene_list",
        "open_questions",
    ],
    "event_plan": [
        "overview",
        "date_venue_capacity",
        "budget",
        "vendors",
        "run_of_show",
        "logistics",
        "marketing_ticketing",
        "sponsorship",
        "safety_permits_insurance",
        "day_of_staffing",
        "teardown_followup",
    ],
    "marketing_plan": [
        "executive_summary",
        "situation_analysis",
        "audience_personas",
        "positioning",
        "objectives_kpis",
        "channel_strategy",
        "calendar",
        "budget_allocation",
        "measurement",
        "team_partners",
        "risks_dependencies",
        "appendix",
    ],
    "research_proposal": [
        "title_abstract",
        "background_lit_review",
        "research_questions",
        "methodology",
        "significance",
        "timeline_milestones",
        "budget_resources",
        "team_collaborators",
        "risk_ethics",
        "references",
    ],
    "course_outline": [
        "title_tagline",
        "description",
        "learning_outcomes",
        "audience_prerequisites",
        "module_breakdown",
        "per_module_detail",
        "materials_readings",
        "schedule_pacing",
        "grading_assessment",
        "instructor_bio",
        "tech_requirements",
        "support_community",
        "reading_list_appendix",
    ],
}


# (minItems, maxItems) per doc type. Mirrors the schemas above; consumed by
# the sanitizer to enforce the count bounds and by the prompt formatter to
# parameterize the "produce {n_min}-{n_max} sections" instruction.
DOCUMENT_SECTION_COUNTS: dict[str, tuple[int, int]] = {
    "business_plan": (14, 14),
    "prd": (13, 13),
    "story_outline": (9, 9),
    "event_plan": (9, 11),
    "marketing_plan": (12, 12),
    "research_proposal": (10, 10),
    "course_outline": (11, 13),
}


# ---------------------------------------------------------------------------
# Tool registry — name → (schema, human description).
# Used by adapters to wire OpenAI / Claude tool calls.
# ---------------------------------------------------------------------------
TOOL_SPECS: dict[str, dict] = {
    "kickoff_response": {
        "schema": KICKOFF_RESPONSE_SCHEMA,
        "description": "Map the user's kickoff idea into 5–10 topic cards with dotted relationships.",
    },
    "topic_turn": {
        "schema": TOPIC_TURN_SCHEMA,
        "description": "Produce one planner turn inside a topic's Q&A thread.",
    },
    "composer_routing": {
        "schema": COMPOSER_ROUTING_SCHEMA,
        "description": "Route a free-text composer input to the right surface.",
    },
    "summary_synthesis": {
        "schema": SUMMARY_SYNTHESIS_SCHEMA,
        "description": "Regenerate or update the Plan Summary as adaptive sections.",
    },
    "propagation_preview": {
        "schema": PROPAGATION_PREVIEW_SCHEMA,
        "description": "Preview downstream effects of a decision edit. Never mutates.",
    },
    "extract_themes_response": {
        "schema": EXTRACT_THEMES_RESPONSE_SCHEMA,
        "description": (
            "Cluster pasted customer feedback items into 3-5 recurring "
            "themes. Each theme becomes one auto-generated project on "
            "the workspace home (YC v4 paste-feedback flow). Cheap "
            "model (gpt-4o-mini); single call per paste."
        ),
    },
    "business_plan_response": {
        "schema": BUSINESS_PLAN_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete investor-pitch-ready Business Plan in "
            "one call (#094 / Item 3). 14 sections (cover + executive "
            "summary + 12 substantive). On-demand only; pinned to "
            "gpt-5.5; gated to Pro+/Frontier."
        ),
    },
    "prd_response": {
        "schema": PRD_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete Product Requirements Document in one "
            "call (#094 / Item 3). 13 sections, problem-led, Cagan-style. "
            "On-demand only; pinned to gpt-5.5; gated to Pro+/Frontier."
        ),
    },
    "story_outline_response": {
        "schema": STORY_OUTLINE_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete Story Outline in one call (#094 / Item 3). "
            "9 sections, logline-first, single framework spine. Form "
            "(short story / novel / screenplay) inferred from input. "
            "On-demand only; pinned to gpt-5.5; gated to Pro+/Frontier."
        ),
    },
    "event_plan_response": {
        "schema": EVENT_PLAN_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete Event Plan in one call (#094 / Item 3). "
            "9–11 sections (marketing_ticketing + sponsorship are "
            "conditional on event-type signal). Run-of-show as markdown "
            "table. On-demand only; pinned to gpt-5.5; gated to "
            "Pro+/Frontier."
        ),
    },
    "marketing_plan_response": {
        "schema": MARKETING_PLAN_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete Marketing Plan in one call (#094 / "
            "Item 3). 12 sections, Dunford 5-component positioning, "
            "PESO channel matrix, SMART KPIs. On-demand only; pinned to "
            "gpt-5.5; gated to Pro+/Frontier."
        ),
    },
    "research_proposal_response": {
        "schema": RESEARCH_PROPOSAL_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete Research Proposal in one call (#094 / "
            "Item 3). 10 sections, methodology-heavy, NSF/NIH/industry "
            "variants by domain signal. On-demand only; pinned to "
            "gpt-5.5; gated to Pro+/Frontier."
        ),
    },
    "course_outline_response": {
        "schema": COURSE_OUTLINE_RESPONSE_SCHEMA,
        "description": (
            "Generate a complete Course Outline in one call (#094 / "
            "Item 3). 11–13 sections (tech_requirements + "
            "support_community conditional on online vs self-paced). "
            "Bloom's-aligned outcomes, backward-design discipline. "
            "On-demand only; pinned to gpt-5.5; gated to Pro+/Frontier."
        ),
    },
}
