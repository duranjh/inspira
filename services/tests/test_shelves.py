"""HTTP + store tests for the Shelves feature.

Covers the five routes (``GET /api/v2/shelves``, create / update / delete,
and ``POST /api/v2/projects/{project_id}/shelve``) end-to-end against a
real FastAPI TestClient + isolated SQLite store. Also exercises the cross-
user IDOR defence — user B must not be able to see, move, rename, or delete
anything on user A's shelves (matching the same convention every v2 mutation
route already enforces for projects, topics, and decisions).

Shape contracts verified here:
  * ``project_count`` on each shelf in the list response (derived via JOIN,
    not user-supplied) — the frontend uses this for the "N projects" chip
    without a second round-trip.
  * Deleting a shelf un-shelves member projects (``shelf_id`` becomes NULL)
    rather than cascading to project deletion.
  * An empty-string ``shelf_id`` on the shelve route is treated as "un-shelve"
    rather than "find shelf with id=''" (HTML select quirk).

These tests use the shared ``make_test_app`` helper so they run against a
real FastAPI TestClient + an isolated SQLite store — no HTTP boundary
mocking, no OpenAI traffic (the planner adapter is a MagicMock that we
don't invoke).
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class ShelfLifecycleTests(unittest.TestCase):
    """Create → list → update → delete across a single user."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="shelves@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_shelf_then_list_shows_it_with_zero_projects(self) -> None:
        # Create a shelf.
        response = self.client.post(
            "/api/v2/shelves", json={"name": "The novel and its research"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        shelf = response.json()["shelf"]
        self.assertEqual(shelf["name"], "The novel and its research")
        self.assertEqual(shelf["project_count"], 0)
        self.assertIn("shelf_id", shelf)
        self.assertIn("sort_order", shelf)

        # List it back.
        listed = self.client.get("/api/v2/shelves")
        self.assertEqual(listed.status_code, 200, listed.text)
        shelves = listed.json()["shelves"]
        self.assertEqual(len(shelves), 1)
        self.assertEqual(shelves[0]["shelf_id"], shelf["shelf_id"])
        self.assertEqual(shelves[0]["name"], "The novel and its research")
        self.assertEqual(shelves[0]["project_count"], 0)

    def test_rename_shelf_with_empty_name_returns_400(self) -> None:
        created = self.client.post(
            "/api/v2/shelves", json={"name": "First draft"},
        ).json()["shelf"]
        shelf_id = created["shelf_id"]

        response = self.client.post(
            f"/api/v2/shelves/{shelf_id}/update",
            json={"name": "   "},
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "invalid_shelf_name")

        # And the name didn't change.
        listed = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(listed[0]["name"], "First draft")

    def test_create_shelf_with_empty_name_returns_400(self) -> None:
        response = self.client.post(
            "/api/v2/shelves", json={"name": ""},
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "invalid_shelf_name")

        # No phantom row landed.
        listed = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(listed, [])

    def test_rename_shelf_updates_name(self) -> None:
        created = self.client.post(
            "/api/v2/shelves", json={"name": "Side projects"},
        ).json()["shelf"]
        shelf_id = created["shelf_id"]

        renamed = self.client.post(
            f"/api/v2/shelves/{shelf_id}/update",
            json={"name": "All the side experiments"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(
            renamed.json()["shelf"]["name"], "All the side experiments",
        )

    def test_update_shelf_with_no_fields_returns_400(self) -> None:
        # Guard against silent no-op UPDATEs from a stale frontend payload.
        created = self.client.post(
            "/api/v2/shelves", json={"name": "Scratch"},
        ).json()["shelf"]
        response = self.client.post(
            f"/api/v2/shelves/{created['shelf_id']}/update", json={},
        )
        self.assertEqual(response.status_code, 400, response.text)


class MoveProjectToShelfTests(unittest.TestCase):
    """POST /api/v2/projects/{project_id}/shelve + project_count updates."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="shelf-mover@example.com", password="password123",
        )
        # Make a project we can move onto a shelf.
        created = self.client.post(
            "/api/v2/projects", json={"title": "My project"},
        ).json()
        self.project_id = created["project"]["project_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_move_project_to_shelf_updates_shelf_id_and_count(self) -> None:
        shelf = self.client.post(
            "/api/v2/shelves", json={"name": "Work"},
        ).json()["shelf"]

        # Move the project onto the shelf.
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": shelf["shelf_id"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        moved = response.json()["project"]
        self.assertEqual(moved["shelf_id"], shelf["shelf_id"])
        self.assertEqual(moved["project_id"], self.project_id)

        # Project listing reflects the new shelf_id.
        projects = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["shelf_id"], shelf["shelf_id"])

        # Shelf listing reflects the incremented project_count.
        shelves = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(len(shelves), 1)
        self.assertEqual(shelves[0]["project_count"], 1)

    def test_move_to_null_unshelves_project(self) -> None:
        shelf = self.client.post(
            "/api/v2/shelves", json={"name": "Ongoing"},
        ).json()["shelf"]
        self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": shelf["shelf_id"]},
        )

        # Un-shelve by sending explicit null.
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["project"]["shelf_id"])

        # Shelf's project_count dropped back to 0.
        shelves = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(shelves[0]["project_count"], 0)

    def test_empty_string_shelf_id_is_treated_as_unshelve(self) -> None:
        # The HTML <select> "unfiled" option often sends "" rather than
        # dropping the key — the route normalises that to None.
        shelf = self.client.post(
            "/api/v2/shelves", json={"name": "Reading"},
        ).json()["shelf"]
        self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": shelf["shelf_id"]},
        )

        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": ""},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["project"]["shelf_id"])


class DeleteShelfUnshelvesProjectsTests(unittest.TestCase):
    """Deleting a shelf must un-shelve its projects, not delete them."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="shelf-del@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_delete_shelf_unshelves_member_projects(self) -> None:
        shelf = self.client.post(
            "/api/v2/shelves", json={"name": "The startup"},
        ).json()["shelf"]
        first = self.client.post(
            "/api/v2/projects", json={"title": "Main idea"},
        ).json()["project"]
        second = self.client.post(
            "/api/v2/projects", json={"title": "Side experiment"},
        ).json()["project"]
        for project in (first, second):
            self.client.post(
                f"/api/v2/projects/{project['project_id']}/shelve",
                json={"shelf_id": shelf["shelf_id"]},
            )

        # Delete the shelf.
        response = self.client.post(
            f"/api/v2/shelves/{shelf['shelf_id']}/delete",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["deleted"])

        # Shelf is gone from the listing.
        shelves = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(shelves, [])

        # Projects are NOT deleted — just un-shelved (shelf_id=NULL).
        projects = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(projects), 2)
        for p in projects:
            self.assertIsNone(p["shelf_id"])


class CrossUserShelfIdorTests(unittest.TestCase):
    """Another user can't see / move / rename / delete someone else's shelf."""

    def setUp(self) -> None:
        self.alice, self.store, self.adapter, self.temp_dir = make_test_app()
        self.bob = TestClient(self.alice.app)

        signup_and_login(self.alice, email="alice@example.com", password="alice-pw-1")
        signup_and_login(self.bob, email="bob@example.com", password="bob-pw-123")

        # Alice has one shelf with one project on it.
        self.alice_shelf = self.alice.post(
            "/api/v2/shelves", json={"name": "Alice's bookshelf"},
        ).json()["shelf"]
        self.alice_project = self.alice.post(
            "/api/v2/projects", json={"title": "Alice's novel"},
        ).json()["project"]
        self.alice.post(
            f"/api/v2/projects/{self.alice_project['project_id']}/shelve",
            json={"shelf_id": self.alice_shelf["shelf_id"]},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bob_cannot_see_alice_shelf(self) -> None:
        # Bob's shelf listing is empty — it does NOT include alice's shelf.
        response = self.bob.get("/api/v2/shelves")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["shelves"], [])

    def test_bob_cannot_rename_alice_shelf(self) -> None:
        response = self.bob.post(
            f"/api/v2/shelves/{self.alice_shelf['shelf_id']}/update",
            json={"name": "Bob owns this now"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "shelf_not_found")

        # Alice sees the original name still.
        alice_shelves = self.alice.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(alice_shelves[0]["name"], "Alice's bookshelf")

    def test_bob_cannot_delete_alice_shelf(self) -> None:
        response = self.bob.post(
            f"/api/v2/shelves/{self.alice_shelf['shelf_id']}/delete",
        )
        self.assertEqual(response.status_code, 404, response.text)
        # Alice's shelf still exists.
        alice_shelves = self.alice.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(len(alice_shelves), 1)

    def test_bob_cannot_move_alice_project_onto_a_shelf(self) -> None:
        # Bob has his own shelf.
        bob_shelf = self.bob.post(
            "/api/v2/shelves", json={"name": "Bob's shelf"},
        ).json()["shelf"]

        # Bob tries to move alice's project onto his own shelf → 404.
        response = self.bob.post(
            f"/api/v2/projects/{self.alice_project['project_id']}/shelve",
            json={"shelf_id": bob_shelf["shelf_id"]},
        )
        self.assertEqual(response.status_code, 404, response.text)

        # Alice's project stays where it was.
        alice_projects = self.alice.get("/api/v2/projects").json()["projects"]
        moved = next(
            p for p in alice_projects
            if p["project_id"] == self.alice_project["project_id"]
        )
        self.assertEqual(moved["shelf_id"], self.alice_shelf["shelf_id"])

    def test_alice_cannot_put_project_on_bobs_shelf(self) -> None:
        # Symmetric case: alice tries to move her own project onto bob's
        # shelf. Ownership check fails on the shelf, not the project.
        bob_shelf = self.bob.post(
            "/api/v2/shelves", json={"name": "Bob's shelf"},
        ).json()["shelf"]
        response = self.alice.post(
            f"/api/v2/projects/{self.alice_project['project_id']}/shelve",
            json={"shelf_id": bob_shelf["shelf_id"]},
        )
        self.assertEqual(response.status_code, 404, response.text)


class ShelfOrderingTests(unittest.TestCase):
    """list_shelves returns shelves in sort_order order."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="shelf-order@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_multiple_shelves_listed_in_creation_order_by_default(self) -> None:
        # Create three shelves in order.
        for name in ("Alpha", "Beta", "Gamma"):
            resp = self.client.post("/api/v2/shelves", json={"name": name})
            self.assertEqual(resp.status_code, 201, resp.text)

        listed = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(len(listed), 3)
        # sort_order is strictly non-decreasing from the API's perspective.
        sort_orders = [s["sort_order"] for s in listed]
        self.assertEqual(sort_orders, sorted(sort_orders))

    def test_shelf_name_at_max_length_is_accepted(self) -> None:
        max_name = "A" * 80  # exactly MAX_SHELF_NAME_CHARS
        resp = self.client.post("/api/v2/shelves", json={"name": max_name})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["shelf"]["name"], max_name)

    def test_shelf_name_exceeding_max_length_is_rejected(self) -> None:
        # Pydantic enforces the 80-char cap at request-validation (422)
        # before the route handler runs. Either 400 or 422 is fine; the
        # invariant is that no row is ever created.
        too_long = "X" * 81  # one over the limit
        resp = self.client.post("/api/v2/shelves", json={"name": too_long})
        self.assertIn(resp.status_code, (400, 422), resp.text)

        # No row created.
        listed = self.client.get("/api/v2/shelves").json()["shelves"]
        self.assertEqual(listed, [])


class MoveProjectBetweenShelvesTests(unittest.TestCase):
    """Moving a project from one shelf directly onto another shelf."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="shelf-switch@example.com", password="password123",
        )
        created = self.client.post(
            "/api/v2/projects", json={"title": "Switchable project"},
        ).json()
        self.project_id = created["project"]["project_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_move_between_shelves_decrements_old_and_increments_new(self) -> None:
        shelf_a = self.client.post(
            "/api/v2/shelves", json={"name": "Shelf A"},
        ).json()["shelf"]
        shelf_b = self.client.post(
            "/api/v2/shelves", json={"name": "Shelf B"},
        ).json()["shelf"]

        # Place project on Shelf A.
        self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": shelf_a["shelf_id"]},
        )

        # Move directly to Shelf B without un-shelving first.
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/shelve",
            json={"shelf_id": shelf_b["shelf_id"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["project"]["shelf_id"], shelf_b["shelf_id"])

        # Counts must have updated.
        shelves = {
            s["shelf_id"]: s
            for s in self.client.get("/api/v2/shelves").json()["shelves"]
        }
        self.assertEqual(shelves[shelf_a["shelf_id"]]["project_count"], 0)
        self.assertEqual(shelves[shelf_b["shelf_id"]]["project_count"], 1)


class ShelfModuleUnitTests(unittest.TestCase):
    """Unit tests for shelves.py validation helpers (no HTTP layer)."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="shelf-unit@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validate_name_strips_whitespace(self) -> None:
        from planning_studio_service.shelves import _validate_name
        self.assertEqual(_validate_name("  My shelf  "), "My shelf")

    def test_validate_name_empty_after_strip_raises(self) -> None:
        from planning_studio_service.shelves import ShelfValidationError, _validate_name
        with self.assertRaises(ShelfValidationError):
            _validate_name("   ")

    def test_validate_name_too_long_raises(self) -> None:
        from planning_studio_service.shelves import (
            MAX_SHELF_NAME_CHARS,
            ShelfValidationError,
            _validate_name,
        )
        with self.assertRaises(ShelfValidationError):
            _validate_name("x" * (MAX_SHELF_NAME_CHARS + 1))

    def test_create_then_list_via_module_functions(self) -> None:
        from planning_studio_service import shelves as shelves_module
        shelf = shelves_module.create_shelf(
            self.store, user_id=self.user_id, name="Unit test shelf",
        )
        self.assertEqual(shelf["name"], "Unit test shelf")
        self.assertEqual(shelf["project_count"], 0)

        listed = shelves_module.list_shelves(self.store, user_id=self.user_id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["shelf_id"], shelf["shelf_id"])

    def test_rename_returns_none_for_missing_shelf(self) -> None:
        from planning_studio_service import shelves as shelves_module
        result = shelves_module.rename_shelf(
            self.store,
            shelf_id="does-not-exist",
            user_id=self.user_id,
            name="New name",
        )
        self.assertIsNone(result)

    def test_delete_returns_false_for_missing_shelf(self) -> None:
        from planning_studio_service import shelves as shelves_module
        result = shelves_module.delete_shelf(
            self.store,
            shelf_id="does-not-exist",
            user_id=self.user_id,
        )
        self.assertFalse(result)

    def test_list_shelves_user_scoped_never_returns_other_users_shelves(
        self,
    ) -> None:
        """Store-level isolation: listing shelves for user_id never returns
        another user's shelf row, even if their shelf was created first."""
        from planning_studio_service import shelves as shelves_module

        # Sign up a second user and create a shelf for them.
        self.client.post("/api/auth/logout")
        self.client.cookies.clear()
        signup_and_login(self.client, email="other-shelf@example.com")
        other_me = self.client.get("/api/auth/me").json()
        other_user_id = other_me["user_id"]

        shelves_module.create_shelf(
            self.store, user_id=other_user_id, name="Other user's shelf",
        )

        # Original user sees an empty list.
        result = shelves_module.list_shelves(self.store, user_id=self.user_id)
        self.assertEqual(result, [])

        # Other user sees their own shelf.
        result_other = shelves_module.list_shelves(
            self.store, user_id=other_user_id,
        )
        self.assertEqual(len(result_other), 1)
        self.assertEqual(result_other[0]["name"], "Other user's shelf")


if __name__ == "__main__":
    unittest.main()
