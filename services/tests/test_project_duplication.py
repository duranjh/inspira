"""Tests for the project duplication endpoint and store method.

Covers:
  * ``POST /api/v2/projects/{id}/duplicate`` returns 201 with a new
    project envelope matching ``POST /api/v2/projects``.
  * Deep-copy preserves topic counts, relationship counts, decisions,
    Q&A turns (with parent-turn re-parenting), and topic positions.
  * Title has " (copy)" suffix.
  * The duplicate lands with a new project_id and new topic_ids — no
    id collision that would re-open the source's rows through the copy.
  * IDOR: user B duplicating user A's project resolves to 404, leaving
    the source untouched.
  * shelf_id is NOT copied: the duplicate lands on the implicit
    "Unfiled" shelf.
  * shared_links (share tokens) are NOT copied.

Tests hit both the store layer directly (unit-shaped) and the HTTP
route (route-shaped) so regressions at either layer surface.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _seed_project_with_content(
    store,
    *,
    user_id: str,
    title: str = "Festival plan",
) -> dict:
    """Create a project with 3 topics, 2 relationships, 2 decisions, 1
    open question, 1 risk, and a short Q&A thread (parent/child).

    Returns a dict of the seeded ids so tests can assert on individual
    rows. Uses the store's public methods rather than raw SQL so the
    seeding stays in lockstep with how production code writes.
    """
    project = store.create_v2_project(user_id=user_id, title=title)
    project_id = project["project_id"]

    topic_a = store.create_topic(
        project_id=project_id,
        title="Venue",
        icon="map-pin",
        position_x=100.0,
        position_y=200.0,
        order_index=0,
        origin="planner_initial",
    )
    topic_b = store.create_topic(
        project_id=project_id,
        title="Budget",
        icon="chart",
        position_x=300.0,
        position_y=200.0,
        order_index=1,
        origin="planner_initial",
    )
    topic_c = store.create_topic(
        project_id=project_id,
        title="Safety",
        icon="flag",
        position_x=500.0,
        position_y=200.0,
        order_index=2,
        origin="user_manual",
    )

    store.create_relationship(
        project_id=project_id,
        source_topic_id=topic_a["topic_id"],
        target_topic_id=topic_c["topic_id"],
        label="requires",
        origin="planner_inferred",
        strength="implied",
    )
    store.create_relationship(
        project_id=project_id,
        source_topic_id=topic_b["topic_id"],
        target_topic_id=topic_a["topic_id"],
        label="bounds",
        origin="user_drawn",
        strength="confirmed",
    )

    store.create_decision(
        topic_id=topic_a["topic_id"],
        project_id=project_id,
        statement="Use the outdoor amphitheatre.",
        rationale="Already booked; insurance covers it.",
        status="confirmed",
        proposed_by="user",
    )
    store.create_decision(
        topic_id=topic_b["topic_id"],
        project_id=project_id,
        statement="Cap total spend at $8k.",
        rationale=None,
        status="proposed",
        proposed_by="planner",
    )

    # A parent+child turn pair so we can assert parent rewiring.
    first_turn = store.append_qna_turn(
        topic_id=topic_a["topic_id"],
        project_id=project_id,
        role="planner",
        body="What's non-negotiable about the venue?",
        status="answered",
    )
    store.append_qna_turn(
        topic_id=topic_a["topic_id"],
        project_id=project_id,
        role="user",
        body="Must be outdoors. Must have shade.",
        status="answered",
        parent_turn_id=first_turn["turn_id"],
    )

    return {
        "project_id": project_id,
        "topic_ids": [
            topic_a["topic_id"],
            topic_b["topic_id"],
            topic_c["topic_id"],
        ],
    }


class DuplicateStoreTests(unittest.TestCase):
    """Direct-store tests: preserves counts, maps ids, excludes shared_links."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="dup@example.com", password="password123",
        )
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_duplicate_preserves_topic_and_relationship_counts(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        source_id = seeded["project_id"]

        duplicated = self.store.duplicate_v2_project(
            source_project_id=source_id, user_id=self.user_id,
        )
        self.assertIsNotNone(duplicated)
        new_id = duplicated["project_id"]

        src_topics = self.store.list_topics(project_id=source_id)
        dup_topics = self.store.list_topics(project_id=new_id)
        self.assertEqual(len(src_topics), len(dup_topics))

        src_rels = self.store.list_relationships(project_id=source_id)
        dup_rels = self.store.list_relationships(project_id=new_id)
        self.assertEqual(len(src_rels), len(dup_rels))

        src_decs = self.store.list_decisions(project_id=source_id)
        dup_decs = self.store.list_decisions(project_id=new_id)
        self.assertEqual(len(src_decs), len(dup_decs))

    def test_duplicate_generates_fresh_project_and_topic_ids(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        source_id = seeded["project_id"]

        duplicated = self.store.duplicate_v2_project(
            source_project_id=source_id, user_id=self.user_id,
        )
        self.assertNotEqual(duplicated["project_id"], source_id)

        dup_topics = self.store.list_topics(project_id=duplicated["project_id"])
        src_topic_ids = {t["topic_id"] for t in self.store.list_topics(project_id=source_id)}
        for t in dup_topics:
            self.assertNotIn(t["topic_id"], src_topic_ids)

        # And relationships in the copy reference topic_ids that live in
        # the copy (not the source's topic_ids).
        dup_topic_ids = {t["topic_id"] for t in dup_topics}
        dup_rels = self.store.list_relationships(project_id=duplicated["project_id"])
        for rel in dup_rels:
            self.assertIn(rel["source_topic_id"], dup_topic_ids)
            self.assertIn(rel["target_topic_id"], dup_topic_ids)

    def test_duplicate_title_has_copy_suffix(self) -> None:
        seeded = _seed_project_with_content(
            self.store, user_id=self.user_id, title="My great plan",
        )
        duplicated = self.store.duplicate_v2_project(
            source_project_id=seeded["project_id"], user_id=self.user_id,
        )
        self.assertEqual(duplicated["title"], "My great plan (copy)")

    def test_duplicate_preserves_topic_positions_and_origin(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        duplicated = self.store.duplicate_v2_project(
            source_project_id=seeded["project_id"], user_id=self.user_id,
        )
        # Pair source and copy topics up by title (stable ordering works
        # too — we seeded with distinct titles).
        src_by_title = {
            t["title"]: t for t in self.store.list_topics(project_id=seeded["project_id"])
        }
        dup_by_title = {
            t["title"]: t for t in self.store.list_topics(project_id=duplicated["project_id"])
        }
        self.assertEqual(set(src_by_title.keys()), set(dup_by_title.keys()))
        for title, src in src_by_title.items():
            dup = dup_by_title[title]
            self.assertEqual(src["position_x"], dup["position_x"])
            self.assertEqual(src["position_y"], dup["position_y"])
            self.assertEqual(src["origin"], dup["origin"])
            self.assertEqual(src["icon"], dup["icon"])

    def test_duplicate_rewires_qna_parent_turn_id(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        duplicated = self.store.duplicate_v2_project(
            source_project_id=seeded["project_id"], user_id=self.user_id,
        )
        # The seed created a parent+child pair on topic_a. Find the copy's
        # topic_a and inspect its turn list.
        dup_topics = self.store.list_topics(project_id=duplicated["project_id"])
        topic_a_copy = next(t for t in dup_topics if t["title"] == "Venue")
        turns = self.store.list_qna_turns(topic_id=topic_a_copy["topic_id"])
        self.assertEqual(len(turns), 2)
        parent, child = turns[0], turns[1]
        # parent_turn_id on the child must point at the COPY's parent
        # turn, not at the source's turn id.
        self.assertEqual(child["parent_turn_id"], parent["turn_id"])

    def test_duplicate_starts_on_unfiled_shelf_regardless_of_source(self) -> None:
        # Source is on a shelf; the duplicate must land off-shelf.
        shelf = self.store.create_shelf(user_id=self.user_id, name="Work")
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        moved = self.store.move_project_to_shelf(
            project_id=seeded["project_id"],
            user_id=self.user_id,
            shelf_id=shelf["shelf_id"],
        )
        self.assertIsNotNone(moved)
        self.assertEqual(moved.get("shelf_id"), shelf["shelf_id"])

        duplicated = self.store.duplicate_v2_project(
            source_project_id=seeded["project_id"], user_id=self.user_id,
        )
        # shelf_id on the new project is None — the duplicate starts on
        # the implicit Unfiled shelf.
        self.assertIsNone(duplicated.get("shelf_id"))


class DuplicateHttpTests(unittest.TestCase):
    """Route-level tests: 201 shape, IDOR, side effects surface in the list."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="dup-http@example.com", password="password123",
        )
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_duplicate_endpoint_returns_201_with_project_envelope(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        response = self.client.post(
            f"/api/v2/projects/{seeded['project_id']}/duplicate",
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertIn("project", payload)
        project = payload["project"]
        self.assertIn("project_id", project)
        self.assertIn("title", project)
        self.assertNotEqual(project["project_id"], seeded["project_id"])
        self.assertTrue(project["title"].endswith(" (copy)"))

    def test_duplicate_appears_in_user_project_list(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        pre = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(pre), 1)

        self.client.post(f"/api/v2/projects/{seeded['project_id']}/duplicate")

        post = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(post), 2)
        titles = {p["title"] for p in post}
        self.assertIn("Festival plan", titles)
        self.assertIn("Festival plan (copy)", titles)

    def test_duplicate_unknown_project_returns_404(self) -> None:
        response = self.client.post(
            "/api/v2/projects/project-does-not-exist/duplicate",
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_duplicate_share_tokens_are_not_copied(self) -> None:
        seeded = _seed_project_with_content(self.store, user_id=self.user_id)
        # Mint a share token on the source.
        share_resp = self.client.post(
            f"/api/v2/projects/{seeded['project_id']}/share",
        )
        self.assertEqual(share_resp.status_code, 201, share_resp.text)
        source_share = share_resp.json().get("share_link") or {}
        source_token = source_share.get("token")
        self.assertIsNotNone(source_token)

        dup = self.client.post(
            f"/api/v2/projects/{seeded['project_id']}/duplicate",
        ).json()["project"]

        # The duplicate has NO active share_link — the endpoint returns
        # ``share_link=null`` until the user explicitly mints a fresh one.
        dup_share = self.client.get(
            f"/api/v2/projects/{dup['project_id']}/share",
        )
        self.assertEqual(dup_share.status_code, 200, dup_share.text)
        self.assertIsNone(dup_share.json().get("share_link"))


class DuplicateIdorTests(unittest.TestCase):
    """User B cannot duplicate user A's project."""

    def setUp(self) -> None:
        self.alice, self.store, self.adapter, self.temp_dir = make_test_app()
        self.bob = TestClient(self.alice.app)
        signup_and_login(self.alice, email="alice-dup@example.com", password="alice-pw-1")
        signup_and_login(self.bob, email="bob-dup@example.com", password="bob-pw-123")

        alice_me = self.alice.get("/api/auth/me").json()
        self.alice_user_id = alice_me["user_id"]

        self.alice_seed = _seed_project_with_content(
            self.store, user_id=self.alice_user_id, title="Alice's plan",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bob_duplicating_alices_project_returns_404(self) -> None:
        response = self.bob.post(
            f"/api/v2/projects/{self.alice_seed['project_id']}/duplicate",
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_bob_sees_no_new_project_after_failed_duplicate(self) -> None:
        # Sanity: bob has zero projects before the attempt.
        self.assertEqual(
            self.bob.get("/api/v2/projects").json()["projects"], [],
        )
        self.bob.post(
            f"/api/v2/projects/{self.alice_seed['project_id']}/duplicate",
        )
        # And still zero after — no project leaked into his account.
        self.assertEqual(
            self.bob.get("/api/v2/projects").json()["projects"], [],
        )

    def test_alices_project_is_not_modified_by_bobs_attempt(self) -> None:
        before = self.alice.get("/api/v2/projects").json()["projects"]
        self.bob.post(
            f"/api/v2/projects/{self.alice_seed['project_id']}/duplicate",
        )
        after = self.alice.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(before), 1)
        self.assertEqual(len(after), 1)
        # Title wasn't rewritten and no phantom "(copy)" landed.
        self.assertEqual(after[0]["title"], "Alice's plan")

    def test_store_returns_none_when_source_missing_or_wrong_owner(self) -> None:
        # Missing entirely.
        self.assertIsNone(
            self.store.duplicate_v2_project(
                source_project_id="project-missing", user_id=self.alice_user_id,
            )
        )
        # Wrong owner (bob, but project is alice's).
        bob_me = self.bob.get("/api/auth/me").json()
        bob_user_id = bob_me["user_id"]
        self.assertIsNone(
            self.store.duplicate_v2_project(
                source_project_id=self.alice_seed["project_id"],
                user_id=bob_user_id,
            )
        )


if __name__ == "__main__":
    unittest.main()
