"""Tests for planning_studio_service.json_import + /api/v2/projects/from-json.

The scenarios:
  1. Happy path round-trip — ``exportToJson``-shaped blob → import →
     new project with matching topic titles, relationships rewired, and
     decisions preserved.
  2. Malformed blob (non-dict / missing keys / bad types) → parser
     ValueError and route 400.
  3. Schema version mismatch (``inspira.canvas.v99``) → ValueError and
     route 400.
  4. Anonymous user can import (no sign-in required).
  5. JsonImportBody Pydantic model sanity.

The backend tests use the real PlanningStudioStore + FastAPI TestClient
so they exercise the actual SQL paths and model_rebuild() logic, matching
the style of test_markdown_import + test_anonymous_to_account.
"""
from __future__ import annotations

import unittest
from typing import Any

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.json_import import (
    JsonImportBody,
    ParsedCanvas,
    instantiate_from_json,
    parse_inspira_canvas_v1,
)

ensure_loaded()


try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _canonical_blob() -> dict[str, Any]:
    """A v1 blob with two topics, one relationship, and two decisions.

    Shape matches ``exportToJson`` in app/src/features/inspira/export.ts.
    IDs look like real persisted ones; the importer should rewrite them.
    """
    return {
        "schema": "inspira.canvas.v1",
        "exported_at": "2026-04-21T12:00:00.000Z",
        "project": {
            "project_id": "project-source-00000001",
            "user_id": "user-abc",
            "title": "Outdoor wine festival",
            "metadata": {"opening_note": "Long weekend in June."},
            "created_at": "2026-04-10T00:00:00+00:00",
            "updated_at": "2026-04-20T00:00:00+00:00",
        },
        "topics": [
            {
                "topic_id": "topic-aaaaaaaaaa",
                "project_id": "project-source-00000001",
                "title": "Venue",
                "icon": "map-pin",
                "position_x": 120.0,
                "position_y": 80.0,
                "status": "in_progress",
                "order_index": 0,
                "origin": "planner_initial",
                "metadata": {"why_this_topic": "The space anchors everything."},
                "created_at": "2026-04-10T00:00:00+00:00",
                "updated_at": "2026-04-10T00:00:00+00:00",
            },
            {
                "topic_id": "topic-bbbbbbbbbb",
                "project_id": "project-source-00000001",
                "title": "Budget",
                "icon": "chart",
                "position_x": 560.0,
                "position_y": 80.0,
                "status": "empty",
                "order_index": 1,
                "origin": "planner_initial",
                "metadata": {},
                "created_at": "2026-04-10T00:00:00+00:00",
                "updated_at": "2026-04-10T00:00:00+00:00",
            },
        ],
        "relationships": [
            {
                "relationship_id": "rel-src0000000",
                "project_id": "project-source-00000001",
                "source_topic_id": "topic-bbbbbbbbbb",
                "target_topic_id": "topic-aaaaaaaaaa",
                "label": "bounds",
                "origin": "planner_inferred",
                "strength": "confirmed",
                "created_at": "2026-04-10T00:00:00+00:00",
            },
        ],
        "decisions": [
            {
                "decision_id": "dec-src0000001",
                "topic_id": "topic-aaaaaaaaaa",
                "project_id": "project-source-00000001",
                "statement": "Park with covered pavilion.",
                "rationale": "Rain contingency without losing the outdoor feel.",
                "status": "confirmed",
                "proposed_by": "user",
                "source_turn_id": "turn-old-id",
                "confirmed_by_user_id": "user-abc",
                "created_at": "2026-04-10T00:00:00+00:00",
                "updated_at": "2026-04-10T00:00:00+00:00",
                "retracted_at": None,
            },
            {
                "decision_id": "dec-src0000002",
                "topic_id": "topic-bbbbbbbbbb",
                "project_id": "project-source-00000001",
                "statement": "Cap spend at twelve thousand.",
                "rationale": None,
                "status": "proposed",
                "proposed_by": "planner",
                "source_turn_id": None,
                "confirmed_by_user_id": None,
                "created_at": "2026-04-12T00:00:00+00:00",
                "updated_at": "2026-04-12T00:00:00+00:00",
                "retracted_at": None,
            },
        ],
        "turns": [],
    }


# ---------------------------------------------------------------------------
# 1. parse_inspira_canvas_v1 — happy path structure
# ---------------------------------------------------------------------------


