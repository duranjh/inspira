"""Cross-user IDOR prevention (audit finding C2).

The audit found that every read/write route that touches a topic,
decision, relationship, or project had to gate on
``verify_project_ownership``. This suite spins up two authenticated
users against the SAME app instance (same store, same session
secret) and proves that user B cannot read or mutate anything user A
created.

Every probe checks for 404 — NOT 403 — because the ownership helpers
in api.py deliberately conflate "doesn't exist" with "not yours" to
prevent ID enumeration. A 403 would tell an attacker "that ID is real,
keep probing"; a 404 leaks nothing.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

try:
    # Works when tests are invoked as ``services.tests.test_ownership``
    from ._helpers import fake_kickoff_response, make_test_app, signup_and_login
except ImportError:
    # Works under ``python -m unittest discover -s services/tests`` where the
    # tests package context isn't set up for relative imports.
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )


class CrossUserOwnershipTests(unittest.TestCase):
    """Two clients, two users, one shared app/store."""

    def setUp(self) -> None:
        # Build one app, bind two TestClients. Each client has its own
        # cookie jar so signup on ``self.a`` does NOT authenticate
        # ``self.b`` — exactly the two-browsers scenario we want.
        self.a, self.store, self.adapter, self.temp_dir = make_test_app()
        self.b = TestClient(self.a.app)

        signup_and_login(self.a, email="alice@example.com", password="alice-pw-1")
        signup_and_login(self.b, email="bob@example.com", password="bob-pw-123")

        # Wire up the planner mock and let user A kick off a project.
        # We capture the resulting IDs so user B can try to probe them.
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.project_id = "proj-alices"
        kick = self.a.post(
            f"/api/v2/projects/{self.project_id}/kickoff",
            json={"user_idea": "A small outdoor wine festival."},
        ).json()
        self.topic_id = kick["topics"][0]["topic_id"]

        # Also put a decision on alice's topic so we can probe that too.
        created = self.a.post(
            f"/api/v2/topics/{self.topic_id}/decisions",
            json={"statement": "No single-use plastics."},
        ).json()
        self.decision_id = created["decision"]["decision_id"]

        # Reset the adapter mock after setup so per-test assertions on
        # kickoff/topic_turn call counts start from zero.
        self.adapter.reset_mock()
        self.adapter.kickoff.return_value = fake_kickoff_response()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ---------- Read-path probes (user B attempts to read A's data) -----

    def test_user_b_cannot_list_user_a_topics(self) -> None:
        """GET project topics for someone else's project → 404."""
        response = self.b.get(f"/api/v2/projects/{self.project_id}/topics")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "project_not_found",
        )

    def test_user_b_cannot_list_user_a_topic_turns(self) -> None:
        response = self.b.get(f"/api/v2/topics/{self.topic_id}/turns")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "topic_not_found",
        )

    def test_user_b_cannot_list_user_a_topic_decisions(self) -> None:
        response = self.b.get(f"/api/v2/topics/{self.topic_id}/decisions")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "topic_not_found",
        )

    def test_user_b_cannot_list_user_a_project_decisions(self) -> None:
        response = self.b.get(f"/api/v2/projects/{self.project_id}/decisions")
        self.assertEqual(response.status_code, 404)

    def test_user_b_cannot_list_user_a_relationships(self) -> None:
        response = self.b.get(
            f"/api/v2/projects/{self.project_id}/relationships",
        )
        self.assertEqual(response.status_code, 404)

    # ---------- Write-path probes (user B attempts to mutate A's data) --

    def test_user_b_cannot_update_user_a_topic(self) -> None:
        response = self.b.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"title": "pwnd"},
        )
        self.assertEqual(response.status_code, 404)

        # Confirm the title wasn't actually changed — read via user A
        topics = self.a.get(
            f"/api/v2/projects/{self.project_id}/topics",
        ).json()["topics"]
        matching = [t for t in topics if t["topic_id"] == self.topic_id]
        self.assertEqual(len(matching), 1)
        self.assertNotEqual(matching[0]["title"], "pwnd")

    def test_user_b_cannot_delete_user_a_topic(self) -> None:
        response = self.b.post(f"/api/v2/topics/{self.topic_id}/delete")
        self.assertEqual(response.status_code, 404)

        # The topic must still be visible to user A
        topics = self.a.get(
            f"/api/v2/projects/{self.project_id}/topics",
        ).json()["topics"]
        ids = {t["topic_id"] for t in topics}
        self.assertIn(self.topic_id, ids)

    def test_user_b_cannot_delete_user_a_decision(self) -> None:
        response = self.b.post(f"/api/v2/decisions/{self.decision_id}/delete")
        self.assertEqual(response.status_code, 404)

        # User A still sees the decision
        decisions = self.a.get(
            f"/api/v2/topics/{self.topic_id}/decisions",
        ).json()["decisions"]
        ids = {d["decision_id"] for d in decisions}
        self.assertIn(self.decision_id, ids)

    def test_user_b_cannot_kickoff_on_user_a_project(self) -> None:
        """Kickoff on someone else's project → 404, no LLM call.

        This is the pre-flight ownership check in ``v2_kickoff`` — we
        don't burn OpenAI tokens for a request we'd reject. The
        adapter mock is checked to confirm it was never invoked.
        """
        response = self.b.post(
            f"/api/v2/projects/{self.project_id}/kickoff",
            json={"user_idea": "let me in"},
        )
        self.assertEqual(response.status_code, 404)
        self.adapter.kickoff.assert_not_called()

    def test_user_b_cannot_run_topic_turn_on_user_a_topic(self) -> None:
        response = self.b.post(
            f"/api/v2/topics/{self.topic_id}/turn",
            json={"user_answer": "hello from B"},
        )
        self.assertEqual(response.status_code, 404)
        self.adapter.topic_turn.assert_not_called()

    # ---------- Project listings stay segregated -----------------------

    def test_project_list_is_per_user(self) -> None:
        """Each user's ``/api/v2/projects`` returns only their own rows."""
        a_projects = self.a.get("/api/v2/projects").json()["projects"]
        b_projects = self.b.get("/api/v2/projects").json()["projects"]

        a_ids = {p["project_id"] for p in a_projects}
        b_ids = {p["project_id"] for p in b_projects}
        self.assertIn(self.project_id, a_ids)
        self.assertNotIn(self.project_id, b_ids)
        # B has no projects yet
        self.assertEqual(b_projects, [])


if __name__ == "__main__":
    unittest.main()
