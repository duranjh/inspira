"""DB-level coverage for the project state machine store methods.

Mirrors the store API exposed for the workspace Kanban (B1.1) and
review state machine (B3.3):

- ``update_v2_project_state`` (transition + manual override)
- ``update_v2_project_priority_order``
- ``list_v2_workspace_projects`` (sort + filter)

Audit rows must always land — tests assert on ``audit_log`` directly.
Concurrent-writer race is simulated by mutating ``project_state``
behind the store's back between read and write so the optimistic
``WHERE project_state = ?`` filter sees zero rows.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:  # pragma: no cover — fallback for non-package run
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]

from planning_studio_service.project_state import IllegalTransitionError
from planning_studio_service.store import PlanningStudioStore, StaleProjectStateError


def _create_project_in_workspace(
    store, *, user_id: str, workspace_id: str, title: str,
) -> str:
    """Create a v2_projects row already scoped to ``workspace_id``.

    The legacy ``v2_create_project`` route doesn't accept workspace_id
    yet — the orchestrator path will. For now the test stamps
    workspace_id directly on the row after creation, which is exactly
    what the production migration backfill does.

    Passes ``project_state="pending_review"`` explicitly because the
    state-machine tests assume the row starts at the head of the
    transition graph; the production default for ``create_v2_project``
    is ``"approved"`` (kickoff path) which would skip past every
    legal /transition target.
    """
    project = store.create_v2_project(
        user_id=user_id, title=title, project_state="pending_review",
    )
    project_id = project["project_id"]
    with store._connect() as conn:
        conn.execute(
            "UPDATE v2_projects SET workspace_id = ? WHERE project_id = ?",
            (workspace_id, project_id),
        )
        conn.commit()
    return project_id


def _audit_rows(store, *, project_id: str) -> list[dict]:
    """Read ``audit_log`` rows for a project, newest-first."""
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT category, action, before_json, after_json, "
            "actor_user_id, workspace_id "
            "FROM audit_log WHERE project_id = ? "
            "ORDER BY created_at DESC, event_id DESC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


class _BaseStateStoreTest(unittest.TestCase):
    """Common scaffolding: a signed-in user, a workspace, and a single
    project pinned to that workspace in the default ``pending_review``
    state."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = (
            make_test_app()
        )
        self.user = signup_and_login(
            self.client,
            email="kanban@example.org",
            password="s3cret-pass",
            display_name="K",
        )
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "kanban-acme", "name": "Kanban Acme"},
        )
        self.assertEqual(ws_resp.status_code, 201, ws_resp.text)
        self.workspace_id = ws_resp.json()["workspace"]["workspace_id"]
        self.project_id = _create_project_in_workspace(
            self.store,
            user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
            title="Test project",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class TransitionTests(_BaseStateStoreTest):

    def test_legal_transition_updates_state_and_audits(self) -> None:
        updated = self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="in_review",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["project_state"], "in_review")
        events = _audit_rows(self.store, project_id=self.project_id)
        # Filter by category to avoid relying on tied-timestamp ordering
        # against the project_create row written by create_v2_project.
        state_events = [
            e for e in events if e["category"] == "project_state"
        ]
        self.assertEqual(len(state_events), 1)
        transition = state_events[0]
        self.assertEqual(transition["action"], "transition")
        # before/after JSON shape pinned for the API contract.
        import json as _json
        self.assertEqual(
            _json.loads(transition["before_json"]),
            {"state": "pending_review"},
        )
        after = _json.loads(transition["after_json"])
        self.assertEqual(after["state"], "in_review")
        self.assertFalse(after["manual"])

    def test_illegal_transition_raises_and_does_not_change_state(
        self,
    ) -> None:
        # pending_review -> approved skips in_review and is a 409.
        with self.assertRaises(IllegalTransitionError):
            self.store.update_v2_project_state(
                project_id=self.project_id,
                workspace_id=self.workspace_id,
                actor_user_id=self.user["user_id"],
                target_state="approved",
            )
        # Row didn't move.
        live = self.store._get_v2_project(self.project_id)
        self.assertEqual(live["project_state"], "pending_review")
        # No audit row was written for the rejection — that's the
        # endpoint's job (the store doesn't know whether the failure
        # was user-driven or programmatic).
        events = [
            e for e in _audit_rows(self.store, project_id=self.project_id)
            if e["category"] == "project_state"
        ]
        self.assertEqual(events, [])

    def test_terminal_states_are_unreachable_via_transition(self) -> None:
        # Drive the project to ``approved`` legally, then any further
        # transition through this method must 409.
        self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="in_review",
        )
        self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="approved",
        )
        for target in (
            "pending_review", "in_review", "rejected", "summary_ready",
        ):
            with self.subTest(target=target):
                with self.assertRaises(IllegalTransitionError):
                    self.store.update_v2_project_state(
                        project_id=self.project_id,
                        workspace_id=self.workspace_id,
                        actor_user_id=self.user["user_id"],
                        target_state=target,
                    )

    def test_cross_column_transition_resets_priority_order(self) -> None:
        # User had manually re-ordered this card in ``pending_review``;
        # moving to ``in_review`` is a state change, not a re-order, so
        # the manual order resets and the new column sorts by ROI.
        self.store.update_v2_project_priority_order(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            priority_order=2048,
        )
        before = self.store._get_v2_project(self.project_id)
        self.assertEqual(before["priority_order"], 2048)
        after = self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="in_review",
        )
        self.assertIsNone(after["priority_order"])

    def test_concurrent_transition_loser_raises_stale(self) -> None:
        """Two admins try ``pending_review -> in_review`` simultaneously.
        The first wins; the second's ``WHERE project_state = ?`` filter
        finds zero rows and surfaces a stale-state error so the UI
        refetches.

        The store class is ``@dataclass(slots=True)`` so instance-level
        attribute swap is forbidden — we patch on the class with
        ``unittest.mock.patch.object``, which mutates the class
        descriptor table for the duration of the test only.
        """
        original = PlanningStudioStore._get_v2_project
        store = self.store
        project_id = self.project_id

        def _racing_get(self_inner, pid):
            # Read first so we can return a real, but soon-to-be-stale,
            # row. Then mutate the row before returning so the optimistic
            # UPDATE inside update_v2_project_state finds zero rows.
            row = original(self_inner, pid)
            with store._connect() as conn:
                conn.execute(
                    "UPDATE v2_projects SET project_state = 'in_review' "
                    "WHERE project_id = ?",
                    (pid,),
                )
                conn.commit()
            return row

        with patch.object(
            PlanningStudioStore, "_get_v2_project", _racing_get
        ):
            with self.assertRaises(StaleProjectStateError) as ctx:
                self.store.update_v2_project_state(
                    project_id=project_id,
                    workspace_id=self.workspace_id,
                    actor_user_id=self.user["user_id"],
                    target_state="in_review",
                )
        self.assertEqual(ctx.exception.observed, "pending_review")
        self.assertEqual(ctx.exception.project_id, project_id)

    def test_manual_override_bypasses_validation(self) -> None:
        # First reach a terminal state legally, then manually re-open it.
        self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="in_review",
        )
        self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="approved",
        )
        result = self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="in_review",
            note="customer reopened the request",
            manual=True,
        )
        self.assertEqual(result["project_state"], "in_review")
        # Filter by action — within-second timestamp ties make raw ORDER
        # BY created_at DESC unreliable for picking "the latest", so we
        # assert on the override row directly.
        events = _audit_rows(self.store, project_id=self.project_id)
        overrides = [e for e in events if e["action"] == "manual_override"]
        self.assertEqual(len(overrides), 1)
        import json as _json
        after = _json.loads(overrides[0]["after_json"])
        self.assertTrue(after["manual"])
        self.assertEqual(after["note"], "customer reopened the request")

    def test_cross_workspace_returns_none(self) -> None:
        # Project belongs to ``self.workspace_id``; another workspace
        # MUST NOT be able to mutate it. Returns None silently — the
        # API layer converts to 404 to avoid leaking IDs.
        result = self.store.update_v2_project_state(
            project_id=self.project_id,
            workspace_id="ws-not-mine",
            actor_user_id=self.user["user_id"],
            target_state="in_review",
        )
        self.assertIsNone(result)


