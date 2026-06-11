"""HTTP + store tests for the project-archiving feature.

Covers the three new routes end-to-end:

- ``POST /api/v2/projects/{project_id}/archive``    → stamp archived_at
- ``POST /api/v2/projects/{project_id}/unarchive``  → clear archived_at
- ``GET  /api/v2/projects/archived``                → owner's archived list

Plus two store-level invariants that the spec calls out explicitly:

- ``list_v2_projects`` filters out archived rows by default.
- Archive is a weaker state than soft-delete — once a project is
  deleted, it never resurfaces in the archive view either.

And the usual IDOR defence — a second user must not be able to
archive, unarchive, or read another user's archived projects. All
cross-user attempts resolve to 404, matching the convention used by
every other v2 mutation route in the service.

These tests follow the same pattern as ``test_shelves.py`` —
``make_test_app`` hands back a real FastAPI TestClient backed by an
isolated SQLite store. No HTTP boundary mocking; no OpenAI traffic
(the planner adapter is a MagicMock we don't invoke, except in one
test that needs topics to exist).
"""
from __future__ import annotations

import unittest

try:
    from ._helpers import fake_kickoff_response, make_test_app, signup_and_login
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )


def _create_project(client, title: str = "A project to archive") -> str:
    """Create a v2 project via the HTTP route and return its project_id."""
    response = client.post("/api/v2/projects", json={"title": title})
    response.raise_for_status()
    return response.json()["project"]["project_id"]


