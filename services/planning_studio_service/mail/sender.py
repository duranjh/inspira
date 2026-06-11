"""Provider-agnostic email sender abstraction.

Shape: callers import :func:`get_email_sender` and call ``.send(...)``.
The concrete class behind the interface is chosen by the ``EMAIL_PROVIDER``
env var — empty (the default) returns :class:`NoopEmailSender`, which
renders the template and logs it without making a network call.

Three live providers are wired: ``ResendSender``, ``PostmarkSender``, and
``LoopsSender``. Flip ``EMAIL_PROVIDER`` to pick one; each reads its own
API credential from env and translates a ``send(...)`` call into the
provider's HTTP API. Resend and Postmark render our own template bodies
and push the rendered ``html`` / ``text``; Loops delegates template
content to the provider side and only ships a ``dataVariables`` dict.

Safe for unit tests: tests can instantiate :class:`NoopEmailSender`
directly and assert on log output, or substitute a fake sender via
dependency injection. Provider classes are also tested with ``httpx``
mocked at the client-constructor level — no live network calls.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Mapping, Protocol

import httpx

from .templates import registry, render, resolve_from_identity

logger = logging.getLogger("planning_studio.mail")


class EmailDeliveryError(RuntimeError):
    """Raised when a provider fails to accept a transactional email.

    Thin wrapper around ``RuntimeError`` kept for callers that want to
    narrow their ``except`` clause to "the mail layer couldn't deliver"
    without catching unrelated runtime errors. Concrete senders raise
    this (via its ``RuntimeError`` base) on non-2xx HTTP status codes.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact_email(email: str) -> str:
    """Return an email with the local part middle redacted for logs.

    ``alice@example.com`` -> ``a***@example.com``. Short locals collapse
    gracefully: an empty or one-char local becomes ``***@domain``.
    We never log the full address — transactional email recipients are
    PII and the app log stream is broader than the audit stream.
    """
    if "@" not in email:
        # Defensive — upstream code should have validated the address,
        # but we still refuse to leak it whole.
        return "***"
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _log_success(
    *,
    provider: str,
    template_id: str,
    to_email: str,
    status: int,
    latency_ms: float,
) -> None:
    """Single place that formats the success line.

    Keeping this in one function makes it easy to verify in tests that
    every provider logs the same shape, and means a future change to
    add e.g. a message_id only touches one spot.
    """
    logger.info(
        "email_send_ok provider=%s template_id=%s to=%s status=%d latency_ms=%.1f",
        provider,
        template_id,
        _redact_email(to_email),
        status,
        latency_ms,
    )


def _log_failure(
    *,
    provider: str,
    template_id: str,
    to_email: str,
    exc: BaseException,
) -> None:
    """Warning-level log for a failed send.

    We deliberately don't use ``logger.exception`` — the exception is
    re-raised by the caller so the traceback still reaches the outer
    handler. Here we just want a structured breadcrumb.
    """
    logger.warning(
        "email_send_fail provider=%s template_id=%s to=%s exc=%s msg=%s",
        provider,
        template_id,
        _redact_email(to_email),
        type(exc).__name__,
        str(exc),
    )


class EmailSender(Protocol):
    """Structural contract for every email provider Inspira might use.

    Implementations MUST be idempotent at the transport level — the
    calling code cannot tell whether a send succeeded, only whether the
    call raised. Retries, dedupe, and bounce handling live below this
    interface.

    ``context`` is a plain dict of placeholder values. The sender is
    responsible for rendering the template (via :func:`mail.templates.render`)
    before handing the body to the provider.
    """

    def send(
        self,
        *,
        to_email: str,
        template_id: str,
        context: Mapping[str, object],
    ) -> None:
        """Render and transmit ``template_id`` to ``to_email``.

        Raises:
            KeyError: unknown ``template_id``.
            ValueError: missing required placeholder in ``context``.
            RuntimeError: provider-side failure (for concrete senders).
        """
        ...


