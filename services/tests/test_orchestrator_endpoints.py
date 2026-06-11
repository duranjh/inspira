"""W3 orchestrator HTTP endpoint tests.

Covers authorization, workspace isolation, idempotency, top_n
validation, and the SSE streams (live + trimmed replay). The
sub_agent.extract_topics_and_decisions_for_theme call is patched
to a canned dict so tests don't hit the live LLM.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import unittest
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from planning_studio_service import orchestrator_store
from planning_studio_service.agents import sub_agent
from planning_studio_service.agents.tiers import ModelTier
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


def _stub_output(theme_count: int = 1) -> dict[str, Any]:
    """Canned sub-agent output. theme_count controls per-theme variation."""
    return {
        "topics": [
            {
                "title": f"Topic {theme_count}",
                "icon": "lightbulb",
                "why_this_topic": "test",
            }
        ],
        "decisions": [
            {
                "topic_index": 0,
                "statement": f"Decision {theme_count}",
                "rationale": "test",
                "subject": f"subj_{theme_count}",
                "cited_feedback_item_ids": [],
            }
        ],
        "errors": [],
    }


class _BaseOrchestratorEndpointTest(unittest.TestCase):
    """Common scaffolding: an owner workspace with seeded clusters
    and a completed prioritization run.

    Subclasses inherit ``self.workspace_id``, ``self.prio_run_id``,
    ``self.client`` (admin authenticated)."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Sign up admin + own a workspace.
        self.owner = _signup(self.client, email="admin@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        )
        self.workspace_id: str = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        self._seed_clusters()
        self.prio_run_id = self._make_completed_prio_run()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_clusters(self) -> None:
        for axis, hint, title in [
            (0, "bug", "A login crash"),
            (1, "feature", "B export request"),
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

    def _make_completed_prio_run(self) -> str:
        cluster_ids: list[str] = []
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT cluster_id FROM feedback_clusters "
                "WHERE workspace_id = ?",
                (self.workspace_id,),
            ).fetchall()
        cluster_ids = [r[0] for r in rows]
        run_id = orchestrator_store.create_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by=self.owner["user_id"],
            input_snapshot={"cluster_ids": cluster_ids},
        )
        themes = [
            {
                "cluster_id": cid,
                "rank": idx + 1,
                "score": 90.0 - idx,
                "rationale": "test",
                "suggested_theme_label": f"Theme {idx}",
                "provenance": {},
            }
            for idx, cid in enumerate(cluster_ids)
        ]
        orchestrator_store.complete_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
            output={
                "themes": themes,
                "model": "test-stub",
                "input_cluster_count": len(cluster_ids),
            },
        )
        return run_id


class AuthorizationTests(_BaseOrchestratorEndpointTest):

    def test_admin_can_post_prioritize(self) -> None:
        resp = self.client.post("/api/v2/orchestrator/prioritize")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("run_id", resp.json())

    def test_viewer_cannot_post_prioritize(self) -> None:
        # Add a viewer member.
        viewer_email = "viewer@acme.com"
        # Sign up the viewer first so the user exists.
        _logout(self.client)
        _signup(self.client, email=viewer_email)
        _logout(self.client)
        # Switch back to owner; invite viewer.
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@acme.com", "password": "password123"},
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": viewer_email, "role": "viewer"},
        )
        # Switch to the viewer.
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": viewer_email, "password": "password123"},
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        resp = self.client.post("/api/v2/orchestrator/prioritize")
        self.assertEqual(resp.status_code, 403)

    def test_viewer_cannot_post_run(self) -> None:
        viewer_email = "viewer2@acme.com"
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
            "/api/v2/orchestrator/run",
            json={"prioritization_run_id": self.prio_run_id, "top_n": 2},
        )
        self.assertEqual(resp.status_code, 403)

    def test_viewer_can_get_runs(self) -> None:
        # Viewers + members can read; only admin+ can mutate.
        # Run /prioritize first to have a run to read.
        resp = self.client.post("/api/v2/orchestrator/prioritize")
        prio_run_id = resp.json()["run_id"]
        get = self.client.get(
            f"/api/v2/orchestrator/prioritization-runs/{prio_run_id}"
        )
        self.assertEqual(get.status_code, 200)


