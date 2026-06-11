"""Tests for cross-topic decision routing in the topic_turn endpoint.

Covers two layers:
1. Sanitizer unit tests — ``_sanitize_topic_turn`` should:
   - Pass through ``target_topic_title`` when it matches a sibling topic
     (case-insensitive, trimmed).
   - Drop (set to None) when the title doesn't match any sibling.
2. API integration tests — POST /api/v2/topics/{id}/turn should:
   - Save a decision to the *current* topic when target_topic_title is null.
   - Save a decision to the *target* topic when target_topic_title resolves
     to a sibling topic_id.
   - Return ``rerouted_decisions`` in the response envelope.
   - Return an empty ``rerouted_decisions`` list when no decisions were rerouted.
"""
from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

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

from planning_studio_service.agents.openai_adapter import _sanitize_topic_turn


# ---------------------------------------------------------------------------
# Sanitizer unit tests — no HTTP, no network.
# ---------------------------------------------------------------------------


def _base_turn(
    proposed_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Minimal valid topic_turn result dict for sanitizer tests."""
    return {
        "action": "ask",
        "question": "What is the budget?",
        "why_this_matters": "Anchors everything else.",
        "suggested_responses": [],
        "proposed_decisions": proposed_decisions or [],
        "consistency_flags": [],
        "new_topic_proposal": None,
        "close_recommendation_reason": None,
    }


def _other_topics(*titles: str) -> list[dict[str, Any]]:
    return [{"title": t, "decisions": []} for t in titles]


class SanitizeTargetTopicTitleTests(unittest.TestCase):
    """_sanitize_topic_turn should sanitize target_topic_title."""

    def test_null_target_passes_through(self) -> None:
        parsed = _base_turn([
            {
                "statement": "Use Python.",
                "rationale": None,
                "extracted_from_turn_id": "turn-1",
                "target_topic_title": None,
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Tech", "Marketing"))
        self.assertIsNone(parsed["proposed_decisions"][0]["target_topic_title"])

    def test_absent_target_normalised_to_null(self) -> None:
        """target_topic_title absent in the raw dict → sanitizer sets None."""
        parsed = _base_turn([
            {
                "statement": "Use Python.",
                "rationale": None,
                "extracted_from_turn_id": "turn-1",
                # target_topic_title intentionally absent
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Tech"))
        self.assertIsNone(parsed["proposed_decisions"][0]["target_topic_title"])

    def test_valid_target_passes_through_exact_case(self) -> None:
        parsed = _base_turn([
            {
                "statement": "Price at $9.99.",
                "rationale": "Competitive.",
                "extracted_from_turn_id": "turn-2",
                "target_topic_title": "Pricing",
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Pricing", "Marketing"))
        self.assertEqual(
            parsed["proposed_decisions"][0]["target_topic_title"], "Pricing"
        )

    def test_valid_target_case_insensitive(self) -> None:
        """Case-insensitive match preserves the canonical casing from other_topics."""
        parsed = _base_turn([
            {
                "statement": "Price at $9.99.",
                "rationale": None,
                "extracted_from_turn_id": "turn-3",
                "target_topic_title": "pricing",  # lower-case
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Pricing", "Marketing"))
        # Should normalise to the canonical spelling from other_topics.
        self.assertEqual(
            parsed["proposed_decisions"][0]["target_topic_title"], "Pricing"
        )

    def test_valid_target_trimmed(self) -> None:
        """Leading/trailing whitespace is stripped before matching."""
        parsed = _base_turn([
            {
                "statement": "Ship on Tuesdays.",
                "rationale": None,
                "extracted_from_turn_id": "turn-4",
                "target_topic_title": "  Logistics  ",
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Logistics"))
        self.assertEqual(
            parsed["proposed_decisions"][0]["target_topic_title"], "Logistics"
        )

    def test_unknown_target_dropped(self) -> None:
        """A target that doesn't match any sibling is silently set to None."""
        parsed = _base_turn([
            {
                "statement": "Hire ten engineers.",
                "rationale": None,
                "extracted_from_turn_id": "turn-5",
                "target_topic_title": "NonExistentTopic",
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Team", "Budget"))
        self.assertIsNone(parsed["proposed_decisions"][0]["target_topic_title"])

    def test_unknown_target_logged_in_sanitize(self) -> None:
        """Dropped targets are recorded in _sanitize.dropped_target_topic_titles."""
        parsed = _base_turn([
            {
                "statement": "Use React.",
                "rationale": None,
                "extracted_from_turn_id": "turn-6",
                "target_topic_title": "Frontend",  # not in sibling list
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Backend"))
        dropped = parsed["_sanitize"]["dropped_target_topic_titles"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["target_topic_title"], "Frontend")

    def test_empty_string_target_normalised_to_null(self) -> None:
        parsed = _base_turn([
            {
                "statement": "Some decision.",
                "rationale": None,
                "extracted_from_turn_id": "turn-7",
                "target_topic_title": "",
            }
        ])
        _sanitize_topic_turn(parsed, _other_topics("Pricing"))
        self.assertIsNone(parsed["proposed_decisions"][0]["target_topic_title"])

    def test_multiple_decisions_mixed_targets(self) -> None:
        """Multiple proposals with different routing outcomes are each handled."""
        parsed = _base_turn([
            {
                "statement": "Keep on current topic.",
                "rationale": None,
                "extracted_from_turn_id": "turn-8",
                "target_topic_title": None,
            },
            {
                "statement": "Route to Pricing.",
                "rationale": "Budget-related.",
                "extracted_from_turn_id": "turn-8",
                "target_topic_title": "Pricing",
            },
            {
                "statement": "Route to nowhere.",
                "rationale": None,
                "extracted_from_turn_id": "turn-8",
                "target_topic_title": "Nonexistent",
            },
        ])
        _sanitize_topic_turn(parsed, _other_topics("Pricing", "Marketing"))
        decisions = parsed["proposed_decisions"]
        self.assertIsNone(decisions[0]["target_topic_title"])
        self.assertEqual(decisions[1]["target_topic_title"], "Pricing")
        self.assertIsNone(decisions[2]["target_topic_title"])
        self.assertEqual(len(parsed["_sanitize"]["dropped_target_topic_titles"]), 1)

    def test_no_other_topics_any_target_dropped(self) -> None:
        """With no siblings, any non-null target_topic_title is invalid."""
        parsed = _base_turn([
            {
                "statement": "Route somewhere.",
                "rationale": None,
                "extracted_from_turn_id": "turn-9",
                "target_topic_title": "Anywhere",
            }
        ])
        _sanitize_topic_turn(parsed, other_topics=[])
        self.assertIsNone(parsed["proposed_decisions"][0]["target_topic_title"])


# ---------------------------------------------------------------------------
# API integration tests — full HTTP round-trip via TestClient.
# ---------------------------------------------------------------------------


def _turn_with_decisions(
    decisions: list[dict[str, Any]],
    action: str = "ask",
) -> dict[str, Any]:
    """Build a fake turn_result that includes the given proposed_decisions."""
    base = fake_turn_response(action=action)
    base["proposed_decisions"] = decisions
    return base


class TopicTurnDecisionRoutingTests(unittest.TestCase):
    """POST /api/v2/topics/{id}/turn routes decisions to the correct topic."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="r@example.com", password="password123")

        # Seed a project with a small topic set via kickoff.  The kickoff
        # fixture creates exactly five topics: Venue, Budget, Audience,
        # Timing, Safety.
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-routing/kickoff",
            json={"user_idea": "A small outdoor wine festival."},
        ).json()
        self.project_id = "proj-routing"
        topics = kick["topics"]

        # Pin down stable topic references for the test body.
        title_to = {t["title"]: t for t in topics}
        self.venue_topic = title_to["Venue"]
        self.budget_topic = title_to["Budget"]
        self.venue_id = self.venue_topic["topic_id"]
        self.budget_id = self.budget_topic["topic_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _post_turn(self, topic_id: str, answer: str = "Some answer") -> dict:
        resp = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": answer},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def _list_decisions(self, topic_id: str) -> list[dict]:
        return self.client.get(
            f"/api/v2/topics/{topic_id}/decisions"
        ).json()["decisions"]

    # ------------------------------------------------------------------ #
    # Decision saved on current topic (no rerouting)                       #
    # ------------------------------------------------------------------ #

    def test_decision_saved_on_current_topic_when_no_target(self) -> None:
        """target_topic_title=None → decision lands on the active topic."""
        self.adapter.topic_turn.return_value = _turn_with_decisions([
            {
                "statement": "Venue capacity capped at 200.",
                "rationale": "Insurance limit.",
                "extracted_from_turn_id": "turn-x",
                "target_topic_title": None,
            }
        ])
        payload = self._post_turn(self.venue_id, "Cap at 200 guests.")

        # No rerouting happened.
        self.assertEqual(payload["rerouted_decisions"], [])

        # Decision lives on the Venue topic.
        venue_decisions = self._list_decisions(self.venue_id)
        statements = [d["statement"] for d in venue_decisions]
        self.assertIn("Venue capacity capped at 200.", statements)

        # Budget topic is untouched.
        self.assertEqual(self._list_decisions(self.budget_id), [])

    # ------------------------------------------------------------------ #
    # Decision rerouted to sibling topic                                   #
    # ------------------------------------------------------------------ #

    def test_decision_rerouted_to_sibling_topic(self) -> None:
        """target_topic_title matching a sibling → decision lands there."""
        self.adapter.topic_turn.return_value = _turn_with_decisions([
            {
                "statement": "Total budget is $48k.",
                "rationale": "All-in cost estimate.",
                "extracted_from_turn_id": "turn-y",
                "target_topic_title": "Budget",  # matches sibling
            }
        ])
        payload = self._post_turn(self.venue_id, "We have 48k total.")

        # One rerouted decision returned in envelope.
        self.assertEqual(len(payload["rerouted_decisions"]), 1)
        rr = payload["rerouted_decisions"][0]
        self.assertEqual(rr["original_topic_id"], self.venue_id)
        self.assertEqual(rr["actual_topic_id"], self.budget_id)
        self.assertEqual(rr["actual_topic_title"], "Budget")
        self.assertIn("decision_id", rr)

        # Decision lives on the Budget topic, NOT Venue.
        budget_decisions = self._list_decisions(self.budget_id)
        statements = [d["statement"] for d in budget_decisions]
        self.assertIn("Total budget is $48k.", statements)

        venue_decisions = self._list_decisions(self.venue_id)
        venue_statements = [d["statement"] for d in venue_decisions]
        self.assertNotIn("Total budget is $48k.", venue_statements)

    # ------------------------------------------------------------------ #
    # Case-insensitive rerouting                                           #
    # ------------------------------------------------------------------ #

    def test_rerouting_is_case_insensitive(self) -> None:
        """Rerouting matches topic titles case-insensitively."""
        self.adapter.topic_turn.return_value = _turn_with_decisions([
            {
                "statement": "Keep costs under $50k.",
                "rationale": None,
                "extracted_from_turn_id": "turn-z",
                "target_topic_title": "budget",  # lower-case
            }
        ])
        payload = self._post_turn(self.venue_id, "Under 50k total.")
        self.assertEqual(len(payload["rerouted_decisions"]), 1)
        self.assertEqual(
            payload["rerouted_decisions"][0]["actual_topic_id"], self.budget_id
        )

    # ------------------------------------------------------------------ #
    # Unknown target title → falls back to current topic, no rerouting    #
    # ------------------------------------------------------------------ #

    def test_unknown_target_falls_back_to_current_topic(self) -> None:
        """target_topic_title not matching any sibling → stays on current topic."""
        self.adapter.topic_turn.return_value = _turn_with_decisions([
            {
                "statement": "Hire a sound technician.",
                "rationale": None,
                "extracted_from_turn_id": "turn-w",
                "target_topic_title": "Nonexistent Topic",
            }
        ])
        payload = self._post_turn(self.venue_id, "Need sound tech.")

        # No rerouting.
        self.assertEqual(payload["rerouted_decisions"], [])

        # Decision lands on Venue (fallback).
        venue_decisions = self._list_decisions(self.venue_id)
        statements = [d["statement"] for d in venue_decisions]
        self.assertIn("Hire a sound technician.", statements)

    # ------------------------------------------------------------------ #
    # Mixed: one stays, one rerouted                                       #
    # ------------------------------------------------------------------ #

    def test_mixed_decisions_some_rerouted_some_not(self) -> None:
        """Multiple proposals: some rerouted, some stay on current topic."""
        self.adapter.topic_turn.return_value = _turn_with_decisions([
            {
                "statement": "Indoor backup tent required.",
                "rationale": "Weather risk.",
                "extracted_from_turn_id": "turn-v",
                "target_topic_title": None,  # stays on Venue
            },
            {
                "statement": "Budget contingency is 15%.",
                "rationale": "Industry standard.",
                "extracted_from_turn_id": "turn-v",
                "target_topic_title": "Budget",  # goes to Budget
            },
        ])
        payload = self._post_turn(self.venue_id, "Need tent and contingency.")

        # One rerouted.
        self.assertEqual(len(payload["rerouted_decisions"]), 1)
        self.assertEqual(
            payload["rerouted_decisions"][0]["actual_topic_title"], "Budget"
        )

        # Venue got only the non-rerouted decision.
        venue_statements = [d["statement"] for d in self._list_decisions(self.venue_id)]
        self.assertIn("Indoor backup tent required.", venue_statements)
        self.assertNotIn("Budget contingency is 15%.", venue_statements)

        # Budget got the rerouted one.
        budget_statements = [d["statement"] for d in self._list_decisions(self.budget_id)]
        self.assertIn("Budget contingency is 15%.", budget_statements)

    # ------------------------------------------------------------------ #
    # rerouted_decisions always present in envelope                        #
    # ------------------------------------------------------------------ #

    def test_rerouted_decisions_field_present_when_empty(self) -> None:
        """rerouted_decisions key is always in the response envelope."""
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        payload = self._post_turn(self.venue_id, "Some answer.")
        self.assertIn("rerouted_decisions", payload)
        self.assertEqual(payload["rerouted_decisions"], [])


if __name__ == "__main__":
    unittest.main()
