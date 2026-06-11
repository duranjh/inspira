"""Unit tests for the v4 workspaces package.

Covers the three pieces that ship in slice B2:
- ``workspaces.helpers`` (short_uid, slugify, make_personal_slug)
- ``workspaces.models`` (Role enum, role_at_least, body validators)
- ``workspaces.store`` (create / get / list / add / get_member /
  update_member_role / remove_member / default-workspace round-trip
  / create_workspace_for_signup / append_workspace_audit_event)

The store helpers run against a fresh isolated SQLite via the
shared ``make_test_app()`` fixture from ``_helpers.py``. The
TestClient + adapter handles are unused here — we exercise the
store directly. The dependency + router + endpoint tests land in
slice B3.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from planning_studio_service.workspaces import (
    CreateWorkspaceBody,
    InviteMemberBody,
    Role,
    Workspace,
    WorkspaceMember,
    role_at_least,
)
from planning_studio_service.workspaces.helpers import (
    make_personal_slug,
    short_uid,
    slugify,
)
from planning_studio_service.workspaces.store import (
    LastOwnerError,
    WorkspaceSlugExists,
    add_member,
    append_workspace_audit_event,
    create_workspace,
    create_workspace_for_signup,
    get_member,
    get_user_default_workspace_id,
    get_workspace,
    list_members,
    list_workspaces_for_user,
    remove_member,
    set_user_default_workspace_id,
    slug_exists,
    update_member_role,
)

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class HelpersTests(unittest.TestCase):
    """Pure helpers — no DB."""

    def test_short_uid_default_prefix_and_length(self) -> None:
        ws_id = short_uid()
        self.assertTrue(ws_id.startswith("ws-"))
        self.assertEqual(len(ws_id), 3 + 10)  # 'ws-' + 10 hex
        # All hex after prefix.
        int(ws_id[3:], 16)

    def test_short_uid_custom_prefix(self) -> None:
        run_id = short_uid("run-", 10)
        self.assertTrue(run_id.startswith("run-"))
        self.assertEqual(len(run_id), 4 + 10)

    def test_short_uid_unique(self) -> None:
        ids = {short_uid() for _ in range(50)}
        # 50 random 40-bit IDs should not collide. If they do, we'd
        # need a higher entropy default.
        self.assertEqual(len(ids), 50)

    def test_short_uid_handles_odd_length(self) -> None:
        # n=11 (odd) should still produce 11-char suffix, not 12.
        x = short_uid("x-", 11)
        self.assertEqual(len(x), 2 + 11)

    def test_slugify_lowercases_and_replaces_invalid(self) -> None:
        self.assertEqual(slugify("Acme Corp"), "acme-corp")
        self.assertEqual(slugify("Acme & Co!"), "acme-co")

    def test_slugify_collapses_dashes_and_trims(self) -> None:
        self.assertEqual(slugify("--Acme---Corp--"), "acme-corp")

    def test_slugify_truncates_to_40_chars(self) -> None:
        long_name = "a" * 100
        result = slugify(long_name)
        self.assertEqual(len(result), 40)
        self.assertEqual(result, "a" * 40)

    def test_slugify_fallback_for_empty(self) -> None:
        self.assertEqual(slugify("***"), "workspace")
        self.assertEqual(slugify(""), "workspace")
        self.assertEqual(slugify("   "), "workspace")

    def test_make_personal_slug(self) -> None:
        self.assertEqual(
            make_personal_slug("user-abc12345def6"), "personal-abc12345"
        )
        # No 'user-' prefix → use the raw id directly.
        self.assertEqual(
            make_personal_slug("plain12345"), "personal-plain123"
        )


class RoleAndModelsTests(unittest.TestCase):
    """Role rank + Pydantic validators."""

    def test_role_at_least_owner_above_all(self) -> None:
        for low in (Role.viewer, Role.member, Role.admin, Role.owner):
            self.assertTrue(role_at_least(Role.owner, low))

    def test_role_at_least_strict_ordering(self) -> None:
        self.assertTrue(role_at_least(Role.admin, Role.member))
        self.assertTrue(role_at_least(Role.member, Role.viewer))
        self.assertFalse(role_at_least(Role.viewer, Role.member))
        self.assertFalse(role_at_least(Role.member, Role.admin))

    def test_create_workspace_body_accepts_good_slug(self) -> None:
        body = CreateWorkspaceBody(slug="acme-corp", name="Acme Corp")
        self.assertEqual(body.slug, "acme-corp")
        self.assertEqual(body.name, "Acme Corp")

    def test_create_workspace_body_rejects_uppercase(self) -> None:
        with self.assertRaises(ValidationError):
            CreateWorkspaceBody(slug="Acme", name="Acme")

    def test_create_workspace_body_rejects_leading_dash(self) -> None:
        with self.assertRaises(ValidationError):
            CreateWorkspaceBody(slug="-acme", name="Acme")

    def test_create_workspace_body_rejects_short_slug(self) -> None:
        with self.assertRaises(ValidationError):
            CreateWorkspaceBody(slug="ab", name="Ab")

    def test_create_workspace_body_rejects_personal_reserved(self) -> None:
        with self.assertRaises(ValidationError):
            CreateWorkspaceBody(
                slug="personal-someone", name="Stolen Personal"
            )

    def test_invite_member_body_role_enum_coerced(self) -> None:
        body = InviteMemberBody(email="u@example.com", role="admin")
        self.assertEqual(body.role, Role.admin)


class StoreSetUpMixin:
    """Shared setUp/tearDown — fresh isolated store per test."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Sign up two users we'll use across the tests. The signup
        # helper persists session cookies on self.client; we don't
        # need them here but we DO need the user_ids.
        owner = signup_and_login(
            self.client,
            email="owner@acme.com",
            password="password123",
            display_name="Owner",
        )
        self.owner_user_id: str = owner["user_id"]
        # Signup auto-logs the new user in, replacing the session
        # cookie. To create a second account we just sign up again
        # on the SAME client — the session cookie gets replaced.
        member = signup_and_login(
            self.client,
            email="member@acme.com",
            password="password123",
            display_name="Member",
        )
        self.member_user_id: str = member["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class CreateWorkspaceTests(StoreSetUpMixin, unittest.TestCase):

    def test_create_workspace_returns_workspace_with_owner_membership(self) -> None:
        ws = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme Corp",
        )
        self.assertIsInstance(ws, Workspace)
        self.assertTrue(ws.workspace_id.startswith("ws-"))
        self.assertEqual(ws.slug, "acme")
        self.assertEqual(ws.plan_tier, "free")
        self.assertEqual(ws.billing_owner_user_id, self.owner_user_id)

        member = get_member(
            self.store,
            workspace_id=ws.workspace_id,
            user_id=self.owner_user_id,
        )
        self.assertIsNotNone(member)
        assert member is not None
        self.assertEqual(member.role, Role.owner)

    def test_create_workspace_slug_collision_raises(self) -> None:
        create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme Corp",
        )
        with self.assertRaises(WorkspaceSlugExists):
            create_workspace(
                self.store,
                owner_user_id=self.member_user_id,
                slug="acme",
                name="Acme Two",
            )

    def test_get_workspace_returns_none_when_absent(self) -> None:
        self.assertIsNone(get_workspace(self.store, "ws-bogus"))

    def test_slug_exists_round_trip(self) -> None:
        self.assertFalse(slug_exists(self.store, "acme"))
        create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )
        self.assertTrue(slug_exists(self.store, "acme"))


