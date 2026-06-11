"""Option A: conditional ``SameSite=None`` for Cloudflare Pages preview origins.

Pins the wave-3-4 cookie-attribute behavior introduced for #143:

- Requests from a CF Pages preview origin (``*.inspira-frontend.pages.dev``)
  must mint the session cookie with ``SameSite=None`` so the cross-site
  fetch from the preview FE to ``api.tryinspira.com`` carries the cookie.
- Production requests (``tryinspira.com``) and missing-Origin requests stay
  on ``SameSite=Lax`` — no security regression.
- ``SameSite=None`` requires ``Secure=True``. If a dev environment explicitly
  disables Secure cookies (``INSPIRA_COOKIE_SECURE=false``) while a preview
  origin somehow reaches the dev backend, we degrade gracefully to Lax so
  the cookie still lands (browsers reject ``SameSite=None`` without Secure).
"""
from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

from fastapi import Request, Response

from planning_studio_service import auth

# The carve-out is opt-in via env; tests pin the historical CF-Pages pattern.
_PREVIEW_REGEX = r"^https://[a-z0-9-]+\.inspira-frontend\.pages\.dev$"
_PREVIEW_ENV = {"INSPIRA_PREVIEW_ORIGIN_REGEX": _PREVIEW_REGEX}


def _make_request(origin: str | None) -> Request:
    """Build a minimal ASGI Request with the given Origin header."""
    headers: list[tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((b"origin", origin.encode("latin-1")))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/signup",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


def _set_cookie_header(response: Response) -> str:
    """Return the ``Set-Cookie`` header value for assertions."""
    for key, value in response.raw_headers:
        if key.lower() == b"set-cookie":
            return value.decode("latin-1")
    raise AssertionError("no Set-Cookie header on response")


class SameSiteForOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = mock.patch.dict(os.environ, _PREVIEW_ENV, clear=False)
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()

    def test_unset_regex_disables_carveout(self) -> None:
        # With no INSPIRA_PREVIEW_ORIGIN_REGEX, even a matching origin
        # stays on Lax — the carve-out is opt-in.
        with mock.patch.dict(os.environ, {"INSPIRA_PREVIEW_ORIGIN_REGEX": ""}):
            request = _make_request("https://abc1234.inspira-frontend.pages.dev")
            self.assertEqual(auth._samesite_for_origin(request), "lax")

    def test_preview_origin_returns_none(self) -> None:
        request = _make_request("https://abc1234.inspira-frontend.pages.dev")
        self.assertEqual(auth._samesite_for_origin(request), "none")

    def test_preview_origin_with_hyphens_returns_none(self) -> None:
        request = _make_request("https://feat-wave-3-4-s1-batch.inspira-frontend.pages.dev")
        self.assertEqual(auth._samesite_for_origin(request), "none")

    def test_prod_origin_returns_lax(self) -> None:
        request = _make_request("https://tryinspira.com")
        self.assertEqual(auth._samesite_for_origin(request), "lax")

    def test_missing_origin_returns_lax(self) -> None:
        request = _make_request(None)
        self.assertEqual(auth._samesite_for_origin(request), "lax")

    def test_empty_origin_returns_lax(self) -> None:
        request = _make_request("")
        self.assertEqual(auth._samesite_for_origin(request), "lax")

    def test_none_request_returns_lax(self) -> None:
        # Defensive: callers without a Request object (e.g. background tasks
        # or tests) preserve the historical Lax behavior.
        self.assertEqual(auth._samesite_for_origin(None), "lax")

    def test_path_traversal_attempt_returns_lax(self) -> None:
        # Reject anything that isn't a clean *.inspira-frontend.pages.dev host.
        bad = "https://attacker.com/.inspira-frontend.pages.dev"
        self.assertEqual(auth._samesite_for_origin(_make_request(bad)), "lax")

    def test_http_not_https_returns_lax(self) -> None:
        # Preview pattern requires https.
        request = _make_request("http://abc.inspira-frontend.pages.dev")
        self.assertEqual(auth._samesite_for_origin(request), "lax")

    def test_uppercase_origin_returns_lax(self) -> None:
        # Pattern is lowercase a-z0-9-; uppercase is a safety reject.
        request = _make_request("https://ABC.inspira-frontend.pages.dev")
        self.assertEqual(auth._samesite_for_origin(request), "lax")


class SetSessionCookieIntegrationTests(unittest.TestCase):
    """End-to-end: _set_session_cookie picks the right SameSite per Origin."""

    def setUp(self) -> None:
        # ENVIRONMENT=production so Secure defaults to true. Without Secure,
        # the SameSite=None branch is rejected at the cookie layer (browser
        # would also reject it), which we degrade to Lax for safety. Tests
        # below pin both branches explicitly.
        self._env_patcher = mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "INSPIRA_SESSION_SECRET": "x" * 32,
                **_PREVIEW_ENV,
            },
            clear=False,
        )
        self._env_patcher.start()
        # Re-memoize the session serializer against the test secret.
        auth._session_serializer_cached = None

    def tearDown(self) -> None:
        self._env_patcher.stop()
        auth._session_serializer_cached = None

    def test_preview_origin_mints_samesite_none(self) -> None:
        response = Response()
        request = _make_request("https://abc1234.inspira-frontend.pages.dev")
        auth._set_session_cookie(response, "user-abc", request=request)
        header = _set_cookie_header(response)
        self.assertIn("samesite=none", header.lower())
        # SameSite=None mandates Secure per browser spec.
        self.assertIn("secure", header.lower())

    def test_prod_origin_mints_samesite_lax(self) -> None:
        response = Response()
        request = _make_request("https://tryinspira.com")
        auth._set_session_cookie(response, "user-abc", request=request)
        header = _set_cookie_header(response)
        self.assertIn("samesite=lax", header.lower())

    def test_no_request_preserves_lax(self) -> None:
        # Historical callers without a Request still get Lax.
        response = Response()
        auth._set_session_cookie(response, "user-abc")
        header = _set_cookie_header(response)
        self.assertIn("samesite=lax", header.lower())

    def test_preview_origin_with_secure_disabled_degrades_to_lax(self) -> None:
        # If a dev env disables Secure cookies (HTTP tunnel scenario), the
        # browser would reject SameSite=None without Secure → degrade to Lax
        # so at least the cookie lands. Verifies the safety branch in code.
        with mock.patch.dict(os.environ, {"INSPIRA_COOKIE_SECURE": "false"}):
            response = Response()
            request = _make_request("https://abc.inspira-frontend.pages.dev")
            auth._set_session_cookie(response, "user-abc", request=request)
        header = _set_cookie_header(response)
        self.assertIn("samesite=lax", header.lower())
        # And NOT secure, per the env override.
        self.assertNotIn("; secure", header.lower())


if __name__ == "__main__":
    unittest.main()
