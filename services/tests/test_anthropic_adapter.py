"""Tests for the Anthropic Claude planner adapter.

Covers three concerns:

1. **Happy path** — ``topic_turn`` against a mocked Anthropic client
   returns a dict whose shape matches the OpenAI adapter's shape.
2. **Missing API key** — ``ClaudePlanningInterviewer()`` fails fast with a
   clear error. The API layer (``api.py:_get_claude_adapter``) catches
   that failure and falls back to OpenAI; we exercise the fallback at the
   tier-dispatcher level via ``tier_to_adapter``.
3. **Malformed response sanitizer safety net** — a response whose
   ``consistency_flags`` / ``proposed_decisions`` reference ghost topics
   gets repaired silently (not raised) so one flaky model output doesn't
   break a turn. Same contract as the OpenAI adapter.

We intentionally do NOT test the live Anthropic path here — those tests
live alongside ``test_openai_adapter.py::LiveKickoffIntegrationTests``
once a ``TEST_ANTHROPIC_LIVE=1`` escape hatch is added. This file stays
fully offline and deterministic.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

# Load repo-root .env before any env-dependent code runs. Matches the
# pattern in test_openai_adapter.py so a developer running these tests
# from a fresh shell picks up their ANTHROPIC_API_KEY without exporting
# it manually.
from planning_studio_service._env_bootstrap import ensure_loaded

ensure_loaded()

from planning_studio_service.agents import (
    ClaudeConfig,
    ClaudePlanningInterviewer,
    ModelTier,
    OpenAIPlanningInterviewer,
    tier_to_adapter,
    tier_to_claude_model,
)
from planning_studio_service.agents.claude_adapter import (
    DEFAULT_CLAUDE_MODEL,
    _build_claude_tool_spec,
    _extract_tool_use_args,
)
from planning_studio_service.billing import NoopBillingProvider

try:
    from ._helpers import (
        fake_kickoff_response,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )


# ---------------------------------------------------------------------------
# Fixtures — Anthropic response shapes.
# ---------------------------------------------------------------------------


def _fake_tool_use_response(*, tool_name: str, args: dict[str, Any]) -> Any:
    """Build a SimpleNamespace that quacks like an Anthropic ``Message``.

    Anthropic's response object exposes:
      ``.content`` — list of typed content blocks. The block of interest has
                     ``type == "tool_use"``, ``.name``, and ``.input``
                     (already-parsed dict, no JSON-string round-trip).
      ``.stop_reason`` / ``.usage`` — used by ``_extract_tool_use_args``
                     for diagnostic messages when the expected block is
                     absent.

    We mimic that minimally — just enough for the adapter to find the
    tool_use block and pull ``.input`` off it.
    """
    block = SimpleNamespace(type="tool_use", name=tool_name, input=args)
    return SimpleNamespace(
        content=[block],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


def _valid_topic_turn_args() -> dict[str, Any]:
    """A topic_turn tool_use input that passes every sanitizer check.

    Matches the shape the OpenAI adapter returns so callers at the API
    layer can't tell the two providers apart. Every field the schema
    requires is present, even when null — Claude's forced tool_choice
    emits the full object.
    """
    return {
        "action": "ask",
        "question": "Which line items are non-negotiable?",
        "why_this_matters": "Pre-deciding the cuts saves negotiation later.",
        "suggested_responses": [
            {"label": "Safety and insurance.", "intent": "conservative"},
            {"label": "Talent flexes first.", "intent": "ambitious"},
            {"label": "Let me think.", "intent": "defer"},
        ],
        "proposed_decisions": [],
        "consistency_flags": [],
        "new_topic_proposal": None,
        "topic_deletion_suggestion": None,
        "close_recommendation_reason": None,
        "conflict_resolution": None,
        "planned_checkpoints": None,
        "checkpoint_updates": None,
    }


# ---------------------------------------------------------------------------
# Construction / API-key handling.
# ---------------------------------------------------------------------------


class ConstructionTests(unittest.TestCase):
    """Adapter construction — env-key resolution + default model selection."""

    def setUp(self) -> None:
        # Snapshot env so per-test mutations don't leak.
        self._saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        self._saved_model = os.environ.pop("ANTHROPIC_MODEL", None)

    def tearDown(self) -> None:
        if self._saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._saved_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        if self._saved_model is not None:
            os.environ["ANTHROPIC_MODEL"] = self._saved_model
        else:
            os.environ.pop("ANTHROPIC_MODEL", None)

    def test_missing_api_key_raises_at_construction(self) -> None:
        """No key + no injected client → fail fast, not a mystery 401 later."""
        with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY not set"):
            ClaudePlanningInterviewer()

    def test_default_model_is_sonnet_4_5(self) -> None:
        """The frontier model pins to Claude Sonnet 4.5 by default."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-dummy"
        adapter = ClaudePlanningInterviewer(client=MagicMock())
        self.assertEqual(adapter.config.model, "claude-sonnet-4-5-20250929")
        self.assertEqual(DEFAULT_CLAUDE_MODEL, "claude-sonnet-4-5-20250929")

    def test_anthropic_model_env_overrides_default(self) -> None:
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-dummy"
        os.environ["ANTHROPIC_MODEL"] = "claude-opus-99"
        adapter = ClaudePlanningInterviewer(client=MagicMock())
        self.assertEqual(adapter.config.model, "claude-opus-99")

    def test_config_api_key_wins_over_env(self) -> None:
        """Explicit config key beats env — useful for tests and multi-tenant."""
        os.environ["ANTHROPIC_API_KEY"] = "from-env"
        adapter = ClaudePlanningInterviewer(
            config=ClaudeConfig(api_key="from-config"),
            client=MagicMock(),
        )
        # No direct getter for the key; we just assert construction succeeded.
        self.assertIsNotNone(adapter.client)


