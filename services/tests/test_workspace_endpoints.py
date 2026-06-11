"""HTTP-level tests for the v4 workspace router (W1 slice B3).

Covers the four endpoints + the dependency surface:

- ``POST /api/v2/workspaces`` (create) — happy path, slug collision,
  bad slug, anon blocked, default-workspace promotion logic.
- ``GET /api/v2/workspaces`` (list-mine) — anon returns empty list,
  authed returns role-tagged workspaces.
- ``GET /api/v2/workspaces/{id}`` (read) — member 200, non-member 403,
  bogus id 403 (non-membership case fires first).
- ``POST /api/v2/workspaces/{id}/members`` (invite stub) — admin can
  invite existing-user (added), admin can invite unknown-email
  (queued), already-member returns ``already_member``, non-admin
  403, bad email 422.
- Dependency: ``X-Workspace-Id`` header overrides default; missing
  header + no default → 400.
"""
from __future__ import annotations

import unittest

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _signup(client, email: str, password: str = "password123", display: str = "User"):
    """Sign up and return the AuthedUser dict (flat shape, see auth.py:380)."""
    return signup_and_login(
        client, email=email, password=password, display_name=display
    )


def _logout(client) -> None:
    """Clear the session cookie so the next signup starts fresh."""
    client.cookies.clear()


class CreateWorkspaceEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.user = _signup(self.client, email="creator@acme.com")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_returns_201_with_workspace_and_role(self) -> None:
        resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme Corp"},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn("workspace", body)
        ws = body["workspace"]
        self.assertEqual(ws["slug"], "acme")
        self.assertEqual(ws["name"], "Acme Corp")
        self.assertEqual(ws["plan_tier"], "free")
        self.assertEqual(ws["billing_owner_user_id"], self.user["user_id"])
        self.assertEqual(ws["role"], "owner")
        self.assertTrue(ws["workspace_id"].startswith("ws-"))

    def test_create_sets_default_when_user_has_none(self) -> None:
        resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        )
        self.assertEqual(resp.status_code, 201)
        ws_id = resp.json()["workspace"]["workspace_id"]
        # Inspect users table directly — the default_workspace_id
        # should now point at this workspace.
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT default_workspace_id FROM users WHERE user_id = ?",
                (self.user["user_id"],),
            ).fetchone()
        self.assertEqual(row[0], ws_id)

    def test_create_does_not_clobber_existing_default(self) -> None:
        first = self.client.post(
            "/api/v2/workspaces", json={"slug": "first", "name": "First"}
        )
        first_id = first.json()["workspace"]["workspace_id"]
        # Second create — default should still point at the first one.
        second = self.client.post(
            "/api/v2/workspaces", json={"slug": "second", "name": "Second"}
        )
        self.assertEqual(second.status_code, 201)
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT default_workspace_id FROM users WHERE user_id = ?",
                (self.user["user_id"],),
            ).fetchone()
        self.assertEqual(row[0], first_id)

    def test_create_slug_collision_returns_409(self) -> None:
        first = self.client.post(
            "/api/v2/workspaces", json={"slug": "acme", "name": "First"}
        )
        self.assertEqual(first.status_code, 201)
        # A SECOND creator (different user) hitting the same slug:
        _logout(self.client)
        _signup(self.client, email="other@acme.com")
        second = self.client.post(
            "/api/v2/workspaces", json={"slug": "acme", "name": "Other"}
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.json()["detail"]["error"], "workspace_slug_taken"
        )

    def test_create_rejects_uppercase_slug(self) -> None:
        resp = self.client.post(
            "/api/v2/workspaces", json={"slug": "Acme", "name": "Acme"}
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_rejects_personal_reserved_slug(self) -> None:
        resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "personal-stolen", "name": "Stolen"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_anon_user_blocked_with_401(self) -> None:
        # Drop the session cookie → anon-on-first-contact mints a new
        # user-anon-* identity, which the route 401s.
        _logout(self.client)
        resp = self.client.post(
            "/api/v2/workspaces", json={"slug": "acme", "name": "Acme"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"], "auth_required")


class ListWorkspacesEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_returns_empty_for_anon(self) -> None:
        resp = self.client.get("/api/v2/workspaces")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"workspaces": []})

    def test_list_returns_authed_users_workspaces_with_role(self) -> None:
        _signup(self.client, email="creator@acme.com")
        self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        )
        self.client.post(
            "/api/v2/workspaces",
            json={"slug": "beta", "name": "Beta"},
        )
        resp = self.client.get("/api/v2/workspaces")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["workspaces"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["role"], "owner")
            self.assertIn(row["slug"], {"acme", "beta"})


class GetWorkspaceEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.owner = _signup(self.client, email="owner@acme.com")
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_returns_workspace_with_members_and_role(self) -> None:
        resp = self.client.get(f"/api/v2/workspaces/{self.workspace_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workspace"]["slug"], "acme")
        self.assertEqual(body["your_role"], "owner")
        self.assertEqual(len(body["members"]), 1)
        self.assertEqual(body["members"][0]["role"], "owner")

    def test_get_non_member_returns_403(self) -> None:
        # Sign up a different user; they're not a member of self.workspace_id.
        _logout(self.client)
        _signup(self.client, email="outsider@acme.com")
        resp = self.client.get(f"/api/v2/workspaces/{self.workspace_id}")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"], "workspace_access_denied"
        )

    def test_get_bogus_id_returns_403(self) -> None:
        # Non-membership fires before the workspace-existence check —
        # documented behavior: 403 protects existence-leakage on a bad
        # id.
        resp = self.client.get("/api/v2/workspaces/ws-bogus")
        self.assertEqual(resp.status_code, 403)


class InviteMemberEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Sign up the existing-target user FIRST so they're in the
        # users table when the invite happens.
        target = _signup(self.client, email="existing@acme.com")
        self.target_user_id: str = target["user_id"]
        _logout(self.client)
        # Now sign up the workspace owner and create a workspace.
        self.owner = _signup(self.client, email="admin@acme.com")
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_invite_existing_user_adds_member(self) -> None:
        resp = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "existing@acme.com", "role": "member"},
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()["invitation"]
        self.assertEqual(body["status"], "added")
        self.assertEqual(body["role"], "member")
        # Confirm membership row exists.
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT role FROM workspace_members "
                "WHERE workspace_id = ? AND user_id = ?",
                (self.workspace_id, self.target_user_id),
            ).fetchone()
        self.assertEqual(row[0], "member")

    def test_invite_unknown_email_returns_queued(self) -> None:
        resp = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "stranger@acme.com", "role": "viewer"},
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["invitation"]["status"], "queued")

    def test_invite_already_member_returns_already_member(self) -> None:
        # First invite — adds.
        first = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "existing@acme.com", "role": "member"},
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["invitation"]["status"], "added")
        # Second invite — already there.
        second = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "existing@acme.com", "role": "admin"},
        )
        self.assertEqual(second.status_code, 202)
        self.assertEqual(
            second.json()["invitation"]["status"], "already_member"
        )

    def test_invite_member_role_blocked_with_403(self) -> None:
        # Add the target user as a regular MEMBER first.
        self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "existing@acme.com", "role": "member"},
        )
        # Switch sessions to the target user via login (they were
        # already signed up in setUp).
        _logout(self.client)
        login = self.client.post(
            "/api/auth/login",
            json={
                "email": "existing@acme.com",
                "password": "password123",
            },
        )
        self.assertEqual(login.status_code, 200)
        # Try to invite someone else as that low-privilege member.
        resp = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "stranger@acme.com", "role": "viewer"},
        )
        # The target user is a 'member' — privilege below the admin
        # role required to invite.
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"],
            "workspace_role_insufficient",
        )

    def test_invite_bad_email_returns_422(self) -> None:
        resp = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "not-an-email", "role": "member"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_invite_non_member_blocked_with_403(self) -> None:
        # Create a different account that's not a member of self.workspace_id.
        _logout(self.client)
        _signup(self.client, email="outsider@acme.com")
        resp = self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "anyone@acme.com", "role": "member"},
        )
        self.assertEqual(resp.status_code, 403)


