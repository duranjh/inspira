"""Tests for the per-project Activity Timeline.

Two layers of coverage:

- Store-level: ``PlanningStudioStore.list_project_activity`` must filter
  categories, order newest first, paginate with ``has_more``, derive
  ``subject_title`` from the audit JSON payloads, and reject cross-user
  access with ``PermissionError``.
- HTTP-level: ``GET /api/v2/projects/{id}/activity`` is user-scoped —
  another signed-in user probing the same ID gets a 404. Shape follows
  the spec (``{events: [...], has_more: bool}``) and paging works via
  the ``limit`` + ``offset`` query parameters.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class ListProjectActivityStoreTests(unittest.TestCase):
    """Store-level tests hitting ``list_project_activity`` directly.

    Exercise the filter / pagination / subject-title derivation paths
    without going through the HTTP layer. Keeps these tests small and
    fast — the HTTP class below covers the route-level wiring.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="activity-timeline-store-", ignore_cleanup_errors=True,
        )
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        self.store = PlanningStudioStore(load_config())
        # Create a real owned project so verify_project_ownership passes.
        self.user_id = "user-owner"
        self.other_user_id = "user-intruder"
        self.project = self.store.create_v2_project(
            title="Timeline Project",
            user_id=self.user_id,
        )
        self.project_id = self.project["project_id"]
        # create_v2_project now emits a `project/create` audit event.
        # Clear the audit_log so these targeted unit tests start from a
        # clean slate — they care about events the tests themselves
        # insert, not the setup-phase project-create event.
        with self.store._connect() as c:  # noqa: SLF001
            c.execute("DELETE FROM audit_log")
            c.commit()

    def tearDown(self) -> None:
        os.environ.pop("PLANNING_STUDIO_STORAGE_ROOT", None)
        self.temp_dir.cleanup()

    def _append(
        self,
        *,
        category: str,
        action: str,
        subject_id: str | None = None,
        before: dict | None = None,
        after: dict | None = None,
        actor: str | None = None,
    ) -> None:
        self.store.append_audit_event(
            workspace_id="ws-default",
            actor_user_id=actor or self.user_id,
            category=category,
            action=action,
            project_id=self.project_id,
            subject_id=subject_id,
            before=before,
            after=after,
        )

    # ---------- Shape + ordering ----------------------------------------

    def test_returns_newest_first(self) -> None:
        self._append(category="topic", action="create",
                     subject_id="t-1", after={"title": "Venue"})
        self._append(category="topic", action="create",
                     subject_id="t-2", after={"title": "Budget"})
        self._append(category="decision", action="create",
                     subject_id="d-1", after={"statement": "No plastic."})

        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        events = result["events"]
        self.assertEqual(len(events), 3)
        # Newest first — decision came last chronologically.
        self.assertEqual(events[0]["category"], "decision")
        self.assertEqual(events[0]["subject_title"], "No plastic.")
        self.assertEqual(events[-1]["subject_title"], "Venue")

    def test_shape_matches_spec(self) -> None:
        self._append(category="topic", action="create",
                     subject_id="t-1", after={"title": "Audience"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertIn("events", result)
        self.assertIn("has_more", result)
        self.assertIsInstance(result["has_more"], bool)
        event = result["events"][0]
        # Every field the frontend timeline renders must be present.
        self.assertIn("event_id", event)
        self.assertIn("category", event)
        self.assertIn("action", event)
        self.assertIn("subject_title", event)
        self.assertIn("created_at", event)
        self.assertIn("actor_display_name", event)
        self.assertEqual(event["subject_title"], "Audience")

    # ---------- Category filtering --------------------------------------

    def test_internal_categories_are_hidden(self) -> None:
        """``system``, ``auth``, ``admin`` events never appear in the feed."""
        self._append(category="topic", action="create",
                     subject_id="t-1", after={"title": "Visible"})
        self._append(category="system", action="bootstrap",
                     subject_id=None, after={"note": "hidden"})
        self._append(category="auth", action="login",
                     subject_id=None, after={"ip": "1.2.3.4"})
        self._append(category="admin", action="impersonate",
                     subject_id=None, after={"who": "someone"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        categories = {e["category"] for e in result["events"]}
        self.assertEqual(categories, {"topic"})

    def test_all_visible_categories_surface(self) -> None:
        """topic / relationship / decision / project / share / export all show."""
        self._append(category="topic", action="create", after={"title": "T"})
        self._append(category="relationship", action="create",
                     after={"label": "requires"})
        self._append(category="decision", action="create",
                     after={"statement": "Ship in April."})
        self._append(category="project", action="rename",
                     after={"title": "New Title"})
        self._append(category="share", action="mint",
                     after={"url_path": "/shared/abc"})
        self._append(category="export", action="markdown",
                     after={"format": "md"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        categories = {e["category"] for e in result["events"]}
        self.assertEqual(
            categories,
            {"topic", "relationship", "decision", "project", "share", "export"},
        )

    # ---------- Subject title derivation --------------------------------

    def test_subject_title_uses_after_json_title(self) -> None:
        self._append(category="topic", action="create",
                     after={"title": "Marketing launch"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertEqual(result["events"][0]["subject_title"], "Marketing launch")

    def test_subject_title_uses_statement_for_decision(self) -> None:
        self._append(category="decision", action="create",
                     after={"statement": "Avoid plastic."})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertEqual(result["events"][0]["subject_title"], "Avoid plastic.")

    def test_subject_title_falls_back_to_before_for_delete(self) -> None:
        self._append(category="topic", action="delete",
                     before={"title": "Removed Topic"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertEqual(result["events"][0]["subject_title"], "Removed Topic")

    def test_subject_title_empty_when_no_known_key(self) -> None:
        self._append(category="topic", action="create", after={"odd": "field"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertEqual(result["events"][0]["subject_title"], "")

    # ---------- Pagination ----------------------------------------------

    def test_paging_has_more_and_offset(self) -> None:
        # Seed 5 events; page with limit=2.
        for i in range(5):
            self._append(category="topic", action="create",
                         after={"title": f"Topic {i}"})

        page1 = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
            limit=2, offset=0,
        )
        self.assertEqual(len(page1["events"]), 2)
        self.assertTrue(page1["has_more"])

        page2 = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
            limit=2, offset=2,
        )
        self.assertEqual(len(page2["events"]), 2)
        self.assertTrue(page2["has_more"])

        page3 = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
            limit=2, offset=4,
        )
        self.assertEqual(len(page3["events"]), 1)
        self.assertFalse(page3["has_more"])

        # No overlap between pages.
        ids_1 = {e["event_id"] for e in page1["events"]}
        ids_2 = {e["event_id"] for e in page2["events"]}
        ids_3 = {e["event_id"] for e in page3["events"]}
        self.assertEqual(ids_1 & ids_2, set())
        self.assertEqual(ids_2 & ids_3, set())
        self.assertEqual(ids_1 & ids_3, set())

    def test_limit_clamped(self) -> None:
        self._append(category="topic", action="create", after={"title": "One"})
        # Absurd limit values get clamped into range rather than erroring.
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
            limit=99999, offset=0,
        )
        self.assertEqual(len(result["events"]), 1)

    # ---------- IDOR ----------------------------------------------------

    def test_cross_user_access_raises(self) -> None:
        self._append(category="topic", action="create", after={"title": "X"})
        with self.assertRaises(PermissionError):
            self.store.list_project_activity(
                project_id=self.project_id, user_id=self.other_user_id,
            )

    def test_unknown_project_raises(self) -> None:
        with self.assertRaises(PermissionError):
            self.store.list_project_activity(
                project_id="project-does-not-exist",
                user_id=self.user_id,
            )

    # ---------- Events for OTHER projects don't leak in -----------------

    def test_other_project_events_excluded(self) -> None:
        """Audit rows for a sibling project must never surface here."""
        other = self.store.create_v2_project(
            title="Other", user_id=self.user_id,
        )
        other_id = other["project_id"]
        # Event on the OTHER project — must NOT show up on the target feed.
        self.store.append_audit_event(
            workspace_id="ws-default",
            actor_user_id=self.user_id,
            category="topic",
            action="create",
            project_id=other_id,
            after={"title": "Sibling topic"},
        )
        # Event on the target project — must show up.
        self._append(category="topic", action="create", after={"title": "Mine"})
        result = self.store.list_project_activity(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["subject_title"], "Mine")


class ListProjectActivityHttpTests(unittest.TestCase):
    """HTTP-layer tests against ``GET /api/v2/projects/{id}/activity``."""

    def setUp(self) -> None:
        self.a, self.store, self.adapter, self.temp_dir = make_test_app()
        self.b = TestClient(self.a.app)
        alice = signup_and_login(
            self.a, email="alice@example.com", password="alice-password-1",
        )
        signup_and_login(
            self.b, email="bob@example.com", password="bob-password-123",
        )
        self.alice_user_id = alice["user_id"]
        # Create a real project for alice and seed a few audit events
        # on it so the feed has content.
        project = self.a.post(
            "/api/v2/projects", json={"title": "Alice's Canvas"},
        ).json()["project"]
        self.project_id = project["project_id"]
        # POST /projects now emits a project/create audit event. Clear
        # the audit_log so these HTTP tests count only the events they
        # explicitly seed below.
        with self.store._connect() as c:  # noqa: SLF001
            c.execute("DELETE FROM audit_log")
            c.commit()
        for i in range(3):
            self.store.append_audit_event(
                workspace_id="ws-default",
                actor_user_id=self.alice_user_id,
                category="topic",
                action="create",
                project_id=self.project_id,
                subject_id=f"t-{i}",
                after={"title": f"Topic {i}"},
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_shape_is_events_and_has_more(self) -> None:
        response = self.a.get(f"/api/v2/projects/{self.project_id}/activity")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("events", payload)
        self.assertIn("has_more", payload)
        self.assertIsInstance(payload["events"], list)
        # 3 seeded events, newest first.
        self.assertEqual(len(payload["events"]), 3)
        titles = [e["subject_title"] for e in payload["events"]]
        self.assertEqual(titles, ["Topic 2", "Topic 1", "Topic 0"])

    def test_paging_via_query_string(self) -> None:
        r1 = self.a.get(
            f"/api/v2/projects/{self.project_id}/activity?limit=2&offset=0",
        ).json()
        self.assertEqual(len(r1["events"]), 2)
        self.assertTrue(r1["has_more"])

        r2 = self.a.get(
            f"/api/v2/projects/{self.project_id}/activity?limit=2&offset=2",
        ).json()
        self.assertEqual(len(r2["events"]), 1)
        self.assertFalse(r2["has_more"])

    def test_cross_user_access_returns_404(self) -> None:
        """User B probing user A's project gets a 404 (IDOR hygiene)."""
        response = self.b.get(
            f"/api/v2/projects/{self.project_id}/activity",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "project_not_found",
        )

    def test_internal_categories_filtered(self) -> None:
        """system / auth / admin audit rows are hidden from the feed."""
        self.store.append_audit_event(
            workspace_id="ws-default",
            actor_user_id=self.alice_user_id,
            category="system",
            action="housekeeping",
            project_id=self.project_id,
            after={"note": "should not appear"},
        )
        payload = self.a.get(
            f"/api/v2/projects/{self.project_id}/activity",
        ).json()
        for event in payload["events"]:
            self.assertNotEqual(event["category"], "system")

    def test_invalid_limit_returns_400(self) -> None:
        response = self.a.get(
            f"/api/v2/projects/{self.project_id}/activity?limit=0",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_offset_returns_400(self) -> None:
        response = self.a.get(
            f"/api/v2/projects/{self.project_id}/activity?offset=-1",
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
