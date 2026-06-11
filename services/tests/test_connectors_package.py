"""Unit tests for the v4 connectors package (W2 C1).

Covers:
- ``connectors.base`` — ConnectorTier enum, descriptor frozenness,
  ConnectorState defaults.
- ``connectors.registry`` — three-tier shape, mailto routes on the
  coming-soon entries, no contact_route leak on LIVE/FUTURE,
  ``descriptor_for(slug)`` lookup + None on miss.
- ``connectors.store`` — credential upsert (idempotent on PK),
  workspace-scoping (two workspaces, same provider, isolated
  rows), repo_snapshots upsert, sync_runs lifecycle, orphan
  reconciler, ``state_for()`` composite (not_connected /
  connected / error / needs_reauth).

Endpoint tests live in test_connectors_endpoints.py.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from planning_studio_service.connectors import (
    ConnectorDescriptor,
    ConnectorState,
    ConnectorTier,
)
from planning_studio_service.connectors.base import ConnectorStatus
from planning_studio_service.connectors import registry
from planning_studio_service.connectors.store import (
    count_repo_snapshots,
    delete_credential,
    finish_sync_run,
    get_credential,
    latest_successful_sync_run,
    latest_sync_run,
    list_repo_snapshots,
    mark_credential_status,
    reconcile_orphaned_runs,
    start_sync_run,
    state_for,
    upsert_credential,
    upsert_repo_snapshot,
    workspaces_with_active_credential,
)
from planning_studio_service.workspaces.models import Role
from planning_studio_service.workspaces.store import create_workspace

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class RegistryTests(unittest.TestCase):
    """Static descriptor list — no DB."""

    def test_three_tiers_have_expected_counts(self) -> None:
        self.assertEqual(len(registry.LIVE), 3)  # github, linear, csv_json
        self.assertEqual(len(registry.COMING_SOON), 4)  # intercom, productboard, salesforce, helpscout
        self.assertEqual(len(registry.FUTURE), 3)  # jira, zendesk, notion

    def test_live_entries_carry_logo_slug_no_contact_route(self) -> None:
        for d in registry.LIVE:
            self.assertEqual(d.tier, ConnectorTier.live)
            self.assertIsNotNone(d.logo_slug)
            self.assertIsNone(d.contact_route)

    def test_coming_soon_entries_carry_mailto_no_logo(self) -> None:
        for d in registry.COMING_SOON:
            self.assertEqual(d.tier, ConnectorTier.coming_soon)
            self.assertIsNone(d.logo_slug)
            self.assertIsNotNone(d.contact_route)
            self.assertTrue(d.contact_route.startswith("mailto:"))

    def test_future_entries_have_no_actions(self) -> None:
        for d in registry.FUTURE:
            self.assertEqual(d.tier, ConnectorTier.future)
            self.assertIsNone(d.logo_slug)
            self.assertIsNone(d.contact_route)

    def test_descriptor_for_lookup(self) -> None:
        github = registry.descriptor_for("github")
        self.assertIsNotNone(github)
        assert github is not None
        self.assertEqual(github.tier, ConnectorTier.live)

    def test_descriptor_for_missing_returns_none(self) -> None:
        self.assertIsNone(registry.descriptor_for("nonexistent"))

    def test_descriptor_is_frozen(self) -> None:
        github = registry.GITHUB
        with self.assertRaises(Exception):
            github.summary = "tampered"  # type: ignore[misc]

    def test_no_partner_logos_or_usage_claims_in_summaries(self) -> None:
        # Capability-vs-usage rule: descriptor summaries describe
        # capabilities, never usage. Banned tokens per project memory.
        forbidden = (
            "trusted by",
            "fortune",
            "500+",
            "deloitte",
            "ey ",
            "snapchat",
            "faang",
            "users find",
            "are using",
            "dogfooding",
        )
        for d in (
            list(registry.LIVE)
            + list(registry.COMING_SOON)
            + list(registry.FUTURE)
        ):
            haystack = d.summary.lower()
            for token in forbidden:
                self.assertNotIn(
                    token,
                    haystack,
                    f"forbidden token {token!r} in {d.provider} summary",
                )


class StoreSetUp(unittest.TestCase):
    """Shared setUp: fresh isolated store + one workspace owned by the
    test user."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        owner = signup_and_login(
            self.client,
            email="owner@acme.com",
            password="password123",
            display_name="Owner",
        )
        self.user_id: str = owner["user_id"]
        self.workspace = create_workspace(
            self.store,
            owner_user_id=self.user_id,
            slug="acme",
            name="Acme",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class CredentialStoreTests(StoreSetUp):

    def test_upsert_credential_inserts(self) -> None:
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ciphertext-1",
            installation_id="install-1",
            account_login="acme-corp",
            scopes=["repo:read"],
        )
        cred = get_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertIsNotNone(cred)
        assert cred is not None
        self.assertEqual(cred["account_login"], "acme-corp")
        self.assertEqual(cred["status"], "connected")

    def test_upsert_credential_replaces_on_pk_collision(self) -> None:
        # First upsert — initial creds.
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ciphertext-1",
            installation_id="install-1",
            account_login="acme-corp",
        )
        # Second upsert (re-OAuth) — replaces.
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ciphertext-2",
            installation_id="install-2",
            account_login="acme-corp-new",
        )
        cred = get_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        assert cred is not None
        self.assertEqual(cred["encrypted_token"], "ciphertext-2")
        self.assertEqual(cred["installation_id"], "install-2")
        self.assertEqual(cred["account_login"], "acme-corp-new")

    def test_credentials_are_workspace_scoped_not_user_scoped(self) -> None:
        """One user, two workspaces, same provider → two distinct rows.

        This is the watch-point #1 from W2: a user who's a member of
        two workspaces and connects GitHub on each must produce two
        separate encrypted token rows. Composite PK on
        (workspace_id, provider) enforces this at the schema layer;
        this test pins the behavior end-to-end.
        """
        beta = create_workspace(
            self.store,
            owner_user_id=self.user_id,
            slug="beta",
            name="Beta",
        )
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="acme-token",
            installation_id="acme-install",
            account_login="acme-corp",
        )
        upsert_credential(
            self.store,
            workspace_id=beta.workspace_id,
            provider="github",
            encrypted_token="beta-token",
            installation_id="beta-install",
            account_login="beta-org",
        )
        acme_cred = get_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        beta_cred = get_credential(
            self.store,
            workspace_id=beta.workspace_id,
            provider="github",
        )
        assert acme_cred is not None
        assert beta_cred is not None
        self.assertNotEqual(
            acme_cred["encrypted_token"], beta_cred["encrypted_token"]
        )
        self.assertEqual(acme_cred["account_login"], "acme-corp")
        self.assertEqual(beta_cred["account_login"], "beta-org")

    def test_mark_status(self) -> None:
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct",
        )
        mark_credential_status(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            status="needs_reauth",
        )
        cred = get_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        assert cred is not None
        self.assertEqual(cred["status"], "needs_reauth")

    def test_delete_credential(self) -> None:
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct",
        )
        deleted = delete_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertTrue(deleted)
        self.assertIsNone(
            get_credential(
                self.store,
                workspace_id=self.workspace.workspace_id,
                provider="github",
            )
        )

    def test_workspaces_with_active_credential_excludes_revoked(self) -> None:
        beta = create_workspace(
            self.store,
            owner_user_id=self.user_id,
            slug="beta",
            name="Beta",
        )
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct1",
        )
        upsert_credential(
            self.store,
            workspace_id=beta.workspace_id,
            provider="github",
            encrypted_token="ct2",
        )
        mark_credential_status(
            self.store,
            workspace_id=beta.workspace_id,
            provider="github",
            status="revoked",
        )
        active = workspaces_with_active_credential(self.store, "github")
        self.assertEqual(active, [self.workspace.workspace_id])


