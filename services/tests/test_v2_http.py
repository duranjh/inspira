"""HTTP-layer tests for the v2 endpoints (kickoff, topic_turn, list).

We inject a mocked adapter into the app so these tests never hit OpenAI.
Behavior verified end-to-end from `handle_v2_*` → store → response dict.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.app import (
    PlanningStudioApplication,
    _match_route,
)
from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore


ensure_loaded()


# =============================================================================
# Route matcher — low-level tests.
# =============================================================================


class RouteMatcherTests(unittest.TestCase):
    def test_literal_path_matches_itself(self) -> None:
        self.assertEqual(_match_route("/api/health", "/api/health"), {})

    def test_literal_path_wrong_path_returns_none(self) -> None:
        self.assertIsNone(_match_route("/api/health", "/api/projects"))

    def test_single_param_capture(self) -> None:
        captures = _match_route(
            "/api/v2/projects/{project_id}/kickoff",
            "/api/v2/projects/proj_abc123/kickoff",
        )
        self.assertEqual(captures, {"project_id": "proj_abc123"})

    def test_length_mismatch_returns_none(self) -> None:
        self.assertIsNone(_match_route(
            "/api/v2/projects/{project_id}/kickoff",
            "/api/v2/projects/proj_abc123",
        ))

    def test_empty_capture_rejected(self) -> None:
        # "/api/v2/projects//kickoff" — project_id is empty between slashes
        self.assertIsNone(_match_route(
            "/api/v2/projects/{project_id}/kickoff",
            "/api/v2/projects//kickoff",
        ))

    def test_trailing_and_leading_slash_tolerant(self) -> None:
        self.assertEqual(
            _match_route("/api/health/", "/api/health"),
            {},
        )


# =============================================================================
# V2 endpoint tests — mocked adapter, real store.
# =============================================================================


class _FakeRequest:
    """Minimal stand-in for BaseHTTPRequestHandler used by app handlers."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = BytesIO(raw)


class V2EndpointBase(unittest.TestCase):
    """Shared setup: isolated temp store, mocked adapter, application."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="inspira-v2-http-", ignore_cleanup_errors=True,
        )
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        self.store = PlanningStudioStore(load_config())
        self.adapter = MagicMock()
        self.app = PlanningStudioApplication(self.store, adapter=self.adapter)
        # Bootstrap seeds a project; grab it for the v2 endpoints.
        self.project_id = "project-second-brain-commercialization"

    def tearDown(self) -> None:
        os.environ.pop("PLANNING_STUDIO_STORAGE_ROOT", None)
        self.temp_dir.cleanup()


class KickoffEndpointTests(V2EndpointBase):
    def _happy_kickoff_response(self) -> dict[str, Any]:
        return {
            "domain": "event",
            "domain_confidence": "high",
            "opening_card": {"body": "Five topics. Start with Venue."},
            "topics": [
                {"title": "Venue", "icon": "map-pin", "why_this_topic": "The space."},
                {"title": "Budget", "icon": "chart", "why_this_topic": "What we have."},
                {"title": "Audience", "icon": "heart", "why_this_topic": "Who it's for."},
                {"title": "Timing", "icon": "clock", "why_this_topic": "When."},
                {"title": "Safety", "icon": "flag", "why_this_topic": "Compliance."},
            ],
            "relationships": [
                {"from_topic_title": "Venue", "to_topic_title": "Safety", "label": "requires"},
                {"from_topic_title": "Budget", "to_topic_title": "Venue", "label": "bounds"},
            ],
            "suggested_first_topic": "Venue",
            "clarifying_question_if_too_vague": None,
            "_sanitize": {"dropped_relationships": [], "suggested_first_fallback": None},
        }

    def test_kickoff_persists_topics_and_relationships(self) -> None:
        self.adapter.kickoff.return_value = self._happy_kickoff_response()

        status, payload = self.app.handle_v2_kickoff(
            _FakeRequest({"user_idea": "A small outdoor wine festival."}),
            {},
            project_id=self.project_id,
        )
        self.assertEqual(status, 201)
        self.assertEqual(len(payload["topics"]), 5)
        self.assertEqual(len(payload["relationships"]), 2)
        # Every persisted topic has an ID; every relationship points to real topic IDs.
        topic_ids = {t["topic_id"] for t in payload["topics"]}
        for rel in payload["relationships"]:
            self.assertIn(rel["source_topic_id"], topic_ids)
            self.assertIn(rel["target_topic_id"], topic_ids)
        # Adapter was called with the idea.
        self.adapter.kickoff.assert_called_once()

        # Verify round-trip via the store.
        stored_topics = self.store.list_topics(project_id=self.project_id)
        self.assertEqual(len(stored_topics), 5)
        stored_rels = self.store.list_relationships(project_id=self.project_id)
        self.assertEqual(len(stored_rels), 2)

    def test_kickoff_rejects_missing_idea(self) -> None:
        status, payload = self.app.handle_v2_kickoff(
            _FakeRequest({"user_idea": ""}),
            {},
            project_id=self.project_id,
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_kickoff_planner_failure_becomes_500(self) -> None:
        self.adapter.kickoff.side_effect = RuntimeError("no tool call after retry")
        status, payload = self.app.handle_v2_kickoff(
            _FakeRequest({"user_idea": "a long enough idea to warrant a topic map"}),
            {},
            project_id=self.project_id,
        )
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "planner_call_failed")
        self.assertIn("no tool call", payload["detail"])

    def test_kickoff_assigns_plausible_canvas_positions(self) -> None:
        """Cards shouldn't all stack at (0,0) — they should lay out in a readable grid."""
        self.adapter.kickoff.return_value = self._happy_kickoff_response()
        _status, payload = self.app.handle_v2_kickoff(
            _FakeRequest({"user_idea": "idea"}),
            {},
            project_id=self.project_id,
        )
        positions = {(t["position_x"], t["position_y"]) for t in payload["topics"]}
        # Five topics should end up at different positions.
        self.assertEqual(len(positions), 5)


