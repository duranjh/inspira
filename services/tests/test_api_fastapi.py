"""End-to-end HTTP tests for the FastAPI v2 routes via TestClient.

These exercise the actual URL paths, status codes, and JSON shapes the
web client depends on — without hitting OpenAI (the adapter is a
``MagicMock``). Covers:

- ``/api/health`` returns the trimmed payload (no filesystem paths).
- ``/api/v2/projects`` CRUD: list-empty, list-after-create, delete,
  and the known-broken POST-create route (see note inline).
- ``/api/v2/projects/{id}/kickoff`` happy path (topics + relationships
  persisted) and the ``user_idea`` length cap.
- ``/api/v2/topics/{id}/turn`` happy path (planner turn persisted).
- ``/api/v2/topics/{id}/update`` position-change round-trip.
- ``/api/v2/decisions/{id}/delete`` soft-delete + list exclusion.

The tests deliberately do NOT verify auth ownership here — that's
``test_ownership.py`` — but every test signs in first so the
"user-system" fallback doesn't mask anything.
"""
from __future__ import annotations

import unittest

try:
    # Works when tests are invoked as ``services.tests.test_api_fastapi``
    from ._helpers import (
        fake_kickoff_response,
        fake_kickoff_response_with_qa,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    # Works under ``python -m unittest discover -s services/tests`` where the
    # tests package context isn't set up for relative imports.
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        fake_kickoff_response_with_qa,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )


class HealthTests(unittest.TestCase):
    """``/api/health`` is unauthenticated — no need to sign in first."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_returns_trimmed_payload(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service"], "planning-studio")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("generated_at", payload)

    def test_health_does_not_leak_filesystem_paths(self) -> None:
        """Trimmed variant — no ``db_path`` / ``storage_root`` (reconnaissance)."""
        response = self.client.get("/api/health")
        payload = response.json()
        # These fields are present in the untrimmed ``store.health()``
        # dict but must NOT escape the HTTP boundary.
        for leaky_key in ("db_path", "storage_root", "sessions_root", "artifacts_root"):
            self.assertNotIn(leaky_key, payload)


class V2ProjectsTests(unittest.TestCase):
    """GET, DELETE, plus the known-broken POST-create branch."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="u@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fresh_user_has_no_projects(self) -> None:
        response = self.client.get("/api/v2/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"projects": []})

    def test_kickoff_creates_project_and_list_returns_it(self) -> None:
        """Project creation via the kickoff path; list round-trips it.

        We use the kickoff path rather than the direct POST
        /api/v2/projects because that direct route has a ForwardRef
        bug (``ProjectCreateBody`` is declared inside ``create_app``
        and FastAPI misclassifies the body as a query param — see
        ``test_create_project_route_is_known_broken`` below). In the
        meantime, the canonical way a project gets created is via
        ``ensure_project`` inside the kickoff handler, which IS fully
        covered by this test.
        """
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-from-kickoff/kickoff",
            json={"user_idea": "A small outdoor wine festival."},
        )
        self.assertEqual(kick.status_code, 201)

        listed = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["project_id"], "proj-from-kickoff")
        # Title is auto-derived by ``ensure_project`` from the last 6 chars
        # of the project_id when no explicit title is passed.
        self.assertTrue(listed[0]["title"].startswith("Project "))

    def test_create_project_route_is_known_broken(self) -> None:
        """Documents the ProjectCreateBody ForwardRef bug in api.py.

        ``ProjectCreateBody`` is defined INSIDE ``create_app`` (see
        api.py lines ~692-710). Pydantic couldn't resolve the forward
        reference at route build time, so FastAPI reclassified the
        body as a query parameter and every request failed 422.

        FIX landed: the ProjectCreateBody / ProjectUpdateBody models
        moved to module scope. This test now verifies the ROUND TRIP
        (POST creates, GET lists it) so a regression that re-nests
        the models flips this test red.
        """
        response = self.client.post(
            "/api/v2/projects", json={"title": "My Project"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json().get("project") or {}
        self.assertEqual(created.get("title"), "My Project")
        self.assertTrue(created.get("project_id"))

        listed = self.client.get("/api/v2/projects").json().get("projects") or []
        titles = [p["title"] for p in listed]
        self.assertIn("My Project", titles)

    def test_delete_project_removes_it_from_list(self) -> None:
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.client.post(
            "/api/v2/projects/proj-to-delete/kickoff",
            json={"user_idea": "idea"},
        )
        # Sanity
        self.assertEqual(
            len(self.client.get("/api/v2/projects").json()["projects"]), 1,
        )
        resp = self.client.post("/api/v2/projects/proj-to-delete/delete")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("deleted"))
        # List now empty
        self.assertEqual(
            self.client.get("/api/v2/projects").json()["projects"], [],
        )


class V2KickoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="k@example.com", password="password123")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_kickoff_persists_topics_and_relationships(self) -> None:
        self.adapter.kickoff.return_value = fake_kickoff_response()
        response = self.client.post(
            "/api/v2/projects/proj-kickoff/kickoff",
            json={"user_idea": "A small outdoor wine festival."},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(len(payload["topics"]), 5)
        self.assertEqual(len(payload["relationships"]), 2)
        # All relationships reference real topic IDs
        topic_ids = {t["topic_id"] for t in payload["topics"]}
        for rel in payload["relationships"]:
            self.assertIn(rel["source_topic_id"], topic_ids)
            self.assertIn(rel["target_topic_id"], topic_ids)
        self.adapter.kickoff.assert_called_once()

    def test_kickoff_rejects_idea_over_8000_chars(self) -> None:
        """``KickoffBody.user_idea`` caps at 8000 chars — 422 before LLM."""
        self.adapter.kickoff.return_value = fake_kickoff_response()
        response = self.client.post(
            "/api/v2/projects/proj-big/kickoff",
            json={"user_idea": "x" * 9000},
        )
        self.assertEqual(response.status_code, 422)
        # Adapter must not have been called — the cap exists to spare
        # the OpenAI budget, not to prettify error pages.
        self.adapter.kickoff.assert_not_called()

    def test_kickoff_empty_idea_rejected_with_400(self) -> None:
        """Empty string is valid pydantic but rejected by the handler."""
        response = self.client.post(
            "/api/v2/projects/proj-empty/kickoff",
            json={"user_idea": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.adapter.kickoff.assert_not_called()

    def test_kickoff_persists_pre_populated_q_and_a(self) -> None:
        """B1 (YC v4): kickoff response with Q&A → turns + decisions seeded.

        The planner now generates 2-3 Q&A turns + a decision per topic
        in the kickoff response. The handler persists each Q&A as a
        planner-asked turn + a user-roled answer turn + a proposed
        decision so TopicDetail (which loads turns + decisions via the
        existing endpoints) renders pre-populated Q&A immediately
        rather than an empty composer.
        """
        self.adapter.kickoff.return_value = fake_kickoff_response_with_qa()
        response = self.client.post(
            "/api/v2/projects/proj-b1/kickoff",
            json={"user_idea": "An indoor wedding for 120."},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        topics_by_title = {t["title"]: t for t in payload["topics"]}

        # Venue topic has 2 Q&A → 4 turns (2 planner + 2 user) and 2 decisions.
        venue = topics_by_title["Venue"]
        venue_turns = self.client.get(
            f"/api/v2/topics/{venue['topic_id']}/turns",
        ).json()["turns"]
        self.assertEqual(len(venue_turns), 4)
        self.assertEqual([t["role"] for t in venue_turns], ["planner", "user", "planner", "user"])
        self.assertEqual(venue_turns[0]["body"], "Indoor or outdoor venue?")
        self.assertEqual(venue_turns[1]["body"], "Indoor — easier to control sound and weather.")
        venue_decisions = self.client.get(
            f"/api/v2/topics/{venue['topic_id']}/decisions",
        ).json()["decisions"]
        self.assertEqual(len(venue_decisions), 2)
        self.assertEqual(venue_decisions[0]["statement"], "Venue is indoor.")

        # Budget topic has 1 Q&A → 2 turns + 1 decision.
        budget = topics_by_title["Budget"]
        budget_turns = self.client.get(
            f"/api/v2/topics/{budget['topic_id']}/turns",
        ).json()["turns"]
        self.assertEqual(len(budget_turns), 2)
        budget_decisions = self.client.get(
            f"/api/v2/topics/{budget['topic_id']}/decisions",
        ).json()["decisions"]
        self.assertEqual(len(budget_decisions), 1)

        # Audience topic has empty q_and_a — backward-compat path,
        # zero turns / decisions persisted, frontend renders the
        # legacy on-demand composer.
        audience = topics_by_title["Audience"]
        audience_turns = self.client.get(
            f"/api/v2/topics/{audience['topic_id']}/turns",
        ).json()["turns"]
        self.assertEqual(audience_turns, [])
        audience_decisions = self.client.get(
            f"/api/v2/topics/{audience['topic_id']}/decisions",
        ).json()["decisions"]
        self.assertEqual(audience_decisions, [])


class V2TopicTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="t@example.com", password="password123")
        # Seed a project + topic so /turn has something to talk to. Use
        # kickoff so ownership is wired up the same way prod flows do.
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-turn/kickoff",
            json={"user_idea": "A small outdoor wine festival."},
        ).json()
        self.topic_id = kick["topics"][0]["topic_id"]
        self.project_id = "proj-turn"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_topic_turn_persists_planner_turn(self) -> None:
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/turn",
            json={"user_answer": "Hard cap is $48k incl contingency."},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["turn_result"]["action"], "ask")
        self.assertIsNotNone(payload["planner_turn"])

        # Verify two turns persisted: the user answer, then the planner reply
        turns = self.client.get(f"/api/v2/topics/{self.topic_id}/turns").json()["turns"]
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["role"], "user")
        self.assertEqual(turns[1]["role"], "planner")

    def test_topic_turn_with_unknown_topic_returns_404(self) -> None:
        response = self.client.post(
            "/api/v2/topics/topic-nonexistent-id/turn",
            json={"user_answer": "irrelevant"},
        )
        self.assertEqual(response.status_code, 404)
        self.adapter.topic_turn.assert_not_called()


