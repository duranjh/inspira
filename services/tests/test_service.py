from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import BytesIO

from planning_studio_service.app import create_app
from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore


class PlanningStudioServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        # ignore_cleanup_errors: on Windows, SQLite connections may still be
        # open when tearDown tries to remove the temp dir. The underlying
        # sqlite3.Connection context manager commits but does not close the
        # handle; the OS holds the file open until GC runs. Rather than
        # refactor the store's connection lifecycle just to make tests green
        # on Windows, tolerate the cleanup error here.
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="planning-studio-test-",
            ignore_cleanup_errors=True,
        )
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        self.config = load_config()
        self.store = PlanningStudioStore(self.config)
        self.app = create_app()

    def tearDown(self) -> None:
        os.environ.pop("PLANNING_STUDIO_STORAGE_ROOT", None)
        self.temp_dir.cleanup()

    def test_storage_bootstrap_creates_private_roots(self) -> None:
        self.assertTrue(self.config.storage_root.exists())
        self.assertTrue(self.config.sessions_root.exists())
        self.assertTrue(self.config.artifacts_root.exists())
        self.assertTrue(self.config.db_path.exists())

    def test_health_reports_paths(self) -> None:
        status, payload = self.app.handle_health(None, {})  # type: ignore[arg-type]
        self.assertEqual(status, 200)
        self.assertEqual(payload["service"], "planning-studio")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("planning-studio.sqlite", payload["db_path"])

    def test_seed_data_exists(self) -> None:
        status, payload = self.app.handle_projects(None, {})  # type: ignore[arg-type]
        self.assertEqual(status, 200)
        self.assertEqual(payload["projects"][0]["project_id"], "project-second-brain-commercialization")

        status, sessions = self.app.handle_sessions(None, {"project_id": ["project-second-brain-commercialization"]})  # type: ignore[arg-type]
        self.assertEqual(status, 200)
        self.assertEqual(sessions["sessions"][0]["session_id"], "session-bootstrap")

    def test_create_session_supports_save_and_resume(self) -> None:
        request_body = json.dumps(
            {
                "project_id": "project-second-brain-commercialization",
                "title": "Founder interview",
                "objective": "Capture scope, constraints, and release target.",
                "mode": "interview",
            }
        ).encode("utf-8")
        request = type("Req", (), {"headers": {"Content-Length": str(len(request_body))}, "rfile": BytesIO(request_body)})()
        status, payload = self.app.handle_create_session(request, {})
        self.assertEqual(status, 201)
        self.assertEqual(payload["session"]["status"], "active")
        self.assertTrue(os.path.exists(payload["session"]["transcript_path"]))

        status, sessions = self.app.handle_sessions(None, {"project_id": ["project-second-brain-commercialization"]})  # type: ignore[arg-type]
        self.assertEqual(status, 200)
        created = [item for item in sessions["sessions"] if item["title"] == "Founder interview"]
        self.assertEqual(len(created), 1)


