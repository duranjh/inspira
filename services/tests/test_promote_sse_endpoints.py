"""HTTP endpoint tests for /api/v2/projects/promote-from-cluster and
/api/v2/projects/{project_id}/events (closes #115 + #116).

Covers:
- happy path: cluster + title + seeds → 200 envelope with project, metadata,
  state, completed prio_run synthesized
- 4xx error codes: cluster_required (null), title_required (empty),
  cluster_not_found (cross-workspace + missing)
- 502 promote_orchestrator_failed: sub-agent terminal-fails before any
  canvas is written
- 504 promote_timeout: orchestrator hangs past _PROMOTE_TIMEOUT_S
- 403 admin gate (viewer can't promote)
- SSE proxy: replay events for completed run, 404 for project missing,
  404 for project without metadata.orchestrator_run_id, 404 cross-workspace,
  viewer auth (no role gate)
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
from typing import Any
from unittest.mock import patch

from planning_studio_service import orchestrator_router, orchestrator_store
from planning_studio_service.agents import sub_agent
from planning_studio_service.feedback_items import (
    cluster as fc,
    store as fi_store,
)
from planning_studio_service.feedback_items.embedding import EMBEDDING_DIMS

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _logout(client) -> None:
    client.post("/api/auth/logout")


def _signup(client, email: str) -> dict[str, Any]:
    return signup_and_login(client, email=email)


def _stub_sub_agent_output() -> dict[str, Any]:
    """Canned sub-agent output the patched extract call returns."""
    return {
        "topics": [
            {
                "title": "Repro the bug",
                "icon": "lightbulb",
                "why_this_topic": "test",
            }
        ],
        "decisions": [
            {
                "topic_index": 0,
                "statement": "Reproduce in staging first",
                "rationale": "test",
                "subject": "repro",
                "cited_feedback_item_ids": [],
            }
        ],
        "errors": [],
    }


class _BasePromoteTest(unittest.TestCase):
    """Common scaffolding: an admin-owned workspace with one feedback cluster
    seeded. Subclasses inherit ``self.client`` (admin authenticated),
    ``self.workspace_id``, ``self.cluster_id``.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.owner = _signup(self.client, email="admin@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        )
        self.workspace_id: str = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        self.cluster_id = self._seed_cluster()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_cluster(self) -> str:
        """Seed a single feedback cluster with 2 items and return its id."""
        for axis, hint, title in [
            (0, "bug", "Login crash on mobile"),
            (0, "bug", "Auth screen freezes on iOS"),
        ]:
            embedding = [0.0] * EMBEDDING_DIMS
            embedding[axis] = 1.0
            item_id, _ = fi_store.upsert_item(
                self.store,
                workspace_id=self.workspace_id,
                source="csv-import",
                external_id=None,
                title=title,
                body="",
                type_hint=hint,
            )
            fc.assign_or_create_cluster(
                self.store,
                workspace_id=self.workspace_id,
                item_id=item_id,
                embedding=embedding,
            )
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT cluster_id FROM feedback_clusters "
                "WHERE workspace_id = ? LIMIT 1",
                (self.workspace_id,),
            ).fetchone()
        return row[0]