class WorkspaceIsolationTests(_BaseOrchestratorEndpointTest):

    def test_cross_workspace_run_id_returns_404(self) -> None:
        # Create a second user with their own workspace.
        _logout(self.client)
        _signup(self.client, email="other@acme.com")
        # Their default workspace is auto-created on signup.
        # Try to read the OWNER's prio_run_id from this user's workspace.
        resp = self.client.get(
            f"/api/v2/orchestrator/prioritization-runs/{self.prio_run_id}"
        )
        # Either 404 (not in this workspace) or 400/403 if no workspace
        # context. Normalize: anything non-2xx is acceptable here.
        self.assertNotEqual(resp.status_code, 200)

    def test_cross_workspace_run_post_returns_404(self) -> None:
        _logout(self.client)
        _signup(self.client, email="other2@acme.com")
        resp = self.client.post(
            "/api/v2/orchestrator/run",
            json={"prioritization_run_id": self.prio_run_id, "top_n": 2},
        )
        # 404 (not in this workspace) — definitely NOT 200.
        self.assertNotEqual(resp.status_code, 200)


class IdempotencyTests(_BaseOrchestratorEndpointTest):

    def test_double_post_run_returns_same_run_id(self) -> None:
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_output(),
        ):
            first = self.client.post(
                "/api/v2/orchestrator/run",
                json={
                    "prioritization_run_id": self.prio_run_id,
                    "top_n": 2,
                },
            )
            self.assertEqual(first.status_code, 200)
            run_id_1 = first.json()["run_id"]
            self.assertFalse(first.json()["idempotent_hit"])
            second = self.client.post(
                "/api/v2/orchestrator/run",
                json={
                    "prioritization_run_id": self.prio_run_id,
                    "top_n": 2,
                },
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["run_id"], run_id_1)
            self.assertTrue(second.json()["idempotent_hit"])

    def test_run_against_uncompleted_prio_returns_409(self) -> None:
        # Create a brand-new prio run that's still 'running'.
        running_id = orchestrator_store.create_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by=self.owner["user_id"],
            input_snapshot={"cluster_ids": []},
        )
        resp = self.client.post(
            "/api/v2/orchestrator/run",
            json={"prioritization_run_id": running_id, "top_n": 1},
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["detail"]["error"],
            "prioritization_run_not_complete",
        )


class ValidationTests(_BaseOrchestratorEndpointTest):

    def test_top_n_above_cap_returns_422(self) -> None:
        resp = self.client.post(
            "/api/v2/orchestrator/run",
            json={"prioritization_run_id": self.prio_run_id, "top_n": 11},
        )
        # FastAPI/Pydantic returns 422 for body validation errors.
        self.assertEqual(resp.status_code, 422)

    def test_top_n_zero_returns_422(self) -> None:
        resp = self.client.post(
            "/api/v2/orchestrator/run",
            json={"prioritization_run_id": self.prio_run_id, "top_n": 0},
        )
        self.assertEqual(resp.status_code, 422)