class PriorityOrderTests(_BaseStateStoreTest):

    def test_sets_priority_order_and_audits(self) -> None:
        result = self.store.update_v2_project_priority_order(
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            priority_order=1024,
        )
        self.assertEqual(result["priority_order"], 1024)
        events = _audit_rows(self.store, project_id=self.project_id)
        priorities = [
            e for e in events
            if e["action"] == "manual_priority"
            and e["category"] == "project_state"
        ]
        self.assertEqual(len(priorities), 1)

    def test_cross_workspace_returns_none(self) -> None:
        result = self.store.update_v2_project_priority_order(
            project_id=self.project_id,
            workspace_id="ws-not-mine",
            actor_user_id=self.user["user_id"],
            priority_order=1024,
        )
        self.assertIsNone(result)


class ListWorkspaceProjectsTests(_BaseStateStoreTest):

    def setUp(self) -> None:
        super().setUp()
        # Add a few more projects with varying state / priority / ROI
        # so we can lock in the sort order.
        self.queue_a = self.project_id  # pending_review, no roi/priority
        # priority_order 100 in pending_review — should sort first.
        self.queue_b = _create_project_in_workspace(
            self.store,
            user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
            title="Queue B (ranked 100)",
        )
        self.store.update_v2_project_priority_order(
            project_id=self.queue_b,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            priority_order=100,
        )
        # ROI 9 in pending_review, no priority — should sort second
        # (after the manually-ranked card).
        self.queue_c = _create_project_in_workspace(
            self.store,
            user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
            title="Queue C (roi=9)",
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE v2_projects SET roi_score = 9 WHERE project_id = ?",
                (self.queue_c,),
            )
            conn.commit()
        # In ``in_review`` — different column.
        self.review_a = _create_project_in_workspace(
            self.store,
            user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
            title="Review A",
        )
        self.store.update_v2_project_state(
            project_id=self.review_a,
            workspace_id=self.workspace_id,
            actor_user_id=self.user["user_id"],
            target_state="in_review",
        )

    def test_sort_priority_before_roi_before_created_at(self) -> None:
        result = self.store.list_v2_workspace_projects(
            workspace_id=self.workspace_id,
            state="pending_review",
        )
        # 3 projects in pending_review. Order:
        # 1. queue_b (priority_order=100) — manual rank wins
        # 2. queue_c (roi=9) — ROI tiebreaker
        # 3. queue_a (no priority, no roi) — fallthrough
        ids = [p["project_id"] for p in result]
        self.assertEqual(
            ids, [self.queue_b, self.queue_c, self.queue_a]
        )

    def test_state_filter_narrows_to_one_column(self) -> None:
        result = self.store.list_v2_workspace_projects(
            workspace_id=self.workspace_id, state="in_review",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project_id"], self.review_a)
        self.assertEqual(result[0]["project_state"], "in_review")

    def test_no_state_filter_returns_all_columns(self) -> None:
        result = self.store.list_v2_workspace_projects(
            workspace_id=self.workspace_id,
        )
        ids = {p["project_id"] for p in result}
        self.assertEqual(
            ids,
            {self.queue_a, self.queue_b, self.queue_c, self.review_a},
        )

    def test_archived_excluded_by_default(self) -> None:
        # Archive one of the queue projects directly.
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE v2_projects SET archived_at = ? WHERE project_id = ?",
                ("2026-05-03T00:00:00Z", self.queue_c),
            )
            conn.commit()
        result = self.store.list_v2_workspace_projects(
            workspace_id=self.workspace_id, state="pending_review",
        )
        ids = {p["project_id"] for p in result}
        self.assertNotIn(self.queue_c, ids)
        # include_archived=True brings it back.
        result_all = self.store.list_v2_workspace_projects(
            workspace_id=self.workspace_id,
            state="pending_review",
            include_archived=True,
        )
        self.assertIn(
            self.queue_c, {p["project_id"] for p in result_all},
        )

    def test_other_workspace_isolated(self) -> None:
        result = self.store.list_v2_workspace_projects(
            workspace_id="ws-not-mine",
        )
        self.assertEqual(result, [])