class PromoteHappyPathTests(_BasePromoteTest):

    def test_happy_path_returns_project_envelope(self) -> None:
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "Mobile login flow rebuild",
                    "topic_seeds": [
                        {"name": "Repro the bug", "desc": "Reliably trigger it"},
                        {"name": "Audit auth code", "desc": "Find the bug"},
                    ],
                    "feedback_item_id": None,
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        envelope = resp.json()
        self.assertIn("project", envelope)
        project = envelope["project"]

        # Project envelope shape — the frontend reads project.project_id.
        self.assertIn("project_id", project)
        self.assertEqual(project["title"], "Mobile login flow rebuild")
        self.assertEqual(project["workspace_id"], self.workspace_id)
        # Orchestrator success path flips project_state → in_review so
        # the workspace Kanban routes the card to "In review" (human
        # review) instead of leaving it in "In queue". See
        # orchestrator.py _run_sub_agent_for_theme success path.
        self.assertEqual(project["project_state"], "in_review")

        # Metadata: orchestrator_run_id back-pointer + theme_id (the
        # orchestrator writes
        # both inside _create_canvas). The user's topic_seeds + feedback_item_id
        # intentionally don't land here — the orchestrator's success-path overwrite of
        # metadata_json (orchestrator.py:469-484) would clobber them.
        # They live durably in the prio_run input_snapshot instead.
        meta = project["metadata"]
        self.assertEqual(meta["theme_id"], self.cluster_id)
        self.assertIn("orchestrator_run_id", meta)
        self.assertTrue(meta["orchestrator_run_id"].startswith("or-"))

        # The synthesized prioritization_run is completed with one theme,
        # AND retains the user's seeds / title / feedback_item_id for
        # future LLM threading.
        run = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=meta["orchestrator_run_id"],
        )
        prio = orchestrator_store.get_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run["prioritization_run_id"],
        )
        self.assertEqual(prio["status"], "completed")
        self.assertEqual(prio["output"]["model"], "user-promote")
        self.assertEqual(len(prio["output"]["themes"]), 1)
        self.assertEqual(
            prio["output"]["themes"][0]["cluster_id"], self.cluster_id,
        )
        snapshot = prio["input_snapshot"]
        self.assertEqual(snapshot["source"], "promote_from_cluster")
        self.assertEqual(
            snapshot["project_title"], "Mobile login flow rebuild",
        )
        self.assertEqual(
            snapshot["topic_seeds"],
            [
                {"name": "Repro the bug", "desc": "Reliably trigger it"},
                {"name": "Audit auth code", "desc": "Find the bug"},
            ],
        )
        self.assertIsNone(snapshot["feedback_item_id"])

    def test_feedback_item_id_persists_in_prio_run_snapshot(self) -> None:
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "X",
                    "topic_seeds": [],
                    "feedback_item_id": "fi-some-id",
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        meta = resp.json()["project"]["metadata"]
        run = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=meta["orchestrator_run_id"],
        )
        prio = orchestrator_store.get_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run["prioritization_run_id"],
        )
        self.assertEqual(prio["input_snapshot"]["feedback_item_id"], "fi-some-id")
        self.assertEqual(prio["input_snapshot"]["topic_seeds"], [])


class PromoteValidationTests(_BasePromoteTest):

    def test_null_cluster_id_returns_400_cluster_required(self) -> None:
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": None,
                "project_title": "X",
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "cluster_required")

    def test_empty_string_cluster_id_returns_400_cluster_required(self) -> None:
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": "",
                "project_title": "X",
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "cluster_required")

    def test_empty_title_returns_400_title_required(self) -> None:
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": self.cluster_id,
                "project_title": "   ",  # whitespace-only also rejects
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "title_required")

    def test_null_title_returns_400_title_required(self) -> None:
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": self.cluster_id,
                "project_title": None,
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "title_required")

    def test_unknown_cluster_returns_404_cluster_not_found(self) -> None:
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": "cl-does-not-exist",
                "project_title": "X",
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "cluster_not_found")


class PromoteAuthorizationTests(_BasePromoteTest):

    def test_viewer_cannot_post_promote(self) -> None:
        viewer_email = "viewer@acme.com"
        _logout(self.client)
        _signup(self.client, email=viewer_email)
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@acme.com", "password": "password123"},
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": viewer_email, "role": "viewer"},
        )
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": viewer_email, "password": "password123"},
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": self.cluster_id,
                "project_title": "X",
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_cross_workspace_cluster_returns_404(self) -> None:
        # Admin in WS A tries to promote a cluster that belongs to no
        # workspace they're a member of (here: a different fresh user
        # signs up so X-Workspace-Id resolves to *their* workspace,
        # which doesn't contain self.cluster_id).
        _logout(self.client)
        _signup(self.client, email="other@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "other", "name": "Other"},
        )
        other_ws_id = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = other_ws_id
        resp = self.client.post(
            "/api/v2/projects/promote-from-cluster",
            json={
                "cluster_id": self.cluster_id,  # belongs to WS A, not other
                "project_title": "X",
                "topic_seeds": [],
                "feedback_item_id": None,
            },
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "cluster_not_found")