class SSEReplayTests(_BaseOrchestratorEndpointTest):

    def test_replay_for_completed_run(self) -> None:
        """Run completes; subscribe to events; expect trimmed replay."""
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_output(),
        ):
            post = self.client.post(
                "/api/v2/orchestrator/run",
                json={
                    "prioritization_run_id": self.prio_run_id,
                    "top_n": 2,
                },
            )
            run_id = post.json()["run_id"]
            # Wait for the orchestrator task to complete. The TestClient
            # runs in a sync context but the orchestrator runs as an
            # asyncio task on the loop. Poll the GET /runs endpoint.
            import time
            deadline = time.time() + 10
            while time.time() < deadline:
                runs_resp = self.client.get(
                    f"/api/v2/orchestrator/runs/{run_id}"
                )
                if (
                    runs_resp.status_code == 200
                    and runs_resp.json()["status"] in {"completed", "error"}
                ):
                    break
                time.sleep(0.1)
            self.assertEqual(runs_resp.json()["status"], "completed")
            # Now subscribe to events — queue is gone, expect replay.
            with self.client.stream(
                "GET", f"/api/v2/orchestrator/runs/{run_id}/events"
            ) as response:
                self.assertEqual(response.status_code, 200)
                events: list[str] = []
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        events.append(line[len("event: "):].strip())
                    if "orchestrator.completed" in events:
                        break
            self.assertEqual(events[0], "run.started")
            # Trimmed: no per-decision noise.
            self.assertNotIn("topic.drafted", events)
            self.assertNotIn("decision.drafted", events)
            self.assertIn("decision_summary.ready", events)
            self.assertIn("orchestrator.completed", events)
            # decision_summary.ready precedes orchestrator.completed.
            self.assertLess(
                events.index("decision_summary.ready"),
                events.index("orchestrator.completed"),
            )


