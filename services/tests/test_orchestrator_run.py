"""F7-REVISED orchestrator unit tests (W3).

Tests the orchestrator pipeline end-to-end against a real store but
with the sub-agent's LLM call stubbed (returns a canned topics +
decisions dict). Covers:

- Topic + decision + provenance persistence
- Sub-agent failure isolation (one fails, siblings complete)
- Conflict detection across sub-agents (same subject, different statements)
- ``decision_summary.ready`` precedes ``orchestrator.completed``
- Trimmed replay path for late SSE subscribers
- ``moderate_conflict`` writes ``conflict_resolutions`` rows
"""
from __future__ import annotations

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from planning_studio_service import orchestrator_store
from planning_studio_service.agents import (
    conflict_detector,
    orchestrator as orch,
    sub_agent,
)
from planning_studio_service.feedback_items import (
    cluster as fc,
    store as fi_store,
)
from planning_studio_service.feedback_items.embedding import EMBEDDING_DIMS

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


def _stub_sub_agent_output(
    *,
    topic_titles: list[str] = None,
    decisions: list[dict[str, Any]] = None,
    errors: list[str] = None,
) -> dict[str, Any]:
    """Build a canned sub-agent output dict."""
    if topic_titles is None:
        topic_titles = ["Topic A", "Topic B"]
    topics = [
        {
            "title": title,
            "icon": "lightbulb",
            "why_this_topic": f"why {title}",
        }
        for title in topic_titles
    ]
    if decisions is None:
        decisions = [
            {
                "topic_index": 0,
                "statement": "Adopt feature X",
                "rationale": "Customers asked",
                "subject": "feature_x",
                "cited_feedback_item_ids": [],
            }
        ]
    return {
        "topics": topics,
        "decisions": decisions,
        "errors": errors or [],
    }