class PromoteOrchestratorFailureTests(_BasePromoteTest):
    """Cover the 502 fast-fail and 504 timeout polling branches."""

    def test_fast_fail_returns_502_promote_orchestrator_failed(self) -> None:
        # Patch orchestrator.run to mark a sub_agent_runs row as 'error'
        # without ever creating a v2_projects row. The polling loop sees
        # all sub-agents terminal-failed → 502.
        async def _failing_run(
            store,
            *,
            workspace_id: str,
            orchestrator_run_id: str,
            prioritization_run_id: str,
            top_n: int,
            event_queue,
            **kwargs,
        ) -> None:
            sa_id = orchestrator_store.create_sub_agent_run(
                store,
                workspace_id=workspace_id,
                orchestrator_run_id=orchestrator_run_id,
                theme_id=self.cluster_id,
            )
            orchestrator_store.complete_sub_agent_run(
                store,
                workspace_id=workspace_id,
                sub_agent_run_id=sa_id,
                project_id=None,
                decisions_count=0,
                conflicts_count=0,
                error="simulated sub-agent failure",
            )
            orchestrator_store.complete_orchestrator_run(
                store,
                workspace_id=workspace_id,
                run_id=orchestrator_run_id,
                error="simulated orchestrator failure",
            )

        with patch(
            "planning_studio_service.orchestrator_router.orchestrator_agent.run",
            side_effect=_failing_run,
        ), patch.object(
            orchestrator_router, "_PROMOTE_POLL_INTERVAL_S", 0.05,
        ), patch.object(
            orchestrator_router, "_PROMOTE_TIMEOUT_S", 5.0,
        ):
            t0 = time.time()
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "X",
                    "topic_seeds": [],
                    "feedback_item_id": None,
                },
            )
            elapsed = time.time() - t0
        self.assertEqual(resp.status_code, 502, resp.text)
        self.assertEqual(
            resp.json()["detail"]["error"], "promote_orchestrator_failed",
        )
        # Fast-fail: should return well under the 5s ceiling once the
        # sub-agent flips to 'error'. Generous 3s assertion to absorb
        # CI jitter without losing the signal.
        self.assertLess(
            elapsed, 3.0,
            f"fast-fail took {elapsed:.2f}s — should return promptly",
        )

    def test_canvas_exists_but_failed_returns_502_promote_orchestrator_failed(
        self,
    ) -> None:
        # The orchestrator writes the canvas with metadata.state="generating" early, then
        # on LLM failure UPDATEs to state="generation_failed". Polling must
        # check the state on a found canvas and 502 — otherwise the route
        # returns a "successful" 200 envelope pointing at a dead canvas.
        cluster_id_for_test = self.cluster_id
        owner_id = self.owner["user_id"]

        async def _failing_canvas_run(
            store,
            *,
            workspace_id: str,
            orchestrator_run_id: str,
            prioritization_run_id: str,
            top_n: int,
            event_queue,
            **kwargs,
        ) -> None:
            sa_id = orchestrator_store.create_sub_agent_run(
                store,
                workspace_id=workspace_id,
                orchestrator_run_id=orchestrator_run_id,
                theme_id=cluster_id_for_test,
            )
            # Simulate the orchestrator's canvas creation + failure-path metadata write
            # — i.e. the canvas exists but the LLM blew up afterward.
            project = store.create_v2_project(
                user_id=owner_id, title="X failed",
                project_state="pending_review",
            )
            with store._connect() as connection:
                connection.execute(
                    "UPDATE v2_projects SET workspace_id = ?, "
                    "metadata_json = ? WHERE project_id = ?",
                    (
                        workspace_id,
                        json.dumps({
                            "state": "generation_failed",
                            "orchestrator_run_id": orchestrator_run_id,
                            "theme_id": cluster_id_for_test,
                            "autonomous": True,
                        }),
                        project["project_id"],
                    ),
                )
                connection.commit()
            orchestrator_store.complete_sub_agent_run(
                store,
                workspace_id=workspace_id,
                sub_agent_run_id=sa_id,
                project_id=project["project_id"],
                decisions_count=0,
                conflicts_count=0,
                error="simulated LLM failure after canvas write",
            )
            orchestrator_store.complete_orchestrator_run(
                store,
                workspace_id=workspace_id,
                run_id=orchestrator_run_id,
                error="simulated LLM failure",
            )

        with patch(
            "planning_studio_service.orchestrator_router.orchestrator_agent.run",
            side_effect=_failing_canvas_run,
        ), patch.object(
            orchestrator_router, "_PROMOTE_POLL_INTERVAL_S", 0.05,
        ), patch.object(
            orchestrator_router, "_PROMOTE_TIMEOUT_S", 5.0,
        ):
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "X",
                    "topic_seeds": [],
                    "feedback_item_id": None,
                },
            )
        self.assertEqual(resp.status_code, 502, resp.text)
        self.assertEqual(
            resp.json()["detail"]["error"], "promote_orchestrator_failed",
        )

    def test_orchestrator_hang_returns_504_promote_timeout(self) -> None:
        # Patch orchestrator.run to never write a sub_agent_runs row OR
        # a v2_projects row (just sleep). Polling sees no sub_agents,
        # falls through to timeout.
        async def _hanging_run(*args, **kwargs) -> None:
            await asyncio.sleep(60)

        with patch(
            "planning_studio_service.orchestrator_router.orchestrator_agent.run",
            side_effect=_hanging_run,
        ), patch.object(
            orchestrator_router, "_PROMOTE_POLL_INTERVAL_S", 0.05,
        ), patch.object(
            orchestrator_router, "_PROMOTE_TIMEOUT_S", 0.5,
        ):
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "X",
                    "topic_seeds": [],
                    "feedback_item_id": None,
                },
            )
        self.assertEqual(resp.status_code, 504, resp.text)
        self.assertEqual(resp.json()["detail"]["error"], "promote_timeout")