class PlanningStudioV2SchemaTests(unittest.TestCase):
    """Proof-of-life coverage for the Inspira v2 canvas-first schema.

    Not a full domain test — just exercises the CRUD stubs in store.py to
    catch schema drift, column-name typos, and JSON serialization bugs.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="inspira-v2-test-",
            ignore_cleanup_errors=True,
        )
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        self.store = PlanningStudioStore(load_config())
        self.project_id = "project-second-brain-commercialization"

    def tearDown(self) -> None:
        os.environ.pop("PLANNING_STUDIO_STORAGE_ROOT", None)
        self.temp_dir.cleanup()

    def test_topic_crud_roundtrip(self) -> None:
        topic = self.store.create_topic(
            project_id=self.project_id,
            title="Audience",
            icon="heart",
            position_x=120.0,
            position_y=80.0,
            origin="planner_initial",
            order_index=0,
        )
        self.assertEqual(topic["status"], "empty")
        self.assertTrue(topic["topic_id"].startswith("topic-"))

        updated = self.store.update_topic(topic["topic_id"], status="in_progress")
        self.assertEqual(updated["status"], "in_progress")

        topics = self.store.list_topics(project_id=self.project_id)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["title"], "Audience")

    def test_qna_turn_orders_monotonically_per_topic(self) -> None:
        topic = self.store.create_topic(
            project_id=self.project_id, title="Budget", icon="chart",
        )
        first = self.store.append_qna_turn(
            topic_id=topic["topic_id"], project_id=self.project_id,
            role="planner", body="What's the hard cap?",
            why_this_matters="Everything else depends on it.",
            action="ask", status="open",
            suggested_responses=[{"label": "$48k", "intent": "concrete"}],
        )
        second = self.store.append_qna_turn(
            topic_id=topic["topic_id"], project_id=self.project_id,
            role="user", body="Hard cap is $48k including contingency.",
            status="answered",
        )
        self.assertEqual(first["order_index"], 0)
        self.assertEqual(second["order_index"], 1)

        turns = self.store.list_qna_turns(topic_id=topic["topic_id"])
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["suggested_responses"][0]["label"], "$48k")

    def test_decisions_attach_to_topic_and_confirm_lifecycle(self) -> None:
        # PR 1: confirm_decision now enforces project ownership. Seed a
        # v2_project owned by user-founder so the lifecycle exercises
        # the happy path (an owner confirming their own decision).
        founder = self.store.create_user(email="founder@example.com")
        founder_id = founder["user_id"]
        proj = self.store.create_v2_project(
            user_id=founder_id, title="Sponsorship plan",
        )
        topic = self.store.create_topic(
            project_id=proj["project_id"], title="Messaging", icon="megaphone",
        )
        decision = self.store.create_decision(
            topic_id=topic["topic_id"], project_id=proj["project_id"],
            statement="Headline sponsor gets a named stage.",
            rationale="Board requested.",
            proposed_by="user",
        )
        self.assertEqual(decision["status"], "proposed")

        confirmed = self.store.confirm_decision(
            decision["decision_id"], user_id=founder_id,
        )
        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["confirmed_by_user_id"], founder_id)

        decisions = self.store.list_decisions(project_id=proj["project_id"])
        self.assertEqual(len(decisions), 1)

    def test_consistency_flag_roundtrip(self) -> None:
        budget = self.store.create_topic(
            project_id=self.project_id, title="Budget", icon="chart",
        )
        messaging = self.store.create_topic(
            project_id=self.project_id, title="Messaging", icon="megaphone",
        )
        flag = self.store.create_consistency_flag(
            project_id=self.project_id,
            topic_a_id=budget["topic_id"],
            topic_b_id=messaging["topic_id"],
            description="No activations here contradicts named-stage in Messaging.",
        )
        self.assertEqual(flag["status"], "open")
        self.assertEqual(flag["scope"], "within_project")

        resolved = self.store.resolve_consistency_flag(
            flag["flag_id"], resolution="intentional",
        )
        self.assertEqual(resolved["status"], "intentional")

        remaining = self.store.list_consistency_flags(project_id=self.project_id)
        self.assertEqual(remaining, [])

    def test_relationship_unique_constraint(self) -> None:
        a = self.store.create_topic(project_id=self.project_id, title="A", icon="flag")
        b = self.store.create_topic(project_id=self.project_id, title="B", icon="flag")
        first = self.store.create_relationship(
            project_id=self.project_id,
            source_topic_id=a["topic_id"], target_topic_id=b["topic_id"],
        )
        # Duplicate should be ignored (INSERT OR IGNORE on the UNIQUE triple).
        self.store.create_relationship(
            project_id=self.project_id,
            source_topic_id=a["topic_id"], target_topic_id=b["topic_id"],
        )
        self.assertEqual(len(self.store.list_relationships(project_id=self.project_id)), 1)
        self.assertIsNotNone(first["relationship_id"])

    def test_summary_version_append_and_retrieve_latest(self) -> None:
        v1 = self.store.append_summary_version(
            project_id=self.project_id,
            version_hash="hash-v1",
            content_markdown="# Plan\n\nFirst draft.",
            sections=[{"header": "The idea", "body_markdown": "First draft."}],
            generated_by="planner_auto",
        )
        v2 = self.store.append_summary_version(
            project_id=self.project_id,
            version_hash="hash-v2",
            content_markdown="# Plan\n\nSecond draft.",
            sections=[{"header": "The idea", "body_markdown": "Second draft."}],
            generated_by="user_edit",
            generated_by_user_id="user-founder",
        )
        latest = self.store.latest_summary_version(project_id=self.project_id)
        self.assertEqual(latest["version_hash"], "hash-v2")
        self.assertEqual(latest["sections"][0]["body_markdown"], "Second draft.")
        self.assertNotEqual(v1["version_id"], v2["version_id"])

    def test_audit_event_persists(self) -> None:
        event = self.store.append_audit_event(
            workspace_id="ws-1", actor_user_id="user-founder",
            category="topic", action="create",
            project_id=self.project_id, subject_id="topic-abc",
            before=None, after={"title": "Audience"},
        )
        self.assertTrue(event["event_id"].startswith("evt-"))
        self.assertEqual(event["category"], "topic")


if __name__ == "__main__":
    unittest.main()
