"""Tests for the forgot-password / reset-password flow.

The routes cover three security-sensitive behaviors:

- The forgot endpoint must NEVER leak whether an email is registered
  (audit M3 extension). Every call returns 200 with the same body.
- The reset endpoint must only accept a valid, unexpired, unused token.
- A successful reset must rotate the password AND invalidate every
  other live reset token for that user (so a sibling link in another
  inbox / device no longer works).

The mail dispatch is intercepted with a fake sender so the raw reset
token is captured for assertion -- the real code path emails it and we
never see it in the HTTP response (by design).
"""
from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]

from planning_studio_service import auth as auth_module
from planning_studio_service.store import now_timestamp  # noqa: F401


class _CaptureSender:
    """Stand-in email sender that records every .send() call.

    Swapped in for the forgot-password tests so we can pull the raw
    reset token straight out of the context dict without going through
    the mailbox. The real sender is NoopEmailSender by default; this
    subclass adds an in-test "inbox" we can assert on.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(
        self, *, to_email: str, template_id: str, context,
    ) -> None:
        # Take a shallow copy of context -- the route hands us a dict we
        # could mutate, and we want a stable snapshot per call.
        self.sent.append({
            "to_email": to_email,
            "template_id": template_id,
            "context": dict(context),
        })


def _extract_token_from_link(reset_link: str) -> str:
    # reset_link looks like "http://host/reset-password?token=abc123".
    # Historically we emitted the legacy "?reset_token=" shape; accept
    # both for any cached test fixture. Check ``reset_token`` FIRST
    # because the canonical marker ``token=`` is a suffix of it.
    for marker in ("reset_token=", "token="):
        if marker in reset_link:
            idx = reset_link.index(marker) + len(marker)
            return reset_link[idx:]
    raise AssertionError(f"no token marker found in {reset_link!r}")


class ForgotPasswordEnumerationTests(unittest.TestCase):
    """Every caller must see the same 200 response regardless of input."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.captured = _CaptureSender()
        self._patcher = patch.object(
            auth_module,
            "_queue_password_reset_email",
            side_effect=self._capture_enqueue,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self.temp_dir.cleanup()

    def _capture_enqueue(
        self, *, to_email: str, display_name: str, reset_link: str,
    ) -> None:
        self.captured.sent.append({
            "to_email": to_email,
            "display_name": display_name,
            "reset_link": reset_link,
        })

    def test_unknown_email_returns_200_generic(self) -> None:
        """Calling forgot-password for a never-registered email must look
        identical to the happy path -- same status, same body. No email
        is actually queued, but the caller cannot tell."""
        response = self.client.post(
            "/api/auth/forgot-password",
            json={"email": "ghost@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        # The copy mentions "if an account exists" -- never "no such email"
        # or anything else that confirms/denies registration.
        self.assertIn("if an account exists", payload["message"].lower())
        # No email was queued for the unknown address.
        self.assertEqual(self.captured.sent, [])

    def test_known_email_also_returns_200_generic(self) -> None:
        """For a KNOWN email we still return the same body shape.

        This is the paired assertion: body equality between the
        unknown-email and known-email cases is the actual enumeration
        defense. We don't just check 200 -- we check the copy matches.
        """
        signup_and_login(
            self.client,
            email="real@example.com",
            password="original-pwd-123",
        )
        self.client.cookies.clear()
        unknown = self.client.post(
            "/api/auth/forgot-password",
            json={"email": "ghost@example.com"},
        ).json()
        known = self.client.post(
            "/api/auth/forgot-password",
            json={"email": "real@example.com"},
        ).json()
        self.assertEqual(unknown, known)
        # One email was queued -- the known-address one.
        self.assertEqual(len(self.captured.sent), 1)
        self.assertEqual(self.captured.sent[0]["to_email"], "real@example.com")


class ResetPasswordEndToEndTests(unittest.TestCase):
    """The happy path: request a token, receive it, reset, log in."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.email = "carol@example.com"
        self.original_password = "original-horse-7"
        self.new_password = "brandnew-battery-42"
        signup_and_login(
            self.client, email=self.email, password=self.original_password,
        )
        # Pretend the user forgot: drop the session so the forgot flow
        # looks like an anonymous request.
        self.client.cookies.clear()
        self.captured: list[dict[str, object]] = []
        self._patcher = patch.object(
            auth_module,
            "_queue_password_reset_email",
            side_effect=self._capture_enqueue,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self.temp_dir.cleanup()

    def _capture_enqueue(
        self, *, to_email: str, display_name: str, reset_link: str,
    ) -> None:
        self.captured.append({
            "to_email": to_email,
            "display_name": display_name,
            "reset_link": reset_link,
        })

    def test_full_flow_reset_then_login_with_new_password(self) -> None:
        # 1. Request a reset.
        response = self.client.post(
            "/api/auth/forgot-password",
            json={"email": self.email},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.captured), 1)
        raw_token = _extract_token_from_link(
            str(self.captured[0]["reset_link"]),
        )
        self.assertTrue(raw_token)

        # 2. Consume the token via the reset endpoint.
        reset = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "new_password": self.new_password},
        )
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(reset.json()["ok"])

        # 3. The OLD password must no longer log in.
        old_login = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": self.original_password},
        )
        self.assertEqual(old_login.status_code, 401)

        # 4. The NEW password does.
        new_login = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": self.new_password},
        )
        self.assertEqual(new_login.status_code, 200)
        self.assertEqual(new_login.json()["email"], self.email)

    def test_token_cannot_be_reused(self) -> None:
        """Consuming a token marks it used -- second call is rejected."""
        self.client.post(
            "/api/auth/forgot-password",
            json={"email": self.email},
        )
        raw_token = _extract_token_from_link(
            str(self.captured[0]["reset_link"]),
        )
        first = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "new_password": self.new_password},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "new_password": "another-pwd-123"},
        )
        self.assertEqual(second.status_code, 400)
        self.assertEqual(
            (second.json().get("detail") or {}).get("error"),
            "invalid_or_expired_token",
        )


class ResetPasswordExpiryTests(unittest.TestCase):
    """Expired tokens must be rejected."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.email = "dave@example.com"
        signup_and_login(
            self.client, email=self.email, password="original-pwd-9",
        )
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_expired_token_rejected(self) -> None:
        """An already-expired token returns 400 and does not rotate the pwd.

        We mint a token via the store, then backdate its ``expires_at``
        directly in SQLite so we don't need to wait for the real TTL.
        """
        user = self.store.get_user_by_email(self.email)
        assert user is not None
        raw_token = self.store.create_password_reset_token(user["user_id"])
        # Backdate expiry -- any timestamp before now_timestamp() counts
        # as expired per consume_password_reset_token's comparison.
        from datetime import datetime, timezone as _tz
        past = (datetime.now(_tz.utc) - timedelta(hours=2)).isoformat(
            timespec="seconds",
        )
        import hashlib
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.store._connect() as conn:  # noqa: SLF001 -- test-only
            conn.execute(
                "UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?",
                (past, token_hash),
            )
            conn.commit()
        response = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "new_password": "wont-be-applied-8"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "invalid_or_expired_token",
        )
        # Confirm the password was NOT rotated -- the original one still works.
        login = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": "original-pwd-9"},
        )
        self.assertEqual(login.status_code, 200)


class ResetPasswordValidationTests(unittest.TestCase):
    """Pydantic-level validation on the reset endpoint."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_short_new_password_rejected_with_422(self) -> None:
        response = self.client.post(
            "/api/auth/reset-password",
            json={"token": "anything", "new_password": "short"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unknown_token_rejected_with_400(self) -> None:
        response = self.client.post(
            "/api/auth/reset-password",
            json={
                "token": "deadbeef" * 8,
                "new_password": "this-is-long-enough",
            },
        )
        self.assertEqual(response.status_code, 400)


class ForgotPasswordProviderWiringTests(unittest.TestCase):
    """End-to-end: the route must call the installed EmailSender exactly once.

    Unlike the enumeration and expiry tests we patch at the sender
    level (``mail.get_email_sender``) instead of the queue helper, so
    this covers the full render + dispatch path — a regression that
    drops template_id or the reset_link placeholder shows up here.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.email = "fred@example.com"
        signup_and_login(
            self.client, email=self.email, password="original-pwd-42",
        )
        self.client.cookies.clear()
        self.sent: list[dict[str, object]] = []

        class _StubSender:
            def send(_self, *, to_email, template_id, context):  # noqa: N805
                self.sent.append({
                    "to_email": to_email,
                    "template_id": template_id,
                    "context": dict(context),
                })

        from planning_studio_service import mail as mail_pkg

        self._patcher = patch.object(
            mail_pkg, "get_email_sender", return_value=_StubSender(),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self.temp_dir.cleanup()

    def test_handler_calls_provider_once_with_expected_payload(self) -> None:
        response = self.client.post(
            "/api/auth/forgot-password",
            json={"email": self.email},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.sent), 1)
        call = self.sent[0]
        self.assertEqual(call["to_email"], self.email)
        self.assertEqual(call["template_id"], "password_reset")
        ctx = call["context"]
        # The reset link is the meaningful payload; it must contain the
        # raw token and the canonical ``/reset-password?token=`` path.
        self.assertIn("reset_link", ctx)
        reset_link = str(ctx["reset_link"])
        self.assertIn("reset-password?token=", reset_link)
        # Display name falls back to the local-part when the user didn't
        # set one at signup.
        self.assertIn("display_name", ctx)
        # Expiry copy is derived from the store's TTL; just assert it's
        # a non-empty string so a future change can't ship an empty slot.
        self.assertTrue(str(ctx["expires_in_human"]))


class SiblingTokenInvalidationTests(unittest.TestCase):
    """After a successful reset, every other live token must become dead."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.email = "erin@example.com"
        signup_and_login(
            self.client, email=self.email, password="original-horse-8",
        )
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_successful_reset_kills_sibling_tokens(self) -> None:
        user = self.store.get_user_by_email(self.email)
        assert user is not None
        uid = user["user_id"]
        # Mint two tokens -- A (consumed) and B (should be invalidated).
        token_a = self.store.create_password_reset_token(uid)
        token_b = self.store.create_password_reset_token(uid)
        self.assertNotEqual(token_a, token_b)
        first = self.client.post(
            "/api/auth/reset-password",
            json={"token": token_a, "new_password": "long-enough-pwd"},
        )
        self.assertEqual(first.status_code, 200)
        # Token B should no longer work even though it was never consumed.
        second = self.client.post(
            "/api/auth/reset-password",
            json={"token": token_b, "new_password": "another-long-pwd"},
        )
        self.assertEqual(second.status_code, 400)


class ForgotPasswordObservabilityTests(unittest.TestCase):
    """``_queue_password_reset_email`` logs dispatch + provider for fly logs.

    QA regression: fly logs were silent on forgot-password calls, so
    support couldn't tell "user never clicked" from "provider dropped
    the send". The helper now emits a ``dispatched`` INFO line BEFORE
    the send so the dispatch is always visible even if the provider
    hangs. After a successful send a second ``sent`` INFO line carries
    the provider class name.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.email = "obs@anthropic.ai"
        signup_and_login(
            self.client, email=self.email, password="original-obs-1",
        )
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dispatched_log_fires_before_send(self) -> None:
        """The ``dispatched`` log line must include provider + recipient."""
        import logging as _logging
        from planning_studio_service import mail as mail_pkg

        class _CaptureSender:
            def send(self, *, to_email, template_id, context):  # noqa: ARG002
                return None

        with self.assertLogs("planning_studio.auth", level=_logging.INFO) as captured:
            with patch.object(
                mail_pkg, "get_email_sender", return_value=_CaptureSender(),
            ):
                response = self.client.post(
                    "/api/auth/forgot-password",
                    json={"email": self.email},
                )
            self.assertEqual(response.status_code, 200)

        messages = [rec.getMessage() for rec in captured.records]
        dispatched = [m for m in messages if "dispatched" in m]
        sent = [m for m in messages if m.startswith(
            "queue_password_reset_email: sent",
        )]
        self.assertEqual(
            len(dispatched), 1,
            f"expected one dispatched log, got {dispatched!r}",
        )
        self.assertIn("provider=_CaptureSender", dispatched[0])
        # PII audit: the recipient must be REDACTED in the log line.
        # ``alice@example.com`` -> ``a***@example.com``. The raw address
        # must NOT appear, the redacted form MUST. See
        # ``planning_studio_service.mail.sender._redact_email``.
        self.assertNotIn(self.email, dispatched[0])
        local_first, _, domain = self.email.partition("@")
        redacted = f"{local_first[0]}***@{domain}" if local_first else f"***@{domain}"
        self.assertIn(f"recipient={redacted}", dispatched[0])
        self.assertEqual(
            len(sent), 1,
            f"expected one sent log, got {sent!r}",
        )
        self.assertIn("provider=_CaptureSender", sent[0])
        self.assertNotIn(self.email, sent[0])
        self.assertIn(f"recipient={redacted}", sent[0])


if __name__ == "__main__":
    unittest.main()
