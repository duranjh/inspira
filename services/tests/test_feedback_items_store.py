"""Unit tests for the feedback_items store helpers (W2 F4).

Covers the invariants that the F5 ingestion worker will rely on:
- workspace-scoping (no cross-tenant reads/writes)
- idempotency via UNIQUE (workspace_id, content_hash)
- status transitions
- list / count accuracy
"""
from __future__ import annotations

import unittest

from planning_studio_service.feedback_items import store as fi_store

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


class FeedbackItemsStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.ws_a = "ws-a-test"
        self.ws_b = "ws-b-test"

    def tearDown(self) -> None:
        del self.temp_dir

    def test_upsert_inserts_first_time(self) -> None:
        item_id, was_new = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_a,
            source="csv-import",
            external_id=None,
            title="Login fails on Safari",
            body="Cleared cache, no fix.",
        )
        self.assertTrue(item_id.startswith("fi-"))
        self.assertTrue(was_new)

    def test_upsert_idempotent_on_same_content(self) -> None:
        first_id, was_new = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_a,
            source="csv-import",
            external_id=None,
            title="Same title",
            body="Same body",
            received_at="2026-04-12T12:00:00+00:00",
        )
        self.assertTrue(was_new)
        second_id, was_new_again = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_a,
            source="csv-import",
            external_id=None,
            title="Same title",
            body="Same body",
            received_at="2026-04-12T12:00:00+00:00",
        )
        self.assertEqual(second_id, first_id)
        self.assertFalse(was_new_again)

    def test_upsert_workspace_scoped(self) -> None:
        # Same content in two workspaces should produce TWO rows
        # (one per workspace), not collide.
        a_id, _ = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_a,
            source="linear",
            external_id="LIN-1",
            title="Cross-tenant collision check",
        )
        b_id, b_new = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_b,
            source="linear",
            external_id="LIN-1",
            title="Cross-tenant collision check",
        )
        self.assertNotEqual(a_id, b_id)
        self.assertTrue(b_new)

    def test_external_id_drives_dedupe_for_linear(self) -> None:
        # Two Linear issues with the same title but different IDs
        # should both insert.
        first, was_new = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_a,
            source="linear",
            external_id="LIN-1",
            title="Login fails on Safari",
        )
        second, was_new_2 = fi_store.upsert_item(
            self.store,
            workspace_id=self.ws_a,
            source="linear",
            external_id="LIN-2",
            title="Login fails on Safari",
        )
        self.assertNotEqual(first, second)
        self.assertTrue(was_new and was_new_2)

    def test_blank_title_rejected(self) -> None:
        with self.assertRaises(ValueError):
            fi_store.upsert_item(
                self.store,
                workspace_id=self.ws_a,
                source="csv-import",
                external_id=None,
                title="   ",
            )

    def test_list_filters_by_workspace_only(self) -> None:
        for i in range(3):
            fi_store.upsert_item(
                self.store,
                workspace_id=self.ws_a,
                source="csv-import",
                external_id=None,
                title=f"item-a-{i}",
            )
        for i in range(2):
            fi_store.upsert_item(
                self.store,
                workspace_id=self.ws_b,
                source="csv-import",
                external_id=None,
                title=f"item-b-{i}",
            )
        a_items = fi_store.list_items(self.store, workspace_id=self.ws_a)
        b_items = fi_store.list_items(self.store, workspace_id=self.ws_b)
        self.assertEqual(len(a_items), 3)
        self.assertEqual(len(b_items), 2)
        for it in a_items:
            self.assertEqual(it.workspace_id, self.ws_a)

    def test_count_filters_by_source(self) -> None:
        fi_store.upsert_item(
            self.store, workspace_id=self.ws_a, source="csv-import",
            external_id=None, title="csv-1",
        )
        fi_store.upsert_item(
            self.store, workspace_id=self.ws_a, source="linear",
            external_id="LIN-1", title="linear-1",
        )
        fi_store.upsert_item(
            self.store, workspace_id=self.ws_a, source="linear",
            external_id="LIN-2", title="linear-2",
        )
        c_csv = fi_store.count_items(
            self.store, workspace_id=self.ws_a, source="csv-import"
        )
        c_lin = fi_store.count_items(
            self.store, workspace_id=self.ws_a, source="linear"
        )
        c_all = fi_store.count_items(self.store, workspace_id=self.ws_a)
        self.assertEqual(c_csv.total, 1)
        self.assertEqual(c_lin.total, 2)
        self.assertEqual(c_all.total, 3)
        # F5 sync-classify flips status queued → classified on insert,
        # so queued count is 0 for fresh imports. Status='queued' is
        # reserved for the future async-classifier failure path.
        self.assertEqual(c_all.queued, 0)

    def test_mark_status_workspace_scoped(self) -> None:
        a_id, _ = fi_store.upsert_item(
            self.store, workspace_id=self.ws_a, source="csv-import",
            external_id=None, title="t",
        )
        # Right workspace_id → flips.
        ok = fi_store.mark_status(
            self.store, item_id=a_id, workspace_id=self.ws_a,
            status="classified",
        )
        self.assertTrue(ok)
        # Wrong workspace_id → no-op (defense in depth).
        ok2 = fi_store.mark_status(
            self.store, item_id=a_id, workspace_id=self.ws_b,
            status="discarded",
        )
        self.assertFalse(ok2)
        # Confirm the status survived the would-be cross-tenant write.
        items = fi_store.list_items(
            self.store, workspace_id=self.ws_a, status="classified"
        )
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
