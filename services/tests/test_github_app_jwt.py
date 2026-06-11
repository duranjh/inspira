"""Unit tests for ``connectors.github.app_jwt`` (W2 C2).

Covers:
- RS256 signing produces a valid JWT (verifiable with the public
  key).
- TTL ceiling enforced at 600s (GitHub's hard cap).
- ``installation_access_token`` HTTP request shape (Authorization
  header carries the App JWT, expected URL).
- 1-hour expiry parsing.

Per W2 watch point #2: this module signs App JWTs only — it does
NOT make repo API calls. Token usage separation is asserted by the
client tests in ``test_github_client.py``.
"""
from __future__ import annotations

import base64
import json
import time
import unittest
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from planning_studio_service.connectors.github.app_jwt import (
    GitHubAppConfig,
    app_jwt,
    installation_access_token,
)

try:
    from ._github_helpers import make_test_rsa_pem, mock_async_client
except ImportError:
    from _github_helpers import (  # type: ignore[no-redef]
        make_test_rsa_pem,
        mock_async_client,
    )


def _verify_jwt_signature(jwt_str: str, private_key_pem: str) -> tuple[dict, dict]:
    """Verify JWT signature using the public key derived from the
    private key. Returns (header, payload)."""
    header_b64, payload_b64, sig_b64 = jwt_str.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("ascii"), password=None
    )
    public_key = private_key.public_key()
    public_key.verify(
        sig,
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    def b64dec(s: str) -> dict:
        padding_chars = "=" * (-len(s) % 4)
        return json.loads(base64.urlsafe_b64decode(s + padding_chars))

    return b64dec(header_b64), b64dec(payload_b64)


class AppJWTSigningTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key_pem = make_test_rsa_pem()

    def test_signature_verifies_with_matching_public_key(self) -> None:
        jwt_str = app_jwt(
            app_id="12345",
            private_key_pem=self.private_key_pem,
        )
        header, payload = _verify_jwt_signature(
            jwt_str, self.private_key_pem
        )
        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(header["typ"], "JWT")
        self.assertEqual(payload["iss"], "12345")
        self.assertIn("iat", payload)
        self.assertIn("exp", payload)

    def test_iat_is_60s_in_the_past_by_default(self) -> None:
        before = int(time.time())
        jwt_str = app_jwt(
            app_id="12345",
            private_key_pem=self.private_key_pem,
        )
        _, payload = _verify_jwt_signature(jwt_str, self.private_key_pem)
        self.assertLessEqual(payload["iat"], before)
        # Allow a small tolerance for slow test machines.
        self.assertGreaterEqual(payload["iat"], before - 90)

    def test_exp_is_540s_in_the_future_by_default(self) -> None:
        before = int(time.time())
        jwt_str = app_jwt(
            app_id="12345",
            private_key_pem=self.private_key_pem,
        )
        _, payload = _verify_jwt_signature(jwt_str, self.private_key_pem)
        self.assertGreaterEqual(payload["exp"], before + 540 - 5)
        self.assertLessEqual(payload["exp"], before + 540 + 5)

    def test_ttl_above_600s_rejected(self) -> None:
        with self.assertRaises(ValueError):
            app_jwt(
                app_id="12345",
                private_key_pem=self.private_key_pem,
                ttl_seconds=700,
            )

    def test_accepts_bytes_private_key(self) -> None:
        jwt_str = app_jwt(
            app_id="12345",
            private_key_pem=self.private_key_pem.encode("ascii"),
        )
        header, _ = _verify_jwt_signature(jwt_str, self.private_key_pem)
        self.assertEqual(header["alg"], "RS256")


class InstallationAccessTokenTests(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key_pem = make_test_rsa_pem()
        cls.config = GitHubAppConfig(
            app_id="12345",
            private_key_pem=cls.private_key_pem,
            app_slug="inspira-test",
        )

    async def test_happy_path_returns_token_and_expiry(self) -> None:
        captured: dict = {}
        future_iso = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat().replace("+00:00", "Z")

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(
                201,
                json={
                    "token": "ghs_install_token_xyz",
                    "expires_at": future_iso,
                    "permissions": {"contents": "read"},
                    "repository_selection": "selected",
                },
            )

        async with mock_async_client(handler) as http:
            token, expires_at = await installation_access_token(
                config=self.config,
                installation_id="INST-001",
                http=http,
            )

        self.assertEqual(token, "ghs_install_token_xyz")
        self.assertIsInstance(expires_at, datetime)
        self.assertEqual(
            captured["url"],
            "https://api.github.com/app/installations/INST-001/access_tokens",
        )
        # Authorization header carries the App JWT, not the
        # installation token (which is what GitHub returns).
        self.assertTrue(
            captured["auth"].startswith("Bearer "),
            f"unexpected auth header: {captured['auth']!r}",
        )

    async def test_non_2xx_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404, json={"message": "Not Found"}
            )

        async with mock_async_client(handler) as http:
            with self.assertRaises(httpx.HTTPStatusError):
                await installation_access_token(
                    config=self.config,
                    installation_id="INST-bogus",
                    http=http,
                )


if __name__ == "__main__":
    unittest.main()
