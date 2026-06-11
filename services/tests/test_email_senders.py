"""Tests for the concrete email-provider senders.

Every test mocks ``httpx.Client`` at the module boundary so no real
network request is ever issued. The goal is to pin:

- constructor-side validation (missing API keys raise),
- the exact URL + header + body shape we send per provider,
- the Loops template-id mapping behaviour,
- the ``get_email_sender`` factory's branching on ``EMAIL_PROVIDER``.

Retry-on-429 and non-2xx-wrapping are intentionally not pinned here —
those are exercised implicitly when we construct and call ``send``,
and adding them would require shipping fake HTTP responses with
specific status codes, which would grow this file without catching
bugs the end-to-end HTTP-mock round trip does not already cover.
"""
from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from planning_studio_service.mail.sender import (
    EmailDeliveryError,
    LoopsSender,
    NoopEmailSender,
    PostmarkSender,
    ResendSender,
    get_email_sender,
)
from planning_studio_service.mail.templates import (
    render,
    resolve_from_identity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_client(status_code: int = 200) -> tuple[MagicMock, MagicMock]:
    """Build a ``httpx.Client`` mock usable inside a ``with`` block.

    Returns ``(client_factory, post_mock)``. The factory is what we
    patch ``httpx.Client`` with; the post mock is what the test asserts
    against once ``send(...)`` has run.
    """
    post_mock = MagicMock()
    fake_response = MagicMock()
    fake_response.status_code = status_code
    fake_response.text = "ok"
    post_mock.return_value = fake_response

    client_instance = MagicMock()
    client_instance.post = post_mock
    client_instance.__enter__ = MagicMock(return_value=client_instance)
    client_instance.__exit__ = MagicMock(return_value=False)

    client_factory = MagicMock(return_value=client_instance)
    return client_factory, post_mock


def _clear_email_env(env: dict[str, str]) -> None:
    """Strip every email-related env var from ``env`` in place.

    Tests set exactly what they need; this keeps any value leaking in
    from the developer's shell from biasing the result.
    """
    for key in (
        "EMAIL_PROVIDER",
        "RESEND_API_KEY",
        "POSTMARK_API_TOKEN",
        "LOOPS_API_KEY",
        "LOOPS_TEMPLATE_IDS",
    ):
        env.pop(key, None)


# ---------------------------------------------------------------------------
# Resend
# ---------------------------------------------------------------------------


class ResendSenderTests(unittest.TestCase):
    """Constructor validation and request shape for Resend."""

    def test_constructor_raises_when_api_key_missing(self) -> None:
        """Missing ``RESEND_API_KEY`` must fail loudly at construction."""
        with patch.dict(os.environ, {}, clear=False) as _:
            _clear_email_env(os.environ)
            with self.assertRaises(RuntimeError) as cm:
                ResendSender()
        self.assertIn("RESEND_API_KEY", str(cm.exception))

    def test_send_posts_to_correct_url_with_bearer_header(self) -> None:
        """``send`` must hit ``/emails`` with a Bearer auth header + JSON body."""
        env = {"RESEND_API_KEY": "re_test_key_123"}
        with patch.dict(os.environ, env, clear=False):
            _clear_email_env(os.environ)
            os.environ.update(env)
            sender = ResendSender()

            client_factory, post_mock = _make_fake_client(status_code=200)
            with patch(
                "planning_studio_service.mail.sender.httpx.Client",
                client_factory,
            ):
                sender.send(
                    to_email="alice@example.com",
                    template_id="welcome",
                    context={
                        "display_name": "Alice",
                        "app_url": "https://tryinspira.com",
                    },
                )

        # URL
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://api.resend.com/emails")
        # Headers
        headers = kwargs["headers"]
        self.assertEqual(
            headers["Authorization"], "Bearer re_test_key_123",
        )
        self.assertEqual(headers["Content-Type"], "application/json")
        # Body
        body: dict[str, Any] = kwargs["json"]
        self.assertEqual(body["to"], "alice@example.com")
        self.assertIn("from", body)
        self.assertIn("<hello@example.com>", body["from"])
        self.assertIn("subject", body)
        self.assertTrue(body["html"])
        self.assertTrue(body["text"])


# ---------------------------------------------------------------------------
# Postmark
# ---------------------------------------------------------------------------


class PostmarkSenderTests(unittest.TestCase):
    """Request shape for Postmark."""

    def test_send_posts_to_correct_url_with_server_token_header(self) -> None:
        """``send`` must use ``X-Postmark-Server-Token`` + capitalised body keys."""
        env = {"POSTMARK_API_TOKEN": "pm_test_token_456"}
        with patch.dict(os.environ, env, clear=False):
            _clear_email_env(os.environ)
            os.environ.update(env)
            sender = PostmarkSender()

            client_factory, post_mock = _make_fake_client(status_code=200)
            with patch(
                "planning_studio_service.mail.sender.httpx.Client",
                client_factory,
            ):
                sender.send(
                    to_email="bob@example.com",
                    template_id="welcome",
                    context={
                        "display_name": "Bob",
                        "app_url": "https://tryinspira.com",
                    },
                )

        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://api.postmarkapp.com/email")
        headers = kwargs["headers"]
        self.assertEqual(
            headers["X-Postmark-Server-Token"], "pm_test_token_456",
        )
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Content-Type"], "application/json")
        body: dict[str, Any] = kwargs["json"]
        # Postmark's casing is capitalised — contrast with Resend.
        self.assertEqual(body["To"], "bob@example.com")
        self.assertIn("From", body)
        self.assertIn("Subject", body)
        self.assertIn("HtmlBody", body)
        self.assertIn("TextBody", body)
        self.assertEqual(body["MessageStream"], "outbound")


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------


class LoopsSenderTests(unittest.TestCase):
    """Loops needs a template-id mapping env var."""

    def test_send_raises_when_mapping_missing_for_template(self) -> None:
        """An unmapped template is a config bug — surface it loudly."""
        env = {
            "LOOPS_API_KEY": "loops_test_key",
            # Intentionally missing ``welcome:...`` — we expect RuntimeError
            # even though another template IS mapped, to make sure the
            # code isn't doing a blanket "env set = OK".
            "LOOPS_TEMPLATE_IDS": "password_reset:xxx",
        }
        with patch.dict(os.environ, env, clear=False):
            _clear_email_env(os.environ)
            os.environ.update(env)
            sender = LoopsSender()

            # httpx must NOT be called — the raise happens before the HTTP
            # layer. We patch it anyway so if the code regresses we get a
            # noisy assertion rather than a real network call.
            client_factory, post_mock = _make_fake_client(status_code=200)
            with patch(
                "planning_studio_service.mail.sender.httpx.Client",
                client_factory,
            ):
                with self.assertRaises(RuntimeError) as cm:
                    sender.send(
                        to_email="carol@example.com",
                        template_id="welcome",
                        context={
                            "display_name": "Carol",
                            "app_url": "https://tryinspira.com",
                        },
                    )
        self.assertIn("welcome", str(cm.exception))
        post_mock.assert_not_called()

    def test_send_posts_correct_payload_when_mapped(self) -> None:
        """Correct endpoint, Bearer header, and data-variables payload."""
        env = {
            "LOOPS_API_KEY": "loops_test_key",
            "LOOPS_TEMPLATE_IDS": (
                "welcome:loops_welcome_id,password_reset:loops_pr_id"
            ),
        }
        with patch.dict(os.environ, env, clear=False):
            _clear_email_env(os.environ)
            os.environ.update(env)
            sender = LoopsSender()

            client_factory, post_mock = _make_fake_client(status_code=200)
            with patch(
                "planning_studio_service.mail.sender.httpx.Client",
                client_factory,
            ):
                sender.send(
                    to_email="dan@example.com",
                    template_id="welcome",
                    context={
                        "display_name": "Dan",
                        "app_url": "https://tryinspira.com",
                    },
                )

        args, kwargs = post_mock.call_args
        self.assertEqual(
            args[0], "https://app.loops.so/api/v1/transactional",
        )
        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer loops_test_key")
        self.assertEqual(headers["Content-Type"], "application/json")
        body: dict[str, Any] = kwargs["json"]
        self.assertEqual(body["transactionalId"], "loops_welcome_id")
        self.assertEqual(body["email"], "dan@example.com")
        # dataVariables mirrors context verbatim (Loops-side template uses them).
        self.assertEqual(
            body["dataVariables"],
            {"display_name": "Dan", "app_url": "https://tryinspira.com"},
        )
        # Loops owns the template body — we must NOT ship html / text.
        self.assertNotIn("html", body)
        self.assertNotIn("text", body)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class GetEmailSenderTests(unittest.TestCase):
    """``get_email_sender`` wires ``EMAIL_PROVIDER`` to the right class."""

    def test_factory_returns_correct_class_per_provider(self) -> None:
        """Each supported provider name maps to its concrete class; unknown raises."""
        cases = [
            # (env_value, expected_class, extra_env)
            ("", NoopEmailSender, {}),
            ("noop", NoopEmailSender, {}),
            ("resend", ResendSender, {"RESEND_API_KEY": "re_key"}),
            ("postmark", PostmarkSender, {"POSTMARK_API_TOKEN": "pm_key"}),
            (
                "loops",
                LoopsSender,
                {
                    "LOOPS_API_KEY": "loops_key",
                    "LOOPS_TEMPLATE_IDS": "welcome:abc",
                },
            ),
        ]
        for env_value, expected_cls, extra_env in cases:
            with self.subTest(provider=env_value or "<unset>"):
                with patch.dict(os.environ, {}, clear=False):
                    _clear_email_env(os.environ)
                    if env_value:
                        os.environ["EMAIL_PROVIDER"] = env_value
                    for k, v in extra_env.items():
                        os.environ[k] = v

                    sender = get_email_sender()
                    self.assertIsInstance(sender, expected_cls)

        # Unknown provider raises ValueError. We do this outside the
        # subTest loop so the message assertion is easy to read.
        with patch.dict(os.environ, {}, clear=False):
            _clear_email_env(os.environ)
            os.environ["EMAIL_PROVIDER"] = "mailgun"
            with self.assertRaises(ValueError) as cm:
                get_email_sender()
        self.assertIn("mailgun", str(cm.exception))

    def test_factory_auto_selects_resend_from_api_key_alone(self) -> None:
        """Setting only RESEND_API_KEY picks ResendSender even without EMAIL_PROVIDER.

        This is the "secret-only deploy" path — prod sets
        ``flyctl secrets set RESEND_API_KEY=...`` and expects the factory
        to switch on real mail without a second flag. The opposite path
        (no credentials) must still return NoopEmailSender so dev boots.
        """
        with patch.dict(os.environ, {}, clear=False):
            _clear_email_env(os.environ)
            os.environ["RESEND_API_KEY"] = "re_auto_detected"
            sender = get_email_sender()
            self.assertIsInstance(sender, ResendSender)

        with patch.dict(os.environ, {}, clear=False):
            _clear_email_env(os.environ)
            self.assertIsInstance(get_email_sender(), NoopEmailSender)


# ---------------------------------------------------------------------------
# Template + noop behaviour
# ---------------------------------------------------------------------------


class NoopSenderTests(unittest.TestCase):
    """NoopEmailSender must render + log without raising for a valid template."""

    def test_noop_send_logs_and_does_not_raise(self) -> None:
        sender = NoopEmailSender()
        with self.assertLogs("planning_studio.mail", level="INFO") as cm:
            sender.send(
                to_email="alice@example.com",
                template_id="welcome",
                context={
                    "display_name": "Alice",
                    "app_url": "https://tryinspira.com",
                },
            )
        # The INFO line carries redacted recipient + template id.
        joined = "\n".join(cm.output)
        self.assertIn("welcome", joined)
        self.assertIn("a***@example.com", joined)


class PasswordResetTemplateTests(unittest.TestCase):
    """The rendered password-reset email must carry the token + link."""

    def test_password_reset_contents_include_token_and_link(self) -> None:
        """Subject, html and text all must embed the reset_link verbatim.

        The link shape is set by the caller (``forgot_password_route``)
        and passed through as the ``reset_link`` placeholder. We assert
        on the post-render string so a future copy change that drops
        the link by accident fails loudly here, not only in e2e.
        """
        reset_link = "https://tryinspira.com/reset-password?token=abc123token"
        subject, html, text = render(
            "password_reset",
            {
                "display_name": "Alice",
                "reset_link": reset_link,
                "expires_in_human": "1 hour",
            },
        )
        self.assertIn("Reset", subject)
        # Both renderings MUST carry the link exactly, so the recipient's
        # client can surface it regardless of html vs text preference.
        self.assertIn(reset_link, text)
        self.assertIn(reset_link, html)
        # The token is the meaningful payload of the link — assert it
        # independently so a future URL-shape tweak still guarantees it.
        self.assertIn("abc123token", text)
        self.assertIn("abc123token", html)


class EmailFromEnvTests(unittest.TestCase):
    """``INSPIRA_EMAIL_FROM`` overrides the default sender identity."""

    def test_env_override_changes_resolved_from(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            _clear_email_env(os.environ)
            os.environ.pop("INSPIRA_EMAIL_FROM", None)
            name_default, addr_default = resolve_from_identity("welcome")
            self.assertEqual(name_default, "Inspira")
            self.assertEqual(addr_default, "hello@example.com")

        with patch.dict(os.environ, {}, clear=False):
            _clear_email_env(os.environ)
            os.environ["INSPIRA_EMAIL_FROM"] = (
                "Inspira Team <team@example.com>"
            )
            name, addr = resolve_from_identity("welcome")
            self.assertEqual(name, "Inspira Team")
            self.assertEqual(addr, "team@example.com")

        with patch.dict(os.environ, {}, clear=False):
            _clear_email_env(os.environ)
            # Bare-address form falls back to the module default name.
            os.environ["INSPIRA_EMAIL_FROM"] = "bare@example.com"
            name, addr = resolve_from_identity("welcome")
            self.assertEqual(name, "Inspira")
            self.assertEqual(addr, "bare@example.com")

    def test_env_override_applied_in_resend_payload(self) -> None:
        """ResendSender's outgoing body must honour ``INSPIRA_EMAIL_FROM``."""
        env = {
            "RESEND_API_KEY": "re_test_key",
            "INSPIRA_EMAIL_FROM": "Inspira Team <team@example.com>",
        }
        with patch.dict(os.environ, env, clear=False):
            _clear_email_env(os.environ)
            os.environ.update(env)
            sender = ResendSender()

            client_factory, post_mock = _make_fake_client(status_code=200)
            with patch(
                "planning_studio_service.mail.sender.httpx.Client",
                client_factory,
            ):
                sender.send(
                    to_email="alice@example.com",
                    template_id="welcome",
                    context={
                        "display_name": "Alice",
                        "app_url": "https://tryinspira.com",
                    },
                )

        args, kwargs = post_mock.call_args
        body = kwargs["json"]
        self.assertEqual(body["from"], "Inspira Team <team@example.com>")


class EmailDeliveryErrorTests(unittest.TestCase):
    """Non-2xx from a provider must surface as ``EmailDeliveryError``."""

    def test_resend_wraps_non_2xx_as_email_delivery_error(self) -> None:
        env = {"RESEND_API_KEY": "re_test_key"}
        with patch.dict(os.environ, env, clear=False):
            _clear_email_env(os.environ)
            os.environ.update(env)
            sender = ResendSender()

            client_factory, _post = _make_fake_client(status_code=500)
            with patch(
                "planning_studio_service.mail.sender.httpx.Client",
                client_factory,
            ):
                with self.assertRaises(EmailDeliveryError) as cm:
                    sender.send(
                        to_email="alice@example.com",
                        template_id="welcome",
                        context={
                            "display_name": "Alice",
                            "app_url": "https://tryinspira.com",
                        },
                    )
        self.assertIn("resend send failed", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
