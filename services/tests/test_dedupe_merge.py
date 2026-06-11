"""Tests for dedupe_merge.merge_topics.

Covers:
- basic happy path: turns + decisions re-parented, drop topic deleted
- relationship self-edge collapse is dropped
- duplicate relationship after reparent is deduped
- cross-project topics are rejected
- cross-user topics are rejected (IDOR protection)
- self-merge (keep == drop) is rejected
"""
from __future__ import annotations

import os
import tempfile
import unittest

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.config import load_config
from planning_studio_service.dedupe_merge import merge_topics
from planning_studio_service.store import PlanningStudioStore

ensure_loaded()


def _make_store() -> tuple[PlanningStudioStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory(
        prefix="inspira-dedupe-test-", ignore_cleanup_errors=True
    )
    os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = tmp.name
    store = PlanningStudioStore(load_config())
    return store, tmp


class MergeTopicsHappyPathTests(unittest.TestCase):
    """Basic merge: turns and decisions move from drop → keep."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        s = self.store

        # Create a user and two projects owned by that user.
        self.user_id = s.create_user(
            email="alice@example.com", password_hash="x", display_name="Alice"
        )["user_id"]
        self.project_id = s.create_v2_project(
            title="My Project", user_id=self.user_id
        )["project_id"]

        # Two topics in the same project.
        self.keep = s.create_topic(
            project_id=self.project_id, title="Keep Topic", icon="star"
        )["topic_id"]
        self.drop = s.create_topic(
            project_id=self.project_id, title="Drop Topic", icon="flag"
        )["topic_id"]

        # Attach turns to both topics.
        s.append_qna_turn(
            topic_id=self.keep,
            project_id=self.project_id,
            role="planner",
            body="Keep turn 1",
        )
        s.append_qna_turn(
            topic_id=self.drop,
            project_id=self.project_id,
            role="planner",
            body="Drop turn 1",
        )
        s.append_qna_turn(
            topic_id=self.drop,
            project_id=self.project_id,
            role="user",
            body="Drop turn 2",
        )

        # Attach decisions to the drop topic.
        s.create_decision(
            topic_id=self.drop,
            project_id=self.project_id,
            statement="Drop decision A",
            proposed_by="planner",
        )
        s.create_decision(
            topic_id=self.drop,
            project_id=self.project_id,
            statement="Drop decision B",
            proposed_by="user",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_correct_counts(self) -> None:
        result = merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        self.assertEqual(result["merged_turns"], 2)
        self.assertEqual(result["merged_decisions"], 2)
        self.assertEqual(result["rerouted_relationships"], 0)
        self.assertEqual(result["dropped_self_edges"], 0)

    def test_turns_moved_to_keep(self) -> None:
        merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        turns = self.store.list_qna_turns(topic_id=self.keep)
        bodies = {t["body"] for t in turns}
        self.assertIn("Keep turn 1", bodies)
        self.assertIn("Drop turn 1", bodies)
        self.assertIn("Drop turn 2", bodies)
        self.assertEqual(len(turns), 3)

    def test_decisions_moved_to_keep(self) -> None:
        merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT topic_id FROM decisions WHERE topic_id = ?", (self.keep,)
            ).fetchall()
        self.assertEqual(len(rows), 2)

    def test_drop_topic_is_soft_deleted(self) -> None:
        merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        topic = self.store.get_topic(self.drop)
        self.assertIsNotNone(topic)
        self.assertIsNotNone(topic["deleted_at"])  # type: ignore[index]

    def test_keep_topic_still_alive(self) -> None:
        merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        topic = self.store.get_topic(self.keep)
        self.assertIsNotNone(topic)
        self.assertIsNone(topic["deleted_at"])  # type: ignore[index]


class MergeTopicsRelationshipTests(unittest.TestCase):
    """Relationship rerouting: self-edges dropped, duplicates deduped."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        s = self.store
        self.user_id = s.create_user(
            email="bob@example.com", password_hash="y", display_name="Bob"
        )["user_id"]
        self.project_id = s.create_v2_project(
            title="Rels Project", user_id=self.user_id
        )["project_id"]
        self.keep = s.create_topic(
            project_id=self.project_id, title="Keep", icon="star"
        )["topic_id"]
        self.drop = s.create_topic(
            project_id=self.project_id, title="Drop", icon="flag"
        )["topic_id"]
        self.third = s.create_topic(
            project_id=self.project_id, title="Third", icon="check"
        )["topic_id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_self_edge_is_dropped(self) -> None:
        """A keep→drop relationship becomes keep→keep after merge — must be dropped."""
        self.store.create_relationship(
            project_id=self.project_id,
            source_topic_id=self.keep,
            target_topic_id=self.drop,
        )
        result = merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        self.assertEqual(result["dropped_self_edges"], 1)
        rels = self.store.list_relationships(project_id=self.project_id)
        self.assertEqual(len(rels), 0)

    def test_duplicate_relationship_is_deduped(self) -> None:
        """drop→third AND keep→third: after merge both become keep→third; duplicate is dropped."""
        self.store.create_relationship(
            project_id=self.project_id,
            source_topic_id=self.keep,
            target_topic_id=self.third,
        )
        self.store.create_relationship(
            project_id=self.project_id,
            source_topic_id=self.drop,
            target_topic_id=self.third,
        )
        result = merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        self.assertEqual(result["dropped_self_edges"], 1)  # counted as dropped
        rels = self.store.list_relationships(project_id=self.project_id)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["source_topic_id"], self.keep)
        self.assertEqual(rels[0]["target_topic_id"], self.third)

    def test_normal_relationship_rerouted(self) -> None:
        """drop→third after merge becomes keep→third."""
        self.store.create_relationship(
            project_id=self.project_id,
            source_topic_id=self.drop,
            target_topic_id=self.third,
        )
        result = merge_topics(
            self.store,
            user_id=self.user_id,
            project_id=self.project_id,
            keep_id=self.keep,
            drop_id=self.drop,
        )
        self.assertEqual(result["rerouted_relationships"], 1)
        rels = self.store.list_relationships(project_id=self.project_id)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["source_topic_id"], self.keep)


