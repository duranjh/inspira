"""Tests for checkpoint-based progress tracking.

Coverage:
1. First turn emits planned_checkpoints → they persist on topic metadata.
2. Subsequent turn's checkpoint_updates modify statuses.
3. ≥75% answered triggers suggest_close in the response.
4. Close endpoint sets topic status to fleshed_out.
5. Cross-user IDOR: can't close someone else's topic.
6. Metadata schema tolerates old topics with no checkpoints (graceful empty list).
"""
from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

try:
    from ._helpers import fake_turn_response, make_test_app, signup_and_login
except ImportError:
    from _helpers import fake_turn_response, make_test_app, signup_and_login  # type: ignore[no-redef]


def _kickoff_and_get_topic(client: Any, adapter: Any) -> dict[str, Any]:
    """Helper: create a project + kickoff + return the first topic dict."""
    # Create a project
    proj_res = client.post("/api/v2/projects", json={"title": "Checkpoint Test"})
    proj_res.raise_for_status()
    project_id = proj_res.json()["project"]["project_id"]

    # Stub kickoff so we can get topics
    from planning_studio_service.agents.schemas import CURATED_ICONS
    icon = list(CURATED_ICONS)[0]
    adapter.kickoff.return_value = {
        "domain": "business_plan",
        "domain_confidence": "high",
        "opening_card": {"body": "Let's plan this."},
        "topics": [
            {"title": "Pricing", "icon": icon, "why_this_topic": "Money."},
            {"title": "Users", "icon": icon, "why_this_topic": "People."},
        ],
        "relationships": [
            {"from_topic_title": "Pricing", "to_topic_title": "Users", "label": "informs"},
        ],
        "suggested_first_topic": "Pricing",
        "clarifying_question_if_too_vague": None,
        "_sanitize": {"dropped_relationships": [], "suggested_first_fallback": None, "auto_connected_orphans": []},
    }
    kickoff_res = client.post(
        f"/api/v2/projects/{project_id}/kickoff",
        json={"user_idea": "B2B SaaS product", "attached_sources": []},
    )
    kickoff_res.raise_for_status()
    topics = kickoff_res.json()["topics"]
    return topics[0]  # "Pricing" topic


class TestFirstTurnCheckpoints(unittest.TestCase):
    """First turn emits planned_checkpoints which persist on topic metadata."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="a@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_first_turn_persists_planned_checkpoints(self) -> None:
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        planned = [
            {"id": "price_point", "question": "What's the price point?"},
            {"id": "billing_cadence", "question": "What's the billing cadence?"},
            {"id": "trial_structure", "question": "What's the free / trial structure?"},
            {"id": "enterprise_discounting", "question": "Enterprise discounting policy?"},
        ]
        self.adapter.topic_turn.return_value = fake_turn_response(
            action="ask",
            planned_checkpoints=planned,
            checkpoint_updates=None,
        )

        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "", "attached_sources": []},
        )
        res.raise_for_status()

        # Verify checkpoints are returned in the envelope
        envelope = res.json()
        self.assertIn("checkpoints", envelope)
        self.assertEqual(len(envelope["checkpoints"]), 4)
        self.assertEqual(envelope["checkpoints"][0]["id"], "price_point")
        self.assertEqual(envelope["checkpoints"][0]["status"], "open")

        # Verify persisted to topic metadata
        from_db = self.store.get_topic(topic_id)
        meta_cps = from_db["metadata"].get("checkpoints", [])
        self.assertEqual(len(meta_cps), 4)
        self.assertTrue(all(cp["status"] == "open" for cp in meta_cps))

    def test_first_turn_empty_checkpoints_still_ok(self) -> None:
        """A first turn with no planned_checkpoints (null) is gracefully handled."""
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        self.adapter.topic_turn.return_value = fake_turn_response(
            action="ask",
            planned_checkpoints=None,
            checkpoint_updates=None,
        )

        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "", "attached_sources": []},
        )
        res.raise_for_status()
        envelope = res.json()
        self.assertEqual(envelope["checkpoints"], [])


class TestSubsequentTurnCheckpointUpdates(unittest.TestCase):
    """Subsequent turns apply checkpoint_updates to the persisted list."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="b@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_checkpoints(self, topic_id: str, checkpoints: list[dict]) -> None:
        """Directly seed checkpoints into topic metadata via store."""
        self.store.update_topic_checkpoints(topic_id, None, checkpoints)

    def test_checkpoint_updates_modify_statuses(self) -> None:
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        initial = [
            {"id": "price_point", "question": "What's the price point?", "status": "open", "answered_in_turn_id": None},
            {"id": "billing_cadence", "question": "What's the billing cadence?", "status": "open", "answered_in_turn_id": None},
            {"id": "trial_structure", "question": "What's the trial structure?", "status": "open", "answered_in_turn_id": None},
        ]
        self._init_checkpoints(topic_id, initial)

        # First turn (opens interview)
        self.adapter.topic_turn.return_value = fake_turn_response(
            action="ask",
            planned_checkpoints=None,
            checkpoint_updates=None,
        )
        self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "", "attached_sources": []},
        ).raise_for_status()

        # Second turn — user answers price point
        self.adapter.topic_turn.return_value = fake_turn_response(
            action="ask",
            planned_checkpoints=None,
            checkpoint_updates=[
                {"id": "price_point", "status": "answered"},
                {"id": "billing_cadence", "status": "partial"},
            ],
        )
        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "We charge $49/month per seat.", "attached_sources": []},
        )
        res.raise_for_status()

        envelope = res.json()
        cp_map = {cp["id"]: cp for cp in envelope["checkpoints"]}
        self.assertEqual(cp_map["price_point"]["status"], "answered")
        self.assertEqual(cp_map["billing_cadence"]["status"], "partial")
        self.assertEqual(cp_map["trial_structure"]["status"], "open")  # unchanged