# ---------------------------------------------------------------------------
# Tool-spec envelope + response extraction helpers.
# ---------------------------------------------------------------------------


class ToolSpecTests(unittest.TestCase):
    """Claude's tool-definition envelope is flatter than OpenAI's."""

    def test_tool_spec_uses_flat_envelope(self) -> None:
        spec = _build_claude_tool_spec("topic_turn")
        self.assertEqual(spec["name"], "topic_turn")
        self.assertIn("description", spec)
        self.assertIn("input_schema", spec)
        # No ``function`` wrapper, no ``strict`` flag.
        self.assertNotIn("function", spec)
        self.assertNotIn("strict", spec)


class ToolUseExtractionTests(unittest.TestCase):
    """Parsing the ``tool_use`` block off a Claude response."""

    def test_extracts_tool_use_happy_path(self) -> None:
        response = _fake_tool_use_response(
            tool_name="topic_turn",
            args={"action": "ask"},
        )
        parsed = _extract_tool_use_args(response, expected_name="topic_turn")
        self.assertEqual(parsed, {"action": "ask"})

    def test_raises_on_wrong_tool_name(self) -> None:
        """Forced tool_choice pins the name — drift is a hard bug."""
        response = _fake_tool_use_response(
            tool_name="something_else",
            args={},
        )
        with self.assertRaisesRegex(RuntimeError, "Expected tool 'topic_turn'"):
            _extract_tool_use_args(response, expected_name="topic_turn")

    def test_raises_when_no_tool_use_block(self) -> None:
        """Missing tool_use → surface stop_reason + usage for diagnosis."""
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="I'd rather not.")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=5, output_tokens=10),
        )
        with self.assertRaisesRegex(RuntimeError, "Expected a tool_use block"):
            _extract_tool_use_args(response, expected_name="topic_turn")

    def test_raises_on_non_dict_input(self) -> None:
        block = SimpleNamespace(type="tool_use", name="topic_turn", input="not a dict")
        response = SimpleNamespace(content=[block], stop_reason="tool_use", usage=None)
        with self.assertRaisesRegex(RuntimeError, "non-dict input"):
            _extract_tool_use_args(response, expected_name="topic_turn")


# ---------------------------------------------------------------------------
# Happy-path end-to-end: topic_turn against a mocked client.
# ---------------------------------------------------------------------------


