"""Tests for the resolve_conflict action in topic_turn.

Five tests:
1. Valid resolve_conflict + conflict_resolution → sanitize preserves it.
2. Missing conflict_resolution → downgrades to 'ask'.
3. Unknown conflicting_decision_id → downgrades to 'ask'.
4. Integration: POST /api/v2/topics/{id}/turn with mock returning
   resolve_conflict → envelope shape is correct.
5. Prompt assertion: TOPIC_INTERVIEW_MODE_PROMPT contains "resolve_conflict".
"""
from __future__ import annotations

import unittest
from typing import Any

try:
    from ._helpers import (
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )

from planning_studio_service.agents.openai_adapter import _sanitize_topic_turn
from planning_studio_service.agents.prompts import TOPIC_INTERVIEW_MODE_PROMPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_turn(**overrides: Any) -> dict[str, Any]:
    """Minimal valid topic_turn result for sanitizer tests."""
    base: dict[str, Any] = {
        "action": "resolve_conflict",
        "question": "Which way do you want to go?",
        "why_this_matters": "These two can't both be true.",
        "suggested_responses": [
            {"label": "Keep the original — adjust this one to match.", "intent": "keep_original"},
            {"label": "Replace the old one — this new thinking supersedes it.", "intent": "supersede"},
            {"label": "Both are true under different conditions — split the scope.", "intent": "scope_split"},
        ],
        "proposed_decisions": [],
        "consistency_flags": [
            {
                "other_topic_title": "Pricing",
                "other_decision_id": "d-price-001",
                "description": "Pricing set to $9 but now $15 mentioned.",
            }
        ],
        "new_topic_proposal": None,
        "close_recommendation_reason": None,
        "conflict_resolution": {
            "conflicting_decision_id": "d-price-001",
            "conflicting_topic_title": "Pricing",
            "current_statement_summary": "Price at $15 per month.",
            "previous_statement_summary": "Price at $9 per month.",
        },
    }
    base.update(overrides)
    return base


def _other_topics_with_pricing() -> list[dict[str, Any]]:
    """other_topics containing a decision that matches the conflict."""
    return [
        {
            "title": "Pricing",
            "decisions": [
                {"decision_id": "d-price-001", "statement": "Price at $9 per month."},
            ],
        }
    ]


# ---------------------------------------------------------------------------
# Test 1: valid resolve_conflict is preserved by sanitize
# ---------------------------------------------------------------------------


class ResolveConflictPreservedTest(unittest.TestCase):
    """When action=resolve_conflict and conflict_resolution is valid, sanitize passes through."""

    def test_valid_resolve_conflict_preserved(self) -> None:
        parsed = _base_turn()
        other_topics = _other_topics_with_pricing()
        _sanitize_topic_turn(parsed, other_topics)

        self.assertEqual(parsed["action"], "resolve_conflict")
        self.assertIsNotNone(parsed["conflict_resolution"])
        cr = parsed["conflict_resolution"]
        self.assertEqual(cr["conflicting_decision_id"], "d-price-001")
        self.assertEqual(cr["conflicting_topic_title"], "Pricing")
        self.assertEqual(cr["current_statement_summary"], "Price at $15 per month.")
        self.assertEqual(cr["previous_statement_summary"], "Price at $9 per month.")
        # No downgrades logged.
        self.assertEqual(parsed["_sanitize"]["resolve_conflict_downgrades"], [])


# ---------------------------------------------------------------------------
# Test 2: missing conflict_resolution → downgrade to 'ask'
# ---------------------------------------------------------------------------


class ResolveConflictMissingPayloadTest(unittest.TestCase):
    """action=resolve_conflict without conflict_resolution → downgraded to 'ask'."""

    def test_missing_conflict_resolution_downgrades(self) -> None:
        parsed = _base_turn(conflict_resolution=None)
        other_topics = _other_topics_with_pricing()
        _sanitize_topic_turn(parsed, other_topics)

        self.assertEqual(parsed["action"], "ask")
        self.assertIsNone(parsed["conflict_resolution"])
        downgrades = parsed["_sanitize"]["resolve_conflict_downgrades"]
        self.assertEqual(len(downgrades), 1)
        self.assertIn("missing conflict_resolution", downgrades[0]["reason"])


# ---------------------------------------------------------------------------
# Test 3: unknown conflicting_decision_id → downgrade to 'ask'
# ---------------------------------------------------------------------------