class NoopEmailSender:
    """Default sender — renders, logs, and stops.

    Safe for dev and tests. Never issues a network call, never raises for
    provider reasons (only for template / context errors). Use it to
    verify template wiring is correct before picking a real provider.
    """

    def send(
        self,
        *,
        to_email: str,
        template_id: str,
        context: Mapping[str, object],
    ) -> None:
        # Look up the template so we surface the KeyError early if
        # ``template_id`` is wrong. Identity resolution honors
        # ``INSPIRA_EMAIL_FROM`` so dev logs show the same sender the
        # user would see in production.
        _ = registry[template_id]
        subject, html_body, text_body = render(template_id, context)
        from_name, from_email = resolve_from_identity(template_id)
        logger.info(
            "noop_email_send template_id=%s to=%s from=%r <%s> subject=%r "
            "html_len=%d text_len=%d",
            template_id,
            _redact_email(to_email),
            from_name,
            from_email,
            subject,
            len(html_body),
            len(text_body),
        )
        # DEBUG log carries the actual body so a developer following the
        # flow in ``tail -f`` can confirm the rendered output. Kept at
        # DEBUG so production logs aren't spammed with full bodies.
        logger.debug(
            "noop_email_send body template_id=%s to=%s\nSubject: %s\n\n[TEXT]\n%s\n\n[HTML]\n%s",
            template_id,
            _redact_email(to_email),
            subject,
            text_body,
            html_body,
        )


# ---------------------------------------------------------------------------
# Resend
# ---------------------------------------------------------------------------


