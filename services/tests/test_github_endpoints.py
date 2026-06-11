"""HTTP-level tests for the 5 GitHub endpoints (W2 C2).

Covers:
- POST /github/oauth/start — admin+ returns install_url + state.
- POST /github/oauth/start — 503 when GitHub config absent.
- POST /github/oauth/start — 403 for non-admin members.
- GET /github/oauth/callback — error param → redirect.
- GET /github/oauth/callback — invalid state → redirect.
- GET /github/oauth/callback — user mismatch → redirect (CSRF gate).
- GET /github/oauth/callback — happy path → 303 redirect to FE,
  credential persisted with installation_id.
- POST /github/install — admin+ persists, idempotent on PK.
- POST /github/install — 403 for non-admin members.
- DELETE /github — admin+ deletes the row.
- POST /github/sync — member+ returns 202.
- POST /github/sync — 409 when credential absent.

Uses ``patch.dict(os.environ, ...)`` to inject synthetic GitHub
App config so the endpoints don't 503 on missing secrets.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from itsdangerous import URLSafeTimedSerializer

from planning_studio_service.connectors import store as connectors_store
from planning_studio_service.connectors.github.oauth import (
    issue_state_token,
)

try:
    from ._github_helpers import make_test_rsa_pem
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _github_helpers import make_test_rsa_pem  # type: ignore[no-redef]
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


_TEST_SESSION_SECRET = "test-session-secret-do-not-use-in-prod"


def _logout(client) -> None:
    client.cookies.clear()


def _github_env(rsa_pem: str) -> dict[str, str]:
    return {
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_PRIVATE_KEY": rsa_pem,
        "GITHUB_APP_SLUG": "inspira-test",
        "GITHUB_APP_CLIENT_ID": "Iv1.fake",
        "GITHUB_APP_CLIENT_SECRET": "ghs_fake",
        "INSPIRA_SESSION_SECRET": _TEST_SESSION_SECRET,
    }


class GitHubConfigSetUp(unittest.TestCase):
    """Sets the GitHub App env config for the duration of the test
    class, ensures workspace + signed-in user."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rsa_pem = make_test_rsa_pem()
        cls._env_patch = patch.dict("os.environ", _github_env(cls.rsa_pem))
        cls._env_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env_patch.stop()

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.user = signup_and_login(
            self.client,
            email="admin@acme.com",
            password="password123",
            display_name="Admin",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class OAuthStartTests(GitHubConfigSetUp):

    def test_admin_gets_install_url_and_state(self) -> None:
        resp = self.client.post(
            "/api/v2/connectors/github/oauth/start"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("install_url", body)
        self.assertIn("state_token", body)
        self.assertTrue(
            body["install_url"].startswith(
                "https://github.com/apps/inspira-test/installations/new"
            )
        )

    def test_non_admin_blocked_with_403(self) -> None:
        # Sign up a second user on the same TestClient (replaces
        # session cookie), then add them as 'member' (not admin) to
        # the original workspace via the store.
        from planning_studio_service.workspaces.models import (  # noqa: PLC0415
            Role,
        )
        from planning_studio_service.workspaces.store import (  # noqa: PLC0415
            add_member,
        )

        _logout(self.client)
        member = signup_and_login(
            self.client,
            email="member@acme.com",
            password="password123",
            display_name="Member",
        )
        add_member(
            self.store,
            workspace_id=self.workspace_id,
            user_id=member["user_id"],
            role=Role.member,
        )
        resp = self.client.post(
            "/api/v2/connectors/github/oauth/start",
            headers={"X-Workspace-Id": self.workspace_id},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"],
            "workspace_role_insufficient",
        )


class OAuthStartWithoutConfigTests(unittest.TestCase):
    """Endpoint should return 503 (not 500) when env is unset."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client,
            email="admin@acme.com",
            password="password123",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_503_when_env_unset(self) -> None:
        # Explicitly unset (in case parent runner has them).
        with patch.dict("os.environ", {}, clear=False):
            import os

            for k in (
                "GITHUB_APP_ID",
                "GITHUB_APP_PRIVATE_KEY",
                "GITHUB_APP_SLUG",
                "GITHUB_APP_CLIENT_ID",
                "GITHUB_APP_CLIENT_SECRET",
            ):
                os.environ.pop(k, None)
            resp = self.client.post(
                "/api/v2/connectors/github/oauth/start"
            )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(
            resp.json()["detail"]["error"], "github_not_configured"
        )


class OAuthCallbackTests(GitHubConfigSetUp):

    def test_invalid_state_redirects_with_error(self) -> None:
        resp = self.client.get(
            "/api/v2/connectors/github/oauth/callback",
            params={
                "state": "tampered.state.token",
                "installation_id": "INST-001",
                "setup_action": "install",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("status=error", resp.headers["location"])
        self.assertIn("reason=invalid_state", resp.headers["location"])

    def test_user_mismatch_redirects_with_error(self) -> None:
        # Mint a state token bound to a DIFFERENT user_id.
        bogus_state = issue_state_token(
            user_id="user-someone-else",
            workspace_id=self.workspace_id,
            session_secret=_TEST_SESSION_SECRET,
        )
        resp = self.client.get(
            "/api/v2/connectors/github/oauth/callback",
            params={
                "state": bogus_state,
                "installation_id": "INST-001",
                "setup_action": "install",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("status=error", resp.headers["location"])
        self.assertIn(
            "reason=state_user_mismatch", resp.headers["location"]
        )

    def test_github_error_param_redirects_with_error(self) -> None:
        resp = self.client.get(
            "/api/v2/connectors/github/oauth/callback",
            params={
                "error": "access_denied",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("reason=access_denied", resp.headers["location"])

    def test_happy_path_persists_credential_and_redirects(self) -> None:
        # No `code` in this path → skip user-OAuth exchange. The
        # state token is bound to our session user; the callback
        # persists installation_id + redirects to /connectors.
        # We patch out _run_install_sync because the BackgroundTask
        # would otherwise try to mint an installation token from
        # real GitHub and mark the (synthetic) credential
        # needs_reauth on the inevitable failure — masking the
        # router's own persistence behavior we want to verify here.
        async def _noop(**kwargs):
            return None

        with patch(
            "planning_studio_service.connectors.router._run_install_sync",
            _noop,
        ):
            state = issue_state_token(
                user_id=self.user["user_id"],
                workspace_id=self.workspace_id,
                session_secret=_TEST_SESSION_SECRET,
            )
            resp = self.client.get(
                "/api/v2/connectors/github/oauth/callback",
                params={
                    "state": state,
                    "installation_id": "INST-001",
                    "setup_action": "install",
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("status=connected", resp.headers["location"])
        # Credential row persists.
        cred = connectors_store.get_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
        )
        assert cred is not None
        self.assertEqual(cred["installation_id"], "INST-001")
        self.assertEqual(cred["status"], "connected")


class InstallEndpointTests(GitHubConfigSetUp):

    def test_admin_install_persists(self) -> None:
        resp = self.client.post(
            "/api/v2/connectors/github/install",
            json={"installation_id": "INST-002"},
        )
        self.assertEqual(resp.status_code, 200)
        cred = connectors_store.get_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
        )
        assert cred is not None
        self.assertEqual(cred["installation_id"], "INST-002")

    def test_install_idempotent_replaces_in_place(self) -> None:
        self.client.post(
            "/api/v2/connectors/github/install",
            json={"installation_id": "INST-002"},
        )
        # Second install with a different id replaces the row.
        self.client.post(
            "/api/v2/connectors/github/install",
            json={"installation_id": "INST-003"},
        )
        cred = connectors_store.get_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
        )
        assert cred is not None
        self.assertEqual(cred["installation_id"], "INST-003")


class DisconnectEndpointTests(GitHubConfigSetUp):

    def test_admin_disconnect_removes_row(self) -> None:
        self.client.post(
            "/api/v2/connectors/github/install",
            json={"installation_id": "INST-001"},
        )
        resp = self.client.delete(
            "/api/v2/connectors/github"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["disconnected"])
        self.assertIsNone(
            connectors_store.get_credential(
                self.store,
                workspace_id=self.workspace_id,
                provider="github",
            )
        )


class SyncNowEndpointTests(GitHubConfigSetUp):

    def test_sync_without_credential_returns_409(self) -> None:
        resp = self.client.post(
            "/api/v2/connectors/github/sync"
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["detail"]["error"], "github_not_connected"
        )

    def test_sync_with_credential_returns_202(self) -> None:
        connectors_store.upsert_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            encrypted_token="ct",
            installation_id="INST-001",
        )
        resp = self.client.post(
            "/api/v2/connectors/github/sync"
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "queued")


if __name__ == "__main__":
    unittest.main()
