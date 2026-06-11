"""Unit tests for ``connectors.github.oauth`` (W2 C2).

Covers:
- ``issue_state_token`` + ``consume_state_token`` round-trip.
- CSRF gate: signature mismatch → ``OAuthStateInvalidSignature``;
  expired → ``OAuthStateExpired``; bound-user mismatch →
  ``OAuthStateUserMismatch``.
- Salt isolation: a token signed at the realtime ws-ticket salt
  cannot be consumed at the oauth-state salt (defense against
  cross-context replay).
- ``build_install_url`` shape: encodes state in query string,
  uses the configured app slug.
- ``exchange_user_code`` happy path + error path (GitHub returns
  200 with an error envelope).
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import httpx
from itsdangerous import URLSafeTimedSerializer

from planning_studio_service.connectors.github.oauth import (
    GitHubOAuthConfig,
    OAuthStateExpired,
    OAuthStateInvalidSignature,
    OAuthStateUserMismatch,
    build_install_url,
    consume_state_token,
    exchange_user_code,
    issue_state_token,
    load_app_config_from_env,
)

try:
    from ._github_helpers import mock_async_client
except ImportError:
    from _github_helpers import mock_async_client  # type: ignore[no-redef]


_TEST_SECRET = "test-session-secret-do-not-use-in-prod"


class StateTokenRoundTripTests(unittest.TestCase):

    def test_issue_consume_happy_path(self) -> None:
        token = issue_state_token(
            user_id="user-abc",
            workspace_id="ws-001",
            session_secret=_TEST_SECRET,
        )
        payload = consume_state_token(
            token,
            session_secret=_TEST_SECRET,
            expected_user_id="user-abc",
        )
        self.assertEqual(payload["u"], "user-abc")
        self.assertEqual(payload["w"], "ws-001")
        self.assertIn("n", payload)

    def test_two_calls_produce_different_tokens(self) -> None:
        a = issue_state_token(
            user_id="user-abc",
            workspace_id="ws-001",
            session_secret=_TEST_SECRET,
        )
        b = issue_state_token(
            user_id="user-abc",
            workspace_id="ws-001",
            session_secret=_TEST_SECRET,
        )
        self.assertNotEqual(a, b)

    def test_signature_mismatch_raises(self) -> None:
        token = issue_state_token(
            user_id="user-abc",
            workspace_id="ws-001",
            session_secret=_TEST_SECRET,
        )
        with self.assertRaises(OAuthStateInvalidSignature):
            consume_state_token(
                token,
                session_secret="different-secret",
                expected_user_id="user-abc",
            )

    def test_expired_token_raises(self) -> None:
        # Mint a token with a backdated iat by using max_age=0
        # which forces signature_expired regardless of clock.
        token = issue_state_token(
            user_id="user-abc",
            workspace_id="ws-001",
            session_secret=_TEST_SECRET,
        )
        with self.assertRaises(OAuthStateExpired):
            consume_state_token(
                token,
                session_secret=_TEST_SECRET,
                expected_user_id="user-abc",
                max_age_s=-1,
            )

    def test_user_mismatch_raises(self) -> None:
        """The CSRF gate. State minted by user A; user B tries to
        consume → mismatch → reject."""
        token = issue_state_token(
            user_id="user-A",
            workspace_id="ws-001",
            session_secret=_TEST_SECRET,
        )
        with self.assertRaises(OAuthStateUserMismatch):
            consume_state_token(
                token,
                session_secret=_TEST_SECRET,
                expected_user_id="user-B",
            )

    def test_salt_isolation_from_session_serializer(self) -> None:
        """A state token signed at the session salt MUST NOT verify
        at the oauth-state salt. Defends against an attacker who
        somehow gets a session-cookie-style token and tries to use
        it as an oauth-state."""
        # Sign at the session salt
        wrong_salt_serializer = URLSafeTimedSerializer(
            _TEST_SECRET, salt="inspira-session"
        )
        token = wrong_salt_serializer.dumps({"u": "user-A", "w": "ws-001"})
        with self.assertRaises(OAuthStateInvalidSignature):
            consume_state_token(
                token,
                session_secret=_TEST_SECRET,
                expected_user_id="user-A",
            )

    def test_salt_isolation_from_ws_ticket_serializer(self) -> None:
        """Same defense vs the ws-ticket salt at auth.py:148."""
        ws_ticket_serializer = URLSafeTimedSerializer(
            _TEST_SECRET, salt="inspira-ws-ticket"
        )
        token = ws_ticket_serializer.dumps({"uid": "user-A"})
        with self.assertRaises(OAuthStateInvalidSignature):
            consume_state_token(
                token,
                session_secret=_TEST_SECRET,
                expected_user_id="user-A",
            )


class InstallUrlTests(unittest.TestCase):

    def test_install_url_encodes_state(self) -> None:
        url = build_install_url(app_slug="inspira-test", state="abc123")
        self.assertIn("https://github.com/apps/inspira-test/installations/new", url)
        self.assertIn("state=abc123", url)


class ExchangeUserCodeTests(unittest.IsolatedAsyncioTestCase):

    async def test_happy_path(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                "https://github.com/login/oauth/access_token",
            )
            self.assertEqual(request.method, "POST")
            return httpx.Response(
                200,
                json={
                    "access_token": "user-token-xyz",
                    "token_type": "bearer",
                    "scope": "",
                },
            )

        async with mock_async_client(handler) as http:
            body = await exchange_user_code(
                code="raw-code",
                config=GitHubOAuthConfig(
                    client_id="cid",
                    client_secret="csecret",
                    session_secret=_TEST_SECRET,
                ),
                http=http,
            )
        self.assertEqual(body["access_token"], "user-token-xyz")

    async def test_error_envelope_raises(self) -> None:
        """GitHub returns 200 with ``{error: ...}`` on bad codes —
        our wrapper turns this into an HTTPStatusError so the
        callback can map to a redirect-error reason."""

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "error": "bad_verification_code",
                    "error_description": "The code passed is incorrect",
                },
            )

        async with mock_async_client(handler) as http:
            with self.assertRaises(httpx.HTTPStatusError):
                await exchange_user_code(
                    code="bad-code",
                    config=GitHubOAuthConfig(
                        client_id="cid",
                        client_secret="csecret",
                        session_secret=_TEST_SECRET,
                    ),
                    http=http,
                )


class LoadConfigFromEnvTests(unittest.TestCase):

    def test_returns_none_without_app_id(self) -> None:
        with patch.dict(
            "os.environ", {"GITHUB_APP_ID": ""}, clear=False
        ):
            # Explicit unset by deleting if present.
            import os

            os.environ.pop("GITHUB_APP_ID", None)
            self.assertIsNone(load_app_config_from_env())

    def test_returns_none_without_full_set(self) -> None:
        with patch.dict(
            "os.environ",
            {"GITHUB_APP_ID": "12345"},
            clear=False,
        ):
            import os

            for k in (
                "GITHUB_APP_PRIVATE_KEY",
                "GITHUB_APP_SLUG",
                "GITHUB_APP_CLIENT_ID",
                "GITHUB_APP_CLIENT_SECRET",
            ):
                os.environ.pop(k, None)
            self.assertIsNone(load_app_config_from_env())

    def test_returns_configs_when_full(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GITHUB_APP_ID": "12345",
                "GITHUB_APP_PRIVATE_KEY": "fake-pem",
                "GITHUB_APP_SLUG": "inspira-test",
                "GITHUB_APP_CLIENT_ID": "Iv1.fake",
                "GITHUB_APP_CLIENT_SECRET": "ghs_fake",
                "INSPIRA_SESSION_SECRET": _TEST_SECRET,
            },
        ):
            configs = load_app_config_from_env()
            self.assertIsNotNone(configs)
            assert configs is not None
            app_config, oauth_config = configs
            self.assertEqual(app_config.app_id, "12345")
            self.assertEqual(app_config.app_slug, "inspira-test")
            self.assertEqual(oauth_config.client_id, "Iv1.fake")


if __name__ == "__main__":
    unittest.main()