class ResolveConflictUnknownDecisionIdTest(unittest.TestCase):
    """conflict_resolution references a decision_id not in other_topics → downgrade."""

    def test_unknown_decision_id_downgrades(self) -> None:
        parsed = _base_turn(
            conflict_resolution={
                "conflicting_decision_id": "d-ghost-999",  # not in other_topics
                "conflicting_topic_title": "Pricing",
                "current_statement_summary": "Price at $15.",
                "previous_statement_summary": "Price at $9.",
            }
        )
        other_topics = _other_topics_with_pricing()
        _sanitize_topic_turn(parsed, other_topics)

        self.assertEqual(parsed["action"], "ask")
        self.assertIsNone(parsed["conflict_resolution"])
        downgrades = parsed["_sanitize"]["resolve_conflict_downgrades"]
        self.assertEqual(len(downgrades), 1)
        self.assertIn("d-ghost-999", downgrades[0]["reason"])


# ---------------------------------------------------------------------------
# Test 4: integration — HTTP envelope shape when adapter returns resolve_conflict
# ---------------------------------------------------------------------------


class ResolveConflictIntegrationTest(unittest.TestCase):
    """POST /api/v2/topics/{id}/turn returns the right envelope for resolve_conflict."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="cr@example.com", password="password123")

        # Seed a project via kickoff so we have real topics.
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-conflict-test/kickoff",
            json={"user_idea": "A wine festival with a tight budget."},
        ).json()
        topics = kick["topics"]
        title_to = {t["title"]: t for t in topics}
        self.venue_topic = title_to["Venue"]
        self.budget_topic = title_to["Budget"]

        # Seed a decision on Budget so it has a real decision_id.
        dec_resp = self.client.post(
            f"/api/v2/topics/{self.budget_topic['topic_id']}/decisions",
            json={"statement": "Hard cap $30k.", "proposed_by": "user", "status": "confirmed"},
        ).json()
        self.budget_decision_id = dec_resp["decision"]["decision_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolve_conflict_envelope_shape(self) -> None:
        """Adapter returns resolve_conflict → envelope has turn_result with action."""
        self.adapter.topic_turn.return_value = {
            "action": "resolve_conflict",
            "question": "Which price point should we go with?",
            "why_this_matters": "These two numbers can't coexist.",
            "suggested_responses": [
                {"label": "Keep $30k — update this mention.", "intent": "keep_original"},
                {"label": "Go with $50k — supersedes the old cap.", "intent": "supersede"},
                {"label": "Both hold: $30k ops, $50k all-in.", "intent": "scope_split"},
            ],
            "proposed_decisions": [],
            "consistency_flags": [
                {
                    "other_topic_title": "Budget",
                    "other_decision_id": self.budget_decision_id,
                    "description": "Budget says $30k but $50k was just mentioned.",
                }
            ],
            "new_topic_proposal": None,
            "close_recommendation_reason": None,
            "conflict_resolution": {
                "conflicting_decision_id": self.budget_decision_id,
                "conflicting_topic_title": "Budget",
                "current_statement_summary": "Total spend around $50k.",
                "previous_statement_summary": "Hard cap $30k.",
            },
            "_sanitize": {
                "dropped_consistency_flags": [],
                "dropped_target_topic_titles": [],
                "resolve_conflict_downgrades": [],
            },
        }

        resp = self.client.post(
            f"/api/v2/topics/{self.venue_topic['topic_id']}/turn",
            json={"user_answer": "We're thinking around 50k total."},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        payload = resp.json()

        # Envelope always has turn_result and rerouted_decisions.
        self.assertIn("turn_result", payload)
        self.assertIn("rerouted_decisions", payload)

        tr = payload["turn_result"]
        self.assertEqual(tr["action"], "resolve_conflict")
        self.assertIsNotNone(tr["conflict_resolution"])
        cr = tr["conflict_resolution"]
        self.assertEqual(cr["conflicting_decision_id"], self.budget_decision_id)
        self.assertEqual(cr["conflicting_topic_title"], "Budget")
        self.assertIn("$50k", cr["current_statement_summary"])
        self.assertIn("$30k", cr["previous_statement_summary"])
        # 3 resolution-path suggestions.
        self.assertEqual(len(tr["suggested_responses"]), 3)


# ---------------------------------------------------------------------------
# Test 5: prompt contains "resolve_conflict"
# ---------------------------------------------------------------------------


class PromptContainsResolveConflictTest(unittest.TestCase):
    """TOPIC_INTERVIEW_MODE_PROMPT must reference 'resolve_conflict'."""

    def test_prompt_contains_resolve_conflict_keyword(self) -> None:
        self.assertIn(
            "resolve_conflict",
            TOPIC_INTERVIEW_MODE_PROMPT,
            "TOPIC_INTERVIEW_MODE_PROMPT must contain the string 'resolve_conflict' "
            "so the LLM knows about the new action value.",
        )


if __name__ == "__main__":
    unittest.main()