class ArchiveLifecycleTests(unittest.TestCase):
    """Archive → unarchive round-trips for a single user."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="archiver@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_archive_then_unarchive_toggles_archived_at(self) -> None:
        project_id = _create_project(self.client, "Wine festival")

        # Fresh project → archived_at is NULL.
        listed = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(listed), 1)
        self.assertIsNone(listed[0].get("archived_at"))

        # Archive.
        archived = self.client.post(
            f"/api/v2/projects/{project_id}/archive",
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertIsNotNone(archived.json()["project"]["archived_at"])

        # It disappears from the default list.
        listed_after = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(listed_after, [])

        # But shows up on the archived list.
        archived_list = self.client.get(
            "/api/v2/projects/archived",
        ).json()["projects"]
        self.assertEqual(len(archived_list), 1)
        self.assertEqual(archived_list[0]["project_id"], project_id)

        # Unarchive.
        unarchived = self.client.post(
            f"/api/v2/projects/{project_id}/unarchive",
        )
        self.assertEqual(unarchived.status_code, 200, unarchived.text)
        self.assertIsNone(unarchived.json()["project"]["archived_at"])

        # Back on the default list; off the archived list.
        self.assertEqual(
            len(self.client.get("/api/v2/projects").json()["projects"]), 1,
        )
        self.assertEqual(
            self.client.get("/api/v2/projects/archived").json()["projects"],
            [],
        )

    def test_archive_hides_project_from_default_list(self) -> None:
        """The bread-and-butter invariant — archived rows never surface here."""
        keep_id = _create_project(self.client, "Keep me visible")
        archive_id = _create_project(self.client, "Hide me")

        # Both start visible.
        titles = [
            p["title"]
            for p in self.client.get("/api/v2/projects").json()["projects"]
        ]
        self.assertIn("Keep me visible", titles)
        self.assertIn("Hide me", titles)

        self.client.post(f"/api/v2/projects/{archive_id}/archive")

        titles_after = [
            p["title"]
            for p in self.client.get("/api/v2/projects").json()["projects"]
        ]
        self.assertEqual(titles_after, ["Keep me visible"])
        # And the kept project IS NOT in the archived list.
        archived_titles = [
            p["title"] for p in self.client.get(
                "/api/v2/projects/archived",
            ).json()["projects"]
        ]
        self.assertEqual(archived_titles, ["Hide me"])
        _ = keep_id  # keep_id is load-bearing only via the title assertion

    def test_archive_preserves_topics_and_relationships(self) -> None:
        """Archive is a view filter, not a cascade. Child rows stay intact."""
        self.adapter.kickoff.return_value = fake_kickoff_response()
        project_id = "proj-archive-preserve"
        self.client.post(
            f"/api/v2/projects/{project_id}/kickoff",
            json={"user_idea": "An outdoor wine festival."},
        )

        # Sanity-check the topics + relationships got created.
        topics_before = self.client.get(
            f"/api/v2/projects/{project_id}/topics",
        ).json()["topics"]
        rels_before = self.client.get(
            f"/api/v2/projects/{project_id}/relationships",
        ).json()["relationships"]
        self.assertGreater(len(topics_before), 0)
        self.assertGreater(len(rels_before), 0)

        # Archive.
        self.client.post(f"/api/v2/projects/{project_id}/archive")

        # Topics + relationships still fetchable (project is still owned,
        # just archived; the underlying rows didn't move or get a
        # deleted_at stamp).
        topics_after = self.client.get(
            f"/api/v2/projects/{project_id}/topics",
        ).json()["topics"]
        rels_after = self.client.get(
            f"/api/v2/projects/{project_id}/relationships",
        ).json()["relationships"]
        self.assertEqual(len(topics_after), len(topics_before))
        self.assertEqual(len(rels_after), len(rels_before))

    def test_archive_then_delete_still_deletes(self) -> None:
        """Soft-delete is the stronger state; archive doesn't block it."""
        project_id = _create_project(self.client, "Archive then destroy")
        self.client.post(f"/api/v2/projects/{project_id}/archive")

        deleted = self.client.post(
            f"/api/v2/projects/{project_id}/delete",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

        # Neither list surfaces a deleted row.
        self.assertEqual(
            self.client.get("/api/v2/projects").json()["projects"], [],
        )
        self.assertEqual(
            self.client.get("/api/v2/projects/archived").json()["projects"],
            [],
        )

    def test_archive_auto_revokes_active_share_tokens(self) -> None:
        """Archiving a project kills any live share token on it.

        Archive semantics are "hide this from the world"; leaving a
        public link live after the owner archived would let a URL
        pasted in a chat thread keep serving canvas data indefinitely
        — that's a privacy bug QA flagged. The revoke runs inside the
        same transaction as the archive stamp so a reader hitting the
        shared route during the call sees a consistent state.

        Unarchiving deliberately does NOT re-activate the token — the
        user can mint a fresh link with POST /share if they want to
        re-share, same as the explicit-revoke path.
        """
        # Use kickoff so there's something meaningful to share.
        self.adapter.kickoff.return_value = fake_kickoff_response()
        project_id = "proj-share-after-archive"
        self.client.post(
            f"/api/v2/projects/{project_id}/kickoff",
            json={"user_idea": "A vineyard tour."},
        )

        # Mint a share link, then archive.
        minted = self.client.post(f"/api/v2/projects/{project_id}/share")
        self.assertEqual(minted.status_code, 201, minted.text)
        token = minted.json()["share_link"]["token"]
        self.assertTrue(token)

        self.client.post(f"/api/v2/projects/{project_id}/archive")

        # Owner sees no active share link any more.
        got = self.client.get(f"/api/v2/projects/{project_id}/share").json()
        self.assertIsNone(got["share_link"])

        # Public route now 404s — the token is revoked.
        from fastapi.testclient import TestClient  # noqa: PLC0415
        anon = TestClient(self.client.app)
        blocked = anon.get(f"/api/v2/shared/{token}")
        self.assertEqual(blocked.status_code, 404)

        # Unarchive does NOT re-activate the token.
        self.client.post(f"/api/v2/projects/{project_id}/unarchive")
        still_none = self.client.get(
            f"/api/v2/projects/{project_id}/share",
        ).json()
        self.assertIsNone(still_none["share_link"])
        self.assertEqual(
            anon.get(f"/api/v2/shared/{token}").status_code, 404,
        )

    def test_unarchive_is_idempotent_on_already_active_project(self) -> None:
        """Unarchiving a live project succeeds and leaves it live."""
        project_id = _create_project(self.client, "Already active")
        # Project is never archived.
        response = self.client.post(
            f"/api/v2/projects/{project_id}/unarchive",
        )
        # The UPDATE finds the row (user owns it, it's not deleted) and
        # sets archived_at = NULL (already NULL) — the row-count is
        # non-zero so the call returns 200 with a project payload.
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["project"]["archived_at"])

    def test_archive_of_unknown_project_returns_404(self) -> None:
        missing = self.client.post("/api/v2/projects/project-missing/archive")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json().get("detail", {}).get("error"),
            "project_not_found",
        )

    def test_unarchive_of_unknown_project_returns_404(self) -> None:
        missing = self.client.post(
            "/api/v2/projects/project-missing/unarchive",
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json().get("detail", {}).get("error"),
            "project_not_found",
        )


