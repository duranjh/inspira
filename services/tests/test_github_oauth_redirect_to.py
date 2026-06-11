"""Tests for the GitHub OAuth ``redirect_to`` extension.

Covers:
- ``_validate_redirect_to`` allowlist: /connectors and /onboarding
  with optional query strings pass; arbitrary paths and absolute
  URLs are rejected (open-redirect defense).
- ``issue_state_token`` binds a valid ``redirect_to`` into the
  signed payload as ``r``.
- ``consume_state_token`` returns the bound ``r`` for the route
  handler to use.
- Invalid ``redirect_to`` values are silently dropped at issue
  time so a misconfigured FE can never mint open-redirect tokens.
"""
from __future__ import annotations

import unittest

from planning_studio_service.connectors.github.oauth import (
    _validate_redirect_to,
    consume_state_token,
    issue_state_token,
)


_TEST_SECRET = "test-session-secret-do-not-use-in-prod"


class RedirectAllowlistTests(unittest.TestCase):

    def test_connectors_passes(self) -> None:
        self.assertEqual(_validate_redirect_to("/connectors"), "/connectors")

    def test_connectors_with_query_passes(self) -> None:
        self.assertEqual(
            _validate_redirect_to("/connectors?foo=bar"),
            "/connectors?foo=bar",
        )

    def test_onboarding_passes(self) -> None:
        self.assertEqual(_validate_redirect_to("/onboarding"), "/onboarding")

    def test_onboarding_with_query_passes(self) -> None:
        self.assertEqual(
            _validate_redirect_to("/onboarding?step=2"),
            "/onboarding?step=2",
        )

    def test_onboarding_subpath_passes(self) -> None:
        self.assertEqual(
            _validate_redirect_to("/onboarding/wizard"),
            "/onboarding/wizard",
        )

    def test_arbitrary_path_rejected(self) -> None:
        self.assertIsNone(_validate_redirect_to("/billing"))

    def test_absolute_http_rejected(self) -> None:
        self.assertIsNone(
            _validate_redirect_to("http://evil.com/connectors")
        )

    def test_absolute_https_rejected(self) -> None:
        self.assertIsNone(
            _validate_redirect_to("https://evil.com/connectors")
        )

    def test_protocol_relative_rejected(self) -> None:
        self.assertIsNone(_validate_redirect_to("//evil.com/connectors"))

    def test_backslash_rejected(self) -> None:
        self.assertIsNone(_validate_redirect_to("\\evil.com/connectors"))

    def test_prefix_lookalike_rejected(self) -> None:
        # /onboardingfoo must NOT pass — only exact path or path/?...
        self.assertIsNone(_validate_redirect_to("/onboardingfoo"))
        self.assertIsNone(_validate_redirect_to("/connectorshacked"))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_validate_redirect_to(None))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_validate_redirect_to(""))


class StateTokenRedirectToBindingTests(unittest.TestCase):

    def test_redirect_to_bound_into_payload(self) -> None:
        token = issue_state_token(
            user_id="user-1",
            workspace_id="ws-1",
            session_secret=_TEST_SECRET,
            redirect_to="/onboarding?step=2",
        )
        payload = consume_state_token(
            token,
            session_secret=_TEST_SECRET,
            expected_user_id="user-1",
        )
        self.assertEqual(payload.get("r"), "/onboarding?step=2")

    def test_no_redirect_to_omits_r_key(self) -> None:
        token = issue_state_token(
            user_id="user-1",
            workspace_id="ws-1",
            session_secret=_TEST_SECRET,
        )
        payload = consume_state_token(
            token,
            session_secret=_TEST_SECRET,
            expected_user_id="user-1",
        )
        self.assertNotIn("r", payload)

    def test_invalid_redirect_to_silently_dropped(self) -> None:
        # Open-redirect defense: invalid path is silently dropped at
        # issue time. The state token still works for the OAuth flow,
        # the callback just falls through to the default redirect.
        token = issue_state_token(
            user_id="user-1",
            workspace_id="ws-1",
            session_secret=_TEST_SECRET,
            redirect_to="https://evil.com/connectors",
        )
        payload = consume_state_token(
            token,
            session_secret=_TEST_SECRET,
            expected_user_id="user-1",
        )
        self.assertNotIn("r", payload)


if __name__ == "__main__":
    unittest.main()