class ListWorkspacesTests(StoreSetUpMixin, unittest.TestCase):

    def test_list_workspaces_for_user_includes_role(self) -> None:
        ws = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )
        add_member(
            self.store,
            workspace_id=ws.workspace_id,
            user_id=self.member_user_id,
            role=Role.member,
            invited_by=self.owner_user_id,
        )
        owner_list = list_workspaces_for_user(
            self.store, self.owner_user_id
        )
        member_list = list_workspaces_for_user(
            self.store, self.member_user_id
        )
        self.assertEqual(len(owner_list), 1)
        self.assertEqual(owner_list[0].role, Role.owner)
        self.assertEqual(len(member_list), 1)
        self.assertEqual(member_list[0].role, Role.member)
        self.assertEqual(
            owner_list[0].workspace_id, member_list[0].workspace_id
        )

    def test_list_workspaces_for_user_excludes_archived(self) -> None:
        ws = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )
        # Archive the workspace via direct SQL (no store API for this in
        # W1; comes in W6 with the enterprise-archive flow).
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE workspaces SET archived_at = ? WHERE workspace_id = ?",
                ("2026-05-02T00:00:00+00:00", ws.workspace_id),
            )
            conn.commit()
        self.assertEqual(
            list_workspaces_for_user(self.store, self.owner_user_id), []
        )