class MergeTopicsValidationTests(unittest.TestCase):
    """Rejection cases: cross-project, cross-user, self-merge."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        s = self.store
        self.user_a = s.create_user(
            email="alice2@example.com", password_hash="a", display_name="A"
        )["user_id"]
        self.user_b = s.create_user(
            email="bob2@example.com", password_hash="b", display_name="B"
        )["user_id"]
        self.proj_a = s.create_v2_project(
            title="Project A", user_id=self.user_a
        )["project_id"]
        self.proj_b = s.create_v2_project(
            title="Project B", user_id=self.user_b
        )["project_id"]
        self.topic_a1 = s.create_topic(
            project_id=self.proj_a, title="A1", icon="star"
        )["topic_id"]
        self.topic_a2 = s.create_topic(
            project_id=self.proj_a, title="A2", icon="flag"
        )["topic_id"]
        self.topic_b1 = s.create_topic(
            project_id=self.proj_b, title="B1", icon="star"
        )["topic_id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_self_merge_rejected(self) -> None:
        with self.assertRaises(ValueError, msg="keep_id and drop_id must be different"):
            merge_topics(
                self.store,
                user_id=self.user_a,
                project_id=self.proj_a,
                keep_id=self.topic_a1,
                drop_id=self.topic_a1,
            )

    def test_cross_user_rejected(self) -> None:
        """User B cannot merge a topic they don't own."""
        with self.assertRaises(ValueError):
            merge_topics(
                self.store,
                user_id=self.user_b,
                project_id=self.proj_a,
                keep_id=self.topic_a1,
                drop_id=self.topic_a2,
            )

    def test_cross_project_topics_rejected(self) -> None:
        """Topics from different projects may not be merged."""
        with self.assertRaises(ValueError):
            merge_topics(
                self.store,
                user_id=self.user_a,
                project_id=self.proj_a,
                keep_id=self.topic_a1,
                drop_id=self.topic_b1,
            )