class V2TopicUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="u@example.com", password="password123")
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-update/kickoff",
            json={"user_idea": "Topics for the update test."},
        ).json()
        self.topic_id = kick["topics"][0]["topic_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_position_change_persists(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"position_x": 123.5, "position_y": 456.25},
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["topic"]
        self.assertAlmostEqual(updated["position_x"], 123.5)
        self.assertAlmostEqual(updated["position_y"], 456.25)

        # Confirm the write by re-listing
        listed = self.client.get("/api/v2/projects/proj-update/topics").json()["topics"]
        target = next(t for t in listed if t["topic_id"] == self.topic_id)
        self.assertAlmostEqual(target["position_x"], 123.5)
        self.assertAlmostEqual(target["position_y"], 456.25)


class V2DecisionDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="d@example.com", password="password123")
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-dec/kickoff",
            json={"user_idea": "Seed for decision-delete test."},
        ).json()
        self.topic_id = kick["topics"][0]["topic_id"]
        self.project_id = "proj-dec"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_decision_delete_soft_deletes_and_list_excludes(self) -> None:
        # Create a decision on the topic
        created = self.client.post(
            f"/api/v2/topics/{self.topic_id}/decisions",
            json={"statement": "Headline sponsor gets a named stage."},
        )
        self.assertEqual(created.status_code, 201)
        decision_id = created.json()["decision"]["decision_id"]

        # List sees it
        before = self.client.get(
            f"/api/v2/topics/{self.topic_id}/decisions",
        ).json()["decisions"]
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0]["decision_id"], decision_id)

        # Soft-delete
        deleted = self.client.post(f"/api/v2/decisions/{decision_id}/delete")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json().get("deleted"))

        # List no longer sees it (retracted decisions are excluded)
        after = self.client.get(
            f"/api/v2/topics/{self.topic_id}/decisions",
        ).json()["decisions"]
        self.assertEqual(after, [])

        # Double-delete returns 404 — store.delete_decision returns False
        # when the row is already retracted, and the route translates.
        double = self.client.post(f"/api/v2/decisions/{decision_id}/delete")
        self.assertEqual(double.status_code, 404)


if __name__ == "__main__":
    unittest.main()