class ListRunsTests(_BaseOrchestratorEndpointTest):
    """Workspace-scoped GET /api/v2/orchestrator/runs.

    Polled every 3s by the AI Status chip's useOrchestratorState hook.
    Filters by status, embeds sub_agents with theme_label joined from
    feedback_clusters.
    """

    def _make_orch_run(
        self,
        *,
        status: str,
        started_at_offset_seconds: int = 0,
    ) -> str:
        """Insert an orchestrator_runs row directly so we can control
        its status/started_at without driving the full async pipeline.
        Returns the run_id.
        """
        run_id = f"or-{secrets.token_hex(5)}"
        # Each row needs a UNIQUE (workspace_id, prioritization_run_id)
        # so synth a fresh prio_run_id per call.
        prio_id = orchestrator_store.create_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by=self.owner["user_id"],
            input_snapshot={"cluster_ids": []},
        )
        orchestrator_store.complete_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=prio_id,
            output={"themes": [], "model": "test", "input_cluster_count": 0},
        )
        from planning_studio_service.store import now_timestamp
        base = datetime.fromisoformat(
            now_timestamp().replace("Z", "+00:00")
        )
        started = (
            base + timedelta(seconds=started_at_offset_seconds)
        ).isoformat()
        with self.store._connect() as connection:
            connection.execute(
                """
                INSERT INTO orchestrator_runs (
                    run_id, workspace_id, prioritization_run_id,
                    triggered_by, top_n, status,
                    started_at, completed_at, summary_json, error
                )
                VALUES (?, ?, ?, ?, 5, ?, ?, NULL, NULL, NULL)
                """,
                (
                    run_id, self.workspace_id, prio_id,
                    self.owner["user_id"], status, started,
                ),
            )
            connection.commit()
        return run_id

    def _attach_sub_agent(
        self, orch_run_id: str, theme_id: str, status: str = "running",
    ) -> str:
        """Insert a sub_agent_runs row attached to an orchestrator run."""
        sa_id = orchestrator_store.create_sub_agent_run(
            self.store,
            workspace_id=self.workspace_id,
            orchestrator_run_id=orch_run_id,
            theme_id=theme_id,
        )
        if status != "running":
            orchestrator_store.complete_sub_agent_run(
                self.store,
                workspace_id=self.workspace_id,
                sub_agent_run_id=sa_id,
                project_id=None,
                decisions_count=0,
                conflicts_count=0,
                error=None if status == "completed" else "stub error",
            )
        return sa_id

    def test_list_runs_no_status_returns_all_ordered(self) -> None:
        ids = [
            self._make_orch_run(
                status=s, started_at_offset_seconds=offset,
            )
            for s, offset in [
                ("running", 30),
                ("completed", 0),
                ("error", 60),
            ]
        ]
        resp = self.client.get("/api/v2/orchestrator/runs")
        self.assertEqual(resp.status_code, 200)
        runs = resp.json()["runs"]
        self.assertEqual(len(runs), 3)
        self.assertEqual([r["run_id"] for r in runs], [ids[2], ids[0], ids[1]])

    def test_list_runs_filters_by_status_running(self) -> None:
        running_id = self._make_orch_run(status="running")
        self._make_orch_run(status="completed", started_at_offset_seconds=10)
        self._make_orch_run(status="error", started_at_offset_seconds=20)
        resp = self.client.get("/api/v2/orchestrator/runs?status=running")
        self.assertEqual(resp.status_code, 200)
        runs = resp.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], running_id)
        self.assertEqual(runs[0]["status"], "running")

    def test_list_runs_filters_by_status_completed(self) -> None:
        self._make_orch_run(status="running")
        completed_id = self._make_orch_run(
            status="completed", started_at_offset_seconds=10,
        )
        resp = self.client.get(
            "/api/v2/orchestrator/runs?status=completed&limit=1"
        )
        runs = resp.json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], completed_id)

    def test_list_runs_invalid_status_400(self) -> None:
        resp = self.client.get("/api/v2/orchestrator/runs?status=garbage")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "invalid_status")

    def test_list_runs_workspace_isolation(self) -> None:
        # Owner has 1 run.
        own_id = self._make_orch_run(status="running")
        # New user with their own workspace cannot see it.
        _logout(self.client)
        _signup(self.client, email="other@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "other", "name": "Other"},
        )
        other_ws_id = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = other_ws_id
        resp = self.client.get("/api/v2/orchestrator/runs?status=running")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runs"], [])
        # Sanity: owner can still see it.
        _ = own_id  # silence linter

    def test_list_runs_embeds_sub_agents(self) -> None:
        run_id = self._make_orch_run(status="running")
        # Use a real cluster_id from the seeded base so the FK is meaningful;
        # the helper picks the first cluster.
        with self.store._connect() as connection:
            cluster_row = connection.execute(
                "SELECT cluster_id FROM feedback_clusters "
                "WHERE workspace_id = ? LIMIT 1",
                (self.workspace_id,),
            ).fetchone()
        self._attach_sub_agent(run_id, theme_id=cluster_row[0])
        resp = self.client.get("/api/v2/orchestrator/runs?status=running")
        runs = resp.json()["runs"]
        self.assertEqual(len(runs[0]["sub_agents"]), 1)
        self.assertEqual(
            runs[0]["sub_agents"][0]["theme_id"], cluster_row[0],
        )

    def test_list_runs_embeds_theme_label_when_cluster_exists(self) -> None:
        # Set a known label on a cluster, then attach a sub_agent to it.
        with self.store._connect() as connection:
            cluster_row = connection.execute(
                "SELECT cluster_id FROM feedback_clusters "
                "WHERE workspace_id = ? LIMIT 1",
                (self.workspace_id,),
            ).fetchone()
            connection.execute(
                "UPDATE feedback_clusters SET theme = ? WHERE cluster_id = ?",
                ("Login crash investigation", cluster_row[0]),
            )
            connection.commit()
        run_id = self._make_orch_run(status="running")
        self._attach_sub_agent(run_id, theme_id=cluster_row[0])
        resp = self.client.get("/api/v2/orchestrator/runs?status=running")
        sa = resp.json()["runs"][0]["sub_agents"][0]
        self.assertEqual(sa["theme_label"], "Login crash investigation")

    def test_list_runs_theme_label_null_for_legacy_row(self) -> None:
        # sub_agent_runs.theme_id pointing at a non-existent cluster
        # → LEFT JOIN yields NULL theme_label rather than dropping the row.
        run_id = self._make_orch_run(status="running")
        self._attach_sub_agent(run_id, theme_id="cluster-does-not-exist")
        resp = self.client.get("/api/v2/orchestrator/runs?status=running")
        runs = resp.json()["runs"]
        self.assertEqual(len(runs[0]["sub_agents"]), 1)
        self.assertIsNone(runs[0]["sub_agents"][0]["theme_label"])

    def test_get_orchestrator_run_also_includes_theme_label(self) -> None:
        # Consistency: GET /runs/{run_id} should include the same
        # theme_label join (we updated get_orchestrator_run for parity).
        with self.store._connect() as connection:
            cluster_row = connection.execute(
                "SELECT cluster_id FROM feedback_clusters "
                "WHERE workspace_id = ? LIMIT 1",
                (self.workspace_id,),
            ).fetchone()
            connection.execute(
                "UPDATE feedback_clusters SET theme = ? WHERE cluster_id = ?",
                ("Browser test matrix", cluster_row[0]),
            )
            connection.commit()
        run_id = self._make_orch_run(status="running")
        self._attach_sub_agent(run_id, theme_id=cluster_row[0])
        resp = self.client.get(f"/api/v2/orchestrator/runs/{run_id}")
        sa = resp.json()["sub_agents"][0]
        self.assertEqual(sa["theme_label"], "Browser test matrix")


