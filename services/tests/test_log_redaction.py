"""Regression tests for log redaction helpers in auth + mail layers.

PII audit follow-up. The backend log stream is broader than the audit
stream — anything emitted via ``logger`` ends up in fly logs, Sentry
breadcrumbs, and any future log-aggregator the team wires up. Email
addresses, password hashes, BYOK ciphertexts, raw API keys, and
``Authorization`` headers must NEVER appear there.

These tests pin the redaction contract so a future refactor that
accidentally inlines a ``user`` dict or a raw ``to_email`` into a log
line gets caught by CI.
"""
from __future__ import annotations

import logging
import unittest

from planning_studio_service.auth import (
    _queue_password_reset_email,
    _redact_email,
    _redact_user,
)


class RedactUserTests(unittest.TestCase):
    """``_redact_user`` strips everything except ``user_id``."""

    def test_drops_email(self) -> None:
        full_user = {
            "user_id": "user-abc-123",
            "email": "alice@example.com",
            "password_hash": "$argon2id$v=19$m=65536,t=3,p=2$abc...",
            "display_name": "Alice",
        }
        redacted = _redact_user(full_user)
        self.assertEqual(redacted, {"user_id": "user-abc-123"})
        # Belt and braces: the redacted dict, when stringified for a log
        # line, must not surface any PII substrings.
        as_log = repr(redacted)
        self.assertNotIn("alice", as_log)
        self.assertNotIn("example.com", as_log)
        self.assertNotIn("argon2", as_log)

    def test_handles_none(self) -> None:
        # Auth resolvers can return ``None`` for unknown users; the
        # helper must not crash when fed one.
        self.assertEqual(_redact_user(None), {"user_id": None})

    def test_handles_non_dict(self) -> None:
        # Defensive — a future refactor passing a Pydantic model or a
        # tuple should still get a safe projection back.
        self.assertEqual(_redact_user("user-abc"), {"user_id": None})


class RedactEmailTests(unittest.TestCase):
    """``_redact_email`` keeps domain visible, masks the local part."""

    def test_masks_local_part(self) -> None:
        self.assertEqual(_redact_email("alice@example.com"), "a***@example.com")

    def test_short_local_collapses(self) -> None:
        # One-char and empty locals collapse to ``***@domain`` so we do
        # not leak the full identifier through the unmasked first char.
        self.assertEqual(_redact_email("a@example.com"), "***@example.com")
        self.assertEqual(_redact_email("@example.com"), "***@example.com")

    def test_malformed_returns_stars(self) -> None:
        self.assertEqual(_redact_email("not-an-email"), "***")


class PasswordResetLogRedactionTests(unittest.TestCase):
    """``_queue_password_reset_email`` must not leak the recipient address.

    The helper logs two INFO lines per dispatch (one before send, one
    after). Both used to format ``recipient=%s`` with the raw email; the
    audit fix routes them through ``_redact_email`` instead. This test
    captures the actual log records and asserts the raw address never
    appears in any of them.
    """

    def test_recipient_is_redacted(self) -> None:
        with self.assertLogs("planning_studio.auth", level="INFO") as caplog:
            _queue_password_reset_email(
                to_email="alice@example.com",
                display_name="Alice",
                reset_link="http://localhost:5173/reset-password?token=t",
            )
        all_messages = "\n".join(caplog.output)
        # The full local-part-plus-domain string must not appear ANYWHERE
        # in the log output — neither in the dispatched line nor the sent
        # line. The redacted form ``a***@example.com`` is allowed.
        self.assertNotIn("alice@example.com", all_messages)
        self.assertIn("a***@example.com", all_messages)
        # Sanity: the log lines we expect were actually emitted.
        self.assertTrue(
            any("queue_password_reset_email" in line for line in caplog.output),
            f"expected at least one queue_password_reset_email line; got {caplog.output!r}",
        )


if __name__ == "__main__":  # pragma: no cover — manual runner shim
    logging.basicConfig(level=logging.INFO)
    unittest.main()