class TopicTurnEndpointTests(V2EndpointBase):
    def _seed_topic(self) -> dict[str, Any]:
        return self.store.create_topic(
            project_id=self.project_id,
            title="Budget",
            icon="chart",
            origin="planner_initial",
        )

    def test_topic_turn_persists_user_and_planner_turns(self) -> None:
        topic = self._seed_topic()
        self.adapter.topic_turn.return_value = {
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
            "close_recommendation_reason": None,
            "_sanitize": {"dropped_consistency_flags": []},
        }

        status, payload = self.app.handle_v2_topic_turn(
            _FakeRequest({"user_answer": "Hard cap is $48k incl contingency."}),
            {},
            topic_id=topic["topic_id"],
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["turn_result"]["action"], "ask")
        self.assertIsNotNone(payload["planner_turn"])

        # Verify both turns persisted
        turns = self.store.list_qna_turns(topic_id=topic["topic_id"])
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[0]["body"], "Hard cap is $48k incl contingency.")
        self.assertEqual(turns[1]["role"], "planner")
        self.assertEqual(turns[1]["action"], "ask")
        self.assertEqual(len(turns[1]["suggested_responses"]), 3)

    def test_topic_turn_without_user_answer_just_calls_planner(self) -> None:
        topic = self._seed_topic()
        self.adapter.topic_turn.return_value = {
            "action": "ask",
            "question": "What's the hard cap?",
            "why_this_matters": "Everything depends on it.",
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
            "_sanitize": {"dropped_consistency_flags": []},
        }
        status, _ = self.app.handle_v2_topic_turn(
            _FakeRequest({}),
            {},
            topic_id=topic["topic_id"],
        )
        self.assertEqual(status, 201)
        turns = self.store.list_qna_turns(topic_id=topic["topic_id"])
        # No user turn persisted — just the planner's opener.
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["role"], "planner")

    def test_topic_turn_suggest_close_does_not_persist_planner_turn(self) -> None:
        topic = self._seed_topic()
        self.adapter.topic_turn.return_value = {
            "action": "suggest_close",
            "question": None,
            "why_this_matters": None,
            "suggested_responses": [],
            "proposed_decisions": [],
            "consistency_flags": [],
            "new_topic_proposal": None,
            "close_recommendation_reason": "All decisions captured.",
            "_sanitize": {"dropped_consistency_flags": []},
        }
        status, payload = self.app.handle_v2_topic_turn(
            _FakeRequest({}),
            {},
            topic_id=topic["topic_id"],
        )
        self.assertEqual(status, 201)
        self.assertIsNone(payload["planner_turn"])

    def test_topic_turn_unknown_topic_returns_404(self) -> None:
        status, payload = self.app.handle_v2_topic_turn(
            _FakeRequest({"user_answer": "hi"}),
            {},
            topic_id="topic-does-not-exist",
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "topic_not_found")


class ListEndpointTests(V2EndpointBase):
    def test_list_topics_empty(self) -> None:
        status, payload = self.app.handle_v2_list_topics(
            None, {}, project_id=self.project_id,  # type: ignore[arg-type]
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["topics"], [])

    def test_list_topics_returns_persisted(self) -> None:
        self.store.create_topic(
            project_id=self.project_id, title="Budget", icon="chart",
        )
        status, payload = self.app.handle_v2_list_topics(
            None, {}, project_id=self.project_id,  # type: ignore[arg-type]
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["topics"]), 1)
        self.assertEqual(payload["topics"][0]["title"], "Budget")


if __name__ == "__main__":
    unittest.main()
