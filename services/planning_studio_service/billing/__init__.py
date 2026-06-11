"""Billing package — Stripe-ready plans + provider abstraction for Inspira.

Public surface:

- :mod:`billing.plans` — static plan catalog + :class:`Limits` dataclass
  used to express entitlements. Safe to import even when Stripe is not
  configured.
- :mod:`billing.provider` — the :class:`BillingProvider` Protocol plus two
  implementations:
  * :class:`NoopBillingProvider` — the default. Records "subscribed" locally
    without touching a real payment processor. Used in dev, tests, and any
    deploy that doesn't yet have a Stripe account. Checkout + portal calls
    raise 501 so the UI can gracefully degrade.
  * :class:`StripeBillingProvider` — real implementation, gated on
    ``STRIPE_SECRET_KEY`` being set. Lazy-imports the ``stripe`` SDK so
    environments without the optional dep still start cleanly.

The intent is that flipping a single env var (``STRIPE_SECRET_KEY``) plus
populating the per-plan ``STRIPE_PRICE_ID_*`` IDs switches the service from
"scaffolded, not charging" to "actually charging" without touching
application code.
"""

from .plans import Limits, Plan, PLANS, get_plan, plan_catalog_json
from .provider import (
    BillingProvider,
    CheckoutSession,
    NoopBillingProvider,
    NotConfiguredError,
    StripeBillingProvider,
    get_billing_provider,
)

__all__ = [
    "BillingProvider",
    "CheckoutSession",
    "Limits",
    "NoopBillingProvider",
    "NotConfiguredError",
    "PLANS",
    "Plan",
    "StripeBillingProvider",
    "get_billing_provider",
    "get_plan",
    "plan_catalog_json",
]
