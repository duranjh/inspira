"""Tests for the OpenAI planner adapter.

Two kinds of tests here:

1. **Offline** — mock the OpenAI client and exercise the adapter's parsing,
   validation, and prompt-assembly logic. Always runs. Fast. Catches 90%
   of bugs before any API call.
2. **Live integration** — actually call OpenAI's API with a canned kickoff
   idea. Skipped gracefully when ``OPENAI_API_KEY`` is not set. This is
   the "is the spec coherent end-to-end" gut check.

Run the offline tests any time:

    cd services && python -m unittest tests.test_openai_adapter

Run with live calls too:

    export OPENAI_API_KEY="sk-..."   # ($env:OPENAI_API_KEY="sk-..." on Windows)
    cd services && python -m unittest tests.test_openai_adapter -v
"""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

# Load repo-root .env into os.environ BEFORE the @skipUnless decorator below
# evaluates. Without this, OPENAI_API_KEY in a .env file wouldn't unlock the
# live test when running from a fresh shell.
from planning_studio_service._env_bootstrap import ensure_loaded

ensure_loaded()

from planning_studio_service.agents import (
    OpenAIConfig,
    OpenAIPlanningInterviewer,
)
from planning_studio_service.agents.openai_adapter import (
    MODEL_BUSINESS_PLAN,
    TIMEOUT_DOCUMENT_S,
    _DOC_TYPE_CONFIG,
    _DOCUMENT_KEY_POINT_MAX_CHARS,
    _DOCUMENT_PROSE_MAX_CHARS,
    _DOCUMENT_SECTION_TITLE_MAX_CHARS,
    _FLS_LEGEND_MARKER,
    _build_openai_tool_spec,
    _ensure_fls_legend_in_cover,
    _extract_tool_call_args,
    _format_document_user_message,
    _format_kickoff_user_message,
    _format_topic_turn_user_message,
    _repair_one_doc_section,
    _sanitize_business_plan_response,
    _sanitize_course_outline_response,
    _sanitize_document_response_base,
    _sanitize_event_plan_response,
    _sanitize_kickoff_response,
    _sanitize_marketing_plan_response,
    _sanitize_prd_response,
    _sanitize_research_proposal_response,
    _sanitize_story_outline_response,
    _sanitize_topic_turn,
)
from planning_studio_service.agents.prompts import (
    CURATED_ICONS,
    DOCUMENT_MODE_PROMPTS,
    DOMAIN_ENUM,
)
from planning_studio_service.agents.schemas import (
    DOCUMENT_CANONICAL_SECTIONS,
    DOCUMENT_SECTION_COUNTS,
    TOOL_SPECS,
)


# =============================================================================
# Offline tests — always run.
# =============================================================================


class ToolSpecBuildTests(unittest.TestCase):
    """The adapter must wrap our JSON Schemas in OpenAI's function-tool shape."""

    def test_kickoff_tool_spec_has_strict_mode_and_name(self) -> None:
        spec = _build_openai_tool_spec("kickoff_response")
        self.assertEqual(spec["type"], "function")
        self.assertEqual(spec["function"]["name"], "kickoff_response")
        self.assertTrue(spec["function"]["strict"])
        self.assertIn("domain", spec["function"]["parameters"]["properties"])

    def test_every_registered_tool_builds(self) -> None:
        for tool_name in TOOL_SPECS:
            with self.subTest(tool=tool_name):
                spec = _build_openai_tool_spec(tool_name)
                self.assertEqual(spec["function"]["name"], tool_name)
                self.assertIn("parameters", spec["function"])

    def test_schema_icon_enums_match_curated_set(self) -> None:
        """Schema icon enums must stay synced with the curated list."""
        kickoff_schema = TOOL_SPECS["kickoff_response"]["schema"]
        topic_icon_enum = (
            kickoff_schema["properties"]["topics"]["items"]["properties"]["icon"]["enum"]
        )
        self.assertEqual(set(topic_icon_enum), set(CURATED_ICONS))

    def test_schema_domain_enum_matches_template_set(self) -> None:
        kickoff_schema = TOOL_SPECS["kickoff_response"]["schema"]
        self.assertEqual(
            set(kickoff_schema["properties"]["domain"]["enum"]),
            set(DOMAIN_ENUM),
        )


class KickoffMessageFormattingTests(unittest.TestCase):
    """User-message assembly — readable so prompt debugging is easy."""

    def test_includes_user_idea_between_delimiters(self) -> None:
        msg = _format_kickoff_user_message(
            "A novel about a cartographer whose maps rewrite themselves.", []
        )
        self.assertIn("cartographer whose maps rewrite themselves", msg)
        self.assertEqual(msg.count("---"), 2)

    def test_mentions_no_sources_when_none_attached(self) -> None:
        msg = _format_kickoff_user_message("an idea", [])
        self.assertIn("No sources were attached", msg)

    def test_formats_source_excerpts_with_kind_and_display_name(self) -> None:
        msg = _format_kickoff_user_message(
            "a marketing campaign for a wine festival",
            [
                {"kind": "url", "display_name": "venue-brief.pdf",
                 "excerpt": "Riverside park\n800 capacity"},
                {"kind": "upload", "display_name": "wine-list.md", "excerpt": ""},
            ],
        )
        self.assertIn("[url] venue-brief.pdf", msg)
        self.assertIn("Riverside park", msg)
        self.assertIn("[upload] wine-list.md", msg)


class KickoffSanitizeTests(unittest.TestCase):
    """Post-call sanitize: repair minor inconsistencies, raise on structural bugs."""

    def _valid_response(self) -> dict[str, Any]:
        return {
            "domain": "novel",
            "domain_confidence": "medium",
            "opening_card": {"body": "I've drafted five topics. Start with Premise."},
            "topics": [
                {"title": "Premise", "icon": "lightbulb", "why_this_topic": "The core idea."},
                {"title": "Characters", "icon": "heart", "why_this_topic": "Who it's about."},
                {"title": "Setting", "icon": "map-pin", "why_this_topic": "Where & when."},
                {"title": "Arc", "icon": "compass", "why_this_topic": "Shape of the story."},
                {"title": "Voice", "icon": "feather", "why_this_topic": "Whose voice tells it."},
            ],
            "relationships": [
                {"from_topic_title": "Premise", "to_topic_title": "Characters", "label": None},
                {"from_topic_title": "Characters", "to_topic_title": "Arc", "label": "drives"},
            ],
            "suggested_first_topic": "Premise",
            "clarifying_question_if_too_vague": None,
        }

    def test_valid_response_passes(self) -> None:
        response = self._valid_response()
        _sanitize_kickoff_response(response, OpenAIConfig())
        # Sanitizer attaches a repair log; it should show no repairs.
        self.assertEqual(response["_sanitize"]["dropped_relationships"], [])
        self.assertIsNone(response["_sanitize"]["suggested_first_fallback"])

    def test_rejects_too_few_topics(self) -> None:
        response = self._valid_response()
        response["topics"] = response["topics"][:2]
        response["suggested_first_topic"] = "Premise"
        with self.assertRaisesRegex(RuntimeError, "returned 2 topics"):
            _sanitize_kickoff_response(response, OpenAIConfig())

    def test_rejects_too_many_topics(self) -> None:
        response = self._valid_response()
        extras = [
            {"title": f"Extra {i}", "icon": "flag", "why_this_topic": "x"} for i in range(8)
        ]
        response["topics"].extend(extras)
        with self.assertRaisesRegex(RuntimeError, "returned 13 topics"):
            _sanitize_kickoff_response(response, OpenAIConfig())

    def test_orphan_relationships_dropped_not_raised(self) -> None:
        """Model occasionally invents a topic title in relationships.
        Drop the bad relationship, keep the good ones, note in repair log.
        (Topic-orphan auto-connect may add new relationships; we test that
        the BAD ones are gone and no relationship references a ghost
        topic, rather than fixing an exact count.)
        """
        response = self._valid_response()
        response["relationships"].append(
            {"from_topic_title": "Premise", "to_topic_title": "NotATopic", "label": "x"}
        )
        response["relationships"].append(
            {"from_topic_title": "Ghost", "to_topic_title": "Arc", "label": "x"}
        )
        _sanitize_kickoff_response(response, OpenAIConfig())

        # Every surviving relationship references only real topic titles.
        topic_titles = {t["title"] for t in response["topics"]}
        for rel in response["relationships"]:
            self.assertIn(rel["from_topic_title"], topic_titles)
            self.assertIn(rel["to_topic_title"], topic_titles)

        # The two bad ones were logged as dropped with the expected reason.
        self.assertEqual(len(response["_sanitize"]["dropped_relationships"]), 2)
        for orphan in response["_sanitize"]["dropped_relationships"]:
            self.assertEqual(orphan["reason"], "references unknown topic title")

    def test_orphan_topics_auto_connected_to_suggested_first(self) -> None:
        """Every topic must appear in at least one relationship.
        If the LLM leaves some orphan, the sanitizer auto-links them to
        the suggested first topic with a soft 'relates to' label.
        """
        response = self._valid_response()
        # Only 1 relationship (Premise → Characters). Setting, Arc, Voice
        # are orphan.
        response["relationships"] = [
            {"from_topic_title": "Premise", "to_topic_title": "Characters", "label": "drives"},
        ]
        _sanitize_kickoff_response(response, OpenAIConfig())

        # Every topic title should now appear in at least one relationship.
        referenced = set()
        for rel in response["relationships"]:
            referenced.add(rel["from_topic_title"])
            referenced.add(rel["to_topic_title"])
        for topic in response["topics"]:
            self.assertIn(
                topic["title"], referenced,
                f"topic {topic['title']} is still orphan after sanitize",
            )

        # Repair log records what was added.
        self.assertEqual(
            len(response["_sanitize"]["auto_connected_orphans"]), 3,
        )

    def test_suggested_first_topic_not_in_topics_falls_back(self) -> None:
        """Don't raise — fall back to the first topic so the UI still has a default."""
        response = self._valid_response()
        response["suggested_first_topic"] = "Ghost Topic"
        _sanitize_kickoff_response(response, OpenAIConfig())
        self.assertEqual(response["suggested_first_topic"], "Premise")
        self.assertEqual(
            response["_sanitize"]["suggested_first_fallback"],
            {"original": "Ghost Topic", "fallback": "Premise"},
        )

    def test_too_vague_path_must_be_internally_consistent(self) -> None:
        response = self._valid_response()
        response["clarifying_question_if_too_vague"] = "Who is it for?"
        # But we left topics populated — contradiction. This is structural,
        # so we RAISE (not repair) — "the model was confused about whether
        # to map or ask" is the bug, not the data.
        with self.assertRaisesRegex(RuntimeError, "internally inconsistent"):
            _sanitize_kickoff_response(response, OpenAIConfig())

    def test_too_vague_path_happy(self) -> None:
        response = self._valid_response()
        response["clarifying_question_if_too_vague"] = "What's the genre?"
        response["topics"] = []
        response["relationships"] = []
        response["suggested_first_topic"] = ""
        _sanitize_kickoff_response(response, OpenAIConfig())  # must not raise