class TestSuggestCloseThreshold(unittest.TestCase):
    """When ≥75% checkpoints are answered, action is suggest_close."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="c@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_suggest_close_action_returned(self) -> None:
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        # Seed 4 checkpoints, 3 of which are already answered (75%)
        initial = [
            {"id": "a", "question": "Q A?", "status": "answered", "answered_in_turn_id": None},
            {"id": "b", "question": "Q B?", "status": "answered", "answered_in_turn_id": None},
            {"id": "c", "question": "Q C?", "status": "answered", "answered_in_turn_id": None},
            {"id": "d", "question": "Q D?", "status": "open", "answered_in_turn_id": None},
        ]
        self.store.update_topic_checkpoints(topic_id, None, initial)

        self.adapter.topic_turn.return_value = fake_turn_response(
            action="suggest_close",
            planned_checkpoints=None,
            checkpoint_updates=None,
        )
        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "That covers it.", "attached_sources": []},
        )
        res.raise_for_status()
        envelope = res.json()
        self.assertEqual(envelope["turn_result"]["action"], "suggest_close")
        # suggest_close no longer nulls the question — it should have the close prompt
        self.assertIsNotNone(envelope["turn_result"]["question"])


class TestAutoSuggestCloseOnFullCheckpoints(unittest.TestCase):
    """When every checkpoint ends up ``answered`` and the LLM didn't emit
    ``suggest_close``, the API layer overrides the turn to a synthetic close
    prompt so the Q&A never stops silently.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="full@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_all_answered_overrides_to_suggest_close(self) -> None:
        """Full checkpoints + LLM forgot: API synthesises the close prompt."""
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        # Seed 3 checkpoints: two already answered, one open.
        initial = [
            {"id": "a", "question": "Q A?", "status": "answered", "answered_in_turn_id": None},
            {"id": "b", "question": "Q B?", "status": "answered", "answered_in_turn_id": None},
            {"id": "c", "question": "Q C?", "status": "open", "answered_in_turn_id": None},
        ]
        self.store.update_topic_checkpoints(topic_id, None, initial)

        # LLM flips the last checkpoint to answered but still returns "ask".
        # It forgot to close out — the API layer should override.
        self.adapter.topic_turn.return_value = fake_turn_response(
            action="ask",
            planned_checkpoints=None,
            checkpoint_updates=[{"id": "c", "status": "answered"}],
        )
        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "All good.", "attached_sources": []},
        )
        res.raise_for_status()
        envelope = res.json()

        # action overridden to suggest_close
        self.assertEqual(envelope["turn_result"]["action"], "suggest_close")
        # synthetic question references the topic title + checkpoints
        question = envelope["turn_result"]["question"]
        self.assertIsNotNone(question)
        self.assertIn("checkpoint", question.lower())
        # canonical suggestions
        intents = {s["intent"] for s in envelope["turn_result"]["suggested_responses"]}
        self.assertEqual(intents, {"close", "continue"})
        # suggest_close path → no planner turn persisted
        self.assertIsNone(envelope["planner_turn"])
        # all checkpoints now answered
        self.assertTrue(all(cp["status"] == "answered" for cp in envelope["checkpoints"]))

    def test_all_answered_llm_already_closes_preserves_llm_output(self) -> None:
        """Full checkpoints + LLM already closed: API doesn't double-override."""
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        initial = [
            {"id": "a", "question": "Q A?", "status": "answered", "answered_in_turn_id": None},
            {"id": "b", "question": "Q B?", "status": "answered", "answered_in_turn_id": None},
            {"id": "c", "question": "Q C?", "status": "open", "answered_in_turn_id": None},
        ]
        self.store.update_topic_checkpoints(topic_id, None, initial)

        # LLM sets suggest_close itself — API must respect that, not overwrite.
        self.adapter.topic_turn.return_value = fake_turn_response(
            action="suggest_close",
            planned_checkpoints=None,
            checkpoint_updates=[{"id": "c", "status": "answered"}],
        )
        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "That's it.", "attached_sources": []},
        )
        res.raise_for_status()
        envelope = res.json()

        # Still suggest_close, but the LLM-provided question survives
        self.assertEqual(envelope["turn_result"]["action"], "suggest_close")
        question = envelope["turn_result"]["question"]
        # The stub's suggest_close question — NOT the synthetic one.
        self.assertIsNotNone(question)
        self.assertIn("close this topic", question.lower())
        self.assertIsNone(envelope["planner_turn"])