class TopicTurnHappyPathTests(unittest.TestCase):
    """End-to-end ``topic_turn`` call with a mocked Anthropic client.

    Verifies:
    - Returned dict has the same shape as the OpenAI adapter.
    - Claude-specific call kwargs (tool_choice envelope, system kwarg,
      max_tokens) are correctly assembled.
    - ``model_override`` pins the per-call model without mutating config.
    """

    def _make_adapter(self, client: Any, *, model: str | None = None) -> ClaudePlanningInterviewer:
        """Construct the adapter with a dummy config key.

        The tests pass the API key via ClaudeConfig so they don't depend on
        whatever ``ANTHROPIC_API_KEY`` happens to be in the dev shell
        (which might be empty, unset, or a real key loaded from .env).
        """
        config = ClaudeConfig(api_key="sk-ant-test")
        if model is not None:
            config.model = model
        return ClaudePlanningInterviewer(config=config, client=client)

    def _current_topic(self) -> dict[str, Any]:
        return {
            "title": "Budget",
            "icon": "chart",
            "decisions": [],
            "turns": [
                {
                    "turn_id": "T1",
                    "role": "user",
                    "body": "50k all-in.",
                    "status": "answered",
                },
            ],
            "open_questions": [],
            "risks_assumptions": [],
        }

    def test_topic_turn_returns_same_shape_as_openai_adapter(self) -> None:
        """Parity check: the returned dict has every key the sanitizer
        and the API layer expect. If this diverges from the OpenAI
        adapter, downstream code that switches providers will break.
        """
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_tool_use_response(
            tool_name="topic_turn",
            args=_valid_topic_turn_args(),
        )
        adapter = self._make_adapter(fake_client)

        result = adapter.topic_turn(
            current_topic=self._current_topic(),
            other_topics=[{"title": "Audience", "decisions": []}],
        )

        # Core fields from the OpenAI adapter's contract.
        self.assertEqual(result["action"], "ask")
        self.assertEqual(result["question"], "Which line items are non-negotiable?")
        self.assertEqual(result["why_this_matters"],
                         "Pre-deciding the cuts saves negotiation later.")
        self.assertEqual(len(result["suggested_responses"]), 3)

        # Required null-able fields that callers read unconditionally.
        self.assertIn("proposed_decisions", result)
        self.assertIn("consistency_flags", result)
        self.assertIn("new_topic_proposal", result)
        self.assertIn("topic_deletion_suggestion", result)
        self.assertIn("close_recommendation_reason", result)
        self.assertIn("conflict_resolution", result)
        self.assertIn("planned_checkpoints", result)
        self.assertIn("checkpoint_updates", result)

        # Sanitizer bookkeeping (parity with OpenAI adapter path).
        self.assertIn("_sanitize", result)

    def test_topic_turn_wires_anthropic_envelope(self) -> None:
        """tool_choice envelope + system kwarg + max_tokens are correct."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_tool_use_response(
            tool_name="topic_turn",
            args=_valid_topic_turn_args(),
        )
        adapter = self._make_adapter(fake_client)
        adapter.topic_turn(
            current_topic=self._current_topic(),
            other_topics=[],
        )

        call_kwargs = fake_client.messages.create.call_args.kwargs
        # Flat tool_choice shape — no ``function`` wrapper like OpenAI.
        self.assertEqual(
            call_kwargs["tool_choice"],
            {"type": "tool", "name": "topic_turn"},
        )
        # System prompt passes via the top-level kwarg, not messages.
        self.assertIn("MODE: TOPIC_INTERVIEW", call_kwargs["system"])
        self.assertIn("Inspira planning interviewer", call_kwargs["system"])
        # User message body is the only ``messages`` entry.
        self.assertEqual(len(call_kwargs["messages"]), 1)
        self.assertEqual(call_kwargs["messages"][0]["role"], "user")
        self.assertIn("CURRENT TOPIC: Budget", call_kwargs["messages"][0]["content"])
        # Anthropic requires max_tokens on every call.
        self.assertIn("max_tokens", call_kwargs)

    def test_model_override_pins_per_call_model(self) -> None:
        """The tier dispatcher passes model_override — it must win over config."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_tool_use_response(
            tool_name="topic_turn",
            args=_valid_topic_turn_args(),
        )
        adapter = self._make_adapter(fake_client, model="claude-boring")
        adapter.topic_turn(
            current_topic=self._current_topic(),
            other_topics=[],
            model_override="claude-sonnet-4-5-20250929",
        )
        call_kwargs = fake_client.messages.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "claude-sonnet-4-5-20250929")

    def test_topic_turn_rejects_empty_current_topic(self) -> None:
        adapter = self._make_adapter(MagicMock())
        with self.assertRaisesRegex(ValueError, "current_topic is required"):
            adapter.topic_turn(current_topic={}, other_topics=[])


# ---------------------------------------------------------------------------
# Sanitizer safety net — malformed-but-recoverable responses.
# ---------------------------------------------------------------------------


