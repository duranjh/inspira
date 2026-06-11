"""Tests for GET /api/v2/projects/{id}/topics/{topic_id}/provenance.

Backs the Cited-feedback-items section of the Topic Detail reasoning
expander on cold-opens of completed canvases. Live runs populate
provenance from `decision.drafted` SSE events; this endpoint is the
fallback when the SSE stream is no longer flowing.
"""
from __future__ import annotations

import unittest
from typing import Any

from planning_studio_service.feedback_items import store as fi_store
from planning_studio_service.orchestrator_store import (
    record_decision_provenance,
)

try:
    from ._helpers import (
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )


PROJECT_ID = "proj-prov"


def _provision_project_with_topic(client: Any, adapter: Any) -> tuple[str, str]:
    """Kickoff so a project + topics exist. Return (project_id, topic_id)."""
    adapter.kickoff.return_value = fake_kickoff_response()
    response = client.post(
        f"/api/v2/projects/{PROJECT_ID}/kickoff",
        json={"user_idea": "Provenance test."},
    )
    response.raise_for_status()
    payload = response.json()
    return PROJECT_ID, payload["topics"][0]["topic_id"]


def _create_decision(client: Any, topic_id: str, statement: str) -> str:
    response = client.post(
        f"/api/v2/topics/{topic_id}/decisions",
        json={"statement": statement, "proposed_by": "user"},
    )
    response.raise_for_status()
    return response.json()["decision"]["decision_id"]


class ProvenanceEndpointTests(unittest.TestCase):
    """GET /api/v2/projects/{id}/topics/{topic_id}/provenance."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="owner@example.com", password="password123")
        self.project_id, self.topic_id = _provision_project_with_topic(
            self.client, self.adapter,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_provenance(
        self, *, decision_id: str, items: list[tuple[str, str]],
    ) -> list[str]:
        """Insert feedback_items + a decision_provenance row per item.

        The endpoint joins decision_provenance ↔ feedback_items by
        feedback_item_id, not workspace — so the workspace label here
        is incidental. Project-level ownership is enforced by
        _require_owned_project upstream.
        """
        item_ids: list[str] = []
        for idx, (title, body) in enumerate(items):
            item_id, _ = fi_store.upsert_item(
                self.store,
                workspace_id="ws-prov-test",
                source="linear",
                external_id=f"ext-{idx}",
                title=title,
                body=body,
                received_at="2026-04-27T12:00:00+00:00",
            )
            item_ids.append(item_id)
        record_decision_provenance(
            self.store,
            decision_id=decision_id,
            cited_feedback_item_ids=item_ids,
        )
        return item_ids

    def test_happy_path_returns_provenance_grouped_by_decision(self) -> None:
        decision_id = _create_decision(
            self.client, self.topic_id, "Use venue X.",
        )
        self._seed_provenance(
            decision_id=decision_id,
            items=[
                ("Customers want venue X", "Body A"),
                ("Venue X near transit", "Body B"),
            ],
        )
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}"
            f"/topics/{self.topic_id}/provenance",
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("provenance", body)
        rows = body["provenance"]
        self.assertEqual(len(rows), 2)
        # Each row carries the decision_id, weight, and inlined feedback.
        for row in rows:
            self.assertEqual(row["decision_id"], decision_id)
            self.assertAlmostEqual(row["weight"], 0.5)
            self.assertIn("feedback_item", row)
            self.assertIn("title", row["feedback_item"])
            self.assertEqual(row["feedback_item"]["source"], "linear")

    def test_empty_when_no_decisions(self) -> None:
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}"
            f"/topics/{self.topic_id}/provenance",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"provenance": []})

    def test_empty_when_decision_has_no_provenance(self) -> None:
        # Decision exists but no decision_provenance rows recorded.
        _create_decision(self.client, self.topic_id, "No citations here.")
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}"
            f"/topics/{self.topic_id}/provenance",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"provenance": []})

    def test_topic_not_in_project_returns_404(self) -> None:
        # Topic exists and is owned, but the path's project_id is wrong.
        wrong_project = "proj-someone-elses"
        response = self.client.get(
            f"/api/v2/projects/{wrong_project}"
            f"/topics/{self.topic_id}/provenance",
        )
        # The project itself doesn't exist for this user → 404 at the
        # project ownership gate (mirrors the rest of the v2 API).
        self.assertEqual(response.status_code, 404)

    def test_idor_other_user_cannot_read(self) -> None:
        decision_id = _create_decision(
            self.client, self.topic_id, "Use venue X.",
        )
        self._seed_provenance(
            decision_id=decision_id,
            items=[("Title", "Body")],
        )
        # Switch identity by clearing cookies and signing up a different
        # user (mirrors the test_topic_color IDOR case).
        self.client.cookies.clear()
        signup_and_login(
            self.client, email="other@example.com", password="password123",
        )
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}"
            f"/topics/{self.topic_id}/provenance",
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_returns_404(self) -> None:
        # Mirrors the rest of the v2 API: an anonymous caller is a
        # different user identity and "doesn't own" the project, so the
        # ownership gate returns 404 rather than 401. Conflating the two
        # would leak project existence across the auth boundary.
        self.client.cookies.clear()
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}"
            f"/topics/{self.topic_id}/provenance",
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