class ToolCallExtractionTests(unittest.TestCase):
    """Parsing the OpenAI response shape — sensitive to SDK schema drift."""

    def _fake_response(self, *, tool_name: str, args: dict[str, Any]) -> Any:
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name=tool_name, arguments=json.dumps(args))
        )
        message = SimpleNamespace(content=None, tool_calls=[tool_call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_extracts_tool_args_happy_path(self) -> None:
        response = self._fake_response(
            tool_name="kickoff_response", args={"domain": "novel"}
        )
        parsed = _extract_tool_call_args(response, expected_name="kickoff_response")
        self.assertEqual(parsed, {"domain": "novel"})

    def test_raises_on_unexpected_tool_name(self) -> None:
        response = self._fake_response(tool_name="something_else", args={})
        with self.assertRaisesRegex(RuntimeError, "Expected tool.*model called"):
            _extract_tool_call_args(response, expected_name="kickoff_response")

    def test_raises_when_no_tool_call_returned(self) -> None:
        message = SimpleNamespace(content="I'd rather not", tool_calls=[])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with self.assertRaisesRegex(RuntimeError, "Expected a tool call"):
            _extract_tool_call_args(response, expected_name="kickoff_response")

    def test_raises_on_invalid_json_arguments(self) -> None:
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="kickoff_response", arguments="{not json")
        )
        message = SimpleNamespace(content=None, tool_calls=[tool_call])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with self.assertRaisesRegex(RuntimeError, "non-JSON arguments"):
            _extract_tool_call_args(response, expected_name="kickoff_response")


