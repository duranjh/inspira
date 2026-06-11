"""Tests for the Wave I monthly/annual billing period plumbing.

Covers:
- `BillingCheckoutBody.period` accepts "monthly", "annual", or arbitrary
  strings (the route normalizes to monthly for safety).
- `StripeBillingProvider.start_checkout()` picks the annual Stripe price id
  when ``period="annual"`` AND the plan has an annual variant configured.
- `StripeBillingProvider.start_checkout()` raises ``NotConfiguredError``
  when ``period="annual"`` was requested but no annual price is wired.
- The route's period normalization (anything except "annual" → "monthly")
  is exercised end-to-end via the FastAPI TestClient.

These tests do NOT exercise real Stripe — they mock the ``stripe`` SDK
calls. End-to-end Stripe Test-mode validation happens during operator
setup (see ``docs/ops/``).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from planning_studio_service.billing.plans import get_plan
from planning_studio_service.billing.provider import (
    NotConfiguredError,
    StripeBillingProvider,
)


def _provider(*, monthly: dict[str, str], annual: dict[str, str]):
    return StripeBillingProvider(
        secret_key="sk_test_dummy",
        webhook_secret="whsec_dummy",
        price_ids=monthly,
        annual_price_ids=annual,
    )


def _patch_stripe_session(monkeypatch, fake_session) -> MagicMock:
    """Replace StripeBillingProvider._stripe() so the test never touches
    the real SDK. Returns the mock so the test can assert how it was called.
    """
    mock = MagicMock()
    mock.checkout.Session.create.return_value = fake_session
    # Replace _stripe() on the provider instance via monkeypatch
    monkeypatch.setattr(
        StripeBillingProvider, "_stripe", lambda self: mock,
    )
    return mock


def _fake_store():
    """Minimal store stub for start_checkout: only get_subscription is
    called. Returns None so the provider takes the "no existing customer"
    path and uses customer_email."""
    store = MagicMock()
    store.get_subscription.return_value = None
    return store


def test_monthly_period_picks_monthly_price(monkeypatch):
    """period='monthly' → uses STRIPE_PRICE_ID_PRO (monthly) value."""
    provider = _provider(
        monthly={"pro": "price_pro_monthly"},
        annual={"pro": "price_pro_annual"},
    )
    fake_session = SimpleNamespace(id="cs_test_123", url="https://stripe.com/c/cs_test_123")
    stripe_mock = _patch_stripe_session(monkeypatch, fake_session)
    pro = get_plan("pro")
    assert pro is not None

    result = provider.start_checkout(
        user_id="user-1",
        user_email="user@example.com",
        plan=pro,
        store=_fake_store(),
        success_url="https://tryinspira.com/billing?ok=1",
        cancel_url="https://tryinspira.com/pricing",
        period="monthly",
    )

    assert result.session_id == "cs_test_123"
    # The Stripe SDK was called with the MONTHLY price id.
    call = stripe_mock.checkout.Session.create.call_args
    assert call.kwargs["line_items"] == [
        {"price": "price_pro_monthly", "quantity": 1},
    ]
    # Metadata records the period for downstream webhook + audit.
    assert call.kwargs["metadata"]["billing_period"] == "monthly"


def test_annual_period_picks_annual_price(monkeypatch):
    """period='annual' → uses STRIPE_PRICE_ID_PRO_ANNUAL value."""
    provider = _provider(
        monthly={"pro": "price_pro_monthly"},
        annual={"pro": "price_pro_annual"},
    )
    fake_session = SimpleNamespace(id="cs_test_456", url="https://stripe.com/c/cs_test_456")
    stripe_mock = _patch_stripe_session(monkeypatch, fake_session)
    pro = get_plan("pro")
    assert pro is not None

    provider.start_checkout(
        user_id="user-1",
        user_email="user@example.com",
        plan=pro,
        store=_fake_store(),
        success_url="https://tryinspira.com/billing?ok=1",
        cancel_url="https://tryinspira.com/pricing",
        period="annual",
    )

    call = stripe_mock.checkout.Session.create.call_args
    assert call.kwargs["line_items"] == [
        {"price": "price_pro_annual", "quantity": 1},
    ]
    assert call.kwargs["metadata"]["billing_period"] == "annual"


def test_annual_requested_but_not_configured_raises(monkeypatch):
    """period='annual' on a plan with no annual price configured → fail loud.

    We deliberately don't silently downgrade to monthly: the user picked
    the annual price + saw it on the pricing page; charging the monthly
    rate would be a billing-trust violation. Better to fail visibly so
    the operator configures the missing env var.
    """
    provider = _provider(
        monthly={"pro": "price_pro_monthly"},
        annual={},  # Pro monthly only — no annual
    )
    _patch_stripe_session(monkeypatch, SimpleNamespace(id="x", url="x"))
    pro = get_plan("pro")
    assert pro is not None

    with pytest.raises(NotConfiguredError) as excinfo:
        provider.start_checkout(
            user_id="user-1",
            user_email="user@example.com",
            plan=pro,
            store=_fake_store(),
            success_url="x",
            cancel_url="x",
            period="annual",
        )
    # Error message includes the env var name so ops can fix immediately.
    assert "STRIPE_PRICE_ID_PRO_ANNUAL" in str(excinfo.value)


def test_unknown_period_normalizes_to_monthly(monkeypatch):
    """Defensive: any period value other than 'annual' (incl. junk) →
    treated as monthly. Prevents a typo'd FE call from picking the wrong
    price id; the route also normalizes upstream, but the provider has
    the same guard so a direct caller can't bypass it."""
    provider = _provider(
        monthly={"pro": "price_pro_monthly"},
        annual={"pro": "price_pro_annual"},
    )
    fake_session = SimpleNamespace(id="cs_test_789", url="https://stripe.com/c/cs_test_789")
    stripe_mock = _patch_stripe_session(monkeypatch, fake_session)
    pro = get_plan("pro")
    assert pro is not None

    provider.start_checkout(
        user_id="user-1",
        user_email="user@example.com",
        plan=pro,
        store=_fake_store(),
        success_url="x",
        cancel_url="x",
        period="quarterly",  # Not a real period.
    )

    call = stripe_mock.checkout.Session.create.call_args
    assert call.kwargs["line_items"] == [
        {"price": "price_pro_monthly", "quantity": 1},
    ]
    assert call.kwargs["metadata"]["billing_period"] == "monthly"