class ResendSender:
    """Send transactional email through Resend's HTTP API.

    Endpoint: ``POST https://api.resend.com/emails``. Auth is a bearer
    token read from ``RESEND_API_KEY``. The request body carries a
    rendered HTML + plain-text pair so Resend has no template state of
    its own — every new template rolls out with a backend deploy, which
    is what we want for dev-prod parity.

    One retry is attempted on HTTP 429 after a 1s sleep; any other 4xx
    or 5xx is raised as ``RuntimeError`` with the response body truncated
    into the message. The caller layers its own retry policy if needed.
    """

    ENDPOINT = "https://api.resend.com/emails"

    def __init__(self) -> None:
        api_key = os.environ.get("RESEND_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ResendSender requires RESEND_API_KEY in the environment.",
            )
        self._api_key = api_key

    def send(
        self,
        *,
        to_email: str,
        template_id: str,
        context: Mapping[str, object],
    ) -> None:
        _ = registry[template_id]
        subject, html_body, text_body = render(template_id, context)

        # Resend accepts either a bare address or "Name <address>" in
        # ``from``; the latter sets the display name on the envelope.
        from_name, from_email = resolve_from_identity(template_id)
        from_header = f"{from_name} <{from_email}>"
        payload = {
            "from": from_header,
            "to": to_email,
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        try:
            status = self._post_with_retry(headers=headers, payload=payload)
        except Exception as exc:
            _log_failure(
                provider="resend",
                template_id=template_id,
                to_email=to_email,
                exc=exc,
            )
            raise
        latency_ms = (time.monotonic() - started) * 1000.0
        _log_success(
            provider="resend",
            template_id=template_id,
            to_email=to_email,
            status=status,
            latency_ms=latency_ms,
        )

    def _post_with_retry(
        self,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> int:
        # One client per send keeps the surface area small — no connection
        # pool to reason about in tests, no async concerns on the sync
        # call path. Mail volume is low; the TCP handshake cost is noise.
        with httpx.Client(timeout=10) as client:
            response = client.post(self.ENDPOINT, headers=headers, json=payload)
            if response.status_code == 429:
                # Resend's rate-limit window is narrow; a single 1s sleep
                # clears most transient bursts and keeps the caller sync.
                time.sleep(1)
                response = client.post(
                    self.ENDPOINT, headers=headers, json=payload,
                )
            if not (200 <= response.status_code < 300):
                body_preview = _preview_body(response)
                raise EmailDeliveryError(
                    f"resend send failed: status={response.status_code}, "
                    f"body={body_preview}",
                )
            return response.status_code


# ---------------------------------------------------------------------------
# Postmark
# ---------------------------------------------------------------------------


class PostmarkSender:
    """Send transactional email through Postmark's ``/email`` endpoint.

    Endpoint: ``POST https://api.postmarkapp.com/email``. Auth is
    ``X-Postmark-Server-Token`` with the token from
    ``POSTMARK_API_TOKEN``. Like Resend, we ship rendered bodies — the
    Postmark-side "template" feature is intentionally not used so the
    source of truth stays in this repo.

    We pin ``MessageStream: "outbound"`` — Postmark requires this for
    transactional mail and would 422 without it.
    """

    ENDPOINT = "https://api.postmarkapp.com/email"

    def __init__(self) -> None:
        api_key = os.environ.get("POSTMARK_API_TOKEN", "").strip()
        if not api_key:
            raise RuntimeError(
                "PostmarkSender requires POSTMARK_API_TOKEN in the environment.",
            )
        self._api_key = api_key

    def send(
        self,
        *,
        to_email: str,
        template_id: str,
        context: Mapping[str, object],
    ) -> None:
        _ = registry[template_id]
        subject, html_body, text_body = render(template_id, context)

        from_name, from_email = resolve_from_identity(template_id)
        from_header = f"{from_name} <{from_email}>"
        payload = {
            "From": from_header,
            "To": to_email,
            "Subject": subject,
            "HtmlBody": html_body,
            "TextBody": text_body,
            "MessageStream": "outbound",
        }
        headers = {
            "X-Postmark-Server-Token": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        try:
            status = self._post_with_retry(headers=headers, payload=payload)
        except Exception as exc:
            _log_failure(
                provider="postmark",
                template_id=template_id,
                to_email=to_email,
                exc=exc,
            )
            raise
        latency_ms = (time.monotonic() - started) * 1000.0
        _log_success(
            provider="postmark",
            template_id=template_id,
            to_email=to_email,
            status=status,
            latency_ms=latency_ms,
        )

    def _post_with_retry(
        self,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> int:
        with httpx.Client(timeout=10) as client:
            response = client.post(self.ENDPOINT, headers=headers, json=payload)
            if response.status_code == 429:
                time.sleep(1)
                response = client.post(
                    self.ENDPOINT, headers=headers, json=payload,
                )
            if not (200 <= response.status_code < 300):
                body_preview = _preview_body(response)
                raise EmailDeliveryError(
                    f"postmark send failed: status={response.status_code}, "
                    f"body={body_preview}",
                )
            return response.status_code


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------


class LoopsSender:
    """Send transactional email through Loops.

    Unlike Resend and Postmark, Loops stores template bodies on its
    side. Callers here pass an Inspira ``template_id`` — we map that to
    a Loops ``transactionalId`` via ``LOOPS_TEMPLATE_IDS``:

        LOOPS_TEMPLATE_IDS=welcome:abc,password_reset:def,account_deleted:ghi,budget_warning:jkl

    The ``context`` dict is forwarded verbatim as ``dataVariables``;
    Loops-side Liquid templates reference ``{{display_name}}`` etc.
    Missing template-id mapping is a configuration bug, so we raise
    ``RuntimeError`` instead of silently falling through.

    Endpoint: ``POST https://app.loops.so/api/v1/transactional``.
    """

    ENDPOINT = "https://app.loops.so/api/v1/transactional"

    def __init__(self) -> None:
        api_key = os.environ.get("LOOPS_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "LoopsSender requires LOOPS_API_KEY in the environment.",
            )
        self._api_key = api_key
        self._template_ids = self._parse_template_ids(
            os.environ.get("LOOPS_TEMPLATE_IDS", ""),
        )

    @staticmethod
    def _parse_template_ids(raw: str) -> dict[str, str]:
        """Parse ``LOOPS_TEMPLATE_IDS`` into ``{our_id: loops_id}``.

        Format is ``key:value,key:value``. Whitespace around keys and
        values is stripped. An empty value is treated as "not set" and
        dropped so a stale ``welcome:`` entry doesn't shadow a real one.
        Malformed entries (no ``:``) are skipped silently rather than
        crashing process startup — the specific-template error later is
        more actionable than "env var parse failed".
        """
        out: dict[str, str] = {}
        if not raw.strip():
            return out
        for piece in raw.split(","):
            if ":" not in piece:
                continue
            key, _, value = piece.partition(":")
            key = key.strip()
            value = value.strip()
            if key and value:
                out[key] = value
        return out

    def send(
        self,
        *,
        to_email: str,
        template_id: str,
        context: Mapping[str, object],
    ) -> None:
        # KeyError here surfaces the same way as other senders — unknown
        # template_id is a programmer error, not a config error.
        _ = registry[template_id]

        loops_id = self._template_ids.get(template_id)
        if not loops_id:
            raise RuntimeError(
                f"loops: no transactional id configured for template={template_id}",
            )

        payload = {
            "transactionalId": loops_id,
            "email": to_email,
            # ``context`` is a Mapping; Loops wants a plain JSON object
            # so we coerce to dict — this also decouples us from any
            # lazy-evaluating Mapping the caller happens to pass.
            "dataVariables": dict(context),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        try:
            status = self._post_with_retry(headers=headers, payload=payload)
        except Exception as exc:
            _log_failure(
                provider="loops",
                template_id=template_id,
                to_email=to_email,
                exc=exc,
            )
            raise
        latency_ms = (time.monotonic() - started) * 1000.0
        _log_success(
            provider="loops",
            template_id=template_id,
            to_email=to_email,
            status=status,
            latency_ms=latency_ms,
        )

    def _post_with_retry(
        self,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> int:
        with httpx.Client(timeout=10) as client:
            response = client.post(self.ENDPOINT, headers=headers, json=payload)
            if response.status_code == 429:
                time.sleep(1)
                response = client.post(
                    self.ENDPOINT, headers=headers, json=payload,
                )
            if not (200 <= response.status_code < 300):
                body_preview = _preview_body(response)
                raise EmailDeliveryError(
                    f"loops send failed: status={response.status_code}, "
                    f"body={body_preview}",
                )
            return response.status_code


def _preview_body(response: httpx.Response) -> str:
    """Return a short, log-safe preview of a response body.

    Provider error bodies can be JSON objects with stack traces or HTML
    error pages. We take the first 500 chars of ``response.text`` and
    strip newlines so the log line stays on one row. If decoding fails
    (non-text body) we fall back to a repr of the raw bytes.
    """
    try:
        text = response.text
    except Exception:  # pragma: no cover — httpx decoding edge case
        return repr(response.content[:500])
    return text.replace("\n", " ").replace("\r", " ")[:500]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_email_sender() -> EmailSender:
    """Return the :class:`EmailSender` for this process.

    Resolution order:

    1. Explicit ``EMAIL_PROVIDER`` env var wins. Supported values:
       ``"resend"``, ``"postmark"``, ``"loops"``, ``"noop"``. Anything
       else raises ``ValueError`` so typos surface immediately.
    2. When ``EMAIL_PROVIDER`` is unset, we auto-pick a provider from
       whichever credential is present: ``RESEND_API_KEY`` ->
       :class:`ResendSender`; otherwise ``POSTMARK_API_TOKEN`` ->
       :class:`PostmarkSender`; otherwise ``LOOPS_API_KEY`` ->
       :class:`LoopsSender`. This lets a production deploy work by
       setting just the secret — no second env-var flag needed.
    3. If no credential is set we fall back to :class:`NoopEmailSender`
       so dev boots still succeed without a provider configured.
    """
    provider = os.environ.get("EMAIL_PROVIDER", "").strip().lower()

    if provider:
        if provider == "noop":
            return NoopEmailSender()
        if provider == "resend":
            return ResendSender()
        if provider == "postmark":
            return PostmarkSender()
        if provider == "loops":
            return LoopsSender()
        raise ValueError(
            f"Unknown EMAIL_PROVIDER={provider!r}. Supported values: "
            "'noop', 'resend', 'postmark', 'loops'.",
        )

    # Auto-detect by credential. Resend is our default production provider
    # per the product docs, so it takes priority; the others are listed
    # for parity with the factory's supported set.
    if os.environ.get("RESEND_API_KEY", "").strip():
        return ResendSender()
    if os.environ.get("POSTMARK_API_TOKEN", "").strip():
        return PostmarkSender()
    if os.environ.get("LOOPS_API_KEY", "").strip():
        return LoopsSender()
    return NoopEmailSender()


__all__ = [
    "EmailDeliveryError",
    "EmailSender",
    "LoopsSender",
    "NoopEmailSender",
    "PostmarkSender",
    "ResendSender",
    "get_email_sender",
]
