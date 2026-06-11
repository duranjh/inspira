"""Tests for the topic duplication endpoint and store method.

Covers:

* ``store.duplicate_topic`` creates a sibling in the same project with
  " (copy)" suffix, offset +40px/+40px from the source position, and a
  fresh topic_id (no id collision). No relationships, decisions, or
  Q&A turns from the source are carried over — shallow duplication.
* ``POST /api/v2/topics/{id}/duplicate`` returns 201 with the new
  topic envelope, matching ``{"topic": Topic}``.
* IDOR: user B duplicating user A's topic resolves to 404 with the
  uniform ``topic_not_found`` error, leaving the source untouched.
* Origin on the duplicate is forced to ``user_manual`` regardless of
  the source's provenance — the copy is explicitly user-initiated.

All tests hit both the store layer (unit-shaped) and the HTTP route
(route-shaped) so regressions surface wherever they happen.
"""
from __future__ import annotations

import unittest
from typing import Any

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _seed_topic(store: Any, *, user_id: str) -> dict[str, Any]:
    """Create a project with a single topic and return the topic row."""
    project = store.create_v2_project(user_id=user_id, title="Festival plan")
    topic = store.create_topic(
        project_id=project["project_id"],
        title="Venue",
        icon="map-pin",
        position_x=120.0,
        position_y=80.0,
        order_index=0,
        origin="planner_initial",
    )
    return {"project_id": project["project_id"], "topic": topic}


class DuplicateTopicStoreTests(unittest.TestCase):
    """Direct-store tests for ``PlanningStudioStore.duplicate_topic``."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="dup-topic@example.com", password="password123",
        )
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_duplicate_creates_sibling_with_copy_suffix(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        source = seeded["topic"]
        duplicate = self.store.duplicate_topic(
            source["topic_id"], user_id=self.user_id,
        )
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["project_id"], seeded["project_id"])
        self.assertEqual(duplicate["title"], "Venue (copy)")
        self.assertNotEqual(duplicate["topic_id"], source["topic_id"])

    def test_duplicate_offsets_position_by_plus_40(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        source = seeded["topic"]
        duplicate = self.store.duplicate_topic(
            source["topic_id"], user_id=self.user_id,
        )
        self.assertEqual(duplicate["position_x"], source["position_x"] + 40.0)
        self.assertEqual(duplicate["position_y"], source["position_y"] + 40.0)

    def test_duplicate_origin_is_user_manual_regardless_of_source(self) -> None:
        # Source origin is planner_initial (from the seed helper).
        seeded = _seed_topic(self.store, user_id=self.user_id)
        source = seeded["topic"]
        self.assertEqual(source["origin"], "planner_initial")
        duplicate = self.store.duplicate_topic(
            source["topic_id"], user_id=self.user_id,
        )
        self.assertEqual(duplicate["origin"], "user_manual")

    def test_duplicate_copies_icon(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        source = seeded["topic"]
        duplicate = self.store.duplicate_topic(
            source["topic_id"], user_id=self.user_id,
        )
        self.assertEqual(duplicate["icon"], source["icon"])

    def test_duplicate_appears_in_project_topic_list(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        self.store.duplicate_topic(
            seeded["topic"]["topic_id"], user_id=self.user_id,
        )
        topics = self.store.list_topics(project_id=seeded["project_id"])
        self.assertEqual(len(topics), 2)
        titles = {t["title"] for t in topics}
        self.assertEqual(titles, {"Venue", "Venue (copy)"})

    def test_duplicate_no_relationships_copied(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        # Add a second topic and a relationship involving the source.
        other = self.store.create_topic(
            project_id=seeded["project_id"],
            title="Safety",
            icon="flag",
            position_x=500.0,
            position_y=80.0,
            order_index=1,
            origin="planner_initial",
        )
        self.store.create_relationship(
            project_id=seeded["project_id"],
            source_topic_id=seeded["topic"]["topic_id"],
            target_topic_id=other["topic_id"],
            label="requires",
            origin="planner_inferred",
        )
        pre_rels = self.store.list_relationships(project_id=seeded["project_id"])
        self.assertEqual(len(pre_rels), 1)
        self.store.duplicate_topic(
            seeded["topic"]["topic_id"], user_id=self.user_id,
        )
        # Relationship count unchanged — the duplicate is a fresh sibling.
        post_rels = self.store.list_relationships(project_id=seeded["project_id"])
        self.assertEqual(len(post_rels), 1)

    def test_duplicate_missing_topic_returns_none(self) -> None:
        self.assertIsNone(
            self.store.duplicate_topic(
                "topic-nonexistent", user_id=self.user_id,
            ),
        )

    def test_duplicate_idor_cross_user_returns_none(self) -> None:
        # User A seeds a topic.
        seeded = _seed_topic(self.store, user_id=self.user_id)
        # User B tries to duplicate it — must see None.
        other_client, _, _, other_temp = make_test_app()
        try:
            signup_and_login(
                other_client, email="other@example.com", password="password123",
            )
            # Build a second store handle on the SAME filesystem so both
            # users live in the same DB. But make_test_app gives each
            # test its own temp dir — instead call duplicate_topic on
            # self.store with a different user_id.
            other_me = other_client.get("/api/auth/me").json()
            other_user_id = other_me["user_id"]
        finally:
            other_temp.cleanup()
        result = self.store.duplicate_topic(
            seeded["topic"]["topic_id"], user_id=other_user_id,
        )
        self.assertIsNone(result)


class DuplicateTopicHttpTests(unittest.TestCase):
    """Route-level tests for ``POST /api/v2/topics/{id}/duplicate``."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="dup-topic-http@example.com", password="password123",
        )
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_duplicate_endpoint_returns_201_with_topic(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        response = self.client.post(
            f"/api/v2/topics/{seeded['topic']['topic_id']}/duplicate",
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertIn("topic", payload)
        new_topic = payload["topic"]
        self.assertIn("topic_id", new_topic)
        self.assertEqual(new_topic["title"], "Venue (copy)")
        self.assertNotEqual(
            new_topic["topic_id"], seeded["topic"]["topic_id"],
        )

    def test_duplicate_endpoint_offsets_position(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        source = seeded["topic"]
        response = self.client.post(
            f"/api/v2/topics/{source['topic_id']}/duplicate",
        )
        self.assertEqual(response.status_code, 201, response.text)
        new_topic = response.json()["topic"]
        self.assertEqual(new_topic["position_x"], source["position_x"] + 40.0)
        self.assertEqual(new_topic["position_y"], source["position_y"] + 40.0)

    def test_duplicate_endpoint_idor_returns_404_for_cross_user(self) -> None:
        seeded = _seed_topic(self.store, user_id=self.user_id)
        # Sign a different user in on a FRESH client (so we don't lose
        # the first user's session cookie). The FastAPI test app is the
        # same process, so both users share the store.
        self.client.post("/api/auth/logout")
        signup_and_login(
            self.client, email="attacker@example.com", password="password123",
        )
        response = self.client.post(
            f"/api/v2/topics/{seeded['topic']['topic_id']}/duplicate",
        )
        self.assertEqual(response.status_code, 404, response.text)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "topic_not_found")
        # Source project still has exactly one topic — no side effect.
        topics = self.store.list_topics(project_id=seeded["project_id"])
        self.assertEqual(len(topics), 1)

    def test_duplicate_endpoint_missing_topic_returns_404(self) -> None:
        response = self.client.post(
            "/api/v2/topics/topic-doesnotexist/duplicate",
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["detail"]["error"], "topic_not_found",
        )


if __name__ == "__main__":
    unittest.main()