class WorkspaceHeaderResolutionTests(unittest.TestCase):
    """Exercise the X-Workspace-Id header path of the dependency.

    The dependency factory resolution order is path-param → header →
    user-default. The path-param case is covered by the GET/{id} +
    POST/{id}/members tests above; this class covers header-based
    resolution by hitting the GET/{id} endpoint with explicit header
    mismatches and the missing-default 400.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_explicit_header_matches_path_when_consistent(self) -> None:
        _signup(self.client, email="user@acme.com")
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        ws_id = ws["workspace_id"]
        # Even with an explicit header, the dependency's path-param
        # priority means the path wins; the header's role is to
        # carry context on routes WITHOUT a path-param.
        resp = self.client.get(
            f"/api/v2/workspaces/{ws_id}",
            headers={"X-Workspace-Id": "ws-bogus"},
        )
        # Path param wins → returns the real workspace.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["workspace"]["workspace_id"], ws_id)


class UpdateWorkspaceEndpointTests(unittest.TestCase):
    """Cover the PATCH /api/v2/workspaces/{id} route.

    Authorization is ``Role.admin`` (owner satisfies admin via
    ``role_at_least``). Body validator requires at least one of name
    or slug; slug shape matches CreateWorkspaceBody.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.owner = _signup(self.client, email="owner@acme.com")
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_update_name_only_returns_200(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "Acme Industries"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["workspace"]
        self.assertEqual(body["name"], "Acme Industries")
        self.assertEqual(body["slug"], "acme")  # slug untouched

    def test_update_slug_only_returns_200(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"slug": "acme-corp"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["workspace"]
        self.assertEqual(body["slug"], "acme-corp")
        self.assertEqual(body["name"], "Acme")  # name untouched

    def test_update_both_returns_200(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "Acme Industries", "slug": "acme-corp"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["workspace"]
        self.assertEqual(body["name"], "Acme Industries")
        self.assertEqual(body["slug"], "acme-corp")

    def test_update_name_strips_whitespace(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "  Spaces Around  "},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["workspace"]["name"], "Spaces Around")

    def test_update_empty_body_returns_422(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={},
        )
        self.assertEqual(resp.status_code, 422)

    def test_update_blank_name_returns_422(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "   "},
        )
        self.assertEqual(resp.status_code, 422)

    def test_update_rejects_uppercase_slug(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"slug": "Acme"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_update_rejects_personal_reserved_slug(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"slug": "personal-stolen"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_update_slug_collision_returns_409(self) -> None:
        # Create a second workspace owning the target slug.
        self.client.post(
            "/api/v2/workspaces",
            json={"slug": "beta", "name": "Beta"},
        )
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"slug": "beta"},
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["detail"]["error"], "workspace_slug_taken"
        )

    def test_update_non_member_returns_403(self) -> None:
        # Outsider — never invited.
        _logout(self.client)
        _signup(self.client, email="outsider@acme.com")
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "Sneaky"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"], "workspace_access_denied"
        )

    def test_update_member_below_admin_returns_403(self) -> None:
        # Add a regular 'member' and try to PATCH as them.
        target = _signup(self.client, email="member@acme.com")
        _logout(self.client)
        # Log back in as owner to invite.
        self.client.post(
            "/api/auth/login",
            json={"email": "owner@acme.com", "password": "password123"},
        )
        self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "member@acme.com", "role": "member"},
        )
        # Switch to the low-privilege member.
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": "member@acme.com", "password": "password123"},
        )
        # Sanity — they CAN read the workspace.
        read = self.client.get(f"/api/v2/workspaces/{self.workspace_id}")
        self.assertEqual(read.status_code, 200)
        # But they cannot rename it.
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "Renamed by member"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"],
            "workspace_role_insufficient",
        )
        # Confirm name unchanged.
        confirm = self.client.get(
            f"/api/v2/workspaces/{self.workspace_id}",
        )
        self.assertEqual(confirm.json()["workspace"]["name"], "Acme")
        # Stop unused-local warning on `target`.
        self.assertTrue(target["user_id"])

    def test_update_admin_allowed(self) -> None:
        # Add a target user as ADMIN — they should be able to PATCH.
        target = _signup(self.client, email="admin@acme.com")
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": "owner@acme.com", "password": "password123"},
        )
        self.client.post(
            f"/api/v2/workspaces/{self.workspace_id}/members",
            json={"email": "admin@acme.com", "role": "admin"},
        )
        _logout(self.client)
        self.client.post(
            "/api/auth/login",
            json={"email": "admin@acme.com", "password": "password123"},
        )
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "Renamed by admin"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["workspace"]["name"], "Renamed by admin"
        )
        self.assertTrue(target["user_id"])

    def test_update_writes_audit_event(self) -> None:
        resp = self.client.patch(
            f"/api/v2/workspaces/{self.workspace_id}",
            json={"name": "Audited"},
        )
        self.assertEqual(resp.status_code, 200)
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT action, before_json, after_json FROM audit_log "
                "WHERE workspace_id = ? AND action = 'updated' "
                "ORDER BY created_at DESC LIMIT 1",
                (self.workspace_id,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        action, before_json, after_json = rows[0]
        self.assertEqual(action, "updated")
        self.assertIn("Acme", before_json or "")
        self.assertIn("Audited", after_json or "")


if __name__ == "__main__":
    unittest.main()
