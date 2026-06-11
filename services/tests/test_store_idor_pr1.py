"""Unit tests for the PR 1 store IDOR fixes.

Covers:
- ``verify_project_ownership(user_id=None)`` returns False (was True).
- ``confirm_decision`` returns None when the caller doesn't own the
  decision's project — and does NOT mutate the row.
- ``delete_decision`` returns False when caller doesn't own the
  decision's project — and does NOT retract the row.

These exercise the store layer directly. Cross-route IDOR is already
covered by ``test_ownership.py``; this file pins the primitives.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any

from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore


class _StoreFixture:
    def __init__(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="inspira-idor-test-", ignore_cleanup_errors=True,
        )
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        self.store = PlanningStudioStore(load_config())

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


class VerifyProjectOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _StoreFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_user_id_none_returns_false(self) -> None:
        """SECURITY (PR 1): None used to mean "trust the caller" and
        returned True; now it returns False so unauthenticated paths
        can't sneak through."""
        # Doesn't matter that the project doesn't exist — None-user
        # short-circuits before the lookup.
        self.assertFalse(
            self.fx.store.verify_project_ownership(
                project_id="proj-anything", user_id=None,
            ),
        )

    def test_correct_owner_returns_true(self) -> None:
        # Create a user + project the V2 way.
        user = self.fx.store.create_user(email="alice@example.com")
        proj = self.fx.store.create_v2_project(
            user_id=user["user_id"], title="Alice project",
        )
        self.assertTrue(
            self.fx.store.verify_project_ownership(
                project_id=proj["project_id"], user_id=user["user_id"],
            ),
        )

    def test_other_user_returns_false(self) -> None:
        user_a = self.fx.store.create_user(email="alice@example.com")
        user_b = self.fx.store.create_user(email="bob@example.com")
        proj = self.fx.store.create_v2_project(
            user_id=user_a["user_id"], title="Alice project",
        )
        self.assertFalse(
            self.fx.store.verify_project_ownership(
                project_id=proj["project_id"], user_id=user_b["user_id"],
            ),
        )


class DecisionIDORTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _StoreFixture()
        self.user_a = self.fx.store.create_user(email="alice@example.com")
        self.user_b = self.fx.store.create_user(email="bob@example.com")
        self.proj = self.fx.store.create_v2_project(
            user_id=self.user_a["user_id"], title="Alice project",
        )
        self.topic = self.fx.store.create_topic(
            project_id=self.proj["project_id"],
            title="Some topic",
            icon="flag",
        )
        self.decision = self.fx.store.create_decision(
            project_id=self.proj["project_id"],
            topic_id=self.topic["topic_id"],
            statement="A decision",
            proposed_by="user",
            user_id=self.user_a["user_id"],
        )

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_confirm_decision_rejects_non_owner(self) -> None:
        """User B must not be able to confirm User A's decision."""
        result: Any = self.fx.store.confirm_decision(
            self.decision["decision_id"], user_id=self.user_b["user_id"],
        )
        self.assertIsNone(result)
        # Verify the decision is still in its original (proposed) state.
        unchanged = self.fx.store.get_decision(self.decision["decision_id"])
        self.assertIsNotNone(unchanged)
        assert unchanged is not None
        self.assertNotEqual(unchanged.get("status"), "confirmed")

    def test_confirm_decision_accepts_owner(self) -> None:
        result: Any = self.fx.store.confirm_decision(
            self.decision["decision_id"], user_id=self.user_a["user_id"],
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "confirmed")

    def test_delete_decision_rejects_non_owner(self) -> None:
        """User B must not be able to retract User A's decision."""
        ok = self.fx.store.delete_decision(
            self.decision["decision_id"], user_id=self.user_b["user_id"],
        )
        self.assertFalse(ok)
        # Decision is NOT retracted — list_decisions still sees it.
        active = self.fx.store.list_decisions(
            project_id=self.proj["project_id"],
        )
        self.assertEqual(len(active), 1)

    def test_delete_decision_accepts_owner(self) -> None:
        ok = self.fx.store.delete_decision(
            self.decision["decision_id"], user_id=self.user_a["user_id"],
        )
        self.assertTrue(ok)
        active = self.fx.store.list_decisions(
            project_id=self.proj["project_id"],
        )
        self.assertEqual(len(active), 0)

    def test_delete_decision_user_id_none_skips_ownership(self) -> None:
        """The legacy / migration path where no user_id is passed still
        works (it's reserved for in-process callers)."""
        ok = self.fx.store.delete_decision(
            self.decision["decision_id"], user_id=None,
        )
        self.assertTrue(ok)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
