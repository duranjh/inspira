"""HTTP-level coverage for the v4 project state-machine endpoints (W2/B3.3).

Endpoints under test (all under ``/api/v2/...``):

- ``POST   /projects/{id}/transition``               — admin
- ``POST   /projects/{id}/manual-state-override``    — admin
- ``POST   /projects/{id}/manual-priority-order``    — admin
- ``GET    /workspaces/{workspace_id}/projects``     — viewer

Auth: every test signs up a real user, creates a workspace (which
becomes their default), and then stamps an existing project's
``workspace_id`` directly so the workspace-member dependency
resolves cleanly. The legacy project-create route doesn't accept
workspace_id yet — Session α's orchestrator path will. Until then,
the direct-stamp approach mirrors what the W1 backfill migration
does in production.
"""
from __future__ import annotations

import unittest

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:  # pragma: no cover
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _stamp_project_workspace(store, project_id: str, workspace_id: str) -> None:
    """Stamp workspace_id + reset project_state to pending_review.

    The v2_create_project route now lands rows in ``project_state="approved"``
    (kickoff = real work in flight, see store.create_v2_project docstring).
    The state-machine endpoint tests assume the row starts at the head
    of the transition graph; reset it explicitly.
    """
    with store._connect() as conn:
        conn.execute(
            "UPDATE v2_projects SET workspace_id = ?, project_state = ? "
            "WHERE project_id = ?",
            (workspace_id, "pending_review", project_id),
        )
        conn.commit()


def _audit_for(store, project_id: str) -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT category, action, actor_user_id, before_json, "
            "after_json FROM audit_log WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


class _BaseEndpointTest(unittest.TestCase):
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
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "kanban-acme", "name": "Kanban Acme"},
        )
        self.assertEqual(ws.status_code, 201, ws.text)
        self.workspace_id = ws.json()["workspace"]["workspace_id"]
        proj = self.client.post(
            "/api/v2/projects", json={"title": "Demo project"},
        )
        self.assertEqual(proj.status_code, 201, proj.text)
        self.project_id = proj.json()["project"]["project_id"]
        _stamp_project_workspace(
            self.store, self.project_id, self.workspace_id,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class TransitionEndpointTests(_BaseEndpointTest):

    def test_legal_transition_returns_200_and_updates_state(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "start_review"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["project"]["project_state"], "in_review")
        # Audit row written.
        events = [
            e for e in _audit_for(self.store, self.project_id)
            if e["category"] == "project_state"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "transition")

    def test_illegal_transition_returns_409_and_audits_rejection(
        self,
    ) -> None:
        # pending_review -> approved skips in_review and is illegal
        # via the verb path (``approve`` is not legal from pending).
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "approve"},
        )
        self.assertEqual(resp.status_code, 409, resp.text)
        body = resp.json()["detail"]
        self.assertEqual(body["error"], "illegal_transition")
        self.assertEqual(body["current"], "pending_review")
        self.assertEqual(body["attempted"], "approved")
        # transition_rejected audit row landed.
        rejected = [
            e for e in _audit_for(self.store, self.project_id)
            if e["action"] == "transition_rejected"
        ]
        self.assertEqual(len(rejected), 1)

    def test_unknown_action_returns_400(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "ship_it"},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(
            resp.json()["detail"]["error"], "unknown_action"
        )

    def test_cross_workspace_returns_404(self) -> None:
        # Sign up a second user on the same store, give them their own
        # workspace, and have them try to transition self.project_id
        # (which lives in self.workspace_id). The dependency picks up
        # *their* default workspace, the store sees the project lives
        # in a different workspace, and returns None → endpoint 404.
        self.client.cookies.clear()
        signup_and_login(
            self.client,
            email="outsider@example.org",
            password="out-pass1",
            display_name="Out",
        )
        ws_other = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "outsider-ws", "name": "Outsider"},
        )
        self.assertEqual(ws_other.status_code, 201, ws_other.text)
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "start_review"},
        )
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(
            resp.json()["detail"]["error"], "project_not_found",
        )

    def test_anon_user_cannot_transition(self) -> None:
        # Clear cookies so we present as anon (system); the dependency
        # 400s on missing workspace_id (no X-Workspace-Id header, no
        # default_workspace_id) before the role check, which is fine —
        # both paths block access. We accept either 400 or 401/403,
        # but require not-200.
        self.client.cookies.clear()
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "start_review"},
        )
        self.assertNotEqual(resp.status_code, 200)
        self.assertIn(resp.status_code, (400, 401, 403))