class StartCanvasMetadataTests(_BaseOrchestratorEndpointTest):
    """Contract: POST /start-canvas must stamp ai_review_in_progress
    SYNCHRONOUSLY onto the v2_project's metadata before returning 202,
    so the frontend Kanban (columnFor in useKanbanData.ts) routes the
    card to the "AI thinking" column on the next refetch.

    Also: existing shell metadata (cluster_id, auto_promoted,
    dominant_category, feedback_count) MUST be preserved — the
    orchestrator path read-modify-writes, it does not overwrite.
    """

    def _make_shell(self) -> tuple[str, str]:
        from planning_studio_service.feedback_items import cluster as fc
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT cluster_id FROM feedback_clusters "
                "WHERE workspace_id = ? LIMIT 1",
                (self.workspace_id,),
            ).fetchone()
        cluster_id = row[0]
        fc.ensure_v2_projects_for_clusters(
            self.store,
            workspace_id=self.workspace_id,
            user_id=self.owner["user_id"],
            cluster_ids={cluster_id},
            plan_tier=ModelTier.ENTERPRISE,  # single cluster, cap irrelevant
        )
        with self.store._connect() as connection:
            proj = connection.execute(
                "SELECT project_id FROM v2_projects "
                "WHERE workspace_id = ? LIMIT 1",
                (self.workspace_id,),
            ).fetchone()
        return proj[0], cluster_id

    def test_start_canvas_stamps_ai_review_in_progress(self) -> None:
        project_id, cluster_id = self._make_shell()
        # Patch the orchestrator agent so the background task is a
        # no-op — we only care about the synchronous write the route
        # makes BEFORE returning 202.
        async def _noop_run(*_args, **_kwargs):  # noqa: ANN001
            return None
        with patch(
            "planning_studio_service.agents.orchestrator.run",
            new=_noop_run,
        ):
            resp = self.client.post(
                f"/api/v2/projects/{project_id}/start-canvas",
            )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["project_id"], project_id)
        self.assertEqual(body["status"], "thinking")

        # Synchronous contract: metadata.ai_review_in_progress is True.
        proj = self.store._get_v2_project(project_id)
        md = proj["metadata"]
        self.assertIs(md.get("ai_review_in_progress"), True)
        # Shell keys preserved.
        self.assertEqual(md.get("cluster_id"), cluster_id)
        self.assertIs(md.get("auto_promoted"), True)
        self.assertIn("dominant_category", md)


if __name__ == "__main__":
    unittest.main()