class KickoffWithMockedClientTests(unittest.TestCase):
    """End-to-end kickoff() call against a mocked OpenAI client."""

    def _canned_response(self, args: dict[str, Any]) -> Any:
        tool_call = SimpleNamespace(
            function=SimpleNamespace(
                name="kickoff_response", arguments=json.dumps(args)
            )
        )
        message = SimpleNamespace(content=None, tool_calls=[tool_call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_kickoff_happy_path_returns_parsed_dict(self) -> None:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = self._canned_response({
            "domain": "campaign",
            "domain_confidence": "high",
            "opening_card": {"body": "I've drafted six topics. Start with Audience."},
            "topics": [
                {"title": "Audience", "icon": "heart", "why_this_topic": "Who we're for."},
                {"title": "Message", "icon": "megaphone", "why_this_topic": "The one idea."},
                {"title": "Channels", "icon": "map-pin", "why_this_topic": "Where it runs."},
                {"title": "Timeline", "icon": "clock", "why_this_topic": "When it lands."},
                {"title": "Budget", "icon": "chart", "why_this_topic": "What we have."},
            ],
            "relationships": [
                {"from_topic_title": "Audience", "to_topic_title": "Message", "label": None},
            ],
            "suggested_first_topic": "Audience",
            "clarifying_question_if_too_vague": None,
        })

        adapter = OpenAIPlanningInterviewer(client=fake_client)
        result = adapter.kickoff(
            user_idea="Launching a local wine festival in early October. Families welcome."
        )

        self.assertEqual(result["domain"], "campaign")
        self.assertEqual(len(result["topics"]), 5)
        self.assertEqual(result["suggested_first_topic"], "Audience")

        # Adapter must have forced the tool_choice.
        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        self.assertEqual(
            call_kwargs["tool_choice"],
            {"type": "function", "function": {"name": "kickoff_response"}},
        )
        # And used the base + kickoff mode prompt.
        system_msg = call_kwargs["messages"][0]["content"]
        self.assertIn("MODE: KICKOFF", system_msg)
        self.assertIn("Inspira planning interviewer", system_msg)

    def test_kickoff_rejects_empty_idea(self) -> None:
        adapter = OpenAIPlanningInterviewer(client=MagicMock())
        with self.assertRaisesRegex(ValueError, "user_idea is required"):
            adapter.kickoff(user_idea="")

    def test_temperature_omitted_by_default(self) -> None:
        """GPT-5 and o-series reject non-default temperature. Default None ⇒ not sent."""
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = self._canned_response({
            "domain": "personal",
            "domain_confidence": "low",
            "opening_card": {"body": "x"},
            "topics": [
                {"title": f"T{i}", "icon": "flag", "why_this_topic": "x"}
                for i in range(5)
            ],
            "relationships": [],
            "suggested_first_topic": "T0",
            "clarifying_question_if_too_vague": None,
        })
        adapter = OpenAIPlanningInterviewer(client=fake_client)
        adapter.kickoff(user_idea="some idea with enough words to not be flagged as vague")

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        self.assertNotIn("temperature", call_kwargs)

    def test_temperature_sent_when_explicitly_set(self) -> None:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = self._canned_response({
            "domain": "personal",
            "domain_confidence": "low",
            "opening_card": {"body": "x"},
            "topics": [
                {"title": f"T{i}", "icon": "flag", "why_this_topic": "x"}
                for i in range(5)
            ],
            "relationships": [],
            "suggested_first_topic": "T0",
            "clarifying_question_if_too_vague": None,
        })
        adapter = OpenAIPlanningInterviewer(
            client=fake_client,
            config=OpenAIConfig(temperature=0.3),
        )
        adapter.kickoff(user_idea="some idea with enough words to not be flagged as vague")

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["temperature"], 0.3)

    def test_retries_once_on_empty_toolcall_response(self) -> None:
        """GPT-5 occasionally returns no tool_call despite tool_choice. Retry wins."""
        # First call: empty tool_calls. Second call: valid payload.
        empty_message = SimpleNamespace(content="", tool_calls=[])
        empty_response = SimpleNamespace(choices=[SimpleNamespace(message=empty_message)])
        valid = self._canned_response({
            "domain": "personal",
            "domain_confidence": "low",
            "opening_card": {"body": "x"},
            "topics": [
                {"title": f"T{i}", "icon": "flag", "why_this_topic": "x"}
                for i in range(5)
            ],
            "relationships": [],
            "suggested_first_topic": "T0",
            "clarifying_question_if_too_vague": None,
        })
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = [empty_response, valid]

        adapter = OpenAIPlanningInterviewer(client=fake_client)
        result = adapter.kickoff(user_idea="a fine idea with enough text to be mappable")

        self.assertEqual(result["domain"], "personal")
        self.assertEqual(fake_client.chat.completions.create.call_count, 2)

    def test_gives_up_after_max_retries_on_persistent_empty_toolcall(self) -> None:
        empty_message = SimpleNamespace(content="", tool_calls=[])
        empty_response = SimpleNamespace(choices=[SimpleNamespace(message=empty_message)])
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = empty_response

        adapter = OpenAIPlanningInterviewer(
            client=fake_client,
            config=OpenAIConfig(max_empty_toolcall_retries=1),
        )
        with self.assertRaisesRegex(RuntimeError, "Expected a tool call"):
            adapter.kickoff(user_idea="a fine idea with enough text to be mappable")

        # 1 initial + 1 retry == 2 attempts before giving up
        self.assertEqual(fake_client.chat.completions.create.call_count, 2)


# =============================================================================
# Mode B: topic_turn — offline tests.
# =============================================================================


class TopicTurnMessageFormattingTests(unittest.TestCase):
    def _current_topic(self) -> dict[str, Any]:
        return {
            "title": "Budget",
            "icon": "chart",
            "decisions": [
                {"decision_id": "D1", "statement": "Hard cap $48k incl. 10% contingency.",
                 "status": "confirmed", "rationale": None},
            ],
            "turns": [
                {"turn_id": "T1", "role": "planner", "body": "What's the hard cap?",
                 "why_this_matters": "Everything else depends on it.",
                 "action": "ask", "status": "answered"},
                {"turn_id": "T2", "role": "user",
                 "body": "48k including contingency.",
                 "status": "answered"},
            ],
            "open_questions": [{"question_id": "Q1", "text": "Do we need a reserve line?", "status": "open"}],
            "risks_assumptions": [{"kind": "risk", "severity": "medium",
                                   "text": "Weather could require a tent upgrade.",
                                   "status": "open"}],
        }

    def test_formats_all_sections(self) -> None:
        msg = _format_topic_turn_user_message(
            self._current_topic(),
            other_topics=[{
                "title": "Audience",
                "decisions": [{"decision_id": "D2", "statement": "Families welcome."}],
            }],
            sources=[{"kind": "pdf", "display_name": "board-brief.pdf",
                      "excerpt": "Budget envelope: $50k.\nMust include insurance."}],
        )
        self.assertIn("CURRENT TOPIC: Budget [chart]", msg)
        self.assertIn("D1 [confirmed]: Hard cap", msg)
        self.assertIn("Q&A thread", msg)
        self.assertIn("[T1] PLANNER", msg)
        self.assertIn("[T2] USER", msg)
        self.assertIn("why_this_matters: Everything else depends", msg)
        self.assertIn("Open questions", msg)
        self.assertIn("Risks & assumptions", msg)
        self.assertIn("OTHER topics", msg)
        self.assertIn("Audience:", msg)
        self.assertIn("D2: Families welcome", msg)
        self.assertIn("[pdf] board-brief.pdf", msg)
        self.assertIn("Budget envelope: $50k", msg)

    def test_no_turns_yet(self) -> None:
        topic = self._current_topic()
        topic["turns"] = []
        msg = _format_topic_turn_user_message(topic, other_topics=[], sources=[])
        self.assertIn("No Q&A turns yet", msg)
        self.assertIn("No other topics in this project yet", msg)


class TopicTurnSanitizeTests(unittest.TestCase):
    def _other_topics(self) -> list[dict[str, Any]]:
        return [
            {"title": "Messaging", "decisions": [{"decision_id": "D5", "statement": "Named stage."}]},
        ]

    def test_ask_action_requires_question(self) -> None:
        parsed = {
            "action": "ask",
            "question": None,
            "why_this_matters": "x",
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
        }
        with self.assertRaisesRegex(RuntimeError, "must include a question"):
            _sanitize_topic_turn(parsed, self._other_topics())

    def test_suggest_close_preserves_question_and_clears_why(self) -> None:
        """suggest_close keeps the question + suggestions; only clears why_this_matters."""
        parsed = {
            "action": "suggest_close",
            "question": "Should we close?",
            "why_this_matters": "stale",
            "suggested_responses": [{"label": "yes", "intent": "defer"}],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": "All decisions captured.",
        }
        _sanitize_topic_turn(parsed, self._other_topics())
        # question is preserved (the user sees the close prompt)
        self.assertEqual(parsed["question"], "Should we close?")
        # why_this_matters is cleared (close prompt speaks for itself)
        self.assertIsNone(parsed["why_this_matters"])
        # suggestions kept (model provided them)
        self.assertEqual(len(parsed["suggested_responses"]), 1)

    def test_suggest_close_synthesises_missing_question(self) -> None:
        """If suggest_close has no question, the sanitizer fills in the canonical one."""
        parsed = {
            "action": "suggest_close",
            "question": None,
            "why_this_matters": None,
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": "All done.",
        }
        _sanitize_topic_turn(parsed, self._other_topics())
        self.assertIsNotNone(parsed["question"])
        self.assertIn("close this topic", parsed["question"])
        self.assertEqual(len(parsed["suggested_responses"]), 2)
        intents = {s["intent"] for s in parsed["suggested_responses"]}
        self.assertEqual(intents, {"close", "continue"})

    def test_orphan_consistency_flag_dropped(self) -> None:
        parsed = {
            "action": "ask",
            "question": "q?",
            "why_this_matters": "w",
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [
                {"other_topic_title": "Messaging", "other_decision_id": "D5",
                 "description": "Conflicts with named-stage."},
                {"other_topic_title": "GhostTopic", "other_decision_id": "D99",
                 "description": "Not real."},
            ],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
        }
        _sanitize_topic_turn(parsed, self._other_topics())
        self.assertEqual(len(parsed["consistency_flags"]), 1)
        self.assertEqual(parsed["consistency_flags"][0]["other_topic_title"], "Messaging")
        self.assertEqual(len(parsed["_sanitize"]["dropped_consistency_flags"]), 1)

    def test_unknown_action_raises(self) -> None:
        parsed = {
            "action": "yodel",
            "question": None,
            "why_this_matters": None,
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
        }
        with self.assertRaisesRegex(RuntimeError, "unknown action.*yodel"):
            _sanitize_topic_turn(parsed, [])

    def test_dedup_guard_forces_suggest_close_on_near_duplicate(self) -> None:
        """A near-duplicate of a prior planner question is forced to suggest_close."""
        prior_turns = [
            {
                "turn_id": "t1",
                "role": "planner",
                "body": "What is the price point for your premium tier?",
            },
            {"turn_id": "t2", "role": "user", "body": "$99 per month."},
        ]
        parsed = {
            "action": "ask",
            # Near-identical rephrasing — swaps "for" → "of". Overlap on
            # content words is 1.0, well above the 0.75 threshold.
            "question": "What is the price point of your premium tier?",
            "why_this_matters": "Pricing clarity.",
            "suggested_responses": [{"label": "yes", "intent": "confirm"}],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
        }
        _sanitize_topic_turn(parsed, self._other_topics(), prior_turns=prior_turns)

        # action forced to suggest_close
        self.assertEqual(parsed["action"], "suggest_close")
        # duplicate question cleared, canonical close prompt synthesised
        self.assertIsNotNone(parsed["question"])
        self.assertIn("close this topic", parsed["question"])
        # repair log records the reason
        self.assertIsNotNone(parsed["_sanitize"]["forced_suggest_close_reason"])
        self.assertGreater(
            parsed["_sanitize"]["forced_suggest_close_reason"]["overlap"], 0.75,
        )
        # The ask-shaped suggestions are replaced by the canonical
        # close / continue pills.
        intents = {s["intent"] for s in parsed["suggested_responses"]}
        self.assertEqual(intents, {"close", "continue"})

    def test_dedup_guard_passes_through_genuinely_different_question(self) -> None:
        """A question with low token overlap is not flagged as a duplicate."""
        prior_turns = [
            {
                "turn_id": "t1",
                "role": "planner",
                "body": "What's your price point?",
            },
            {"turn_id": "t2", "role": "user", "body": "$99."},
        ]
        parsed = {
            "action": "ask",
            # Entirely different topic (distribution channel, not price).
            "question": "Which distribution channels will reach enterprise buyers?",
            "why_this_matters": "Channel fit.",
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
        }
        _sanitize_topic_turn(parsed, self._other_topics(), prior_turns=prior_turns)

        # action preserved, question preserved
        self.assertEqual(parsed["action"], "ask")
        self.assertIn("distribution channels", parsed["question"])
        # no forced override
        self.assertIsNone(parsed["_sanitize"]["forced_suggest_close_reason"])


class TopicTurnWithMockedClientTests(unittest.TestCase):
    def _canned_turn(self, args: dict[str, Any]) -> Any:
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="topic_turn", arguments=json.dumps(args))
        )
        message = SimpleNamespace(content=None, tool_calls=[tool_call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_topic_turn_happy_path(self) -> None:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = self._canned_turn({
            "action": "ask",
            "question": "Which line items are non-negotiable?",
            "why_this_matters": "Pre-deciding the cuts saves negotiation later.",
            "suggested_responses": [
                {"label": "Safety and insurance are fixed.", "intent": "conservative"},
                {"label": "Talent fees flex first.", "intent": "ambitious"},
                {"label": "Let me think about it.", "intent": "defer"},
            ],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
        })
        adapter = OpenAIPlanningInterviewer(client=fake_client)
        result = adapter.topic_turn(
            current_topic={"title": "Budget", "icon": "chart", "decisions": [], "turns": []},
            other_topics=[],
        )
        self.assertEqual(result["action"], "ask")
        self.assertEqual(len(result["suggested_responses"]), 3)

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        self.assertEqual(
            call_kwargs["tool_choice"],
            {"type": "function", "function": {"name": "topic_turn"}},
        )
        self.assertIn("MODE: TOPIC_INTERVIEW", call_kwargs["messages"][0]["content"])
        self.assertIn("CURRENT TOPIC: Budget", call_kwargs["messages"][1]["content"])

    def test_topic_turn_rejects_empty_current_topic(self) -> None:
        adapter = OpenAIPlanningInterviewer(client=MagicMock())
        with self.assertRaisesRegex(ValueError, "current_topic is required"):
            adapter.topic_turn(current_topic={}, other_topics=[])


# =============================================================================
# Next Steps tests (#089 / Item 2 / F2) — offline only.
# =============================================================================


def _make_doc_section(
    section_id: str,
    *,
    title: str | None = None,
    prose: str = "First paragraph.\n\nSecond paragraph.",
    key_points: list[str] | None = None,
    cited_topics: list[str] | None = None,
) -> dict[str, Any]:
    """Build a canonical happy-path section item for any doc type.

    Defaults are deliberately small so individual tests can override one
    field without ripple effects.
    """
    return {
        "section_id": section_id,
        "title": title if title is not None else section_id.replace("_", " ").title(),
        "prose_markdown": prose,
        "key_points": key_points if key_points is not None else [
            "Anchor the section in something concrete",
            "Avoid superlatives without numbers",
        ],
        "cited_topics": cited_topics if cited_topics is not None else [],
    }


def _make_doc_response(doc_type: str, *, sections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a canonical happy-path response for any doc type.

    By default fills in EVERY canonical section_id for the given doc_type
    with the default prose + key_points. Pass ``sections`` to override.
    """
    if sections is None:
        canonical = DOCUMENT_CANONICAL_SECTIONS[doc_type]
        sections = [_make_doc_section(sid) for sid in canonical]
    return {"sections": sections}


_BUSINESS_PLAN_TOPICS: list[dict[str, Any]] = [
    {"title": "Audience"},
    {"title": "Channels"},
    {"title": "Budget"},
    {"title": "Timeline"},
]


class DocumentRegistryTests(unittest.TestCase):
    """The 7 doc-type registries are well-formed and consistent (#094)."""

    def test_all_7_doc_types_in_doc_type_config(self) -> None:
        expected = {
            "business_plan", "prd", "story_outline", "event_plan",
            "marketing_plan", "research_proposal", "course_outline",
        }
        self.assertEqual(set(_DOC_TYPE_CONFIG), expected)

    def test_all_7_doc_types_in_document_mode_prompts(self) -> None:
        self.assertEqual(set(DOCUMENT_MODE_PROMPTS), set(_DOC_TYPE_CONFIG))

    def test_all_7_doc_types_in_canonical_sections(self) -> None:
        self.assertEqual(set(DOCUMENT_CANONICAL_SECTIONS), set(_DOC_TYPE_CONFIG))

    def test_all_7_doc_types_in_section_counts(self) -> None:
        self.assertEqual(set(DOCUMENT_SECTION_COUNTS), set(_DOC_TYPE_CONFIG))

    def test_canonical_sections_lengths_within_section_counts(self) -> None:
        """Canonical list length must equal maxItems (so a complete doc fits)."""
        for doc_type, sections in DOCUMENT_CANONICAL_SECTIONS.items():
            with self.subTest(doc_type=doc_type):
                _, n_max = DOCUMENT_SECTION_COUNTS[doc_type]
                self.assertEqual(len(sections), n_max)

    def test_section_counts_min_le_max(self) -> None:
        for doc_type, (n_min, n_max) in DOCUMENT_SECTION_COUNTS.items():
            with self.subTest(doc_type=doc_type):
                self.assertLessEqual(n_min, n_max)
                self.assertGreater(n_min, 0)

    def test_strict_doc_types_have_min_eq_max(self) -> None:
        """5 of 7 doc types are strict (LLM must produce exact count)."""
        for doc_type in ("business_plan", "prd", "story_outline",
                         "marketing_plan", "research_proposal"):
            with self.subTest(doc_type=doc_type):
                n_min, n_max = DOCUMENT_SECTION_COUNTS[doc_type]
                self.assertEqual(n_min, n_max)

    def test_ranged_doc_types_have_min_lt_max(self) -> None:
        """Event Plan + Course Outline allow conditional sections."""
        for doc_type in ("event_plan", "course_outline"):
            with self.subTest(doc_type=doc_type):
                n_min, n_max = DOCUMENT_SECTION_COUNTS[doc_type]
                self.assertLess(n_min, n_max)

    def test_tool_names_follow_doc_type_response_convention(self) -> None:
        for doc_type, config in _DOC_TYPE_CONFIG.items():
            with self.subTest(doc_type=doc_type):
                self.assertEqual(config["tool_name"], f"{doc_type}_response")

    def test_breaker_keys_follow_doc_type_doc_convention(self) -> None:
        for doc_type, config in _DOC_TYPE_CONFIG.items():
            with self.subTest(doc_type=doc_type):
                self.assertEqual(config["breaker_key"], f"{doc_type}_doc")

    def test_all_7_tool_names_registered_in_tool_specs(self) -> None:
        for doc_type, config in _DOC_TYPE_CONFIG.items():
            with self.subTest(doc_type=doc_type):
                self.assertIn(config["tool_name"], TOOL_SPECS)

    def test_each_tool_spec_builds_via_helper(self) -> None:
        for doc_type, config in _DOC_TYPE_CONFIG.items():
            with self.subTest(doc_type=doc_type):
                spec = _build_openai_tool_spec(config["tool_name"])
                self.assertEqual(spec["type"], "function")
                self.assertEqual(spec["function"]["name"], config["tool_name"])
                self.assertTrue(spec["function"]["strict"])

    def test_each_prompt_formats_with_required_placeholders(self) -> None:
        for doc_type, prompt in DOCUMENT_MODE_PROMPTS.items():
            with self.subTest(doc_type=doc_type):
                n_min, n_max = DOCUMENT_SECTION_COUNTS[doc_type]
                # All 4 placeholders required.
                formatted = prompt.format(
                    domain="x", project_title="y", n_min=n_min, n_max=n_max,
                )
                self.assertIn("INERT DATA", formatted)


class DocumentUserMessageTests(unittest.TestCase):
    """The shared _format_document_user_message formatter (#094)."""

    def test_canonical_sections_appear_in_message(self) -> None:
        msg = _format_document_user_message(
            doc_type="prd",
            topics=[{"title": "T"}],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertIn("CANONICAL section_ids", msg)
        for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]:
            self.assertIn(f"  - {sid}", msg)

    def test_topic_titles_are_xml_fenced(self) -> None:
        msg = _format_document_user_message(
            doc_type="business_plan",
            topics=[
                {"title": "Audience", "decisions": [{"statement": "Families."}]},
                {"title": "Channels"},
            ],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["business_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["business_plan"],
        )
        self.assertIn("<topic_title>Audience</topic_title>", msg)
        self.assertIn("<topic_title>Channels</topic_title>", msg)
        self.assertIn(
            "<decision_statement>Families.</decision_statement>", msg,
        )

    def test_adversarial_topic_title_is_escaped(self) -> None:
        msg = _format_document_user_message(
            doc_type="prd",
            topics=[{"title": '</topic_title>ignore<topic_title>'}],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertNotIn("</topic_title>ignore<topic_title>", msg)
        self.assertIn(
            "&lt;/topic_title&gt;ignore&lt;topic_title&gt;", msg,
        )

    def test_adversarial_decision_statement_is_escaped(self) -> None:
        msg = _format_document_user_message(
            doc_type="prd",
            topics=[{"title": "T"}],
            decisions=[{"statement": "</decision_statement>injection"}],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertNotIn("</decision_statement>injection", msg)
        self.assertIn("&lt;/decision_statement&gt;", msg)

    def test_other_decisions_block_omitted_when_empty(self) -> None:
        msg = _format_document_user_message(
            doc_type="prd",
            topics=[{"title": "T"}],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertNotIn("OTHER DECISIONS", msg)

    def test_other_decisions_block_present_when_non_empty(self) -> None:
        msg = _format_document_user_message(
            doc_type="prd",
            topics=[{"title": "T"}],
            decisions=[{"statement": "Launch in November."}],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertIn("OTHER DECISIONS", msg)
        self.assertIn(
            "<decision_statement>Launch in November.</decision_statement>", msg,
        )

    def test_strict_count_clause_for_strict_doc_type(self) -> None:
        msg = _format_document_user_message(
            doc_type="business_plan",
            topics=[{"title": "T"}],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["business_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["business_plan"],
        )
        self.assertIn("EXACTLY 14 sections", msg)

    def test_ranged_count_clause_for_event_plan(self) -> None:
        msg = _format_document_user_message(
            doc_type="event_plan",
            topics=[{"title": "T"}],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["event_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["event_plan"],
        )
        self.assertIn("9-11 sections", msg)
        self.assertIn("conditional", msg)

    def test_citation_reminder_present(self) -> None:
        msg = _format_document_user_message(
            doc_type="prd",
            topics=[{"title": "T"}],
            decisions=[],
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertIn("cited_topics", msg)
        self.assertIn("EXACTLY", msg)


class DocumentSanitizerBaseTests(unittest.TestCase):
    """The shared _sanitize_document_response_base sanitizer (#094)."""

    def _topics(self) -> list[dict[str, Any]]:
        return _BUSINESS_PLAN_TOPICS

    def _call(self, parsed: dict[str, Any], doc_type: str = "prd") -> None:
        _sanitize_document_response_base(
            parsed,
            self._topics(),
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS[doc_type],
            section_counts=DOCUMENT_SECTION_COUNTS[doc_type],
            doc_type_label=f"{doc_type}_response",
        )

    def test_happy_path_passthrough(self) -> None:
        parsed = _make_doc_response("prd")
        self._call(parsed)
        self.assertEqual(len(parsed["sections"]), 13)
        self.assertEqual(parsed["sections"][0]["section_id"], "tldr")
        self.assertEqual(parsed["_sanitize"]["doc_type"], "prd_response")

    def test_drops_ghost_section_ids(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections.append(_make_doc_section("not_a_real_section"))
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertEqual(len(parsed["sections"]), 13)
        self.assertIn("not_a_real_section", parsed["_sanitize"]["dropped_ghost_sections"])

    def test_reorders_sections_to_canonical_sequence(self) -> None:
        canonical = DOCUMENT_CANONICAL_SECTIONS["prd"]
        # Reverse the canonical order.
        sections = [_make_doc_section(sid) for sid in reversed(canonical)]
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        result_order = [s["section_id"] for s in parsed["sections"]]
        self.assertEqual(result_order, list(canonical))
        self.assertTrue(parsed["_sanitize"]["reordered"])

    def test_no_reorder_log_when_already_canonical(self) -> None:
        parsed = _make_doc_response("prd")
        self._call(parsed)
        self.assertFalse(parsed["_sanitize"]["reordered"])

    def test_drops_duplicate_section_id_keeps_first(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        # Duplicate the first.
        sections.append(_make_doc_section("tldr", title="DUPLICATE"))
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        # The duplicate is logged.
        ghosts = parsed["_sanitize"]["dropped_ghost_sections"]
        self.assertIn("tldr (duplicate)", ghosts)
        # And the first wins.
        first_section = next(s for s in parsed["sections"] if s["section_id"] == "tldr")
        self.assertNotEqual(first_section["title"], "DUPLICATE")

    def test_clamps_oversized_prose_at_paragraph_boundary(self) -> None:
        long_prose = "A first paragraph.\n\n" + ("Body. " * 800)  # ~4800 chars
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["prose_markdown"] = long_prose
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertLessEqual(
            len(parsed["sections"][0]["prose_markdown"]),
            _DOCUMENT_PROSE_MAX_CHARS,
        )
        first_log = parsed["_sanitize"]["per_section"]["tldr"]
        self.assertIsNotNone(first_log["prose_truncated"])

    def test_escapes_raw_html_in_prose(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["prose_markdown"] = '<script>alert("x")</script>\n\nSecond.'
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        first = parsed["sections"][0]
        self.assertNotIn("<script>", first["prose_markdown"])
        self.assertIn("&lt;script&gt;", first["prose_markdown"])
        self.assertTrue(parsed["_sanitize"]["per_section"]["tldr"]["prose_html_escaped"])

    def test_preserves_html_inside_code_fences(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["prose_markdown"] = "Text.\n\n```\n<div>hi</div>\n```\n\nDone."
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertIn("<div>hi</div>", parsed["sections"][0]["prose_markdown"])

    def test_drops_ghost_cited_topics_per_section(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["cited_topics"] = ["Audience", "Imaginary Topic", "Channels"]
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        first = parsed["sections"][0]
        self.assertEqual(first["cited_topics"], ["Audience", "Channels"])
        log = parsed["_sanitize"]["per_section"]["tldr"]
        self.assertIn("Imaginary Topic", log["dropped_ghost_citations"])

    def test_dedupes_cited_topics_per_section(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["cited_topics"] = ["Audience", "audience", "Audience  "]
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertEqual(parsed["sections"][0]["cited_topics"], ["Audience"])

    def test_clamps_oversized_key_points_array(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["key_points"] = [f"Point {i}" for i in range(8)]
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertEqual(len(parsed["sections"][0]["key_points"]), 5)

    def test_trims_oversized_individual_key_point(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["key_points"] = ["x" * 200, "Ok point"]
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertEqual(
            len(parsed["sections"][0]["key_points"][0]),
            _DOCUMENT_KEY_POINT_MAX_CHARS,
        )

    def test_accepts_zero_key_points(self) -> None:
        """Cover/references-style sections legitimately have 0 key_points."""
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["key_points"] = []
        parsed = _make_doc_response("prd", sections=sections)
        # Should NOT raise — zero is valid.
        self._call(parsed)
        self.assertEqual(parsed["sections"][0]["key_points"], [])

    def test_trims_oversized_section_title(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["title"] = "X" * 200
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertLessEqual(
            len(parsed["sections"][0]["title"]),
            _DOCUMENT_SECTION_TITLE_MAX_CHARS,
        )

    def test_raises_on_section_count_below_min(self) -> None:
        sections = [_make_doc_section(sid)
                    for sid in DOCUMENT_CANONICAL_SECTIONS["prd"][:5]]  # only 5
        parsed = _make_doc_response("prd", sections=sections)
        with self.assertRaisesRegex(RuntimeError, r"kept only \d+ sections"):
            self._call(parsed)

    def test_clips_section_count_above_max_for_strict_doc_type(self) -> None:
        # PRD is strict: minItems == maxItems == 13. Provide 13 valid + a
        # ghost; the ghost is dropped, which is the standard path. To hit
        # the maxItems clip directly we'd need duplicates that survive —
        # not possible since we drop duplicates. So this test just verifies
        # the max-bound enforcement doesn't break on the canonical happy
        # path.
        parsed = _make_doc_response("prd")
        self._call(parsed)
        self.assertEqual(len(parsed["sections"]), 13)

    def test_raises_on_empty_sections_array(self) -> None:
        parsed = {"sections": []}
        with self.assertRaisesRegex(RuntimeError, "is empty"):
            self._call(parsed)

    def test_raises_on_non_list_sections(self) -> None:
        parsed = {"sections": "not a list"}
        with self.assertRaisesRegex(RuntimeError, "must be a list"):
            self._call(parsed)

    def test_raises_on_section_with_empty_prose(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        sections[0]["prose_markdown"] = ""
        parsed = _make_doc_response("prd", sections=sections)
        with self.assertRaisesRegex(RuntimeError, "prose_markdown must be a non-empty string"):
            self._call(parsed)

    def test_treats_missing_cited_topics_as_empty(self) -> None:
        sections = [_make_doc_section(sid) for sid in DOCUMENT_CANONICAL_SECTIONS["prd"]]
        del sections[0]["cited_topics"]
        parsed = _make_doc_response("prd", sections=sections)
        self._call(parsed)
        self.assertEqual(parsed["sections"][0]["cited_topics"], [])

    def test_attaches_per_section_repair_log(self) -> None:
        parsed = _make_doc_response("prd")
        self._call(parsed)
        self.assertIn("_sanitize", parsed)
        self.assertIn("per_section", parsed["_sanitize"])
        self.assertEqual(
            set(parsed["_sanitize"]["per_section"]),
            set(DOCUMENT_CANONICAL_SECTIONS["prd"]),
        )


class DocumentDispatchTests(unittest.TestCase):
    """The 7 public methods dispatch to _generate_document correctly (#094)."""

    def _canned_response(self, doc_type: str) -> Any:
        tool_call = SimpleNamespace(
            function=SimpleNamespace(
                name=f"{doc_type}_response",
                arguments=json.dumps(_make_doc_response(doc_type)),
            )
        )
        message = SimpleNamespace(content=None, tool_calls=[tool_call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def _dispatch(self, doc_type: str, **kwargs: Any) -> tuple[dict[str, Any], Any]:
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = self._canned_response(doc_type)
        adapter = OpenAIPlanningInterviewer(client=fake_client)
        method = getattr(adapter, doc_type)
        result = method(
            topics=_BUSINESS_PLAN_TOPICS,
            decisions=None,
            domain="general",
            locale=None,
            project_title="Demo",
            **kwargs,
        )
        return result, fake_client

    def test_all_7_methods_pin_gpt55(self) -> None:
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                self.assertEqual(kwargs["model"], MODEL_BUSINESS_PLAN)
                self.assertEqual(kwargs["model"], "gpt-5.5")

    def test_all_7_methods_pin_timeout_120s(self) -> None:
        # Bumped 60 → 120 in #096 fix: gpt-5.5 14-section docs
        # legitimately exceed 60s on cold-start; with OpenAI(max_retries=0)
        # and per-call max_empty_toolcall_retries=0, this is now the hard
        # per-call ceiling, not just a per-attempt knob.
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                self.assertEqual(kwargs["timeout"], TIMEOUT_DOCUMENT_S)
                self.assertEqual(kwargs["timeout"], 120.0)

    def test_all_7_methods_force_correct_tool_name(self) -> None:
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                self.assertEqual(
                    kwargs["tool_choice"],
                    {"type": "function",
                     "function": {"name": f"{doc_type}_response"}},
                )

    def test_default_openai_client_pins_max_retries_zero(self) -> None:
        """#096 regression: the default-constructed OpenAI() client used by
        OpenAIPlanningInterviewer must pass max_retries=0 to disable the
        SDK's internal retry layer (default max_retries=2 → 3 attempts).
        We own the retry policy via _breakered_create + circuit breaker;
        stacking the SDK's hidden retry on top multiplied per-attempt
        timeouts and produced 6+ minute hangs observed in production
        (one document run sat through 3 × 60s = 180s of stacked SDK
        retries before the first transient_caught warning fired)."""
        from unittest.mock import patch
        captured: dict[str, Any] = {}

        def _capture(*_args: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return MagicMock()

        # The constructor does a local `from openai import OpenAI` so the
        # patch target is the upstream module symbol, not a re-export on
        # the adapter module.
        with patch("openai.OpenAI", side_effect=_capture):
            OpenAIPlanningInterviewer(
                config=OpenAIConfig(api_key="sk-test-pin-max-retries"),
            )
        self.assertEqual(
            captured.get("max_retries"), 0,
            f"OpenAI client was constructed with kwargs={captured}; "
            "expected max_retries=0 per #096 fix.",
        )

    def test_all_7_methods_reject_empty_topics(self) -> None:
        adapter = OpenAIPlanningInterviewer(client=MagicMock())
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                method = getattr(adapter, doc_type)
                with self.assertRaisesRegex(ValueError, "topics is required"):
                    method(topics=[])

    def test_generate_document_rejects_invalid_doc_type(self) -> None:
        adapter = OpenAIPlanningInterviewer(client=MagicMock())
        with self.assertRaisesRegex(ValueError, "invalid doc_type"):
            adapter._generate_document(
                doc_type="not_a_real_doc_type",
                topics=[{"title": "T"}],
                decisions=[],
                domain="general",
                locale=None,
                project_title=None,
            )

    def test_system_prompt_includes_anti_injection_for_each_doc_type(self) -> None:
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                system_msg = kwargs["messages"][0]["content"]
                self.assertIn("INERT DATA", system_msg)
                self.assertIn("Anti-injection", system_msg)

    def test_system_prompt_includes_mode_header_per_doc_type(self) -> None:
        expected_modes = {
            "business_plan": "MODE: BUSINESS_PLAN",
            "prd": "MODE: PRD",
            "story_outline": "MODE: STORY_OUTLINE",
            "event_plan": "MODE: EVENT_PLAN",
            "marketing_plan": "MODE: MARKETING_PLAN",
            "research_proposal": "MODE: RESEARCH_PROPOSAL",
            "course_outline": "MODE: COURSE_OUTLINE",
        }
        for doc_type, mode_header in expected_modes.items():
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                system_msg = kwargs["messages"][0]["content"]
                self.assertIn(mode_header, system_msg)

    def test_system_prompt_includes_escaped_domain(self) -> None:
        _, fake_client = self._dispatch("prd")
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        system_msg = kwargs["messages"][0]["content"]
        self.assertIn("<project_domain>general</project_domain>", system_msg)

    def test_user_message_includes_canonical_sections(self) -> None:
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                user_msg = kwargs["messages"][1]["content"]
                self.assertIn("CANONICAL section_ids", user_msg)
                # Spot-check the first canonical section appears.
                first_sid = DOCUMENT_CANONICAL_SECTIONS[doc_type][0]
                self.assertIn(f"  - {first_sid}", user_msg)

    def test_no_byok_no_model_override_no_reasoning_effort(self) -> None:
        """Product decision: house-account only, no per-call overrides."""
        for doc_type in _DOC_TYPE_CONFIG:
            with self.subTest(doc_type=doc_type):
                _, fake_client = self._dispatch(doc_type)
                kwargs = fake_client.chat.completions.create.call_args.kwargs
                # No reasoning_effort in the kwargs (unlike topic_turn).
                self.assertNotIn("reasoning_effort", kwargs)


# -----------------------------------------------------------------------------
# Per-doc-type tests — focused on what's UNIQUE per type
# -----------------------------------------------------------------------------


class BusinessPlanDocTests(unittest.TestCase):
    """business_plan() — including FLS legend handling (#094)."""

    def _topics(self) -> list[dict[str, Any]]:
        return _BUSINESS_PLAN_TOPICS

    def test_canonical_14_sections_happy_path(self) -> None:
        parsed = _make_doc_response("business_plan")
        # Cover section already needs the FLS marker for a clean happy path.
        for s in parsed["sections"]:
            if s["section_id"] == "cover":
                s["prose_markdown"] = (
                    "Cover prose.\n\n"
                    "Forward-looking statements (FLS): standard legend text."
                )
        _sanitize_business_plan_response(
            parsed, self._topics(),
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["business_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["business_plan"],
        )
        self.assertEqual(len(parsed["sections"]), 14)
        self.assertEqual(parsed["sections"][0]["section_id"], "cover")

    def test_fls_legend_appended_when_marker_missing(self) -> None:
        parsed = _make_doc_response("business_plan")
        # Default cover prose has NO FLS marker.
        _sanitize_business_plan_response(
            parsed, self._topics(),
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["business_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["business_plan"],
        )
        self.assertTrue(parsed["_sanitize"]["fls_legend_appended"])
        cover = parsed["sections"][0]
        self.assertIn(_FLS_LEGEND_MARKER, cover["prose_markdown"])

    def test_fls_legend_not_appended_when_marker_present(self) -> None:
        parsed = _make_doc_response("business_plan")
        for s in parsed["sections"]:
            if s["section_id"] == "cover":
                s["prose_markdown"] = (
                    "Cover prose.\n\n"
                    "Forward-looking statements (FLS): tailored legend already in place."
                )
        _sanitize_business_plan_response(
            parsed, self._topics(),
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["business_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["business_plan"],
        )
        self.assertFalse(parsed["_sanitize"]["fls_legend_appended"])

    def test_fls_legend_helper_no_op_when_cover_missing(self) -> None:
        """Defensive: _ensure_fls_legend_in_cover handles missing cover gracefully."""
        parsed = {"sections": [], "_sanitize": {}}
        _ensure_fls_legend_in_cover(parsed)
        self.assertFalse(parsed["_sanitize"]["fls_legend_appended"])

    def test_business_plan_prompt_includes_4_beat_competition(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["business_plan"]
        self.assertIn("4-beat", prompt)
        self.assertIn("competition", prompt.lower())

    def test_business_plan_prompt_forbids_unsourced_superlatives(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["business_plan"]
        self.assertIn("revolutionary", prompt)
        self.assertIn("world-class", prompt)
        self.assertIn("Forbidden", prompt)

    def test_business_plan_prompt_specifies_third_person_voice(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["business_plan"]
        self.assertIn("Third-person institutional", prompt)
        self.assertIn("the Company", prompt)

    def test_business_plan_canonical_first_section_is_cover(self) -> None:
        self.assertEqual(DOCUMENT_CANONICAL_SECTIONS["business_plan"][0], "cover")

    def test_business_plan_canonical_includes_competition_section(self) -> None:
        self.assertIn("competition", DOCUMENT_CANONICAL_SECTIONS["business_plan"])

    def test_business_plan_canonical_includes_risk_section(self) -> None:
        self.assertIn("risk", DOCUMENT_CANONICAL_SECTIONS["business_plan"])

    def test_raises_on_count_below_14(self) -> None:
        sections = [_make_doc_section(sid)
                    for sid in DOCUMENT_CANONICAL_SECTIONS["business_plan"][:10]]
        parsed = _make_doc_response("business_plan", sections=sections)
        with self.assertRaisesRegex(RuntimeError, r"kept only 10 sections"):
            _sanitize_business_plan_response(
                parsed, self._topics(),
                canonical_sections=DOCUMENT_CANONICAL_SECTIONS["business_plan"],
                section_counts=DOCUMENT_SECTION_COUNTS["business_plan"],
            )


class PrdDocTests(unittest.TestCase):
    """prd() — Cagan-style, problem-led, SMART metrics (#094)."""

    def test_canonical_13_sections_happy_path(self) -> None:
        parsed = _make_doc_response("prd")
        _sanitize_prd_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["prd"],
            section_counts=DOCUMENT_SECTION_COUNTS["prd"],
        )
        self.assertEqual(len(parsed["sections"]), 13)

    def test_first_section_is_tldr(self) -> None:
        self.assertEqual(DOCUMENT_CANONICAL_SECTIONS["prd"][0], "tldr")

    def test_canonical_includes_out_of_scope(self) -> None:
        """Without out_of_scope, scope creep is structural."""
        self.assertIn("out_of_scope", DOCUMENT_CANONICAL_SECTIONS["prd"])

    def test_canonical_includes_success_metrics(self) -> None:
        self.assertIn("success_metrics", DOCUMENT_CANONICAL_SECTIONS["prd"])

    def test_prompt_specifies_problem_led(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["prd"]
        self.assertIn("Problem-led", prompt)
        # "NOT" wraps to a new line in the source; check both pieces.
        self.assertIn("feature-led", prompt)

    def test_prompt_specifies_smart_metrics(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["prd"]
        self.assertIn("SMART", prompt)
        self.assertIn("counter-metric", prompt)

    def test_prompt_forbids_design_tbd(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["prd"]
        self.assertIn('"Design TBD"', prompt)


class StoryOutlineDocTests(unittest.TestCase):
    """story_outline() — logline + theme + single-spine (#094)."""

    def test_canonical_9_sections_happy_path(self) -> None:
        parsed = _make_doc_response("story_outline")
        _sanitize_story_outline_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["story_outline"],
            section_counts=DOCUMENT_SECTION_COUNTS["story_outline"],
        )
        self.assertEqual(len(parsed["sections"]), 9)

    def test_first_section_is_logline(self) -> None:
        self.assertEqual(DOCUMENT_CANONICAL_SECTIONS["story_outline"][0], "logline")

    def test_canonical_includes_theme(self) -> None:
        self.assertIn("theme", DOCUMENT_CANONICAL_SECTIONS["story_outline"])

    def test_canonical_includes_scene_list(self) -> None:
        self.assertIn("scene_list", DOCUMENT_CANONICAL_SECTIONS["story_outline"])

    def test_prompt_specifies_august_formula(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["story_outline"]
        self.assertIn("August's formula", prompt)
        self.assertIn("antagonist", prompt)

    def test_prompt_forbids_blending_spines(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["story_outline"]
        self.assertIn("DO NOT BLEND", prompt)

    def test_prompt_distinguishes_outline_from_prose_voice(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["story_outline"]
        self.assertIn("NOT prose fiction", prompt)


class EventPlanDocTests(unittest.TestCase):
    """event_plan() — conditional sections, run-of-show table (#094)."""

    def test_full_11_section_happy_path(self) -> None:
        parsed = _make_doc_response("event_plan")
        _sanitize_event_plan_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["event_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["event_plan"],
        )
        self.assertEqual(len(parsed["sections"]), 11)

    def test_wedding_9_section_subset_happy_path(self) -> None:
        """Wedding signal: omit marketing_ticketing + sponsorship."""
        canonical = DOCUMENT_CANONICAL_SECTIONS["event_plan"]
        wedding_sections = [
            _make_doc_section(sid) for sid in canonical
            if sid not in ("marketing_ticketing", "sponsorship")
        ]
        self.assertEqual(len(wedding_sections), 9)
        parsed = _make_doc_response("event_plan", sections=wedding_sections)
        _sanitize_event_plan_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=canonical,
            section_counts=DOCUMENT_SECTION_COUNTS["event_plan"],
        )
        self.assertEqual(len(parsed["sections"]), 9)
        result_ids = {s["section_id"] for s in parsed["sections"]}
        self.assertNotIn("marketing_ticketing", result_ids)
        self.assertNotIn("sponsorship", result_ids)

    def test_partial_subset_with_only_marketing_omitted(self) -> None:
        """Public conference signal: marketing_ticketing in, sponsorship out."""
        canonical = DOCUMENT_CANONICAL_SECTIONS["event_plan"]
        sections = [
            _make_doc_section(sid) for sid in canonical
            if sid != "sponsorship"
        ]
        self.assertEqual(len(sections), 10)
        parsed = _make_doc_response("event_plan", sections=sections)
        _sanitize_event_plan_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=canonical,
            section_counts=DOCUMENT_SECTION_COUNTS["event_plan"],
        )
        self.assertEqual(len(parsed["sections"]), 10)

    def test_raises_when_under_9_sections(self) -> None:
        canonical = DOCUMENT_CANONICAL_SECTIONS["event_plan"]
        sections = [_make_doc_section(sid) for sid in canonical[:8]]
        parsed = _make_doc_response("event_plan", sections=sections)
        with self.assertRaisesRegex(RuntimeError, r"kept only 8 sections"):
            _sanitize_event_plan_response(
                parsed, _BUSINESS_PLAN_TOPICS,
                canonical_sections=canonical,
                section_counts=DOCUMENT_SECTION_COUNTS["event_plan"],
            )

    def test_first_section_is_overview(self) -> None:
        self.assertEqual(DOCUMENT_CANONICAL_SECTIONS["event_plan"][0], "overview")

    def test_canonical_includes_run_of_show(self) -> None:
        self.assertIn("run_of_show", DOCUMENT_CANONICAL_SECTIONS["event_plan"])

    def test_canonical_includes_safety_permits(self) -> None:
        self.assertIn(
            "safety_permits_insurance",
            DOCUMENT_CANONICAL_SECTIONS["event_plan"],
        )

    def test_prompt_specifies_run_of_show_table_with_contingency(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["event_plan"]
        self.assertIn("CONTINGENCY", prompt)
        self.assertIn("run_of_show", prompt)

    def test_prompt_lists_conditional_signals(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["event_plan"]
        self.assertIn("Wedding", prompt)
        self.assertIn("Fundraiser", prompt)
        self.assertIn("OMIT", prompt)


class MarketingPlanDocTests(unittest.TestCase):
    """marketing_plan() — Dunford 5-component, PESO matrix (#094)."""

    def test_canonical_12_sections_happy_path(self) -> None:
        parsed = _make_doc_response("marketing_plan")
        _sanitize_marketing_plan_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["marketing_plan"],
            section_counts=DOCUMENT_SECTION_COUNTS["marketing_plan"],
        )
        self.assertEqual(len(parsed["sections"]), 12)

    def test_canonical_includes_positioning(self) -> None:
        self.assertIn("positioning", DOCUMENT_CANONICAL_SECTIONS["marketing_plan"])

    def test_canonical_includes_channel_strategy(self) -> None:
        self.assertIn("channel_strategy", DOCUMENT_CANONICAL_SECTIONS["marketing_plan"])

    def test_canonical_includes_measurement(self) -> None:
        self.assertIn("measurement", DOCUMENT_CANONICAL_SECTIONS["marketing_plan"])

    def test_prompt_specifies_dunford_5_component(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["marketing_plan"]
        self.assertIn("Dunford", prompt)
        self.assertIn("5-component", prompt)

    def test_prompt_specifies_peso_matrix(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["marketing_plan"]
        self.assertIn("PESO", prompt)

    def test_prompt_addresses_ceo_cmo_metrics_gap(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["marketing_plan"]
        self.assertIn("revenue", prompt.lower())


class ResearchProposalDocTests(unittest.TestCase):
    """research_proposal() — methodology-heavy, NSF/NIH variants (#094)."""

    def test_canonical_10_sections_happy_path(self) -> None:
        parsed = _make_doc_response("research_proposal")
        _sanitize_research_proposal_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["research_proposal"],
            section_counts=DOCUMENT_SECTION_COUNTS["research_proposal"],
        )
        self.assertEqual(len(parsed["sections"]), 10)

    def test_canonical_includes_methodology(self) -> None:
        self.assertIn("methodology", DOCUMENT_CANONICAL_SECTIONS["research_proposal"])

    def test_canonical_includes_significance(self) -> None:
        self.assertIn("significance", DOCUMENT_CANONICAL_SECTIONS["research_proposal"])

    def test_prompt_specifies_methodology_subsections(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["research_proposal"]
        self.assertIn("research design", prompt)
        self.assertIn("data sources", prompt)
        self.assertIn("sampling", prompt)
        self.assertIn("validity threats", prompt)

    def test_prompt_specifies_synthetic_thematic_review(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["research_proposal"]
        self.assertIn("SYNTHETIC-THEMATIC", prompt)
        self.assertIn("NOT", prompt)
        self.assertIn("chronological", prompt)

    def test_prompt_mentions_nsf_and_nih_variants(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["research_proposal"]
        self.assertIn("NSF", prompt)
        self.assertIn("NIH", prompt)


class CourseOutlineDocTests(unittest.TestCase):
    """course_outline() — conditional sections, Bloom's verbs (#094)."""

    def test_full_13_section_happy_path(self) -> None:
        parsed = _make_doc_response("course_outline")
        _sanitize_course_outline_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=DOCUMENT_CANONICAL_SECTIONS["course_outline"],
            section_counts=DOCUMENT_SECTION_COUNTS["course_outline"],
        )
        self.assertEqual(len(parsed["sections"]), 13)

    def test_self_paced_marketplace_subset_omits_support_community(self) -> None:
        """Udemy-style: omit support_community."""
        canonical = DOCUMENT_CANONICAL_SECTIONS["course_outline"]
        sections = [
            _make_doc_section(sid) for sid in canonical
            if sid != "support_community"
        ]
        self.assertEqual(len(sections), 12)
        parsed = _make_doc_response("course_outline", sections=sections)
        _sanitize_course_outline_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=canonical,
            section_counts=DOCUMENT_SECTION_COUNTS["course_outline"],
        )
        self.assertEqual(len(parsed["sections"]), 12)
        result_ids = {s["section_id"] for s in parsed["sections"]}
        self.assertNotIn("support_community", result_ids)

    def test_in_person_seminar_subset_omits_tech_requirements(self) -> None:
        """In-person academic seminar with no online: omit tech_requirements."""
        canonical = DOCUMENT_CANONICAL_SECTIONS["course_outline"]
        sections = [
            _make_doc_section(sid) for sid in canonical
            if sid != "tech_requirements"
        ]
        self.assertEqual(len(sections), 12)
        parsed = _make_doc_response("course_outline", sections=sections)
        _sanitize_course_outline_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=canonical,
            section_counts=DOCUMENT_SECTION_COUNTS["course_outline"],
        )
        self.assertEqual(len(parsed["sections"]), 12)

    def test_minimum_11_section_subset_omits_both(self) -> None:
        """One-shot keynote: omit both tech_requirements and support_community."""
        canonical = DOCUMENT_CANONICAL_SECTIONS["course_outline"]
        sections = [
            _make_doc_section(sid) for sid in canonical
            if sid not in ("tech_requirements", "support_community")
        ]
        self.assertEqual(len(sections), 11)
        parsed = _make_doc_response("course_outline", sections=sections)
        _sanitize_course_outline_response(
            parsed, _BUSINESS_PLAN_TOPICS,
            canonical_sections=canonical,
            section_counts=DOCUMENT_SECTION_COUNTS["course_outline"],
        )
        self.assertEqual(len(parsed["sections"]), 11)

    def test_raises_when_under_11_sections(self) -> None:
        canonical = DOCUMENT_CANONICAL_SECTIONS["course_outline"]
        sections = [_make_doc_section(sid) for sid in canonical[:10]]
        parsed = _make_doc_response("course_outline", sections=sections)
        with self.assertRaisesRegex(RuntimeError, r"kept only 10 sections"):
            _sanitize_course_outline_response(
                parsed, _BUSINESS_PLAN_TOPICS,
                canonical_sections=canonical,
                section_counts=DOCUMENT_SECTION_COUNTS["course_outline"],
            )

    def test_canonical_includes_learning_outcomes(self) -> None:
        self.assertIn(
            "learning_outcomes",
            DOCUMENT_CANONICAL_SECTIONS["course_outline"],
        )

    def test_canonical_includes_module_breakdown(self) -> None:
        self.assertIn(
            "module_breakdown",
            DOCUMENT_CANONICAL_SECTIONS["course_outline"],
        )

    def test_prompt_specifies_blooms_verbs(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["course_outline"]
        self.assertIn("Bloom", prompt)
        self.assertIn("Apply", prompt)
        self.assertIn("Analyze", prompt)
        self.assertIn("Create", prompt)

    def test_prompt_forbids_unmeasurable_verbs(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["course_outline"]
        self.assertIn("FORBIDDEN", prompt)
        self.assertIn("understand", prompt)
        self.assertIn("appreciate", prompt)

    def test_prompt_specifies_backward_design(self) -> None:
        prompt = DOCUMENT_MODE_PROMPTS["course_outline"]
        # "UbD" is Understanding-by-Design, the canonical name for the
        # backward-design framework taught in instructional-design programs.
        self.assertIn("UbD", prompt)


class RepairOneDocSectionTests(unittest.TestCase):
    """The shared per-section repair helper (#094)."""

    def _valid_titles(self) -> dict[str, str]:
        return {"audience": "Audience", "channels": "Channels"}

    def test_happy_path(self) -> None:
        section = _make_doc_section("tldr", cited_topics=["Audience"])
        cleaned, log = _repair_one_doc_section(
            section, "tldr", self._valid_titles(),
        )
        self.assertEqual(cleaned["section_id"], "tldr")
        self.assertEqual(cleaned["cited_topics"], ["Audience"])
        self.assertIsNone(log["prose_truncated"])

    def test_section_id_overrides_input(self) -> None:
        """Section_id is supplied by sanitizer; helper trusts it."""
        section = _make_doc_section("tldr")
        section["section_id"] = "WRONG"  # adversarial
        cleaned, _ = _repair_one_doc_section(
            section, "tldr", self._valid_titles(),
        )
        self.assertEqual(cleaned["section_id"], "tldr")

    def test_raises_on_empty_prose(self) -> None:
        section = _make_doc_section("tldr", prose="")
        with self.assertRaisesRegex(RuntimeError, "prose_markdown"):
            _repair_one_doc_section(section, "tldr", self._valid_titles())

    def test_raises_on_non_string_prose(self) -> None:
        section = _make_doc_section("tldr")
        section["prose_markdown"] = 12345  # type: ignore[assignment]
        with self.assertRaisesRegex(RuntimeError, "prose_markdown"):
            _repair_one_doc_section(section, "tldr", self._valid_titles())


# =============================================================================
# Live integration test — only runs when OPENAI_API_KEY is set.
# =============================================================================


@unittest.skipUnless(
    os.environ.get("OPENAI_API_KEY"),
    "OPENAI_API_KEY not set — skipping live API test. Set it to exercise the real path.",
)
class LiveKickoffIntegrationTests(unittest.TestCase):
    """Actually call OpenAI. Proves the spec is coherent end-to-end.

    Budget: one call per test, ~$0.0002 on gpt-4o-mini. Skip the class during
    rapid iteration if you prefer (``-k 'not Live'``).
    """

    def test_live_kickoff_produces_valid_topic_set(self) -> None:
        adapter = OpenAIPlanningInterviewer(
            config=OpenAIConfig(
                # Default matches production. Override with INSPIRA_TEST_MODEL
                # if you want to validate against a different model.
                model=os.environ.get("INSPIRA_TEST_MODEL", "gpt-4o-mini"),
            )
        )

        result = adapter.kickoff(
            user_idea=(
                "I'm planning a small outdoor wine festival on a Saturday in early October. "
                "Local vineyards, 500–800 guests, families welcome, on the riverside park. "
                "Budget around $50k all-in. Never run one of these before."
            )
        )

        self.assertIn(result["domain"], DOMAIN_ENUM)
        # Event-planning idea should classify as event (or adjacent).
        self.assertIn(result["domain"], ("event", "campaign", "personal"))

        self.assertIsNone(result["clarifying_question_if_too_vague"])
        self.assertGreaterEqual(len(result["topics"]), 5)
        self.assertLessEqual(len(result["topics"]), 10)

        titles = {t["title"] for t in result["topics"]}
        self.assertIn(result["suggested_first_topic"], titles)

        for topic in result["topics"]:
            self.assertIn(topic["icon"], CURATED_ICONS)
            self.assertTrue(topic["title"])
            self.assertTrue(topic["why_this_topic"])


@unittest.skipUnless(
    os.environ.get("OPENAI_API_KEY"),
    "OPENAI_API_KEY not set — skipping live API test.",
)
class LiveTopicTurnIntegrationTests(unittest.TestCase):
    """Actually call OpenAI with a topic-turn scenario. Budget: ~$0.001 per run."""

    def test_live_topic_turn_produces_valid_ask_turn(self) -> None:
        adapter = OpenAIPlanningInterviewer(
            config=OpenAIConfig(
                model=os.environ.get("INSPIRA_TEST_MODEL", "gpt-4o-mini"),
            )
        )

        # Scenario: a wine-festival project, Budget topic is open, user just
        # said they have $50k cap. Planner should probe deeper.
        current_topic = {
            "title": "Budget",
            "icon": "chart",
            "decisions": [],
            "turns": [
                {
                    "turn_id": "T1", "role": "planner",
                    "body": "What's the hard cap on spend, and what's non-negotiable?",
                    "why_this_matters": "Everything else depends on this number.",
                    "action": "ask", "status": "answered",
                },
                {
                    "turn_id": "T2", "role": "user",
                    "body": (
                        "Hard cap is $50k all-in, including a small contingency. "
                        "The venue and insurance are non-negotiable; we can cut music if we need to."
                    ),
                    "status": "answered",
                },
            ],
            "open_questions": [],
            "risks_assumptions": [],
        }
        other_topics = [
            {
                "title": "Audience",
                "decisions": [
                    {"decision_id": "D1", "statement": "Target 500–800 guests, families welcome."},
                ],
            },
            {
                "title": "Venue & logistics",
                "decisions": [
                    {"decision_id": "D2", "statement": "Riverside park, one day only."},
                ],
            },
        ]

        result = adapter.topic_turn(
            current_topic=current_topic,
            other_topics=other_topics,
        )

        # Action must be one of the valid options
        self.assertIn(result["action"], ("ask", "pressure_test", "followup", "suggest_close"))

        if result["action"] in ("ask", "pressure_test", "followup"):
            self.assertTrue(result["question"])
            self.assertTrue(result["why_this_matters"])
            # Up to 3 suggested responses, each a reasonable sentence
            self.assertLessEqual(len(result["suggested_responses"]), 3)

        # Sanitize log present
        self.assertIn("_sanitize", result)


if __name__ == "__main__":
    unittest.main()