class ManualStateOverrideTests(_BaseEndpointTest):

    def test_override_with_note_bypasses_validation(self) -> None:
        # Drive to approved legally first.
        self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "start_review"},
        )
        self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "approve"},
        )
        # Re-open via manual override — illegal via /transition,
        # legal via the override path.
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/manual-state-override",
            json={
                "target_state": "in_review",
                "note": "customer reopened",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json()["project"]["project_state"], "in_review",
        )

    def test_empty_note_succeeds(self) -> None:
        # Founder direction 2026-05-05: note is optional. The audit trail
        # captures actor_user_id from the auth context, so the WHO is
        # always recorded; the WHY is a nice-to-have. Whitespace-only
        # notes are trimmed to empty server-side.
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/manual-state-override",
            json={"target_state": "approved", "note": "   "},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json()["project"]["project_state"], "approved",
        )

    def test_omitted_note_succeeds(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/manual-state-override",
            json={"target_state": "approved"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json()["project"]["project_state"], "approved",
        )

    def test_unknown_target_state_returns_400(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/manual-state-override",
            json={"target_state": "shipped", "note": "..."},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(
            resp.json()["detail"]["error"], "unknown_target_state",
        )


class ManualPriorityOrderTests(_BaseEndpointTest):

    def test_writes_priority_and_audits(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/manual-priority-order",
            json={"priority_order": 2048},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json()["project"]["priority_order"], 2048,
        )
        events = [
            e for e in _audit_for(self.store, self.project_id)
            if e["action"] == "manual_priority"
        ]
        self.assertEqual(len(events), 1)


class ListWorkspaceProjectsTests(_BaseEndpointTest):

    def test_returns_workspace_projects(self) -> None:
        resp = self.client.get(
            f"/api/v2/workspaces/{self.workspace_id}/projects",
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [p["project_id"] for p in resp.json()["projects"]]
        self.assertIn(self.project_id, ids)

    def test_state_filter(self) -> None:
        # Move the project to in_review then assert filtering.
        self.client.post(
            f"/api/v2/projects/{self.project_id}/transition",
            json={"action": "start_review"},
        )
        in_review = self.client.get(
            f"/api/v2/workspaces/{self.workspace_id}/projects",
            params={"state": "in_review"},
        )
        self.assertEqual(in_review.status_code, 200)
        ids = [p["project_id"] for p in in_review.json()["projects"]]
        self.assertEqual(ids, [self.project_id])
        # Filtering by a state with no rows returns []
        approved = self.client.get(
            f"/api/v2/workspaces/{self.workspace_id}/projects",
            params={"state": "approved"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["projects"], [])

    def test_unknown_state_filter_returns_400(self) -> None:
        resp = self.client.get(
            f"/api/v2/workspaces/{self.workspace_id}/projects",
            params={"state": "shipped"},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(
            resp.json()["detail"]["error"], "unknown_state",
        )

    def test_non_member_cannot_list(self) -> None:
        # Sign up a second user with no membership in self.workspace_id.
        self.client.cookies.clear()
        signup_and_login(
            self.client,
            email="outsider@example.org",
            password="out-pass1",
            display_name="Out",
        )
        resp = self.client.get(
            f"/api/v2/workspaces/{self.workspace_id}/projects",
        )
        # current_workspace_member returns 403 for non-members.
        self.assertEqual(resp.status_code, 403, resp.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