class SanitizerSafetyNetTests(unittest.TestCase):
    """A response that references a ghost topic gets cleaned up silently.

    This is the "same safety net as openai_adapter" — the sanitizer is
    shared, so proving it runs on the Claude path is a single-call test.
    """

    def _make_adapter(self, client: Any) -> ClaudePlanningInterviewer:
        return ClaudePlanningInterviewer(
            config=ClaudeConfig(api_key="sk-ant-test"),
            client=client,
        )

    def _current_topic(self) -> dict[str, Any]:
        return {
            "title": "Budget",
            "icon": "chart",
            "decisions": [],
            "turns": [],
            "open_questions": [],
            "risks_assumptions": [],
        }

    def test_ghost_topic_in_consistency_flags_gets_dropped(self) -> None:
        """Hallucinated ``other_topic_title`` → silent drop with repair log."""
        args = _valid_topic_turn_args()
        args["consistency_flags"] = [
            {
                "other_topic_title": "GhostTopic",
                "other_decision_id": "D99",
                "description": "Conflicts with nothing real.",
            },
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_tool_use_response(
            tool_name="topic_turn",
            args=args,
        )
        adapter = self._make_adapter(fake_client)

        result = adapter.topic_turn(
            current_topic=self._current_topic(),
            other_topics=[{"title": "Audience", "decisions": []}],
        )

        # Flag was silently dropped, not raised.
        self.assertEqual(result["consistency_flags"], [])
        # Repair log records the drop reason — operators can audit if needed.
        self.assertEqual(len(result["_sanitize"]["dropped_consistency_flags"]), 1)

    def test_ghost_target_topic_title_normalised_to_none(self) -> None:
        """Unknown ``target_topic_title`` → None, with bookkeeping."""
        args = _valid_topic_turn_args()
        args["proposed_decisions"] = [
            {
                "statement": "Ship Tuesday.",
                "rationale": None,
                "extracted_from_turn_id": "T1",
                "target_topic_title": "NonexistentTopic",
            },
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_tool_use_response(
            tool_name="topic_turn",
            args=args,
        )
        adapter = self._make_adapter(fake_client)

        result = adapter.topic_turn(
            current_topic=self._current_topic(),
            other_topics=[{"title": "Audience", "decisions": []}],
        )

        self.assertIsNone(result["proposed_decisions"][0]["target_topic_title"])
        self.assertEqual(len(result["_sanitize"]["dropped_target_topic_titles"]), 1)

    def test_structural_bug_surfaces_as_runtime_error(self) -> None:
        """Unknown ``action`` IS a structural bug — the sanitizer raises.

        Mirrors the OpenAI adapter. We want this to propagate so operators
        see the model is off-distribution, not silently coerce to ``ask``.
        """
        args = _valid_topic_turn_args()
        args["action"] = "yodel"
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _fake_tool_use_response(
            tool_name="topic_turn",
            args=args,
        )
        adapter = self._make_adapter(fake_client)

        with self.assertRaisesRegex(RuntimeError, "unknown action"):
            adapter.topic_turn(
                current_topic=self._current_topic(),
                other_topics=[],
            )


# ---------------------------------------------------------------------------
# Tier dispatcher — fallback semantics when Claude isn't wired.
# ---------------------------------------------------------------------------


class TierDispatcherTests(unittest.TestCase):
    """``tier_to_adapter`` is the only place the frontier-vs-OpenAI pick lives."""

    def setUp(self) -> None:
        self.openai_mock = MagicMock(spec=OpenAIPlanningInterviewer)
        self.claude_mock = MagicMock(spec=ClaudePlanningInterviewer)

    def test_frontier_with_claude_adapter_picks_claude(self) -> None:
        picked = tier_to_adapter(
            ModelTier.FRONTIER,
            openai_adapter=self.openai_mock,
            claude_adapter=self.claude_mock,
        )
        self.assertIs(picked, self.claude_mock)

    def test_frontier_without_claude_adapter_falls_back_to_openai(self) -> None:
        """This IS the 'ANTHROPIC_API_KEY missing' fallback path.

        The API layer catches the construction error, passes ``None``, and
        the dispatcher then routes to OpenAI — keeping dev environments
        without an Anthropic key working.
        """
        picked = tier_to_adapter(
            ModelTier.FRONTIER,
            openai_adapter=self.openai_mock,
            claude_adapter=None,
        )
        self.assertIs(picked, self.openai_mock)

    def test_pro_never_picks_claude_even_when_available(self) -> None:
        picked = tier_to_adapter(
            ModelTier.PRO,
            openai_adapter=self.openai_mock,
            claude_adapter=self.claude_mock,
        )
        self.assertIs(picked, self.openai_mock)

    def test_base_never_picks_claude_even_when_available(self) -> None:
        picked = tier_to_adapter(
            ModelTier.BASE,
            openai_adapter=self.openai_mock,
            claude_adapter=self.claude_mock,
        )
        self.assertIs(picked, self.openai_mock)

    def test_tier_to_claude_model_only_returns_string_for_frontier(self) -> None:
        self.assertEqual(
            tier_to_claude_model(ModelTier.FRONTIER),
            "claude-sonnet-4-5-20250929",
        )
        self.assertIsNone(tier_to_claude_model(ModelTier.PRO))
        self.assertIsNone(tier_to_claude_model(ModelTier.BASE))


# ---------------------------------------------------------------------------
# HTTP-level integration: v2_topic_turn actually routes to the right adapter.
# ---------------------------------------------------------------------------


class TopicTurnRoutesToClaudeOnFrontierTests(unittest.TestCase):
    """End-to-end: POST /api/v2/topics/{id}/turn with frontier tier hits Claude.

    Exercises the single conditional branch added to ``v2_topic_turn``:
    when the resolved tier is frontier and a Claude adapter is wired
    (via ``app.state.claude_adapter``), the HTTP path calls the Claude
    adapter's ``topic_turn`` (NOT the OpenAI adapter's).
    """

    def setUp(self) -> None:
        self.client, self.store, self.openai_adapter, self.temp_dir = make_test_app()
        # Inject a mocked Claude adapter; the API layer picks it up via
        # ``getattr(app.state, "claude_adapter", None)``.
        self.claude_adapter = MagicMock(spec=ClaudePlanningInterviewer)
        self.claude_adapter.topic_turn.return_value = fake_turn_response(action="ask")
        self.client.app.state.claude_adapter = self.claude_adapter

        signup_and_login(self.client, email="frontrout@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

        # Team plan unlocks frontier selection at the picker layer.
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        # Kickoff seeds a project with topics the turn endpoint needs.
        self.openai_adapter.kickoff.return_value = fake_kickoff_response()
        self.openai_adapter.topic_turn.return_value = fake_turn_response(action="ask")
        kick = self.client.post(
            "/api/v2/projects/proj-frontrout/kickoff",
            json={"user_idea": "A small wine festival."},
        ).json()
        self.venue_id = next(
            t["topic_id"] for t in kick["topics"] if t["title"] == "Venue"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_frontier_tier_routes_to_claude_adapter(self) -> None:
        """With Claude wired, a frontier turn calls Claude (not OpenAI)."""
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes.", "model_tier": "frontier"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        # Claude was called; OpenAI was not (beyond the kickoff above).
        self.claude_adapter.topic_turn.assert_called_once()
        self.openai_adapter.topic_turn.assert_not_called()
        # And the model_override passed to Claude is the Sonnet id, not gpt-4o.
        kwargs = self.claude_adapter.topic_turn.call_args.kwargs
        self.assertEqual(kwargs["model_override"], "claude-sonnet-4-5-20250929")

    def test_pro_tier_still_uses_openai_adapter(self) -> None:
        """Non-frontier tiers keep going through OpenAI even with Claude wired."""
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes.", "model_tier": "pro"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.openai_adapter.topic_turn.assert_called_once()
        self.claude_adapter.topic_turn.assert_not_called()


class TopicTurnFallsBackToOpenAIWhenClaudeMissingTests(unittest.TestCase):
    """Without ``app.state.claude_adapter`` set, frontier turns still work.

    Simulates the "ANTHROPIC_API_KEY missing" scenario: no Claude adapter
    gets injected, the env key is absent, so ``_get_claude_adapter`` logs
    a warning and returns None. The tier dispatcher then routes to
    OpenAI — dev environment stays functional.
    """

    def setUp(self) -> None:
        # Ensure ANTHROPIC_API_KEY is unset so _get_claude_adapter's
        # fallback path is exercised. Save + restore so the rest of the
        # suite isn't affected.
        self._saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)

        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Explicitly DO NOT set app.state.claude_adapter.

        signup_and_login(self.client, email="fallback@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        kick = self.client.post(
            "/api/v2/projects/proj-fallback/kickoff",
            json={"user_idea": "A small wine festival."},
        ).json()
        self.venue_id = next(
            t["topic_id"] for t in kick["topics"] if t["title"] == "Venue"
        )

    def tearDown(self) -> None:
        if self._saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._saved_key
        self.temp_dir.cleanup()

    def test_frontier_without_claude_falls_back_to_openai(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes.", "model_tier": "frontier"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        # OpenAI got the call (kickoff above + this turn = 1 topic_turn).
        self.adapter.topic_turn.assert_called_once()
        kwargs = self.adapter.topic_turn.call_args.kwargs
        # Fallback uses the OpenAI frontier model id (gpt-5.5), NOT Sonnet.
        self.assertEqual(kwargs["model_override"], "gpt-5.5")


if __name__ == "__main__":
    unittest.main()
