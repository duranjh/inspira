"""Tests for the Sentry initialisation helper in api.py.

Two cases:
- No DSN set → sentry_sdk.init is never called.
- SENTRY_DSN_BACKEND set → sentry_sdk.init is called with the correct dsn arg.

We monkeypatch ``sentry_sdk.init`` so no real network calls are made.
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


class SentryInitNoDSNTests(unittest.TestCase):
    """When neither SENTRY_DSN_BACKEND nor SENTRY_DSN is set, init is skipped."""

    def test_no_dsn_does_not_call_init(self) -> None:
        # Ensure both env vars are absent for this test.
        env_patch = patch.dict(
            "os.environ",
            {"SENTRY_DSN_BACKEND": "", "SENTRY_DSN": ""},
            clear=False,
        )
        init_mock = MagicMock()

        with env_patch, patch("sentry_sdk.init", init_mock):
            # Import the helper from the api module.
            from planning_studio_service.api import _maybe_init_sentry  # type: ignore[attr-defined]
            _maybe_init_sentry()

        init_mock.assert_not_called()


class SentryInitWithDSNTests(unittest.TestCase):
    """When SENTRY_DSN_BACKEND is set, sentry_sdk.init receives the correct DSN."""

    def test_dsn_backend_calls_init_with_correct_dsn(self) -> None:
        fake_dsn = "https://abc123@o0.ingest.sentry.io/000000"
        env_patch = patch.dict(
            "os.environ",
            {"SENTRY_DSN_BACKEND": fake_dsn, "SENTRY_DSN": ""},
            clear=False,
        )
        init_mock = MagicMock()

        with env_patch, patch("sentry_sdk.init", init_mock):
            from planning_studio_service.api import _maybe_init_sentry  # type: ignore[attr-defined]
            _maybe_init_sentry()

        init_mock.assert_called_once()
        call_kwargs = init_mock.call_args
        # dsn can be positional or keyword — check both paths.
        if call_kwargs.args:
            self.assertEqual(call_kwargs.args[0], fake_dsn)
        else:
            self.assertEqual(call_kwargs.kwargs.get("dsn"), fake_dsn)

    def test_legacy_sentry_dsn_fallback(self) -> None:
        """SENTRY_DSN (legacy) still triggers init when SENTRY_DSN_BACKEND is absent."""
        fake_dsn = "https://legacy@o0.ingest.sentry.io/111111"
        env_patch = patch.dict(
            "os.environ",
            {"SENTRY_DSN_BACKEND": "", "SENTRY_DSN": fake_dsn},
            clear=False,
        )
        init_mock = MagicMock()

        with env_patch, patch("sentry_sdk.init", init_mock):
            from planning_studio_service.api import _maybe_init_sentry  # type: ignore[attr-defined]
            _maybe_init_sentry()

        init_mock.assert_called_once()


class SentryBeforeSendTests(unittest.TestCase):
    """The before_send hook scrubs credentials before events leave the box."""

    def test_strips_auth_headers(self) -> None:
        from planning_studio_service.api import _sentry_before_send

        event = {
            "request": {
                "headers": {
                    "Authorization": "Bearer abc.def.ghi",
                    "Cookie": "session=xyz",
                    "X-Api-Key": "sk-123",
                    "User-Agent": "pytest",
                },
            },
        }
        out = _sentry_before_send(event, {})
        assert out is not None
        h = out["request"]["headers"]
        self.assertEqual(h["Authorization"], "[scrubbed]")
        self.assertEqual(h["Cookie"], "[scrubbed]")
        self.assertEqual(h["X-Api-Key"], "[scrubbed]")
        self.assertEqual(h["User-Agent"], "pytest")  # untouched

    def test_strips_password_fields_in_body(self) -> None:
        from planning_studio_service.api import _sentry_before_send

        event = {
            "request": {
                "url": "https://example.com/api/v2/projects",
                "data": {
                    "title": "Demo",
                    "password": "hunter2",
                    "nested": {"api_key": "sk-xyz", "label": "ok"},
                },
            },
        }
        out = _sentry_before_send(event, {})
        assert out is not None
        d = out["request"]["data"]
        self.assertEqual(d["title"], "Demo")
        self.assertEqual(d["password"], "[scrubbed]")
        self.assertEqual(d["nested"]["api_key"], "[scrubbed]")
        self.assertEqual(d["nested"]["label"], "ok")

    def test_wipes_auth_route_body_entirely(self) -> None:
        from planning_studio_service.api import _sentry_before_send

        event = {
            "request": {
                "url": "https://api.tryinspira.com/api/auth/login?next=/",
                "data": {"email": "u@x.com", "password": "hunter2"},
                "query_string": "next=/",
            },
        }
        out = _sentry_before_send(event, {})
        assert out is not None
        self.assertEqual(out["request"]["data"], "[scrubbed]")
        self.assertEqual(out["request"]["query_string"], "[scrubbed]")
        self.assertTrue(out["request"]["url"].endswith("?[query-scrubbed]"))

    def test_drops_user_email_username_keeps_id(self) -> None:
        from planning_studio_service.api import _sentry_before_send

        event = {"user": {"id": "u_abc", "email": "u@x.com", "username": "u"}}
        out = _sentry_before_send(event, {})
        assert out is not None
        self.assertEqual(out["user"], {"id": "u_abc"})


if __name__ == "__main__":
    unittest.main()