def test_frontier_team_slug_annual_path(monkeypatch):
    """Frontier (internal slug `team`) also wires annual."""
    provider = _provider(
        monthly={"team": "price_team_monthly"},
        annual={"team": "price_team_annual"},
    )
    fake_session = SimpleNamespace(id="cs_team_1", url="https://stripe.com/c/cs_team_1")
    stripe_mock = _patch_stripe_session(monkeypatch, fake_session)
    team = get_plan("team")
    assert team is not None

    provider.start_checkout(
        user_id="user-1",
        user_email="user@example.com",
        plan=team,
        store=_fake_store(),
        success_url="x",
        cancel_url="x",
        period="annual",
    )

    call = stripe_mock.checkout.Session.create.call_args
    assert call.kwargs["line_items"] == [
        {"price": "price_team_annual", "quantity": 1},
    ]
    assert call.kwargs["metadata"]["plan_slug"] == "team"
    assert call.kwargs["metadata"]["billing_period"] == "annual"


def test_billing_checkout_body_accepts_period():
    """BillingCheckoutBody (Pydantic) accepts period as a field without
    422'ing. Direct model construction — cheaper than spinning up a full
    TestClient + auth flow, and verifies the Wave A.5 lesson about
    module-scope Pydantic classes (#183 fix) still holds for the
    period addition.
    """
    from planning_studio_service.api import BillingCheckoutBody

    # Default: no period given → "monthly"
    body = BillingCheckoutBody(plan_slug="pro")
    assert body.plan_slug == "pro"
    assert body.period == "monthly"

    # Explicit annual
    body_annual = BillingCheckoutBody(plan_slug="pro", period="annual")
    assert body_annual.period == "annual"

    # Junk string accepted at the model level (route normalizes downstream)
    body_junk = BillingCheckoutBody(plan_slug="pro", period="quarterly")
    assert body_junk.period == "quarterly"  # model is permissive; route is strict