class OrchestratorRunTests(unittest.TestCase):
    """End-to-end run() with a stubbed sub_agent."""

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        # OpenAI key off → moderation falls to deterministic placeholder.
        self._old_key = os.environ.pop("OPENAI_API_KEY", None)
        self.workspace_id = "ws-orch-test"
        self._seed_workspace_owner()
        # Seed clusters + items.
        self.cluster_a_id = self._seed_cluster(
            ["A item 1", "A item 2"], type_hint="bug", axis=0
        )
        self.cluster_b_id = self._seed_cluster(
            ["B item 1"], type_hint="feature", axis=1,
        )
        # Pre-create a completed prioritization run referencing both
        # clusters as themes.
        self.prio_run_id = orchestrator_store.create_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            triggered_by="user-test",
            input_snapshot={"cluster_ids": [
                self.cluster_a_id, self.cluster_b_id
            ]},
        )
        orchestrator_store.complete_prioritization_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=self.prio_run_id,
            output={
                "themes": [
                    {
                        "cluster_id": self.cluster_a_id,
                        "rank": 1, "score": 90.0,
                        "rationale": "lots of bugs",
                        "suggested_theme_label": "Login bugs",
                        "provenance": {},
                    },
                    {
                        "cluster_id": self.cluster_b_id,
                        "rank": 2, "score": 60.0,
                        "rationale": "feature ask",
                        "suggested_theme_label": "Bulk export",
                        "provenance": {},
                    },
                ],
                "model": "test-stub",
                "input_cluster_count": 2,
            },
        )

    def tearDown(self) -> None:
        if self._old_key is not None:
            os.environ["OPENAI_API_KEY"] = self._old_key
        del self.temp_dir

    def _seed_workspace_owner(self) -> None:
        """Insert a workspaces row so canvas creation can find the owner.

        The orchestrator looks up billing_owner_user_id; without a row
        we'd fall through to ``user-system``. For test clarity we wire
        a real owner.
        """
        with self.store._connect() as connection:
            connection.execute(
                "INSERT INTO workspaces (workspace_id, slug, name, "
                "created_at, billing_owner_user_id, "
                "plan_tier, settings_json) "
                "VALUES (?, ?, ?, ?, ?, 'free', '{}')",
                (
                    self.workspace_id, "orch-test", "Orch Test",
                    "2026-05-03T10:00:00Z", "user-orch-owner",
                ),
            )
            connection.commit()

    def _seed_cluster(
        self, item_titles: list[str], *, type_hint: str, axis: int,
    ) -> str:
        embedding = [0.0] * EMBEDDING_DIMS
        embedding[axis] = 1.0
        cluster_id = ""
        for title in item_titles:
            item_id, _ = fi_store.upsert_item(
                self.store,
                workspace_id=self.workspace_id,
                source="csv-import",
                external_id=None,
                title=title,
                body="",
                type_hint=type_hint,
            )
            cid, _ = fc.assign_or_create_cluster(
                self.store,
                workspace_id=self.workspace_id,
                item_id=item_id,
                embedding=embedding,
            )
            cluster_id = cid
        return cluster_id

    def _create_orchestrator_run(self, top_n: int = 5) -> str:
        run_id, _ = orchestrator_store.create_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            prioritization_run_id=self.prio_run_id,
            triggered_by="user-orch-owner",
            top_n=top_n,
        )
        return run_id

    def test_happy_path_creates_canvases_and_summary(self) -> None:
        run_id = self._create_orchestrator_run(top_n=2)
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=2,
                )
            )
        result = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["summary"])
        self.assertEqual(len(result["sub_agents"]), 2)
        for sa in result["sub_agents"]:
            self.assertEqual(sa["status"], "completed")
            self.assertIsNotNone(sa["project_id"])

    def test_topic_generation_persists_topics_and_decisions(self) -> None:
        run_id = self._create_orchestrator_run(top_n=1)
        canned = _stub_sub_agent_output(
            topic_titles=["Auth", "Logging"],
            decisions=[
                {
                    "topic_index": 0,
                    "statement": "Use JWT",
                    "rationale": "ok",
                    "subject": "auth",
                    "cited_feedback_item_ids": [],
                },
                {
                    "topic_index": 1,
                    "statement": "Use Sentry",
                    "rationale": "ok",
                    "subject": "logging",
                    "cited_feedback_item_ids": [],
                },
            ],
        )
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=canned,
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=1,
                )
            )
        result = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        self.assertEqual(len(result["sub_agents"]), 1)
        project_id = result["sub_agents"][0]["project_id"]
        # Verify topics + decisions exist on the canvas with correct FKs.
        with self.store._connect() as connection:
            topics = connection.execute(
                "SELECT topic_id, title FROM topics WHERE project_id = ? "
                "ORDER BY order_index",
                (project_id,),
            ).fetchall()
            decisions = connection.execute(
                "SELECT decision_id, topic_id, statement FROM decisions "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        self.assertEqual([t[1] for t in topics], ["Auth", "Logging"])
        topic_id_set = {t[0] for t in topics}
        self.assertEqual(len(decisions), 2)
        for dec in decisions:
            self.assertIn(
                dec[1], topic_id_set,
                "every decision must FK to a real topic on this canvas",
            )

    def test_sub_agent_failure_isolated(self) -> None:
        run_id = self._create_orchestrator_run(top_n=2)
        call_count = {"n": 0}

        def fake(*_args, **kwargs):
            call_count["n"] += 1
            # First sub-agent fails; second succeeds.
            if call_count["n"] == 1:
                return {
                    "topics": [], "decisions": [],
                    "errors": ["sub_agent_llm_failed: APITimeoutError"],
                }
            return _stub_sub_agent_output()

        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            side_effect=fake,
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=2,
                )
            )
        result = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        # Run completes despite one sub-agent failure.
        self.assertEqual(result["status"], "completed")
        statuses = sorted(s["status"] for s in result["sub_agents"])
        self.assertEqual(statuses, ["completed", "error"])
        # Failed sub-agent's canvas is in generation_failed state.
        failed_sa = next(
            s for s in result["sub_agents"] if s["status"] == "error"
        )
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM v2_projects WHERE project_id = ?",
                (failed_sa["project_id"],),
            ).fetchone()
        import json
        meta = json.loads(row[0])
        self.assertEqual(meta["state"], "generation_failed")
        # Decision Summary mentions the failed theme.
        self.assertEqual(len(result["summary"]["failed_themes"]), 1)

    def test_conflict_resolution_persisted(self) -> None:
        run_id = self._create_orchestrator_run(top_n=2)
        call_count = {"n": 0}

        def fake(*_args, **kwargs):
            call_count["n"] += 1
            # Two sub-agents both speak to "auth_provider" with
            # different statements → conflict.
            return _stub_sub_agent_output(
                topic_titles=[f"Auth ({call_count['n']})"],
                decisions=[
                    {
                        "topic_index": 0,
                        "statement": (
                            "Use Stripe Identity"
                            if call_count["n"] == 1
                            else "Use Lemon Squeezy"
                        ),
                        "rationale": "oh",
                        "subject": "auth_provider",
                        "cited_feedback_item_ids": [],
                    }
                ],
            )

        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            side_effect=fake,
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=2,
                )
            )
        # conflict_resolutions row should exist.
        resolutions = orchestrator_store.list_conflict_resolutions(
            self.store, orchestrator_run_id=run_id,
        )
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(resolutions[0]["subject"], "auth_provider")
        # Resolution text is non-empty (deterministic fallback when no
        # OPENAI_API_KEY is set in the test env).
        self.assertTrue(len(resolutions[0]["resolution_text"]) > 10)
        # Summary's conflicts section enumerates it.
        result = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        self.assertEqual(len(result["summary"]["conflicts"]), 1)

    def test_event_sequence_summary_before_completed(self) -> None:
        """SSE contract: decision_summary.ready ALWAYS precedes
        orchestrator.completed."""
        run_id = self._create_orchestrator_run(top_n=2)
        events: list[tuple[str, dict[str, Any]]] = []
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        async def _drain():
            while True:
                event, payload = await queue.get()
                events.append((event, payload))
                if event in {"orchestrator.completed", "error"}:
                    return

        async def _scenario():
            with patch.object(
                sub_agent, "extract_topics_and_decisions_for_theme",
                return_value=_stub_sub_agent_output(),
            ):
                run_task = asyncio.create_task(
                    orch.run(
                        self.store,
                        workspace_id=self.workspace_id,
                        orchestrator_run_id=run_id,
                        prioritization_run_id=self.prio_run_id,
                        top_n=2,
                        event_queue=queue,
                    )
                )
                drain_task = asyncio.create_task(_drain())
                await run_task
                await drain_task

        asyncio.run(_scenario())
        event_names = [e[0] for e in events]
        self.assertIn("decision_summary.ready", event_names)
        self.assertIn("orchestrator.completed", event_names)
        summary_idx = event_names.index("decision_summary.ready")
        completed_idx = event_names.index("orchestrator.completed")
        self.assertLess(
            summary_idx, completed_idx,
            "decision_summary.ready must fire BEFORE orchestrator.completed",
        )

    def test_replay_for_completed_run(self) -> None:
        run_id = self._create_orchestrator_run(top_n=2)
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=2,
                )
            )
        events = orch.build_replay_events(
            self.store,
            workspace_id=self.workspace_id,
            orchestrator_run_id=run_id,
        )
        event_names = [e[0] for e in events]
        # Trimmed replay shape: run.started, sub_agent.completed×N,
        # decision_summary.ready, orchestrator.completed. NO topic.drafted
        # / decision.drafted / conflict.detected.
        self.assertEqual(event_names[0], "run.started")
        self.assertEqual(event_names[-1], "orchestrator.completed")
        self.assertNotIn("topic.drafted", event_names)
        self.assertNotIn("decision.drafted", event_names)
        self.assertEqual(
            event_names.count("sub_agent.completed"), 2,
            "Both sub-agents should appear in the replay",
        )

    def test_idempotency_via_unique_constraint(self) -> None:
        run_id_1, is_new_1 = orchestrator_store.create_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            prioritization_run_id=self.prio_run_id,
            triggered_by="user-orch-owner",
            top_n=5,
        )
        self.assertTrue(is_new_1)
        run_id_2, is_new_2 = orchestrator_store.create_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            prioritization_run_id=self.prio_run_id,
            triggered_by="user-orch-owner",
            top_n=5,
        )
        self.assertFalse(is_new_2)
        self.assertEqual(run_id_1, run_id_2)

    # -----------------------------------------------------------------
    # Pre-warm artifact (#184)
    # -----------------------------------------------------------------

    def test_pre_warm_dispatched_on_success_outcomes(self) -> None:
        """With pre_warm_artifacts=True, the helper is invoked once per
        completed outcome and not at all for failed ones."""
        run_id = self._create_orchestrator_run(top_n=2)
        pre_warm_mock = AsyncMock()

        async def _run_and_drain() -> None:
            await orch.run(
                self.store,
                workspace_id=self.workspace_id,
                orchestrator_run_id=run_id,
                prioritization_run_id=self.prio_run_id,
                top_n=2,
                pre_warm_artifacts=True,
            )
            # Drain any pending pre-warm tasks before asyncio.run closes
            # the loop (avoids "Task was destroyed but pending" warnings).
            pending = list(orch._pre_warm_tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ), patch.object(
            orch, "_pre_warm_artifact_async", new=pre_warm_mock,
        ):
            asyncio.run(_run_and_drain())

        # Both themes seeded → both should have succeeded → 2 pre-warm
        # dispatches.
        self.assertEqual(pre_warm_mock.call_count, 2)
        for call in pre_warm_mock.call_args_list:
            args, _ = call
            self.assertIs(args[0], self.store)
            self.assertIsInstance(args[1], str)
            self.assertTrue(args[1])  # non-empty project_id
            self.assertEqual(args[2], self.workspace_id)

    def test_pre_warm_not_dispatched_when_disabled(self) -> None:
        """Default pre_warm_artifacts=False keeps existing call sites
        (and existing tests) unaffected."""
        run_id = self._create_orchestrator_run(top_n=2)
        pre_warm_mock = AsyncMock()

        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ), patch.object(
            orch, "_pre_warm_artifact_async", new=pre_warm_mock,
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=2,
                    # pre_warm_artifacts omitted → False
                )
            )

        self.assertEqual(pre_warm_mock.call_count, 0)

    def test_pre_warm_artifact_skips_if_artifact_exists(self) -> None:
        """The sync helper short-circuits if an artifact is already
        persisted (idempotency). Adapter must not be instantiated."""
        run_id = self._create_orchestrator_run(top_n=1)
        with patch.object(
            sub_agent, "extract_topics_and_decisions_for_theme",
            return_value=_stub_sub_agent_output(),
        ):
            asyncio.run(
                orch.run(
                    self.store,
                    workspace_id=self.workspace_id,
                    orchestrator_run_id=run_id,
                    prioritization_run_id=self.prio_run_id,
                    top_n=1,
                )
            )
        result = orchestrator_store.get_orchestrator_run(
            self.store,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        project_id = result["sub_agents"][0]["project_id"]
        self.assertIsNotNone(project_id)

        # Pre-seed an existing artifact for this project.
        self.store.set_v2_project_artifact(
            project_id=project_id,
            artifact={
                "version": 1,
                "latest_scaffold_id": "fake-scaffold-existing",
                "model_used": "test-stub",
                "messages": [],
            },
        )

        # Mock the adapter class so any instantiation/call would fail
        # the test loudly.
        mock_adapter_cls = MagicMock()
        mock_adapter_cls.return_value.generate.side_effect = AssertionError(
            "pre-warm should short-circuit when artifact already exists",
        )

        with patch.object(orch, "CodeScaffoldAdapter", mock_adapter_cls):
            # Sync call — no asyncio. Must not raise.
            orch._pre_warm_artifact(
                self.store, project_id, self.workspace_id,
            )

        # Adapter class never instantiated.
        mock_adapter_cls.assert_not_called()
        # Existing artifact unchanged.
        artifact = self.store.get_v2_project_artifact(project_id=project_id)
        self.assertEqual(
            artifact["latest_scaffold_id"], "fake-scaffold-existing",
        )


class ConflictDetectorTests(unittest.TestCase):
    """Pure-function conflict_detector tests."""

    def test_subject_match_different_statements_is_conflict(self) -> None:
        decisions = [
            {
                "decision_id": "d1", "subject": "auth_provider",
                "statement": "Use Stripe", "sub_agent_run_id": "s1",
            },
            {
                "decision_id": "d2", "subject": "auth_provider",
                "statement": "Use Lemon", "sub_agent_run_id": "s2",
            },
        ]
        candidates = conflict_detector.find_conflict_candidates(decisions)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["subject"], "auth_provider")
        # Stable order: d1 < d2 lexicographically.
        self.assertEqual(candidates[0]["decision_a_id"], "d1")
        self.assertEqual(candidates[0]["decision_b_id"], "d2")

    def test_identical_statements_not_conflict(self) -> None:
        decisions = [
            {
                "decision_id": "d1", "subject": "auth",
                "statement": "Use SSO", "sub_agent_run_id": "s1",
            },
            {
                "decision_id": "d2", "subject": "auth",
                "statement": "Use SSO", "sub_agent_run_id": "s2",
            },
        ]
        candidates = conflict_detector.find_conflict_candidates(decisions)
        self.assertEqual(candidates, [])

    def test_different_subjects_no_conflict(self) -> None:
        decisions = [
            {
                "decision_id": "d1", "subject": "auth",
                "statement": "Use SSO", "sub_agent_run_id": "s1",
            },
            {
                "decision_id": "d2", "subject": "logging",
                "statement": "Use Sentry", "sub_agent_run_id": "s2",
            },
        ]
        self.assertEqual(
            conflict_detector.find_conflict_candidates(decisions), [],
        )

    def test_subject_normalization(self) -> None:
        # Auth provider / auth_provider / AUTH-provider all match.
        decisions = [
            {
                "decision_id": "d1", "subject": "Auth provider",
                "statement": "X", "sub_agent_run_id": "s1",
            },
            {
                "decision_id": "d2", "subject": "auth_provider",
                "statement": "Y", "sub_agent_run_id": "s2",
            },
            {
                "decision_id": "d3", "subject": "AUTH-provider",
                "statement": "Z", "sub_agent_run_id": "s3",
            },
        ]
        candidates = conflict_detector.find_conflict_candidates(decisions)
        # 3 distinct decisions, 3 pairs.
        self.assertEqual(len(candidates), 3)
        for c in candidates:
            self.assertEqual(c["subject"], "auth_provider")

    def test_unspecified_subject_skipped(self) -> None:
        decisions = [
            {
                "decision_id": "d1", "subject": "_unspecified",
                "statement": "X", "sub_agent_run_id": "s1",
            },
            {
                "decision_id": "d2", "subject": "_unspecified",
                "statement": "Y", "sub_agent_run_id": "s2",
            },
        ]
        self.assertEqual(
            conflict_detector.find_conflict_candidates(decisions), [],
        )


