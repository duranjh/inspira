"""Safety-guard tests for the URL-fetch proxy endpoint.

The proxy is a classic SSRF footgun if the private-IP check fails —
a user could redirect us to ``http://169.254.169.254/`` (cloud
metadata) or any internal service. This module pins the block.

We deliberately don't make any real network calls:
  - Literal-IP URLs (``http://127.0.0.1``) are parsed directly, so the
    block runs without touching ``socket.getaddrinfo``.
  - Hostname-based URLs are tested by monkey-patching
    ``socket.getaddrinfo`` so a public-looking hostname resolves to a
    private address, and we confirm the endpoint still refuses.
"""
from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class UrlFetchSSRFTests(unittest.TestCase):
    """Block literal-private IPs and hostnames that resolve to private IPs."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="urlfetch@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rejects_literal_loopback_ipv4(self) -> None:
        """``http://127.0.0.1`` short-circuits before DNS — never fetched."""
        response = self.client.post(
            "/api/v2/fetch-url", json={"url": "http://127.0.0.1/anything"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "blocked_internal_address")

    def test_rejects_hostname_resolving_to_private_range(self) -> None:
        """A public-looking host whose DNS points into 10/8 is refused.

        We mock ``socket.getaddrinfo`` at the fetcher module boundary so
        no real DNS call happens. The resolver returns a single v4
        address inside 10.0.0.0/8 — which the private-IP check must
        treat as an SSRF attempt.
        """

        def fake_getaddrinfo(
            host: str, *args, **kwargs,  # noqa: ARG001, ANN002, ANN003
        ) -> list:
            # (family, type, proto, canonname, sockaddr) — sockaddr is
            # (ip, port) for v4. Mimics a DNS A-record pointing inside
            # RFC 1918 private space.
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("10.0.0.1", 0),
                ),
            ]

        with patch(
            "planning_studio_service.fetchers.url.socket.getaddrinfo",
            side_effect=fake_getaddrinfo,
        ):
            response = self.client.post(
                "/api/v2/fetch-url",
                json={"url": "http://evil.example.com/path"},
            )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "blocked_internal_address")

    def test_rejects_invalid_scheme(self) -> None:
        response = self.client.post(
            "/api/v2/fetch-url", json={"url": "file:///etc/passwd"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "invalid_url")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