class ParseHappyPath(unittest.TestCase):
    def test_returns_parsed_canvas(self) -> None:
        parsed = parse_inspira_canvas_v1(_canonical_blob())
        self.assertIsInstance(parsed, ParsedCanvas)

    def test_title_preserved(self) -> None:
        parsed = parse_inspira_canvas_v1(_canonical_blob())
        self.assertEqual(parsed.title, "Outdoor wine festival")

    def test_topics_parsed(self) -> None:
        parsed = parse_inspira_canvas_v1(_canonical_blob())
        self.assertEqual(len(parsed.topics), 2)
        self.assertEqual(parsed.topics[0].title, "Venue")
        self.assertEqual(parsed.topics[0].icon, "map-pin")
        self.assertEqual(parsed.topics[0].status, "in_progress")
        self.assertEqual(parsed.topics[1].title, "Budget")

    def test_relationships_parsed(self) -> None:
        parsed = parse_inspira_canvas_v1(_canonical_blob())
        self.assertEqual(len(parsed.relationships), 1)
        self.assertEqual(parsed.relationships[0].label, "bounds")

    def test_decisions_parsed(self) -> None:
        parsed = parse_inspira_canvas_v1(_canonical_blob())
        self.assertEqual(len(parsed.decisions), 2)
        statements = {d.statement for d in parsed.decisions}
        self.assertIn("Park with covered pavilion.", statements)
        self.assertIn("Cap spend at twelve thousand.", statements)


# ---------------------------------------------------------------------------
# 2. parse_inspira_canvas_v1 — malformed + schema-mismatch rejection
# ---------------------------------------------------------------------------


class ParseRejectsBadInput(unittest.TestCase):
    def test_rejects_non_dict(self) -> None:
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1("not a dict")  # type: ignore[arg-type]

    def test_rejects_missing_schema(self) -> None:
        blob = _canonical_blob()
        del blob["schema"]
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1(blob)

    def test_rejects_wrong_schema(self) -> None:
        blob = _canonical_blob()
        blob["schema"] = "inspira.canvas.v99"
        with self.assertRaises(ValueError) as cm:
            parse_inspira_canvas_v1(blob)
        # The message should identify the mismatch so the UI can surface it.
        self.assertIn("schema", str(cm.exception).lower())

    def test_rejects_missing_project(self) -> None:
        blob = _canonical_blob()
        del blob["project"]
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1(blob)

    def test_rejects_missing_topics_list(self) -> None:
        blob = _canonical_blob()
        del blob["topics"]
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1(blob)

    def test_rejects_non_list_topics(self) -> None:
        blob = _canonical_blob()
        blob["topics"] = "not a list"
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1(blob)

    def test_rejects_topic_without_title(self) -> None:
        blob = _canonical_blob()
        blob["topics"][0]["title"] = ""
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1(blob)

    def test_rejects_decision_without_statement(self) -> None:
        blob = _canonical_blob()
        blob["decisions"][0]["statement"] = ""
        with self.assertRaises(ValueError):
            parse_inspira_canvas_v1(blob)


# ---------------------------------------------------------------------------
# 3. instantiate_from_json — round-trip via the real store
# ---------------------------------------------------------------------------


class InstantiateRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_round_trip_creates_project_with_expected_pieces(self) -> None:
        # Boot an anon user so the store has a valid user_id.
        me = self.client.get("/api/auth/me").json()
        user_id = me["user_id"]

        parsed = parse_inspira_canvas_v1(_canonical_blob())
        project = instantiate_from_json(
            self.store, user_id=user_id, parsed=parsed, title_override=None,
        )
        self.assertEqual(project["user_id"], user_id)
        self.assertEqual(project["title"], "Outdoor wine festival")
        # New project_id, NOT the exported one.
        self.assertNotEqual(project["project_id"], "project-source-00000001")
        self.assertTrue(project["project_id"].startswith("project-"))

        topics = self.store.list_topics(project_id=project["project_id"])
        titles = sorted(t["title"] for t in topics)
        self.assertEqual(titles, ["Budget", "Venue"])

        # Topic ids must be fresh (none of the exported ones).
        for t in topics:
            self.assertNotEqual(t["topic_id"], "topic-aaaaaaaaaa")
            self.assertNotEqual(t["topic_id"], "topic-bbbbbbbbbb")

        # Metadata round-tripped on the Venue topic.
        venue = next(t for t in topics if t["title"] == "Venue")
        self.assertEqual(
            venue["metadata"].get("why_this_topic"),
            "The space anchors everything.",
        )
        # Status was lifted from "empty" default to "in_progress".
        self.assertEqual(venue["status"], "in_progress")

        rels = self.store.list_relationships(project_id=project["project_id"])
        self.assertEqual(len(rels), 1)
        # Endpoints were remapped onto the new topic ids.
        topic_ids = {t["topic_id"] for t in topics}
        self.assertIn(rels[0]["source_topic_id"], topic_ids)
        self.assertIn(rels[0]["target_topic_id"], topic_ids)
        self.assertEqual(rels[0]["label"], "bounds")

        decisions = self.store.list_decisions(project_id=project["project_id"])
        self.assertEqual(len(decisions), 2)
        statements = {d["statement"] for d in decisions}
        self.assertIn("Park with covered pavilion.", statements)
        self.assertIn("Cap spend at twelve thousand.", statements)
        # Decisions landed on the right topics (resolved via new ids).
        venue_decisions = [
            d for d in decisions if d["topic_id"] == venue["topic_id"]
        ]
        self.assertEqual(len(venue_decisions), 1)
        self.assertEqual(
            venue_decisions[0]["statement"], "Park with covered pavilion.",
        )

    def test_title_override_wins(self) -> None:
        me = self.client.get("/api/auth/me").json()
        user_id = me["user_id"]
        parsed = parse_inspira_canvas_v1(_canonical_blob())
        project = instantiate_from_json(
            self.store,
            user_id=user_id,
            parsed=parsed,
            title_override="A different title",
        )
        self.assertEqual(project["title"], "A different title")

    def test_orphan_relationship_dropped(self) -> None:
        """A relationship whose endpoints aren't in the topics list is skipped."""
        me = self.client.get("/api/auth/me").json()
        user_id = me["user_id"]
        blob = _canonical_blob()
        blob["relationships"].append(
            {
                "relationship_id": "rel-orphan",
                "project_id": "project-source-00000001",
                "source_topic_id": "topic-does-not-exist",
                "target_topic_id": "topic-aaaaaaaaaa",
                "label": "orphan",
                "origin": "user_drawn",
                "strength": "confirmed",
                "created_at": "2026-04-10T00:00:00+00:00",
            },
        )
        parsed = parse_inspira_canvas_v1(blob)
        project = instantiate_from_json(
            self.store, user_id=user_id, parsed=parsed,
        )
        rels = self.store.list_relationships(project_id=project["project_id"])
        # The orphan was dropped — only the original valid relationship survives.
        self.assertEqual(len(rels), 1)