class TestCloseTopicEndpoint(unittest.TestCase):
    """Close endpoint sets topic status to fleshed_out."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="d@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_close_sets_fleshed_out(self) -> None:
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        res = self.client.post(f"/api/v2/topics/{topic_id}/close", json={})
        res.raise_for_status()
        payload = res.json()
        self.assertEqual(payload["topic"]["status"], "fleshed_out")

        # Confirm persisted in DB
        from_db = self.store.get_topic(topic_id)
        self.assertEqual(from_db["status"], "fleshed_out")

    def test_close_unknown_topic_returns_404(self) -> None:
        res = self.client.post("/api/v2/topics/topic-does-not-exist/close", json={})
        self.assertEqual(res.status_code, 404)


class TestCloseTopicIDOR(unittest.TestCase):
    """Cross-user IDOR: user B cannot close user A's topic."""

    def setUp(self) -> None:
        self.client_a, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client_a, email="alice@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cannot_close_another_users_topic(self) -> None:
        topic = _kickoff_and_get_topic(self.client_a, self.adapter)
        topic_id = topic["topic_id"]

        # Bob uses the same app but different client (different session cookie)
        from fastapi.testclient import TestClient
        from planning_studio_service.api import create_app
        from planning_studio_service.config import load_config
        import os
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        bob_store = self.store  # same DB
        bob_adapter = MagicMock()
        bob_app = create_app(store=bob_store, adapter=bob_adapter)
        client_b = TestClient(bob_app)
        signup_and_login(client_b, email="bob@example.com", password="password456")

        res = client_b.post(f"/api/v2/topics/{topic_id}/close", json={})
        # Should be 404 (topic not found for this user — IDOR protection)
        self.assertEqual(res.status_code, 404)


class TestOldTopicsGraceful(unittest.TestCase):
    """Old topics with no checkpoints in metadata get an empty list gracefully."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="e@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_old_topic_no_checkpoints_returns_empty_list(self) -> None:
        topic = _kickoff_and_get_topic(self.client, self.adapter)
        topic_id = topic["topic_id"]

        # The topic has no checkpoints in metadata (old topic).
        # A turn with no planned_checkpoints and no checkpoint_updates should
        # return an empty checkpoints list — not raise.
        self.adapter.topic_turn.return_value = fake_turn_response(
            action="ask",
            planned_checkpoints=None,
            checkpoint_updates=None,
        )
        res = self.client.post(
            f"/api/v2/topics/{topic_id}/turn",
            json={"user_answer": "", "attached_sources": []},
        )
        res.raise_for_status()
        envelope = res.json()
        self.assertEqual(envelope["checkpoints"], [])


if __name__ == "__main__":
    unittest.main()
