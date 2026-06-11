"""HTTP + store tests for the Recently Deleted recovery feature.

Covers the three new routes end-to-end:

- ``GET  /api/v2/projects/recently-deleted``       → owner's soft-deleted list
- ``POST /api/v2/projects/{project_id}/restore``   → clear deleted_at if in grace
- ``POST /api/v2/projects/{project_id}/purge``     → hard-delete (must be soft-deleted first)

Plus the spec invariants the design doc calls out for "soft delete with grace":

- list within the grace window includes the project, with days_remaining
- list outside the grace window omits the project (lazy purge)
- restore happy path: project is back in the active list
- restore expired: 410 Gone
- purge requires the project to be soft-deleted first (404 otherwise)
- ownership: a different user cannot list / restore / purge another's projects

These tests follow the same pattern as ``test_project_archiving.py``.
"""
from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        make_test_app,
        signup_and_login,
    )


def _create_project(client, title: str = "A project to soft-delete") -> str:
    response = client.post("/api/v2/projects", json={"title": title})
    response.raise_for_status()
    return response.json()["project"]["project_id"]


def _backdate_deleted_at(store, project_id: str, days_ago: int) -> None:
    """Rewind a soft-delete timestamp to N days ago (test-only helper).

    The store doesn't expose this — soft-delete always stamps "now". To
    simulate "deleted N days ago" without sleeping for N days, we reach
    into the SQLite store directly. Tests that target the grace window
    behaviour use this to land a row just before / just after the cutoff.
    """
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds",
    )
    db_path = store.config.db_path
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE v2_projects SET deleted_at = ? WHERE project_id = ?",
            (when, project_id),
        )
        conn.commit()
    finally:
        conn.close()


class RecentlyDeletedListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="trasher@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_includes_recently_deleted_within_grace(self) -> None:
        project_id = _create_project(self.client, "Wine festival")
        deleted = self.client.post(f"/api/v2/projects/{project_id}/delete")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        # Not on active list.
        self.assertEqual(
            self.client.get("/api/v2/projects").json()["projects"], [],
        )
        # Is on recently-deleted list.
        listed = self.client.get(
            "/api/v2/projects/recently-deleted",
        ).json()["projects"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["project_id"], project_id)
        self.assertIsNotNone(listed[0]["deleted_at"])
        self.assertIn("days_remaining", listed[0])
        # Default grace 30 days; just-deleted should still show ~30 left.
        self.assertGreaterEqual(listed[0]["days_remaining"], 29)
        self.assertLessEqual(listed[0]["days_remaining"], 30)

    def test_list_lazily_purges_rows_past_grace_window(self) -> None:
        project_id = _create_project(self.client, "Old and gone")
        self.client.post(f"/api/v2/projects/{project_id}/delete")
        _backdate_deleted_at(self.store, project_id, days_ago=45)

        listed = self.client.get(
            "/api/v2/projects/recently-deleted",
        ).json()["projects"]
        self.assertEqual(listed, [])

        # The row really is hard-deleted: a follow-up restore must 404.
        restored = self.client.post(f"/api/v2/projects/{project_id}/restore")
        self.assertEqual(restored.status_code, 404, restored.text)

    def test_list_excludes_active_projects(self) -> None:
        keep_id = _create_project(self.client, "Stay alive")
        delete_id = _create_project(self.client, "Goodbye")
        self.client.post(f"/api/v2/projects/{delete_id}/delete")

        listed = self.client.get(
            "/api/v2/projects/recently-deleted",
        ).json()["projects"]
        self.assertEqual([p["project_id"] for p in listed], [delete_id])
        _ = keep_id


class RestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="restorer@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_restore_happy_path(self) -> None:
        project_id = _create_project(self.client, "Resurrect me")
        self.client.post(f"/api/v2/projects/{project_id}/delete")

        restored = self.client.post(f"/api/v2/projects/{project_id}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        body = restored.json()
        self.assertEqual(body["project"]["project_id"], project_id)
        self.assertIsNone(body["project"].get("deleted_at"))

        # Back on the active list.
        active_ids = [
            p["project_id"]
            for p in self.client.get("/api/v2/projects").json()["projects"]
        ]
        self.assertIn(project_id, active_ids)
        # And gone from recently-deleted.
        rd = self.client.get(
            "/api/v2/projects/recently-deleted",
        ).json()["projects"]
        self.assertEqual([p["project_id"] for p in rd], [])

    def test_restore_expired_returns_410(self) -> None:
        project_id = _create_project(self.client, "Too late")
        self.client.post(f"/api/v2/projects/{project_id}/delete")
        _backdate_deleted_at(self.store, project_id, days_ago=31)

        # Going through the HTTP route directly (skipping the GET that
        # would lazy-purge) gives us a 410.
        restored = self.client.post(f"/api/v2/projects/{project_id}/restore")
        self.assertEqual(restored.status_code, 410, restored.text)

    def test_restore_unknown_project_returns_404(self) -> None:
        restored = self.client.post(
            "/api/v2/projects/project-doesnotexist/restore",
        )
        self.assertEqual(restored.status_code, 404)

    def test_restore_active_project_returns_404(self) -> None:
        # Restore is meaningless for a not-soft-deleted project.
        project_id = _create_project(self.client, "Already alive")
        restored = self.client.post(f"/api/v2/projects/{project_id}/restore")
        self.assertEqual(restored.status_code, 404)


class PurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="purger@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_purge_requires_soft_delete_first(self) -> None:
        project_id = _create_project(self.client, "Live project")
        purged = self.client.post(f"/api/v2/projects/{project_id}/purge")
        self.assertEqual(purged.status_code, 404, purged.text)

        # The active list still has it.
        ids = [
            p["project_id"]
            for p in self.client.get("/api/v2/projects").json()["projects"]
        ]
        self.assertIn(project_id, ids)

    def test_purge_after_soft_delete_succeeds(self) -> None:
        project_id = _create_project(self.client, "Hard-delete me")
        self.client.post(f"/api/v2/projects/{project_id}/delete")

        purged = self.client.post(f"/api/v2/projects/{project_id}/purge")
        self.assertEqual(purged.status_code, 204, purged.text)

        # Recently-deleted no longer lists it.
        rd = self.client.get(
            "/api/v2/projects/recently-deleted",
        ).json()["projects"]
        self.assertEqual(rd, [])

        # And restore now 404s (row is truly gone).
        restored = self.client.post(f"/api/v2/projects/{project_id}/restore")
        self.assertEqual(restored.status_code, 404)


class OwnershipTests(unittest.TestCase):
    """A second user must not see / touch another's recently-deleted projects."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # User A creates + deletes a project.
        signup_and_login(self.client, email="alice@example.com", password="passwordone")
        self.alice_project_id = _create_project(self.client, "Alice's project")
        self.client.post(f"/api/v2/projects/{self.alice_project_id}/delete")
        # Sign out & in as B.
        self.client.post("/api/auth/logout")
        signup_and_login(self.client, email="bob@example.com", password="passwordtwo")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_other_user_cannot_see_recently_deleted(self) -> None:
        listed = self.client.get(
            "/api/v2/projects/recently-deleted",
        ).json()["projects"]
        self.assertEqual(listed, [])

    def test_other_user_cannot_restore(self) -> None:
        restored = self.client.post(
            f"/api/v2/projects/{self.alice_project_id}/restore",
        )
        self.assertEqual(restored.status_code, 404)

    def test_other_user_cannot_purge(self) -> None:
        purged = self.client.post(
            f"/api/v2/projects/{self.alice_project_id}/purge",
        )
        self.assertEqual(purged.status_code, 404)


if __name__ == "__main__":
    unittest.main()
