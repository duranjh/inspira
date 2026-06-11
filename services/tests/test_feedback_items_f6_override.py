"""F6 manual category override tests.

Covers:
- PATCH /api/v2/connectors/feedback/items/{id} happy path
- Workspace-scoping: an item from workspace B can't be edited
  via a session active on workspace A
- Invalid category → 422
- Non-existent item → 404
- Override survives re-import (content_hash idempotency)
"""
from __future__ import annotations

import unittest

from planning_studio_service.feedback_items import store as fi_store

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class FeedbackOverrideEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="member@acme.com", password="password123",
            display_name="Member",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme-corp", "name": "Acme Corp"},
        )
        self.workspace_id = ws.json()["workspace"]["workspace_id"]
        # Seed one item via the CSV import path.
        self.client.post(
            "/api/v2/connectors/csv/import",
            json={"rows": [{
                "title": "Login fails on Safari",
                "body": "Cleared cache, no fix.",
                "type_hint": "",
            }]},
        )
        items = fi_store.list_items(
            self.store, workspace_id=self.workspace_id
        )
        assert len(items) == 1
        self.item_id = items[0].item_id

    def tearDown(self) -> None:
        del self.temp_dir

    def test_override_happy_path(self) -> None:
        resp = self.client.patch(
            f"/api/v2/connectors/feedback/items/{self.item_id}",
            json={"type_hint": "bug"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["item"]["type_hint"], "bug")
        # Persisted in store.
        item = fi_store.get_item(
            self.store,
            item_id=self.item_id,
            workspace_id=self.workspace_id,
        )
        assert item is not None
        self.assertEqual(item.type_hint, "bug")

    def test_override_normalizes_case(self) -> None:
        resp = self.client.patch(
            f"/api/v2/connectors/feedback/items/{self.item_id}",
            json={"type_hint": "FEATURE"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["item"]["type_hint"], "feature")

    def test_invalid_category_rejected(self) -> None:
        resp = self.client.patch(
            f"/api/v2/connectors/feedback/items/{self.item_id}",
            json={"type_hint": "critical"},
        )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["detail"]["error"], "invalid_category")
        self.assertIn("bug", body["detail"]["allowed"])

    def test_unknown_item_404(self) -> None:
        resp = self.client.patch(
            "/api/v2/connectors/feedback/items/fi-000000000000",
            json={"type_hint": "bug"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_workspace_scoped_404(self) -> None:
        # Create a second workspace; an item from the first should
        # 404 when active workspace is the second.
        ws2 = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "beta-corp", "name": "Beta Corp"},
        ).json()["workspace"]
        self.client.headers["X-Workspace-Id"] = ws2["workspace_id"]
        try:
            resp = self.client.patch(
                f"/api/v2/connectors/feedback/items/{self.item_id}",
                json={"type_hint": "bug"},
            )
        finally:
            self.client.headers.pop("X-Workspace-Id", None)
        self.assertEqual(resp.status_code, 404)

    def test_override_survives_re_import(self) -> None:
        # Override the classifier's first label.
        self.client.patch(
            f"/api/v2/connectors/feedback/items/{self.item_id}",
            json={"type_hint": "praise"},
        )
        # Re-import the same row — content_hash idempotency means
        # the existing row is preserved untouched.
        resp = self.client.post(
            "/api/v2/connectors/csv/import",
            json={"rows": [{
                "title": "Login fails on Safari",
                "body": "Cleared cache, no fix.",
                "type_hint": "",
            }]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["inserted"], 0)
        self.assertEqual(resp.json()["skipped"], 1)
        item = fi_store.get_item(
            self.store,
            item_id=self.item_id,
            workspace_id=self.workspace_id,
        )
        assert item is not None
        # Override survived — not classifier value.
        self.assertEqual(item.type_hint, "praise")


if __name__ == "__main__":
    unittest.main()