class MembershipTests(StoreSetUpMixin, unittest.TestCase):

    def setUp(self) -> None:
        super().setUp()
        self.workspace = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )

    def test_add_member_idempotent_on_pk(self) -> None:
        first = add_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.member,
            invited_by=self.owner_user_id,
        )
        # Second call with the same PK returns the existing row, not
        # an error — ergonomic for invite-replay flows.
        second = add_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.viewer,  # Different role — should NOT change.
            invited_by=self.owner_user_id,
        )
        self.assertEqual(first.role, Role.member)
        self.assertEqual(second.role, Role.member)  # Original wins.

    def test_get_member_returns_none_when_absent(self) -> None:
        self.assertIsNone(
            get_member(
                self.store,
                workspace_id=self.workspace.workspace_id,
                user_id="user-bogus",
            )
        )

    def test_list_members_returns_all_members(self) -> None:
        add_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.member,
        )
        rows = list_members(self.store, self.workspace.workspace_id)
        self.assertEqual(len(rows), 2)
        # Order by created_at ASC is best-effort — two rows added
        # within the same wall-clock second tie unpredictably. Verify
        # by user_id instead.
        by_uid = {m.user_id: m.role for m in rows}
        self.assertEqual(by_uid[self.owner_user_id], Role.owner)
        self.assertEqual(by_uid[self.member_user_id], Role.member)

    def test_update_member_role_happy_path(self) -> None:
        add_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.member,
        )
        updated = update_member_role(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.admin,
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.role, Role.admin)

    def test_update_member_role_returns_none_when_absent(self) -> None:
        self.assertIsNone(
            update_member_role(
                self.store,
                workspace_id=self.workspace.workspace_id,
                user_id="user-bogus",
                role=Role.admin,
            )
        )

    def test_update_member_role_blocks_last_owner_demotion(self) -> None:
        with self.assertRaises(LastOwnerError):
            update_member_role(
                self.store,
                workspace_id=self.workspace.workspace_id,
                user_id=self.owner_user_id,
                role=Role.admin,
            )

    def test_update_member_role_allows_demotion_when_other_owner_exists(self) -> None:
        add_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.owner,
        )
        updated = update_member_role(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.owner_user_id,
            role=Role.admin,
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.role, Role.admin)

    def test_remove_member_happy_path(self) -> None:
        add_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
            role=Role.member,
        )
        removed = remove_member(
            self.store,
            workspace_id=self.workspace.workspace_id,
            user_id=self.member_user_id,
        )
        self.assertTrue(removed)
        self.assertIsNone(
            get_member(
                self.store,
                workspace_id=self.workspace.workspace_id,
                user_id=self.member_user_id,
            )
        )

    def test_remove_member_blocks_last_owner_removal(self) -> None:
        with self.assertRaises(LastOwnerError):
            remove_member(
                self.store,
                workspace_id=self.workspace.workspace_id,
                user_id=self.owner_user_id,
            )

    def test_remove_member_returns_false_when_absent(self) -> None:
        self.assertFalse(
            remove_member(
                self.store,
                workspace_id=self.workspace.workspace_id,
                user_id="user-bogus",
            )
        )


class DefaultWorkspaceTests(StoreSetUpMixin, unittest.TestCase):

    def test_set_and_get_default_workspace(self) -> None:
        ws = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )
        self.assertIsNone(
            get_user_default_workspace_id(self.store, self.owner_user_id)
        )
        set_user_default_workspace_id(
            self.store,
            user_id=self.owner_user_id,
            workspace_id=ws.workspace_id,
        )
        self.assertEqual(
            get_user_default_workspace_id(self.store, self.owner_user_id),
            ws.workspace_id,
        )

    def test_create_workspace_for_signup_sets_default(self) -> None:
        ws = create_workspace_for_signup(
            self.store,
            user_id=self.owner_user_id,
            display_name="Owner",
        )
        self.assertTrue(ws.slug.startswith("personal-"))
        self.assertEqual(ws.plan_tier, "free")
        self.assertEqual(
            get_user_default_workspace_id(self.store, self.owner_user_id),
            ws.workspace_id,
        )

    def test_create_workspace_for_signup_does_not_overwrite_existing_default(self) -> None:
        first = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )
        set_user_default_workspace_id(
            self.store,
            user_id=self.owner_user_id,
            workspace_id=first.workspace_id,
        )
        # Now create a personal workspace; it should NOT clobber the
        # already-set default.
        create_workspace_for_signup(
            self.store,
            user_id=self.owner_user_id,
            display_name="Owner",
        )
        self.assertEqual(
            get_user_default_workspace_id(self.store, self.owner_user_id),
            first.workspace_id,
        )


class AuditEventTests(StoreSetUpMixin, unittest.TestCase):

    def test_append_workspace_audit_event_writes_workspace_id(self) -> None:
        ws = create_workspace(
            self.store,
            owner_user_id=self.owner_user_id,
            slug="acme",
            name="Acme",
        )
        append_workspace_audit_event(
            self.store,
            workspace_id=ws.workspace_id,
            actor_user_id=self.owner_user_id,
            category="workspace",
            action="member.invited",
            subject_id="user-pending",
        )
        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, actor_user_id, category, action,
                       subject_id
                FROM audit_log
                WHERE category = 'workspace' AND action = 'member.invited'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row[0], ws.workspace_id)
        self.assertEqual(row[1], self.owner_user_id)
        self.assertEqual(row[2], "workspace")
        self.assertEqual(row[3], "member.invited")
        self.assertEqual(row[4], "user-pending")

    def test_append_workspace_audit_event_swallows_errors(self) -> None:
        # Even if the underlying store call fails, the helper logs +
        # returns rather than propagating — audit is never load-bearing.
        # PlanningStudioStore uses slots=True so patch.object on the
        # instance fails; patch the class instead.
        with patch.object(
            type(self.store),
            "append_audit_event",
            side_effect=RuntimeError("boom"),
        ):
            # Should NOT raise.
            append_workspace_audit_event(
                self.store,
                workspace_id="ws-anything",
                actor_user_id=self.owner_user_id,
                category="workspace",
                action="test",
            )


if __name__ == "__main__":
    unittest.main()
