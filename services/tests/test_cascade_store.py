"""W2-θ cascade_store helpers — unit tests.

Covers the lazy-v1 snapshot, version insert + read paths, and the
cascade_runs lifecycle. Workspace + project scoping is exercised
end-to-end against a real SQLite store (no LLM, no FastAPI client).
"""
from __future__ import annotations

import unittest

from planning_studio_service import cascade_store

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


class CascadeStoreTests(unittest.TestCase):
    """Direct store-helper exercise — no HTTP layer."""

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.workspace_id = "ws-cascade-test"
        self.project_id = "proj-cascade-test"
        self.user_id = "user-cascade-test"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ----- helpers --------------------------------------------------

    def _seed_topic(self) -> str:
        topic = self.store.create_topic(
            project_id=self.project_id,
            title="Test Topic",
            icon="flag",
        )
        return topic["topic_id"]

    def _seed_decision(
        self,
        *,
        statement: str = "Original statement",
        rationale: str | None = "Original rationale",
        topic_id: str | None = None,
    ) -> str:
        if topic_id is None:
            topic_id = self._seed_topic()
        return self.store.create_decision(
            topic_id=topic_id,
            project_id=self.project_id,
            statement=statement,
            rationale=rationale,
            proposed_by="orchestrator",
        )["decision_id"]

    # ----- ensure_v1_snapshot --------------------------------------

    def test_ensure_v1_snapshot_inserts_when_missing(self) -> None:
        decision_id = self._seed_decision()
        version_id = cascade_store.ensure_v1_snapshot(
            self.store, decision_id=decision_id,
        )
        self.assertIsNotNone(version_id)
        v1 = cascade_store.get_decision_version(
            self.store, decision_id=decision_id, version_int=1,
        )
        assert v1 is not None
        self.assertEqual(v1["statement"], "Original statement")
        self.assertEqual(v1["rationale"], "Original rationale")
        self.assertEqual(v1["change_note"], "initial")
        self.assertIsNone(v1["cascade_id"])

    def test_ensure_v1_snapshot_idempotent(self) -> None:
        decision_id = self._seed_decision()
        v1_id_first = cascade_store.ensure_v1_snapshot(
            self.store, decision_id=decision_id,
        )
        v1_id_second = cascade_store.ensure_v1_snapshot(
            self.store, decision_id=decision_id,
        )
        self.assertEqual(v1_id_first, v1_id_second)
        versions = cascade_store.list_versions_for_decision(
            self.store, decision_id=decision_id,
        )
        self.assertEqual(len(versions), 1)

    def test_ensure_v1_snapshot_returns_none_for_missing_decision(self) -> None:
        result = cascade_store.ensure_v1_snapshot(
            self.store, decision_id="dec-does-not-exist",
        )
        self.assertIsNone(result)

    # ----- versioning roundtrip ------------------------------------

    def test_insert_and_advance_to_v2(self) -> None:
        decision_id = self._seed_decision()
        v1_id = cascade_store.ensure_v1_snapshot(
            self.store, decision_id=decision_id,
        )
        cascade_store.insert_decision_version(
            self.store,
            decision_id=decision_id,
            version_int=2,
            statement="New statement",
            rationale="New rationale",
            subject="my_subject",
            prior_version_id=v1_id,
            change_note="@@ -1 +1 @@\n-Original\n+New",
            cascade_id="csc-abc",
            cascaded_from_decision_ids=["d-source"],
        )
        cascade_store.update_decision_for_cascade(
            self.store,
            decision_id=decision_id,
            statement="New statement",
            rationale="New rationale",
            current_version_int=2,
        )
        latest = cascade_store.get_latest_version_int(
            self.store, decision_id=decision_id,
        )
        self.assertEqual(latest, 2)
        v2 = cascade_store.get_decision_version(
            self.store, decision_id=decision_id, version_int=2,
        )
        assert v2 is not None
        self.assertEqual(v2["statement"], "New statement")
        self.assertEqual(v2["cascade_id"], "csc-abc")
        self.assertEqual(v2["cascaded_from_decision_ids"], ["d-source"])
        self.assertEqual(v2["prior_version_id"], v1_id)

    def test_version_hash_is_deterministic(self) -> None:
        h1 = cascade_store.compute_version_hash(
            statement="abc", rationale="def", subject="x",
        )
        h2 = cascade_store.compute_version_hash(
            statement="abc", rationale="def", subject="x",
        )
        self.assertEqual(h1, h2)
        h3 = cascade_store.compute_version_hash(
            statement="abc", rationale="def", subject="y",
        )
        self.assertNotEqual(h1, h3)

    def test_get_latest_self_heals_when_pointer_is_stale(self) -> None:
        """C1/H1 regression: if a prior cascade landed a decision_versions
        row but failed to bump decisions.current_version_int (3-step
        non-atomic write), get_latest_version_int still returns the
        correct version so the next cascade computes new_v=v+1 instead
        of UNIQUE-violating on (decision_id, version_int).
        """
        decision_id = self._seed_decision()
        cascade_store.ensure_v1_snapshot(self.store, decision_id=decision_id)
        cascade_store.insert_decision_version(
            self.store,
            decision_id=decision_id, version_int=2,
            statement="v2", rationale=None, subject=None,
            prior_version_id=None, change_note=None,
            cascade_id=None, cascaded_from_decision_ids=None,
        )
        # Note: deliberately skip update_decision_for_cascade — this
        # simulates the partial-failure scenario.
        latest = cascade_store.get_latest_version_int(
            self.store, decision_id=decision_id,
        )
        self.assertEqual(latest, 2)

    def test_get_latest_falls_back_to_pointer_when_no_versions(self) -> None:
        """Steady state: a never-cascaded decision has no decision_versions
        row; get_latest_version_int falls back to current_version_int
        (default 1).
        """
        decision_id = self._seed_decision()
        latest = cascade_store.get_latest_version_int(
            self.store, decision_id=decision_id,
        )
        self.assertEqual(latest, 1)

    def test_list_versions_returns_latest_first(self) -> None:
        decision_id = self._seed_decision()
        cascade_store.ensure_v1_snapshot(
            self.store, decision_id=decision_id,
        )
        cascade_store.insert_decision_version(
            self.store,
            decision_id=decision_id, version_int=2,
            statement="v2", rationale=None, subject=None,
            prior_version_id=None, change_note=None,
            cascade_id=None, cascaded_from_decision_ids=None,
        )
        cascade_store.insert_decision_version(
            self.store,
            decision_id=decision_id, version_int=3,
            statement="v3", rationale=None, subject=None,
            prior_version_id=None, change_note=None,
            cascade_id=None, cascaded_from_decision_ids=None,
        )
        versions = cascade_store.list_versions_for_decision(
            self.store, decision_id=decision_id,
        )
        self.assertEqual([v["version_int"] for v in versions], [3, 2, 1])

    # ----- cascade_runs lifecycle ----------------------------------

    def test_create_and_get_cascade_run(self) -> None:
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.user_id,
            scope_mode="cascade",
            commented_decisions=[
                {"decision_id": "d1", "comment_text": "make it cheaper"}
            ],
            affected_scope={
                "decision_ids": ["d2", "d3"],
                "topic_ids": ["t1"],
                "count": 2,
                "banner_state": "narrow",
            },
        )
        self.assertTrue(cascade_id.startswith("csc-"))
        row = cascade_store.get_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            cascade_id=cascade_id,
        )
        assert row is not None
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["scope_mode"], "cascade")
        self.assertEqual(row["affected_scope"]["count"], 2)
        self.assertEqual(
            row["commented_decisions"][0]["comment_text"],
            "make it cheaper",
        )

    def test_update_cascade_status_to_complete(self) -> None:
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": "d1", "comment_text": "..."}],
        )
        cascade_store.update_cascade_status(
            self.store,
            workspace_id=self.workspace_id,
            cascade_id=cascade_id,
            status="complete",
            diff_summary={
                "updated_count": 2,
                "created_count": 0,
                "failed_count": 0,
            },
        )
        row = cascade_store.get_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            cascade_id=cascade_id,
        )
        assert row is not None
        self.assertEqual(row["status"], "complete")
        self.assertIsNotNone(row["completed_at"])
        self.assertEqual(row["diff_summary"]["updated_count"], 2)

    def test_get_cascade_run_workspace_isolation(self) -> None:
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": "d1", "comment_text": "..."}],
        )
        # Wrong workspace → 404
        result = cascade_store.get_cascade_run(
            self.store,
            workspace_id="ws-other",
            cascade_id=cascade_id,
        )
        self.assertIsNone(result)

    def test_get_cascade_run_project_isolation(self) -> None:
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.user_id,
            scope_mode="local",
            commented_decisions=[{"decision_id": "d1", "comment_text": "..."}],
        )
        # Right workspace, wrong project → 404 (cross-project enumeration guard)
        result = cascade_store.get_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            cascade_id=cascade_id,
            project_id="proj-other",
        )
        self.assertIsNone(result)

    def test_update_cascade_status_failed_records_error(self) -> None:
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.user_id,
            scope_mode="cascade",
            commented_decisions=[{"decision_id": "d1", "comment_text": "..."}],
        )
        cascade_store.update_cascade_status(
            self.store,
            workspace_id=self.workspace_id,
            cascade_id=cascade_id,
            status="failed",
            error="OPENAI_API_KEY missing",
        )
        row = cascade_store.get_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            cascade_id=cascade_id,
        )
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "OPENAI_API_KEY missing")
        self.assertIsNotNone(row["completed_at"])


if __name__ == "__main__":
    unittest.main()
