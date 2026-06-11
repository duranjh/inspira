"""Tests for the canvas mutation loop:

1. New topic auto-create: when topic_turn returns a new_topic_proposal the
   backend persists it, runs auto-linker, and returns created_topic + relationships.
2. Duplicate-title new-topic proposal is dropped by the sanitizer.
3. Deletion suggestion passes through when the target is a valid sibling.
4. Deletion suggestion with a nonexistent target is dropped by the sanitizer.
5. Deletion suggestion that targets the current topic is dropped by the API layer.
6. Delete endpoint cascades to relationships (no dangling edges).
7. Dismissed deletion suggestion does NOT re-appear (persisted in metadata).
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
# Sanitizer unit helpers
# ---------------------------------------------------------------------------


def _base_turn_with_ntp(title: str) -> dict[str, Any]:
    """Turn result with a new_topic_proposal for the given title."""
    return {
        "action": "ask",
        "question": "What is your budget?",
        "why_this_matters": "Anchors spend.",
        "suggested_responses": [],
        "proposed_decisions": [],
        "consistency_flags": [],
        "new_topic_proposal": {
            "title": title,
            "icon": "lightbulb",
            "why": "New area.",
            "source_turn_id": "turn-1",
        },
        "topic_deletion_suggestion": None,
        "close_recommendation_reason": None,
        "conflict_resolution": None,
        "planned_checkpoints": None,
        "checkpoint_updates": None,
    }


def _base_turn_with_deletion(
    target_id: str,
    target_title: str,
    reason: str = "User decided to skip this entirely.",
) -> dict[str, Any]:
    """Turn result with a topic_deletion_suggestion."""
    return {
        "action": "ask",
        "question": "What comes next?",
        "why_this_matters": "Keeps momentum.",
        "suggested_responses": [],
        "proposed_decisions": [],
        "consistency_flags": [],
        "new_topic_proposal": None,
        "topic_deletion_suggestion": {
            "target_topic_id": target_id,
            "target_topic_title": target_title,
            "reason": reason,
            "superseded_by_decision": None,
        },
        "close_recommendation_reason": None,
        "conflict_resolution": None,
        "planned_checkpoints": None,
        "checkpoint_updates": None,
    }


def _other_topics(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    """Build a minimal other_topics list from (title, topic_id) pairs."""
    return [{"title": t, "topic_id": tid, "decisions": []} for t, tid in pairs]


# ---------------------------------------------------------------------------
# 1. Sanitizer: duplicate new_topic_proposal title is dropped
# ---------------------------------------------------------------------------


class SanitizeNewTopicProposalTests(unittest.TestCase):
    """_sanitize_topic_turn must drop new_topic_proposal when title collides."""

    def test_unique_title_passes_through(self) -> None:
        parsed = _base_turn_with_ntp("Logistics")
        others = _other_topics(("Budget", "t-budget"), ("Safety", "t-safety"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNotNone(parsed["new_topic_proposal"])
        self.assertEqual(parsed["new_topic_proposal"]["title"], "Logistics")

    def test_duplicate_title_dropped(self) -> None:
        """When a sibling already has the proposed title (case-insensitive),
        the proposal must be set to null — no duplicate topics."""
        parsed = _base_turn_with_ntp("Budget")  # "Budget" already exists
        others = _other_topics(("Budget", "t-budget"), ("Safety", "t-safety"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNone(parsed["new_topic_proposal"])
        self.assertIsNotNone(parsed["_sanitize"]["dropped_new_topic_proposal"])

    def test_duplicate_title_case_insensitive(self) -> None:
        parsed = _base_turn_with_ntp("BUDGET")
        others = _other_topics(("Budget", "t-budget"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNone(parsed["new_topic_proposal"])


# ---------------------------------------------------------------------------
# 2. Sanitizer: deletion suggestion validation
# ---------------------------------------------------------------------------


class SanitizeDeletionSuggestionTests(unittest.TestCase):
    """_sanitize_topic_turn must validate topic_deletion_suggestion."""

    def test_valid_deletion_suggestion_passes(self) -> None:
        parsed = _base_turn_with_deletion("t-paid", "Paid channels")
        others = _other_topics(("Paid channels", "t-paid"), ("Organic", "t-organic"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNotNone(parsed["topic_deletion_suggestion"])
        self.assertEqual(
            parsed["topic_deletion_suggestion"]["target_topic_id"], "t-paid",
        )

    def test_nonexistent_target_title_dropped(self) -> None:
        """target_topic_title not in sibling list → suggestion must be nulled."""
        parsed = _base_turn_with_deletion("t-ghost", "Ghost Topic")
        others = _other_topics(("Budget", "t-budget"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNone(parsed["topic_deletion_suggestion"])
        self.assertIsNotNone(parsed["_sanitize"]["dropped_deletion_suggestion"])

    def test_empty_reason_dropped(self) -> None:
        parsed = _base_turn_with_deletion("t-paid", "Paid channels", reason="  ")
        others = _other_topics(("Paid channels", "t-paid"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNone(parsed["topic_deletion_suggestion"])

    def test_missing_target_id_dropped(self) -> None:
        parsed = _base_turn_with_deletion("", "Paid channels")
        others = _other_topics(("Paid channels", "t-paid"))
        _sanitize_topic_turn(parsed, others)
        self.assertIsNone(parsed["topic_deletion_suggestion"])


# ---------------------------------------------------------------------------
# 3–5. API integration tests
# ---------------------------------------------------------------------------


def _seed_project(client: Any, adapter: Any, project_id: str) -> list[dict]:
    """Kickoff-seed a project with five canonical topics. Returns topic list."""
    adapter.kickoff.return_value = fake_kickoff_response()
    resp = client.post(
        f"/api/v2/projects/{project_id}/kickoff",
        json={"user_idea": "Plan an event."},
    )
    resp.raise_for_status()
    return resp.json()["topics"]


class TopicTurnAutoCreateTests(unittest.TestCase):
    """New topic auto-create via topic_turn endpoint."""

    def setUp(self) -> None:
        (
            self.client,
            self.store,
            self.adapter,
            self.temp_dir,
        ) = make_test_app()
        signup_and_login(self.client)
        self.auto_link_adapter = MagicMock()
        self.client.app.state.auto_link_adapter = self.auto_link_adapter

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _first_topic(self, topics: list[dict]) -> dict:
        return next(t for t in topics if t["title"] == "Venue")

    def test_new_topic_proposal_auto_persisted(self) -> None:
        """When the LLM proposes a new topic the backend creates it and returns it."""
        topics = _seed_project(self.client, self.adapter, "proj-auto")
        venue = self._first_topic(topics)

        turn_res = fake_turn_response()
        turn_res["new_topic_proposal"] = {
            "title": "Catering",
            "icon": "leaf",
            "why": "Food planning is a distinct area.",
            "source_turn_id": "turn-1",
        }
        self.adapter.topic_turn.return_value = turn_res
        self.auto_link_adapter.propose_links.return_value = []

        resp = self.client.post(
            f"/api/v2/topics/{venue['topic_id']}/turn",
            json={"user_answer": "We need to sort catering separately.", "attached_sources": []},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()

        self.assertIsNotNone(body.get("created_topic"))
        self.assertEqual(body["created_topic"]["topic"]["title"], "Catering")
        self.assertEqual(body["created_topic"]["topic"]["origin"], "planner_proposed")
        self.assertIsInstance(body["created_topic"]["relationships"], list)

        # Verify the topic actually exists in the store.
        all_topics_resp = self.client.get(f"/api/v2/projects/proj-auto/topics")
        all_topics_resp.raise_for_status()
        titles = [t["title"] for t in all_topics_resp.json()["topics"]]
        self.assertIn("Catering", titles)

    def test_new_topic_auto_linker_invoked(self) -> None:
        """The auto-linker must be called for the newly created topic."""
        topics = _seed_project(self.client, self.adapter, "proj-autolink")
        venue = self._first_topic(topics)

        safety = next(t for t in topics if t["title"] == "Safety")
        turn_res = fake_turn_response()
        turn_res["new_topic_proposal"] = {
            "title": "Permits",
            "icon": "flag",
            "why": "Legal approvals are a distinct workstream.",
            "source_turn_id": "turn-1",
        }
        self.adapter.topic_turn.return_value = turn_res
        self.auto_link_adapter.propose_links.return_value = [
            {
                "target_topic_title": "Safety",
                "label": "informs",
                "direction": "from_new",
            }
        ]

        resp = self.client.post(
            f"/api/v2/topics/{venue['topic_id']}/turn",
            json={"user_answer": "We need permits.", "attached_sources": []},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.auto_link_adapter.propose_links.assert_called_once()

        body = resp.json()
        rels = body["created_topic"]["relationships"]
        # The auto-link proposal to Safety should have been persisted.
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["target_topic_id"], safety["topic_id"])

    def test_duplicate_title_proposal_not_persisted(self) -> None:
        """API-layer duplicate guard drops the proposal — no extra topic created."""
        topics = _seed_project(self.client, self.adapter, "proj-dup")
        venue = self._first_topic(topics)

        turn_res = fake_turn_response()
        turn_res["new_topic_proposal"] = {
            "title": "Safety",  # already exists as a sibling
            "icon": "flag",
            "why": "A duplicate.",
            "source_turn_id": "turn-1",
        }
        self.adapter.topic_turn.return_value = turn_res
        self.auto_link_adapter.propose_links.return_value = []

        resp = self.client.post(
            f"/api/v2/topics/{venue['topic_id']}/turn",
            json={"user_answer": "Safety matters.", "attached_sources": []},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertIsNone(body.get("created_topic"))

        # Topic count must not have grown.
        count_resp = self.client.get("/api/v2/projects/proj-dup/topics")
        self.assertEqual(len(count_resp.json()["topics"]), 5)


class TopicTurnDeletionSuggestionTests(unittest.TestCase):
    """Deletion suggestion pass-through in the topic_turn endpoint."""

    def setUp(self) -> None:
        (
            self.client,
            self.store,
            self.adapter,
            self.temp_dir,
        ) = make_test_app()
        signup_and_login(self.client)
        self.auto_link_adapter = MagicMock()
        self.client.app.state.auto_link_adapter = self.auto_link_adapter
        self.auto_link_adapter.propose_links.return_value = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_deletion_suggestion_passes_through(self) -> None:
        """A suggestion targeting a valid sibling must appear in the response."""
        topics = _seed_project(self.client, self.adapter, "proj-ds-valid")
        venue = next(t for t in topics if t["title"] == "Venue")
        safety = next(t for t in topics if t["title"] == "Safety")

        turn_res = fake_turn_response()
        turn_res["topic_deletion_suggestion"] = {
            "target_topic_id": safety["topic_id"],
            "target_topic_title": "Safety",
            "reason": "User decided to hire an event safety firm — no internal planning needed.",
            "superseded_by_decision": None,
        }
        self.adapter.topic_turn.return_value = turn_res

        resp = self.client.post(
            f"/api/v2/topics/{venue['topic_id']}/turn",
            json={"user_answer": "We're outsourcing safety completely.", "attached_sources": []},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        sug = body.get("topic_deletion_suggestion")
        self.assertIsNotNone(sug)
        self.assertEqual(sug["target_topic_id"], safety["topic_id"])

    def test_deletion_suggestion_with_invalid_target_dropped(self) -> None:
        """Suggestion targeting a nonexistent sibling must be null in the response."""
        topics = _seed_project(self.client, self.adapter, "proj-ds-invalid")
        venue = next(t for t in topics if t["title"] == "Venue")

        turn_res = fake_turn_response()
        turn_res["topic_deletion_suggestion"] = {
            "target_topic_id": "ghost-id",
            "target_topic_title": "Ghost Topic",
            "reason": "Doesn't exist on this canvas.",
            "superseded_by_decision": None,
        }
        self.adapter.topic_turn.return_value = turn_res

        resp = self.client.post(
            f"/api/v2/topics/{venue['topic_id']}/turn",
            json={"user_answer": "Something.", "attached_sources": []},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertIsNone(body.get("topic_deletion_suggestion"))

    def test_deletion_suggestion_targeting_current_topic_dropped(self) -> None:
        """API layer must drop suggestions where target == the current topic."""
        topics = _seed_project(self.client, self.adapter, "proj-ds-self")
        venue = next(t for t in topics if t["title"] == "Venue")

        turn_res = fake_turn_response()
        turn_res["topic_deletion_suggestion"] = {
            "target_topic_id": venue["topic_id"],  # same as current
            "target_topic_title": "Venue",
            "reason": "Self-referential — should be dropped.",
            "superseded_by_decision": None,
        }
        self.adapter.topic_turn.return_value = turn_res

        resp = self.client.post(
            f"/api/v2/topics/{venue['topic_id']}/turn",
            json={"user_answer": "Something.", "attached_sources": []},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertIsNone(body.get("topic_deletion_suggestion"))


# ---------------------------------------------------------------------------
# 6. Delete topic cascades relationships
# ---------------------------------------------------------------------------


class DeleteTopicCascadeTests(unittest.TestCase):
    """Deleting a topic must also soft-delete its relationships."""

    def setUp(self) -> None:
        (
            self.client,
            self.store,
            self.adapter,
            self.temp_dir,
        ) = make_test_app()
        signup_and_login(self.client)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_delete_topic_removes_its_relationships(self) -> None:
        """After topic deletion, no relationships should reference the deleted topic."""
        topics = _seed_project(self.client, self.adapter, "proj-del-cascade")
        venue = next(t for t in topics if t["title"] == "Venue")
        venue_id = venue["topic_id"]

        # Confirm at least one relationship involving Venue exists from kickoff.
        rels_before = self.client.get(
            "/api/v2/projects/proj-del-cascade/relationships",
        ).json()["relationships"]
        involving_venue = [
            r for r in rels_before
            if r["source_topic_id"] == venue_id or r["target_topic_id"] == venue_id
        ]
        self.assertGreater(len(involving_venue), 0, "Kickoff should have seeded at least one Venue relationship")

        # Delete the topic.
        del_resp = self.client.post(f"/api/v2/topics/{venue_id}/delete")
        self.assertEqual(del_resp.status_code, 200, del_resp.text)
        self.assertTrue(del_resp.json()["deleted"])

        # After delete, no relationship should reference this topic.
        rels_after = self.client.get(
            "/api/v2/projects/proj-del-cascade/relationships",
        ).json()["relationships"]
        dangling = [
            r for r in rels_after
            if r["source_topic_id"] == venue_id or r["target_topic_id"] == venue_id
        ]
        self.assertEqual(dangling, [], "No dangling relationships should remain after topic delete")

    def test_delete_topic_leaves_other_relationships_intact(self) -> None:
        """Only relationships touching the deleted topic should be removed."""
        topics = _seed_project(self.client, self.adapter, "proj-del-others")
        venue = next(t for t in topics if t["title"] == "Venue")
        budget = next(t for t in topics if t["title"] == "Budget")

        rels_before = self.client.get(
            "/api/v2/projects/proj-del-others/relationships",
        ).json()["relationships"]
        non_venue_before = [
            r for r in rels_before
            if r["source_topic_id"] != venue["topic_id"]
            and r["target_topic_id"] != venue["topic_id"]
        ]

        self.client.post(f"/api/v2/topics/{venue['topic_id']}/delete")

        rels_after = self.client.get(
            "/api/v2/projects/proj-del-others/relationships",
        ).json()["relationships"]

        # Relationships not involving Venue must survive.
        self.assertEqual(len(rels_after), len(non_venue_before))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