class RepoSnapshotTests(StoreSetUp):

    def test_upsert_and_list(self) -> None:
        for repo_id in ("1001", "1002", "1003"):
            upsert_repo_snapshot(
                self.store,
                workspace_id=self.workspace.workspace_id,
                provider="github",
                repo_id=repo_id,
                repo_full_name=f"acme-corp/repo-{repo_id}",
                default_branch="main",
                visibility="private",
                snapshot={"tree": [], "issues": [], "commits": []},
            )
        rows = list_repo_snapshots(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            count_repo_snapshots(
                self.store,
                workspace_id=self.workspace.workspace_id,
                provider="github",
            ),
            3,
        )

    def test_upsert_replaces_on_pk_collision(self) -> None:
        upsert_repo_snapshot(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            repo_id="1001",
            repo_full_name="acme-corp/old-name",
            default_branch="main",
            visibility="private",
            snapshot={"v": 1},
        )
        upsert_repo_snapshot(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            repo_id="1001",
            repo_full_name="acme-corp/new-name",  # repo renamed
            default_branch="main",
            visibility="public",
            snapshot={"v": 2},
        )
        rows = list_repo_snapshots(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repo_full_name"], "acme-corp/new-name")
        self.assertEqual(rows[0]["visibility"], "public")


class SyncRunTests(StoreSetUp):

    def test_start_and_finish(self) -> None:
        run_id = start_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            trigger="manual",
        )
        self.assertTrue(run_id.startswith("run-"))
        finish_sync_run(
            self.store,
            run_id=run_id,
            status="ok",
            repos_synced=3,
        )
        latest = latest_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        assert latest is not None
        self.assertEqual(latest["status"], "ok")
        self.assertEqual(latest["repos_synced"], 3)

    def test_latest_successful_skips_errors(self) -> None:
        # Successful run.
        ok_id = start_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            trigger="scheduled",
        )
        finish_sync_run(
            self.store,
            run_id=ok_id,
            status="ok",
            repos_synced=2,
        )
        # Subsequent error run.
        err_id = start_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            trigger="scheduled",
        )
        finish_sync_run(
            self.store,
            run_id=err_id,
            status="error",
            repos_synced=0,
            error="rate_limited",
        )
        latest_ok = latest_successful_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        assert latest_ok is not None
        self.assertEqual(latest_ok["run_id"], ok_id)

    def test_reconcile_orphaned_runs_marks_old_running(self) -> None:
        # Insert a "running" row directly with an old started_at to
        # simulate a Fly-restart orphan.
        old = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(timespec="seconds")
        run_id = "run-orphan01"
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO connector_sync_runs (
                    run_id, workspace_id, provider, trigger,
                    started_at, status, repos_synced
                )
                VALUES (?, ?, 'github', 'scheduled', ?, 'running', 0)
                """,
                (run_id, self.workspace.workspace_id, old),
            )
            conn.commit()
        updated = reconcile_orphaned_runs(self.store, older_than_minutes=30)
        self.assertEqual(updated, 1)
        latest = latest_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        assert latest is not None
        self.assertEqual(latest["status"], "error")
        self.assertEqual(latest["error"], "orphaned: machine restart or crash")


class StateForTests(StoreSetUp):

    def test_not_connected_when_no_credential(self) -> None:
        state = state_for(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(state.status, ConnectorStatus.not_connected)
        self.assertIsNone(state.account)

    def test_connected_with_meta_after_successful_sync(self) -> None:
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct",
            account_login="acme-corp",
        )
        upsert_repo_snapshot(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            repo_id="1001",
            repo_full_name="acme-corp/platform",
            default_branch="main",
            visibility="private",
            snapshot={"v": 1},
        )
        run_id = start_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            trigger="install",
        )
        finish_sync_run(
            self.store,
            run_id=run_id,
            status="ok",
            repos_synced=1,
        )
        state = state_for(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(state.status, ConnectorStatus.connected)
        self.assertEqual(state.account, "acme-corp")
        self.assertEqual(state.primary_repo_full_name, "acme-corp/platform")
        self.assertEqual(state.repo_count, 1)
        self.assertIsNotNone(state.last_sync_at)
        self.assertIsNotNone(state.last_successful_sync_at)
        self.assertIsNone(state.last_error)

    def test_error_state_carries_last_successful(self) -> None:
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct",
            account_login="acme-corp",
        )
        # Successful sync earlier.
        ok_id = start_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            trigger="scheduled",
        )
        finish_sync_run(
            self.store,
            run_id=ok_id,
            status="ok",
            repos_synced=2,
        )
        # Subsequent failed sync.
        err_id = start_sync_run(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            trigger="scheduled",
        )
        finish_sync_run(
            self.store,
            run_id=err_id,
            status="error",
            repos_synced=0,
            error="rate_limited",
        )
        state = state_for(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(state.status, ConnectorStatus.error)
        self.assertEqual(state.last_error, "rate_limited")
        self.assertIsNotNone(state.last_successful_sync_at)

    def test_needs_reauth_state(self) -> None:
        upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct",
            account_login="acme-corp",
        )
        mark_credential_status(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            status="needs_reauth",
        )
        state = state_for(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(state.status, ConnectorStatus.needs_reauth)
        self.assertEqual(state.account, "acme-corp")


if __name__ == "__main__":
    unittest.main()