class SubAgentRetryTests(unittest.TestCase):
    """sub_agent.py retry policy."""

    def test_classify_exception_transient_for_5xx_status(self) -> None:
        err = type(
            "FakeAPIStatusError", (Exception,),
            {"status_code": 503},
        )("Service Unavailable")
        err.__class__.__name__ = "APIStatusError"
        # Force the class name to match what the SDK uses.
        err.__class__ = type(
            "APIStatusError", (Exception,),
            {"status_code": 503},
        )
        raised = err.__class__("Service Unavailable")
        raised.status_code = 503
        self.assertEqual(sub_agent._classify_exception(raised), "transient")

    def test_classify_exception_permanent_for_4xx(self) -> None:
        err_class = type("APIStatusError", (Exception,), {})
        err = err_class("400")
        err.status_code = 400
        self.assertEqual(sub_agent._classify_exception(err), "permanent")

    def test_classify_exception_transient_for_timeout(self) -> None:
        err_class = type("APITimeoutError", (Exception,), {})
        self.assertEqual(
            sub_agent._classify_exception(err_class("timed out")),
            "transient",
        )

    def test_classify_exception_unknown_is_permanent(self) -> None:
        self.assertEqual(
            sub_agent._classify_exception(ValueError("oops")), "permanent",
        )

    def test_no_api_key_returns_disabled_error(self) -> None:
        old = os.environ.pop("OPENAI_API_KEY", None)
        try:
            out = sub_agent.extract_topics_and_decisions_for_theme(
                cluster_id="cl-x",
                theme_label="test",
                rationale="test",
                items=[],
            )
            self.assertEqual(out["topics"], [])
            self.assertIn(
                "sub_agent_disabled_no_api_key", out["errors"],
            )
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