# ---------------------------------------------------------------------
# /api/v2/projects/{project_id}/events  — SSE proxy (closes #116)
# ---------------------------------------------------------------------


class _BaseSSEProxyTest(_BasePromoteTest):
    """Adds a helper to land a real promote-generated project + run."""

    def _promote_to_get_project(self) -> dict[str, Any]:
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "Demo canvas",
                    "topic_seeds": [],
                    "feedback_item_id": None,
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["project"]


class SSEProxyHappyPathTests(_BaseSSEProxyTest):

    def test_replay_for_completed_project_streams_events(self) -> None:
        project = self._promote_to_get_project()
        project_id = project["project_id"]
        # Wait for the orchestrator task to complete so the queue is gone
        # and the SSE handler falls through to the trimmed replay path.
        run_id = project["metadata"]["orchestrator_run_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            r = self.client.get(f"/api/v2/orchestrator/runs/{run_id}")
            if r.status_code == 200 and r.json()["status"] in {
                "completed", "error",
            }:
                break
            time.sleep(0.1)
        self.assertEqual(r.json()["status"], "completed")

        with self.client.stream(
            "GET", f"/api/v2/projects/{project_id}/events",
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers["content-type"],
                "text/event-stream; charset=utf-8",
            )
            events: list[str] = []
            for line in response.iter_lines():
                if line.startswith("event: "):
                    events.append(line[len("event: "):].strip())
                if "orchestrator.completed" in events:
                    break
        self.assertEqual(events[0], "run.started")
        self.assertIn("decision_summary.ready", events)
        self.assertIn("orchestrator.completed", events)


class SSEProxyValidationTests(_BasePromoteTest):

    def test_unknown_project_returns_404_project_not_found(self) -> None:
        resp = self.client.get(
            "/api/v2/projects/project-deadbeef0000/events",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "project_not_found")

    def test_project_without_orchestrator_run_returns_404_no_orchestrator_run(
        self,
    ) -> None:
        # Insert a v2_projects row with empty metadata — i.e. one created
        # by the kickoff path that never went through the orchestrator.
        # Use the existing store helper, then attach workspace_id manually
        # (the helper doesn't take workspace_id since most legacy code
        # paths pre-date the column).
        project = self.store.create_v2_project(
            user_id=self.owner["user_id"],
            title="Manual-kickoff canvas",
        )
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET workspace_id = ?, metadata_json = ? "
                "WHERE project_id = ?",
                (self.workspace_id, "{}", project["project_id"]),
            )
            connection.commit()
        resp = self.client.get(
            f"/api/v2/projects/{project['project_id']}/events",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.json()["detail"]["error"], "no_orchestrator_run",
        )

    def test_cross_workspace_project_returns_404(self) -> None:
        # Promote to get a real project in WS A, then switch to a fresh
        # user/WS B and verify the SSE endpoint 404s rather than leaking.
        project = self._promote_to_get_project_for_cross_ws_test()
        project_id = project["project_id"]
        _logout(self.client)
        _signup(self.client, email="other@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "other", "name": "Other"},
        )
        other_ws_id = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = other_ws_id
        resp = self.client.get(f"/api/v2/projects/{project_id}/events")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"]["error"], "project_not_found")

    def _promote_to_get_project_for_cross_ws_test(self) -> dict[str, Any]:
        # Helper duplicated from _BaseSSEProxyTest because this class
        # extends _BasePromoteTest directly (avoids brittle inheritance
        # with the SSE happy-path class which has its own state).
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            resp = self.client.post(
                "/api/v2/projects/promote-from-cluster",
                json={
                    "cluster_id": self.cluster_id,
                    "project_title": "X-WS test",
                    "topic_seeds": [],
                    "feedback_item_id": None,
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["project"]


class SSEProxyAuthTests(_BaseSSEProxyTest):

    def test_viewer_can_read_sse_proxy(self) -> None:
        # Promote as admin first.
        project = self._promote_to_get_project()
        project_id = project["project_id"]

        # Add a viewer member.
        viewer_email = "viewer-sse@acme.com"
        _logout(self.client)
        _signup(self.client, email=viewer_email)
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@acme.com", "password": "password123"},
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": viewer_email, "role": "viewer"},
        )
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": viewer_email, "password": "password123"},
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        # Viewer GETs the SSE stream — should succeed (member+ gate).
        with self.client.stream(
            "GET", f"/api/v2/projects/{project_id}/events",
        ) as response:
            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
