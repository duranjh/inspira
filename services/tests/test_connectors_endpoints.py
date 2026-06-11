"""HTTP-level tests for the v4 connectors router (W2 C1).

Covers ``GET /api/v2/connectors``:
- Returns the three-tier shape (live / coming_soon / future).
- Each LIVE entry carries a state object (status / account /
  primary_repo_full_name / repo_count / last_sync_at /
  last_successful_sync_at / last_error).
- COMING_SOON entries carry mailto contact_route, no actions.
- FUTURE entries carry no contact_route, no actions.
- GitHub LIVE entry returns the connected state when credentials
  exist + a sync run completed; ``not_connected`` otherwise.
- Linear / CSV/JSON LIVE entries return ``not_implemented`` until
  W2 C5.
- Non-member 403 (workspace-scoping enforced).
- Anon (no workspace) → 400 ``workspace_id_required``.
"""
from __future__ import annotations

import unittest

from planning_studio_service.connectors.store import (
    finish_sync_run,
    start_sync_run,
    upsert_credential,
    upsert_repo_snapshot,
)

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _logout(client) -> None:
    client.cookies.clear()


class ConnectorsEndpointTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.user = signup_and_login(
            self.client,
            email="member@acme.com",
            password="password123",
            display_name="Member",
        )
        # Create a workspace + capture its ID.
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_connectors_returns_three_tiers(self) -> None:
        resp = self.client.get("/api/v2/connectors")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("live", body)
        self.assertIn("coming_soon", body)
        self.assertIn("future", body)
        self.assertEqual(len(body["live"]), 3)
        self.assertEqual(len(body["coming_soon"]), 4)
        self.assertEqual(len(body["future"]), 3)

    def test_live_entries_carry_state_object(self) -> None:
        body = self.client.get("/api/v2/connectors").json()
        for entry in body["live"]:
            self.assertIn("provider", entry)
            self.assertIn("display_name", entry)
            self.assertIn("summary", entry)
            self.assertIn("logo_slug", entry)
            self.assertIn("state", entry)
            state = entry["state"]
            for key in (
                "status",
                "account",
                "primary_repo_full_name",
                "repo_count",
                "last_sync_at",
                "last_successful_sync_at",
                "last_error",
            ):
                self.assertIn(key, state, f"missing {key} on {entry['provider']}")

    def test_github_live_starts_not_connected(self) -> None:
        body = self.client.get("/api/v2/connectors").json()
        github = next(e for e in body["live"] if e["provider"] == "github")
        self.assertEqual(github["state"]["status"], "not_connected")

    def test_linear_and_csv_idle_when_unconnected(self) -> None:
        # F4 wired Linear (API-key flow) and CSV/JSON (paste-in)
        # so both report not_connected with real action URLs. The
        # FE renders the idle-Live tile in either case.
        body = self.client.get("/api/v2/connectors").json()
        linear = next(e for e in body["live"] if e["provider"] == "linear")
        csv = next(e for e in body["live"] if e["provider"] == "csv_json")
        self.assertEqual(linear["state"]["status"], "not_connected")
        self.assertEqual(csv["state"]["status"], "not_connected")
        self.assertEqual(
            linear["actions"]["connect"],
            "/api/v2/connectors/linear/connect",
        )
        self.assertEqual(
            csv["actions"]["import"], "/api/v2/connectors/csv/import"
        )

    def test_github_live_reports_connected_after_seeded_sync(self) -> None:
        # Seed a connected state for GitHub on the active workspace.
        upsert_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            encrypted_token="ciphertext-1",
            installation_id="install-1",
            account_login="acme-corp",
        )
        upsert_repo_snapshot(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            repo_id="1001",
            repo_full_name="acme-corp/platform",
            default_branch="main",
            visibility="private",
            snapshot={"tree_top": [], "open_issues": [], "recent_commits": []},
        )
        run_id = start_sync_run(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            trigger="install",
        )
        finish_sync_run(
            self.store,
            run_id=run_id,
            status="ok",
            repos_synced=1,
        )
        body = self.client.get("/api/v2/connectors").json()
        github = next(e for e in body["live"] if e["provider"] == "github")
        state = github["state"]
        self.assertEqual(state["status"], "connected")
        self.assertEqual(state["account"], "acme-corp")
        self.assertEqual(state["primary_repo_full_name"], "acme-corp/platform")
        self.assertEqual(state["repo_count"], 1)
        self.assertIsNotNone(state["last_sync_at"])
        self.assertIsNotNone(state["last_successful_sync_at"])
        self.assertIsNone(state["last_error"])

    def test_coming_soon_entries_carry_mailto(self) -> None:
        body = self.client.get("/api/v2/connectors").json()
        for entry in body["coming_soon"]:
            self.assertIn("contact_route", entry)
            self.assertTrue(entry["contact_route"].startswith("mailto:"))
            # No state, no actions on coming-soon tier.
            self.assertNotIn("state", entry)
            self.assertNotIn("actions", entry)

    def test_future_entries_have_no_actions(self) -> None:
        body = self.client.get("/api/v2/connectors").json()
        for entry in body["future"]:
            self.assertNotIn("contact_route", entry)
            self.assertNotIn("state", entry)
            self.assertNotIn("actions", entry)

    def test_non_member_blocked_with_403(self) -> None:
        # Sign up an outsider; they have no workspace, so the
        # default-workspace fallback in the dependency fires →
        # outsider's own personal default. Confirm they don't get
        # to see the original workspace's connectors.
        # Note: anon → 400; signed-in-but-not-a-member → 403 only
        # when an explicit X-Workspace-Id header points at a workspace
        # they don't belong to. With no header, the default fallback
        # kicks in and returns their OWN workspace's connectors,
        # which is the right behavior.
        _logout(self.client)
        signup_and_login(
            self.client,
            email="outsider@acme.com",
            password="password123",
            display_name="Outsider",
        )
        resp = self.client.get(
            "/api/v2/connectors",
            headers={"X-Workspace-Id": self.workspace_id},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"], "workspace_access_denied"
        )

    def test_anon_no_workspace_returns_400(self) -> None:
        # Drop the session entirely → anon-on-first-contact mints a
        # fresh user-anon-* with no workspaces, no default. The
        # dependency 400s on workspace_id_required.
        _logout(self.client)
        resp = self.client.get("/api/v2/connectors")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["error"], "workspace_id_required"
        )


if __name__ == "__main__":
    unittest.main()