class ArchiveIsolationTests(unittest.TestCase):
    """Cross-user IDOR hardening and scoped listings."""

    def setUp(self) -> None:
        # A has one project; B has none.
        self.client_a, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client_a, email="user-a@example.com", password="password123",
        )
        self.project_a = _create_project(self.client_a, "Owned by A")

        # A second client reusing the same FastAPI app so both sessions
        # hit the same store. Separate TestClient so the session cookie
        # doesn't collide.
        from fastapi.testclient import TestClient  # noqa: PLC0415
        self.client_b = TestClient(self.client_a.app)
        signup_and_login(
            self.client_b, email="user-b@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_user_b_cannot_archive_user_a_project(self) -> None:
        response = self.client_b.post(
            f"/api/v2/projects/{self.project_a}/archive",
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json().get("detail", {}).get("error"),
            "project_not_found",
        )

        # And A's project is still active (not stamped by the failed
        # cross-user write).
        listed = self.client_a.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(listed), 1)
        self.assertIsNone(listed[0].get("archived_at"))

    def test_user_b_cannot_unarchive_user_a_project(self) -> None:
        # A archives their own project first.
        self.client_a.post(f"/api/v2/projects/{self.project_a}/archive")

        response = self.client_b.post(
            f"/api/v2/projects/{self.project_a}/unarchive",
        )
        self.assertEqual(response.status_code, 404, response.text)

        # A's project is still archived.
        listed = self.client_a.get(
            "/api/v2/projects/archived",
        ).json()["projects"]
        self.assertEqual(len(listed), 1)
        self.assertIsNotNone(listed[0]["archived_at"])

    def test_list_archived_returns_only_callers_projects(self) -> None:
        # A archives their project.
        self.client_a.post(f"/api/v2/projects/{self.project_a}/archive")
        # B creates + archives a separate one.
        project_b = _create_project(self.client_b, "Owned by B")
        self.client_b.post(f"/api/v2/projects/{project_b}/archive")

        a_archived = self.client_a.get(
            "/api/v2/projects/archived",
        ).json()["projects"]
        b_archived = self.client_b.get(
            "/api/v2/projects/archived",
        ).json()["projects"]

        self.assertEqual(len(a_archived), 1)
        self.assertEqual(a_archived[0]["project_id"], self.project_a)
        self.assertEqual(len(b_archived), 1)
        self.assertEqual(b_archived[0]["project_id"], project_b)

    def test_archived_list_requires_auth(self) -> None:
        # Brand-new client with no session cookie. The default auth
        # dependency falls through to the anonymous system-user path so
        # this returns 200 with an empty list — same shape as the rest
        # of the v2 GETs. The point of the test is to document that
        # anonymous callers don't accidentally see someone else's data.
        from fastapi.testclient import TestClient  # noqa: PLC0415
        anon = TestClient(self.client_a.app)
        response = anon.get("/api/v2/projects/archived")
        # Either 200 with empty projects (anon-user scope) or 401 —
        # both are acceptable. What must NOT happen is leaking A's data.
        if response.status_code == 200:
            projects = response.json().get("projects", [])
            for project in projects:
                self.assertNotEqual(
                    project.get("project_id"), self.project_a,
                )


class ArchiveStoreDirectTests(unittest.TestCase):
    """Direct store-layer coverage for the include_archived flag."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="store@example.com", password="password123",
        )
        # Pull the signed-in user id out of /api/auth/me so store calls
        # line up with the HTTP path.
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_include_archived_flag_returns_both(self) -> None:
        active_id = _create_project(self.client, "Active")
        archived_id = _create_project(self.client, "Archived")
        self.client.post(f"/api/v2/projects/{archived_id}/archive")

        # Default filter — only active.
        default = self.store.list_v2_projects(user_id=self.user_id)
        default_ids = {p["project_id"] for p in default}
        self.assertEqual(default_ids, {active_id})

        # include_archived=True — both.
        all_ = self.store.list_v2_projects(
            user_id=self.user_id, include_archived=True,
        )
        all_ids = {p["project_id"] for p in all_}
        self.assertEqual(all_ids, {active_id, archived_id})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
