"""Billing provider abstraction.

Two implementations ship today:

- :class:`NoopBillingProvider` — the default. It's real enough for product
  work: it persists a local ``subscriptions`` row marking the user as
  subscribed to the named plan, without ever touching Stripe. Checkout
  and portal calls raise :class:`NotConfiguredError` so the HTTP layer
  can return a 501 and the frontend can show "billing not set up yet".
- :class:`StripeBillingProvider` — real Stripe integration, activated
  automatically when ``STRIPE_SECRET_KEY`` is set. The ``stripe`` Python
  SDK is imported lazily inside the methods that need it, so deploys
  without the optional dep still start cleanly.

The provider is constructed once at app start via :func:`get_billing_provider`
and handed into the HTTP routes as a parameter — no module-level singleton.
That lets tests swap in a fake provider by hand without monkey-patching.

When the user gets a Stripe account, the sequence is:

1. ``pip install stripe`` (or ``services[stripe]`` extras).
2. Set ``STRIPE_SECRET_KEY``, ``STRIPE_WEBHOOK_SECRET``,
   ``STRIPE_PRICE_ID_PRO``, ``STRIPE_PRICE_ID_TEAM`` in the environment.
3. Point the Stripe dashboard's webhook at ``/api/v2/billing/webhook``.

No application code changes are needed. :func:`get_billing_provider` reads
the env at construction time and picks the right implementation.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .plans import Plan, free_plan, get_plan

logger = logging.getLogger("planning_studio.billing")


class NotConfiguredError(RuntimeError):
    """Raised when a real-billing operation is requested but Stripe is absent.

    The HTTP layer translates this into a 501 Not Implemented so the client
    gets a clear "this backend doesn't do real charges yet" signal instead
    of a generic 500.
    """


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """Minimal Stripe-agnostic shape. Real Stripe sessions carry more fields
    but the frontend only needs a URL to redirect to and a session id to
    log for correlation."""

    session_id: str
    url: str


@dataclass(frozen=True, slots=True)
class PortalSession:
    url: str


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    """Read-model returned by ``GET /api/v2/billing/subscription``.

    ``plan`` is always populated — missing subscriptions resolve to the
    Free plan so the UI can render unconditionally. ``status`` mirrors
    Stripe's vocabulary (active/trialing/past_due/canceled/...), plus a
    synthetic ``free`` for users who never paid.
    """

    plan: Plan
    status: str
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    current_period_end: str | None
    # When the user first landed on the paid plan (ISO-8601). Seeded
    # on migrate from the row's created_at so the Switch-to-annual
    # 30-day gate runs against real data after deploy.
    started_at: str | None = None
    # Set only while Stripe reports the subscription is trialing.
    trial_ends_at: str | None = None
    # "monthly" or "annual" — mirrors the catalog the user bought
    # against. None for Free tier rows; never None for paid.
    billing_period: str | None = None


@runtime_checkable
class BillingProvider(Protocol):
    """Billing provider contract.

    Both providers read + write through the passed-in ``store`` rather
    than holding their own handle — that way tests can build a provider
    against a temp store without plumbing through the usual app factory.
    """

    @property
    def is_configured(self) -> bool:
        """True when real charges are possible. False means checkout +
        portal return 501, webhook logs-and-ignores, etc."""
        ...

    def get_subscription(
        self, *, user_id: str, store: Any,
    ) -> SubscriptionView:
        ...

    def start_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan: Plan,
        store: Any,
        success_url: str,
        cancel_url: str,
        period: str = "monthly",
    ) -> CheckoutSession:
        ...

    def open_customer_portal(
        self,
        *,
        user_id: str,
        store: Any,
        return_url: str,
    ) -> PortalSession:
        ...

    def handle_webhook(
        self,
        *,
        payload: bytes,
        signature: str | None,
        store: Any,
    ) -> dict[str, Any]:
        """Validate the Stripe signature, apply the event to the store.

        Returns a small debug dict (``{"handled": True, "type": ...}``) the
        HTTP layer can log. Raising here produces a 400; the HTTP wrapper
        wraps anything unexpected in a 500.
        """
        ...


# ---------------------------------------------------------------------------
# Shared helpers — both providers read the same subscriptions table so they
# can agree on "who is on which plan."
# ---------------------------------------------------------------------------


def _view_from_row(row: dict[str, Any] | None) -> SubscriptionView:
    """Turn a ``subscriptions`` row (or its absence) into a view.

    Missing row → Free tier, status ``"free"``. This keeps the frontend
    unconditional: there is always a plan to show.
    """
    if not row:
        return SubscriptionView(
            plan=free_plan(),
            status="free",
            stripe_customer_id=None,
            stripe_subscription_id=None,
            current_period_end=None,
            started_at=None,
            trial_ends_at=None,
            billing_period=None,
        )
    slug = row.get("plan") or "free"
    plan = get_plan(slug) or free_plan()
    return SubscriptionView(
        plan=plan,
        status=row.get("status") or "free",
        stripe_customer_id=row.get("stripe_customer_id"),
        stripe_subscription_id=row.get("stripe_subscription_id"),
        current_period_end=row.get("current_period_end"),
        # New columns (migration 2026-04). Older rows seeded from created_at.
        started_at=row.get("started_at") or row.get("created_at"),
        trial_ends_at=row.get("trial_ends_at"),
        billing_period=row.get("billing_period"),
    )


# ---------------------------------------------------------------------------
# Noop provider — the default. Keeps product + UI work moving without a
# Stripe account on file.
# ---------------------------------------------------------------------------


class NoopBillingProvider:
    """Local-only billing for dev / pre-Stripe deploys.

    Behaviour:
    - ``get_subscription`` reads the local ``subscriptions`` table; no row
      → Free tier.
    - ``start_checkout`` / ``open_customer_portal`` raise
      :class:`NotConfiguredError`. The HTTP layer turns that into a 501
      so the frontend can show a clean "coming soon" banner.
    - ``handle_webhook`` logs and returns 200. Stripe retries webhook
      deliveries on non-2xx, so returning 200 when we're not configured
      avoids filling up a would-be dashboard.
    """

    is_configured: bool = False

    def get_subscription(
        self, *, user_id: str, store: Any,
    ) -> SubscriptionView:
        row = store.get_subscription(user_id=user_id)
        return _view_from_row(row)

    def start_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan: Plan,
        store: Any,
        success_url: str,
        cancel_url: str,
        period: str = "monthly",
    ) -> CheckoutSession:
        raise NotConfiguredError(
            "Stripe is not configured on this deployment. Set "
            "STRIPE_SECRET_KEY plus the per-plan STRIPE_PRICE_ID_* variables.",
        )

    def open_customer_portal(
        self,
        *,
        user_id: str,
        store: Any,
        return_url: str,
    ) -> PortalSession:
        raise NotConfiguredError(
            "Stripe is not configured on this deployment.",
        )

    def handle_webhook(
        self,
        *,
        payload: bytes,
        signature: str | None,
        store: Any,
    ) -> dict[str, Any]:
        logger.info(
            "billing webhook received without stripe configured — ignoring "
            "(payload=%d bytes, signature_present=%s)",
            len(payload or b""),
            signature is not None,
        )
        return {"handled": False, "reason": "stripe_not_configured"}

    # ----- Dev/test helper -------------------------------------------------
    # Not part of the Protocol. Lets tests simulate a subscription without
    # going through Stripe. Keeps the seam visible rather than monkey-
    # patching the store.

    def record_local_subscription(
        self,
        *,
        user_id: str,
        plan_slug: str,
        status: str = "active",
        store: Any,
    ) -> SubscriptionView:
        plan = get_plan(plan_slug) or free_plan()
        store.upsert_subscription(
            user_id=user_id,
            plan=plan.slug,
            status=status,
        )
        return self.get_subscription(user_id=user_id, store=store)


# ---------------------------------------------------------------------------
# Stripe provider — activated automatically when STRIPE_SECRET_KEY is set.
# ---------------------------------------------------------------------------


class StripeBillingProvider:
    """Real Stripe integration.

    The ``stripe`` Python SDK is imported lazily in the methods that need
    it, so an environment without the optional dep still starts cleanly
    — it just never reaches any of these methods (the factory picks the
    Noop provider in that case).

    Error handling is deliberately thin here: Stripe raises its own
    typed exceptions (``stripe.error.*``). Let them propagate; the HTTP
    layer turns them into generic 502s with a correlation id. We don't
    leak the raw Stripe request id — that carries account-identifying
    bits.
    """

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str | None,
        price_ids: dict[str, str],
        annual_price_ids: dict[str, str] | None = None,
    ) -> None:
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        # slug -> stripe price id. Missing slugs mean "this plan isn't
        # purchasable yet"; start_checkout raises for those.
        self._price_ids = dict(price_ids)
        # slug -> stripe price id for the annual variant of the same plan.
        # When start_checkout is called with period="annual" we look here
        # first; absence falls back to monthly (start_checkout raises if
        # period=="annual" was requested but no annual price is configured
        # for that plan).
        self._annual_price_ids = dict(annual_price_ids or {})

    @property
    def is_configured(self) -> bool:
        return True

    def _stripe(self) -> Any:
        """Lazy import so tests that don't touch Stripe don't import it."""
        import stripe  # type: ignore

        stripe.api_key = self._secret_key
        return stripe

    def get_subscription(
        self, *, user_id: str, store: Any,
    ) -> SubscriptionView:
        row = store.get_subscription(user_id=user_id)
        return _view_from_row(row)

    def start_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan: Plan,
        store: Any,
        success_url: str,
        cancel_url: str,
        period: str = "monthly",
    ) -> CheckoutSession:
        # Pick the right Stripe price id based on billing period. Annual
        # falls back to monthly if no annual price is configured for the
        # plan (lets the system stay functional during a rollout where
        # only one period is wired). Caller passes period from the FE
        # toggle; the route validates the literal before getting here.
        normalized_period = (period or "monthly").strip().lower()
        if normalized_period not in ("monthly", "annual"):
            normalized_period = "monthly"
        price_id: str | None
        if normalized_period == "annual":
            price_id = self._annual_price_ids.get(plan.slug)
            if not price_id:
                # Annual requested but not configured — fail loud rather
                # than silently downgrade the user to a monthly charge
                # they didn't ask for.
                raise NotConfiguredError(
                    f"No annual Stripe price id configured for plan "
                    f"'{plan.slug}'. Set "
                    f"{plan.stripe_annual_price_env_var or 'STRIPE_PRICE_ID_*_ANNUAL'}.",
                )
        else:
            price_id = self._price_ids.get(plan.slug)
            if not price_id:
                raise NotConfiguredError(
                    f"No Stripe price id configured for plan '{plan.slug}'. "
                    f"Set {plan.stripe_price_env_var or 'STRIPE_PRICE_ID_*'}.",
                )

        stripe = self._stripe()

        # Re-use the Stripe customer id we've seen for this user, if any.
        # Otherwise Stripe will create one for us inside checkout.sessions.
        row = store.get_subscription(user_id=user_id)
        customer_id = (row or {}).get("stripe_customer_id") or None

        kwargs: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            # Carry the user id + period across to the webhook so we can
            # attribute the subscription back to the right local user row
            # AND record the billing cadence (monthly vs annual) on the
            # subscriptions table.
            "client_reference_id": user_id,
            "metadata": {
                "inspira_user_id": user_id,
                "plan_slug": plan.slug,
                "billing_period": normalized_period,
            },
        }
        if customer_id:
            kwargs["customer"] = customer_id
        else:
            kwargs["customer_email"] = user_email

        session = stripe.checkout.Session.create(**kwargs)
        return CheckoutSession(session_id=session.id, url=session.url)

    def open_customer_portal(
        self,
        *,
        user_id: str,
        store: Any,
        return_url: str,
    ) -> PortalSession:
        row = store.get_subscription(user_id=user_id)
        customer_id = (row or {}).get("stripe_customer_id")
        if not customer_id:
            raise NotConfiguredError(
                "This user has no Stripe customer yet. Ask them to start a "
                "checkout first; Stripe creates the customer record on the "
                "first successful checkout.",
            )
        stripe = self._stripe()
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return PortalSession(url=session.url)

    def handle_webhook(
        self,
        *,
        payload: bytes,
        signature: str | None,
        store: Any,
    ) -> dict[str, Any]:
        if not self._webhook_secret:
            # Fail closed: refuse ALL events when the secret is absent so
            # an attacker cannot forge payment confirmations or subscription
            # upgrades by hitting this endpoint with crafted payloads.
            # The startup log in get_billing_provider() already warns ops;
            # a 400 here tells Stripe the delivery failed (it will retry,
            # but that is preferable to silently accepting unsigned events).
            raise ValueError(
                "STRIPE_WEBHOOK_SECRET is not set — webhook rejected. "
                "Configure the secret to re-enable webhook processing.",
            )

        stripe = self._stripe()
        # Validate signature FIRST. Raises
        # stripe.error.SignatureVerificationError on mismatch; the HTTP
        # layer converts that into a 400. The idempotency check below
        # only runs against signature-verified events so an attacker
        # cannot use a forged payload to learn (or pollute) which event
        # ids we have seen.
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature or "",
            secret=self._webhook_secret,
        )

        event_id = (
            event.get("id") if isinstance(event, dict)
            else getattr(event, "id", None)
        )
        event_type = (
            event.get("type") if isinstance(event, dict)
            else getattr(event, "type", None)
        )
        data_object = (
            (event.get("data") or {}).get("object")
            if isinstance(event, dict)
            else getattr(getattr(event, "data", None), "object", None)
        ) or {}

        # Idempotency check. Stripe retries deliveries on 5xx and on
        # missed acks (at-least-once delivery for ~3 days), so the same
        # ``event.id`` can hit this endpoint multiple times. If we have
        # already applied this event, return 200 so Stripe stops
        # retrying, but skip ``_apply_stripe_event`` so we don't
        # double-charge or double-flip subscription state.
        event_id_str = str(event_id) if event_id is not None else ""
        if event_id_str and store.is_webhook_event_processed(
            event_id=event_id_str,
        ):
            logger.info(
                "stripe webhook event %s (type=%s) already processed — "
                "skipping re-apply",
                event_id_str,
                event_type,
            )
            return {
                "status": "already_processed",
                "type": event_type,
                "event_id": event_id_str,
            }

        handled = _apply_stripe_event(
            event_type=str(event_type or ""),
            data=data_object,
            store=store,
        )
        # Mark the event as processed AFTER a successful apply. A
        # raised exception during apply propagates out of this method,
        # the HTTP layer turns it into a 4xx/5xx, and Stripe retries —
        # the missing row in processed_webhook_events is what permits
        # that retry to actually re-run the event. ``handled=False``
        # is still a successful processing decision (we deliberately
        # ignored the event type), so we DO record it to avoid
        # re-evaluating the same ignored event on every retry.
        if event_id_str:
            store.mark_webhook_event_processed(
                event_id=event_id_str,
                event_type=str(event_type) if event_type is not None else None,
            )
        return {"handled": handled, "type": event_type}


def _apply_stripe_event(
    *,
    event_type: str,
    data: Any,
    store: Any,
) -> bool:
    """Translate a Stripe event payload into a local subscriptions row.

    Only four event types touch the store; everything else is logged and
    ignored. Keeping the surface small means we're not implicitly relying
    on any other Stripe behaviour — if Stripe adds something new, we see
    it in the logs and make an explicit call about whether to handle it.
    """

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    if event_type == "checkout.session.completed":
        user_id = _get(data, "client_reference_id") or (
            _get(_get(data, "metadata") or {}, "inspira_user_id")
        )
        stripe_customer_id = _get(data, "customer")
        stripe_subscription_id = _get(data, "subscription")
        plan_slug = (
            _get(_get(data, "metadata") or {}, "plan_slug") or "pro"
        )
        if not user_id:
            logger.warning(
                "checkout.session.completed with no client_reference_id "
                "— cannot attribute subscription to a local user",
            )
            return False
        store.upsert_subscription(
            user_id=user_id,
            plan=plan_slug,
            status="active",
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
        )
        return True

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
    ):
        stripe_subscription_id = _get(data, "id")
        status = _get(data, "status") or "active"
        current_period_end = _get(data, "current_period_end")
        # Stripe returns an integer unix timestamp; keep it as a string
        # ISO-ish representation for the store. Not critical — the store
        # stores it as TEXT — but keeps the column human-readable.
        cpe = str(current_period_end) if current_period_end is not None else None
        metadata = _get(data, "metadata") or {}
        user_id = _get(metadata, "inspira_user_id")
        plan_slug = _get(metadata, "plan_slug") or "pro"
        stripe_customer_id = _get(data, "customer")
        if not user_id:
            # Attempt a fallback: look the user up by stripe_customer_id.
            user_id = store.find_user_by_stripe_customer_id(
                stripe_customer_id=stripe_customer_id,
            )
        if not user_id:
            logger.warning(
                "%s received but no local user could be identified "
                "(stripe_customer_id=%s)",
                event_type,
                stripe_customer_id,
            )
            return False
        store.upsert_subscription(
            user_id=user_id,
            plan=plan_slug,
            status=status,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            current_period_end=cpe,
        )
        return True

    if event_type == "customer.subscription.deleted":
        stripe_subscription_id = _get(data, "id")
        stripe_customer_id = _get(data, "customer")
        user_id = store.find_user_by_stripe_customer_id(
            stripe_customer_id=stripe_customer_id,
        )
        if not user_id:
            logger.warning(
                "customer.subscription.deleted received for unknown "
                "stripe_customer_id=%s",
                stripe_customer_id,
            )
            return False
        # Downgrade to Free rather than deleting the row outright — we
        # want to retain the stripe_customer_id so a future upgrade can
        # re-use the Stripe customer.
        store.upsert_subscription(
            user_id=user_id,
            plan="free",
            status="canceled",
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=None,
        )
        return True

    logger.info("ignoring stripe event type: %s", event_type)
    return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_billing_provider() -> BillingProvider:
    """Return the appropriate provider for the current environment.

    Picks Stripe iff ``STRIPE_SECRET_KEY`` is set. Otherwise returns the
    Noop provider. Safe to call at app-start time; no network I/O.
    """
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        return NoopBillingProvider()
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip() or None
    if not webhook_secret:
        logger.warning(
            "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is not — "
            "the /api/v2/billing/webhook endpoint will reject ALL incoming "
            "events with HTTP 400 until the secret is configured. "
            "Set STRIPE_WEBHOOK_SECRET to enable webhook processing.",
        )
    price_ids: dict[str, str] = {}
    annual_price_ids: dict[str, str] = {}
    for plan in (get_plan("pro"), get_plan("team")):
        if plan is None:
            continue
        if plan.stripe_price_env_var:
            value = os.environ.get(plan.stripe_price_env_var, "").strip()
            if value:
                price_ids[plan.slug] = value
        if plan.stripe_annual_price_env_var:
            value = os.environ.get(plan.stripe_annual_price_env_var, "").strip()
            if value:
                annual_price_ids[plan.slug] = value
    return StripeBillingProvider(
        secret_key=secret,
        webhook_secret=webhook_secret,
        price_ids=price_ids,
        annual_price_ids=annual_price_ids,
    )