# ---------------------------------------------------------------------------
# 4. HTTP route — /api/v2/projects/from-json
# ---------------------------------------------------------------------------


class FromJsonHttpRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_happy_path_returns_201_with_envelope(self) -> None:
        response = self.client.post(
            "/api/v2/projects/from-json",
            json={"json_blob": _canonical_blob()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertIn("project", payload)
        self.assertIn("topics", payload)
        self.assertIn("relationships", payload)
        self.assertIn("decisions", payload)
        self.assertEqual(payload["project"]["title"], "Outdoor wine festival")
        self.assertEqual(len(payload["topics"]), 2)
        self.assertEqual(len(payload["relationships"]), 1)
        self.assertEqual(len(payload["decisions"]), 2)

    def test_title_override_on_route(self) -> None:
        response = self.client.post(
            "/api/v2/projects/from-json",
            json={
                "json_blob": _canonical_blob(),
                "title": "Renamed on import",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["project"]["title"], "Renamed on import")

    def test_anon_user_can_import(self) -> None:
        """No sign-in required — same contract as from-markdown."""
        me = self.client.get("/api/auth/me").json()
        self.assertTrue(me["user_id"].startswith("user-anon-"))

        response = self.client.post(
            "/api/v2/projects/from-json",
            json={"json_blob": _canonical_blob()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        # The new project is owned by the anon user, not the system user.
        self.assertTrue(
            response.json()["project"]["user_id"].startswith("user-anon-"),
        )

    def test_wrong_schema_returns_400(self) -> None:
        blob = _canonical_blob()
        blob["schema"] = "inspira.canvas.v99"
        response = self.client.post(
            "/api/v2/projects/from-json",
            json={"json_blob": blob},
        )
        self.assertEqual(response.status_code, 400, response.text)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "invalid_json_blob")
        self.assertIn("schema", body["detail"]["message"].lower())

    def test_malformed_blob_returns_400(self) -> None:
        response = self.client.post(
            "/api/v2/projects/from-json",
            json={"json_blob": {"schema": "inspira.canvas.v1"}},
        )
        # Missing project / topics — parser raises ValueError → 400.
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"]["error"], "invalid_json_blob",
        )

    def test_missing_body_returns_422(self) -> None:
        """FastAPI's standard validation handles missing body fields."""
        response = self.client.post(
            "/api/v2/projects/from-json", json={},
        )
        # json_blob is required → Pydantic 422.
        self.assertEqual(response.status_code, 422, response.text)


# ---------------------------------------------------------------------------
# 5. JsonImportBody Pydantic sanity
# ---------------------------------------------------------------------------


class JsonImportBodyModel(unittest.TestCase):
    def test_json_blob_required(self) -> None:
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            JsonImportBody()  # type: ignore[call-arg]

    def test_title_optional(self) -> None:
        body = JsonImportBody(json_blob={"schema": "inspira.canvas.v1"})
        self.assertIsNone(body.title)

    def test_title_accepted(self) -> None:
        body = JsonImportBody(
            json_blob={"schema": "inspira.canvas.v1"},
            title="Custom",
        )
        self.assertEqual(body.title, "Custom")


if __name__ == "__main__":
    unittest.main()
