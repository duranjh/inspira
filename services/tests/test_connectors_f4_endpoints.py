"""HTTP-level tests for the W2 F4 endpoints.

Covers:
- POST /api/v2/connectors/linear/connect (mock Linear API)
- POST /api/v2/connectors/linear/sync
- DELETE /api/v2/connectors/linear
- POST /api/v2/connectors/csv/import (full mock-CSV roundtrip)

Linear's HTTP layer is mocked via ``httpx.MockTransport`` mounted
on the AsyncClient inside ``planning_studio_service.connectors.linear.client``.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from planning_studio_service.feedback_items import store as fi_store

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _make_linear_transport(viewer_payload: dict | None,
                           issues_payload: list[dict] | None,
                           *, fail_with: int | None = None) -> httpx.MockTransport:
    """Build a httpx.MockTransport that fakes Linear's GraphQL API."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail_with is not None:
            return httpx.Response(fail_with, json={"errors": "rejected"})
        body = json.loads(request.content.decode("utf-8"))
        query = body.get("query", "")
        if "viewer" in query:
            return httpx.Response(200, json={"data": {"viewer": viewer_payload}})
        if "issues" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": issues_payload or [],
                        }
                    }
                },
            )
        return httpx.Response(200, json={"data": {}})

    return httpx.MockTransport(handler)


class LinearConnectTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="admin@acme.com", password="password123",
            display_name="Admin",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme-corp", "name": "Acme Corp"},
        )
        self.workspace_id = ws.json()["workspace"]["workspace_id"]

    def tearDown(self) -> None:
        del self.temp_dir

    def _patch_linear(self, transport: httpx.MockTransport):
        # The connect/sync routes both call httpx.AsyncClient() with
        # no transport — patch the constructor to inject ours.
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        return patch("httpx.AsyncClient", side_effect=factory)

    def test_linear_connect_persists_credential(self) -> None:
        transport = _make_linear_transport(
            viewer_payload={"id": "lin_ws_1", "name": "Acme Linear",
                            "email": "linear@acme.com"},
            issues_payload=[],
        )
        with self._patch_linear(transport):
            resp = self.client.post(
                "/api/v2/connectors/linear/connect",
                json={"api_key": "lin_api_thisisalongtestkey1234567890"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["account"]["name"], "Acme Linear")

    def test_linear_connect_rejects_short_key(self) -> None:
        resp = self.client.post(
            "/api/v2/connectors/linear/connect",
            json={"api_key": "x"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_linear_connect_surfaces_auth_failure(self) -> None:
        transport = _make_linear_transport(
            viewer_payload=None, issues_payload=None, fail_with=401,
        )
        with self._patch_linear(transport):
            resp = self.client.post(
                "/api/v2/connectors/linear/connect",
                json={"api_key": "lin_api_thisisalongtestkey1234567890"},
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"], "linear_auth_failed")

    def test_linear_disconnect_removes_credential(self) -> None:
        transport = _make_linear_transport(
            viewer_payload={"id": "lin_ws_1", "name": "Acme Linear"},
            issues_payload=[],
        )
        with self._patch_linear(transport):
            self.client.post(
                "/api/v2/connectors/linear/connect",
                json={"api_key": "lin_api_thisisalongtestkey1234567890"},
            )
        resp = self.client.delete("/api/v2/connectors/linear")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["disconnected"])

    def test_linear_sync_409_when_not_connected(self) -> None:
        resp = self.client.post("/api/v2/connectors/linear/sync")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["detail"]["error"], "linear_not_connected"
        )


class CsvImportTests(unittest.TestCase):

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

    def tearDown(self) -> None:
        del self.temp_dir

    def test_csv_import_persists_rows(self) -> None:
        rows = [
            {"title": "Login fails on Safari",
             "body": "Cleared cache, no fix",
             "source": "support-email"},
            {"title": "Loving the kanban board!",
             "body": "Take my money.",
             "type_hint": "praise"},
        ]
        resp = self.client.post(
            "/api/v2/connectors/csv/import",
            json={"rows": rows},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["inserted"], 2)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(body["total"], 2)
        # Items should be in feedback_items + workspace-scoped.
        items = fi_store.list_items(
            self.store, workspace_id=self.workspace_id
        )
        titles = {it.title for it in items}
        self.assertEqual(titles, {"Login fails on Safari",
                                   "Loving the kanban board!"})

    def test_csv_import_idempotent_on_re_paste(self) -> None:
        rows = [{"title": "Same row", "body": "x"}]
        first = self.client.post(
            "/api/v2/connectors/csv/import", json={"rows": rows},
        ).json()
        self.assertEqual(first["inserted"], 1)
        second = self.client.post(
            "/api/v2/connectors/csv/import", json={"rows": rows},
        ).json()
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_csv_import_skips_blank_title_rows(self) -> None:
        rows = [
            {"title": "Valid row"},
            {"title": ""},
            {"title": "   "},
            {"title": "Another valid"},
        ]
        resp = self.client.post(
            "/api/v2/connectors/csv/import", json={"rows": rows},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["inserted"], 2)
        self.assertEqual(body["skipped"], 2)

    def test_csv_import_rejects_empty_batch(self) -> None:
        resp = self.client.post(
            "/api/v2/connectors/csv/import", json={"rows": []},
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"]["error"], "no_rows")

    def test_csv_import_caps_huge_batch(self) -> None:
        rows = [{"title": f"row-{i}"} for i in range(5001)]
        resp = self.client.post(
            "/api/v2/connectors/csv/import", json={"rows": rows},
        )
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(
            resp.json()["detail"]["error"], "too_many_rows"
        )

    def test_csv_import_workspace_scoped(self) -> None:
        # Create a second workspace + import there. First workspace
        # should not see those rows.
        ws2 = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "beta-corp", "name": "Beta Corp"},
        ).json()["workspace"]
        # Switch active workspace via header on the next call.
        rows = [{"title": "Beta-only row"}]
        self.client.headers["X-Workspace-Id"] = ws2["workspace_id"]
        try:
            self.client.post(
                "/api/v2/connectors/csv/import", json={"rows": rows},
            )
        finally:
            self.client.headers.pop("X-Workspace-Id", None)
        # Original workspace should have zero items.
        items_a = fi_store.list_items(
            self.store, workspace_id=self.workspace_id
        )
        items_b = fi_store.list_items(
            self.store, workspace_id=ws2["workspace_id"]
        )
        self.assertEqual(items_a, [])
        self.assertEqual(len(items_b), 1)


if __name__ == "__main__":
    unittest.main()
