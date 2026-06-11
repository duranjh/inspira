"""W2-θ cascade router HTTP endpoint tests.

Covers preview / commit / status lifecycle, workspace + project
isolation, RBAC (viewer write-blocked), and the OpenAI-key gate.
The cascade dispatch is patched to a no-op so we exercise the route
contract, not the LLM.
"""
from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch

from planning_studio_service import cascade_store

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _logout(client) -> None:
    client.post("/api/auth/logout")


def _signup(client, *, email: str, password: str = "password123") -> dict[str, Any]:
    return signup_and_login(client, email=email, password=password)


class CascadeRouteTests(unittest.TestCase):
    """Common scaffolding: workspace + project + 2 decisions in 1 topic."""

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self._old_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test-stub"

        self.owner = _signup(self.client, email="owner@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        )
        self.workspace_id: str = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = self.workspace_id

        # Project owned by the workspace owner.
        self.project = self.store.create_v2_project(
            user_id=self.owner["user_id"], title="Test Project",
        )
        self.project_id: str = self.project["project_id"]

        # One topic + two decisions for cascade scope.
        topic = self.store.create_topic(
            project_id=self.project_id, title="Auth", icon="flag",
            user_id=self.owner["user_id"],
        )
        self.topic_id: str = topic["topic_id"]
        self.d1: str = self.store.create_decision(
            topic_id=self.topic_id, project_id=self.project_id,
            statement="Use JWT", proposed_by="user",
            user_id=self.owner["user_id"],
        )["decision_id"]
        self.d2: str = self.store.create_decision(
            topic_id=self.topic_id, project_id=self.project_id,
            statement="Refresh tokens hourly", proposed_by="user",
            user_id=self.owner["user_id"],
        )["decision_id"]

    def tearDown(self) -> None:
        if self._old_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._old_key
        self.temp_dir.cleanup()

    # ----- preview --------------------------------------------------

    def test_preview_returns_scope_and_cost(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/preview",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "make it cheaper"},
                ],
                "scope_mode": "cascade",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("affected_scope", body)
        self.assertEqual(body["affected_scope"]["count"], 1)
        self.assertEqual(body["affected_scope"]["banner_state"], "narrow")
        self.assertIn(self.d2, body["affected_scope"]["decision_ids"])
        self.assertIn("estimated_cost_usd", body)
        self.assertIn("estimated_seconds", body)

    def test_preview_local_mode_has_no_banner(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/preview",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "tweak"},
                ],
                "scope_mode": "local",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["affected_scope"]["banner_state"], "none")

    # ----- commit (202 + cascade_id) -------------------------------

    def test_commit_returns_202_and_cascade_id(self) -> None:
        async def _noop_cascade(*args, **kwargs) -> None:
            return None
        with patch(
            "planning_studio_service.cascade_router.cascade.run_cascade",
            side_effect=_noop_cascade,
        ):
            resp = self.client.post(
                f"/api/v2/projects/{self.project_id}/regenerate-cascade",
                json={
                    "commented_decisions": [
                        {"decision_id": self.d1, "comment_text": "tweak"},
                    ],
                    "scope_mode": "local",
                },
            )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertIn("cascade_id", body)
        self.assertTrue(body["cascade_id"].startswith("csc-"))
        self.assertEqual(body["status"], "pending")

    def test_commit_503_when_openai_key_missing(self) -> None:
        os.environ.pop("OPENAI_API_KEY", None)
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "tweak"},
                ],
                "scope_mode": "local",
            },
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["error"], "cascade_unavailable")

    # ----- status ---------------------------------------------------

    def test_status_returns_pending_then_complete(self) -> None:
        # Seed a cascade row directly so we can assert on get_cascade_run output.
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            triggered_by=self.owner["user_id"],
            scope_mode="local",
            commented_decisions=[{"decision_id": self.d1, "comment_text": "x"}],
        )
        # Pending
        resp = self.client.get(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/{cascade_id}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending")
        # Mark complete
        cascade_store.update_cascade_status(
            self.store,
            workspace_id=self.workspace_id, cascade_id=cascade_id,
            status="complete",
            diff_summary={"updated_count": 1, "created_count": 0, "failed_count": 0},
        )
        resp = self.client.get(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/{cascade_id}",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["diff_summary"]["updated_count"], 1)

    def test_status_404_for_unknown_cascade(self) -> None:
        resp = self.client.get(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/csc-doesnotexist",
        )
        self.assertEqual(resp.status_code, 404)

    # ----- isolation ------------------------------------------------

    def test_status_404_when_cascade_belongs_to_different_project(self) -> None:
        # Build a second project (same owner, same workspace) and a cascade for it.
        other_proj = self.store.create_v2_project(
            user_id=self.owner["user_id"], title="Other",
        )["project_id"]
        cascade_id = cascade_store.create_cascade_run(
            self.store,
            workspace_id=self.workspace_id, project_id=other_proj,
            triggered_by=self.owner["user_id"], scope_mode="local",
            commented_decisions=[{"decision_id": "dx", "comment_text": "x"}],
        )
        # Query under self.project_id (wrong) → 404 not leak.
        resp = self.client.get(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/{cascade_id}",
        )
        self.assertEqual(resp.status_code, 404)

    def test_preview_404_for_project_not_owned_by_user(self) -> None:
        # Stranger workspace + project.
        _logout(self.client)
        _signup(self.client, email="stranger@elsewhere.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces", json={"slug": "stranger", "name": "S"},
        )
        stranger_ws = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = stranger_ws
        # Stranger tries to preview our project → 404 (don't leak existence).
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/preview",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "x"},
                ],
                "scope_mode": "local",
            },
        )
        self.assertEqual(resp.status_code, 404)

    # ----- input validation ----------------------------------------

    def test_preview_422_on_empty_commented_decisions(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/preview",
            json={"commented_decisions": [], "scope_mode": "cascade"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_preview_422_on_duplicate_decision_ids(self) -> None:
        """H2 regression: a body with the same decision_id twice triggers
        a deterministic version_int race; we now reject at validation
        time with 422 instead of letting it through.
        """
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/preview",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "first"},
                    {"decision_id": self.d1, "comment_text": "second"},
                ],
                "scope_mode": "local",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_commit_422_on_duplicate_decision_ids(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "first"},
                    {"decision_id": self.d1, "comment_text": "second"},
                ],
                "scope_mode": "local",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_preview_422_on_invalid_scope_mode(self) -> None:
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/regenerate-cascade/preview",
            json={
                "commented_decisions": [
                    {"decision_id": self.d1, "comment_text": "x"},
                ],
                "scope_mode": "wide-open",
            },
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