class UserScopedListStateParityTests(_BaseStateStoreTest):
    """#148 regression: ``list_v2_projects`` and ``list_archived_v2_projects``
    must expose ``project_state`` from the column so the FE Kanban sees
    the same state across surfaces.

    Pre-fix the SELECT omitted ``project_state`` and the FE fallback
    ``project.project_state ?? "pending_review"`` made every home-list
    card render as pending_review regardless of its true state.
    """

    def test_list_v2_projects_exposes_project_state_column(self) -> None:
        # Transition the seeded project off the default so a missing
        # column would have surfaced as a state mismatch.
        self.store.update_v2_project_state(
            project_id=self.project_id,
            target_state="in_review",
            actor_user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
        )
        direct = self.store._get_v2_project(self.project_id)
        listed = next(
            p for p in self.store.list_v2_projects(
                user_id=self.user["user_id"],
            )
            if p["project_id"] == self.project_id
        )
        self.assertEqual(listed.get("project_state"), "in_review")
        self.assertEqual(
            listed["project_state"], direct["project_state"],
        )

    def test_list_v2_projects_include_archived_exposes_state(self) -> None:
        # include_archived=True takes the other SELECT branch — verify
        # parity on that path too.
        self.store.update_v2_project_state(
            project_id=self.project_id,
            target_state="in_review",
            actor_user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
        )
        listed = next(
            p for p in self.store.list_v2_projects(
                user_id=self.user["user_id"], include_archived=True,
            )
            if p["project_id"] == self.project_id
        )
        self.assertEqual(listed.get("project_state"), "in_review")

    def test_list_archived_v2_projects_exposes_project_state_column(self) -> None:
        # Walk the state past pending_review, then archive it. The
        # archive surface must keep the column-state visible.
        self.store.update_v2_project_state(
            project_id=self.project_id,
            target_state="in_review",
            actor_user_id=self.user["user_id"],
            workspace_id=self.workspace_id,
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE v2_projects SET archived_at = ? WHERE project_id = ?",
                ("2026-05-13T00:00:00Z", self.project_id),
            )
            conn.commit()
        direct = self.store._get_v2_project(self.project_id)
        archived_listed = next(
            p for p in self.store.list_archived_v2_projects(
                user_id=self.user["user_id"],
            )
            if p["project_id"] == self.project_id
        )
        self.assertEqual(archived_listed.get("project_state"), "in_review")
        self.assertEqual(
            archived_listed["project_state"], direct["project_state"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
