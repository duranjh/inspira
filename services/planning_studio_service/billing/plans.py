"""Plan catalog — the source of truth for what each tier includes.

The three plans (Free, Pro, Team) are hard-coded here rather than stored in
the database because:

- The catalog is identical for every user; there's no per-tenant override.
- It's read constantly by the frontend for the pricing card; a SQL hop on
  every render is wasteful.
- The frontend pricing page (``app/src/features/marketing/PricingPage.tsx``
  plus the ``marketing.pricing.*`` i18n strings) needs to stay in lock-step —
  a code review is the right place to notice drift.

If per-plan entitlements are ever enforced server-side (e.g. blocking
project creation over the Free cap), use :func:`get_plan` to look up the
plan for a user's current subscription; a ``None`` return means "treat as
Free" so the system is safe-by-default for users without a subscriptions
row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Limits:
    """Quantified entitlements for a plan.

    ``None`` means "unlimited" for numeric fields. Booleans default to the
    most restrictive option so a new flag added here without thought
    doesn't silently unlock features for the Free tier.
    """

    max_projects: int | None = None
    # Daily token budget feeds the per-user token gate in api.py. The
    # existing code path reads INSPIRA_USER_DAILY_TOKEN_BUDGET; swapping to
    # per-plan lookup is an intentional follow-up (requires a user_id->plan
    # lookup on every LLM call — cheap SELECT, but out of scope for this
    # scaffolding pass).
    daily_token_budget: int = 50_000
    max_topics_total: int | None = None
    # Feature flags — keep the public API stable by using snake_case booleans
    # that match the pricing page copy (PricingPage.tsx + i18n strings).
    allow_share_links: bool = False
    allow_team: bool = False
    allow_export_pdf: bool = True
    allow_advanced_exports: bool = False
    priority_planner: bool = False
    # #094 — Document generator (7 doc types). Pro+ only; Free users
    # see an upgrade CTA. Pro = 1 doc/month free trial, Frontier = 100
    # docs/month, both via the business_plan_usage counter from #080
    # (NOT tier_usage; rename to document_usage deferred per #095). The
    # API endpoint reads this flag and returns 402 on a Free attempt.
    allow_business_plan: bool = False


@dataclass(frozen=True, slots=True)
class Plan:
    """A single subscription tier."""

    slug: str
    title: str
    monthly_price_cents: int
    description: str
    features: list[str]
    limits: Limits
    # Tag used by the Stripe price-ID env var lookup. Free tier has no
    # Stripe price — it's the default fallback when a user has no
    # subscription row.
    stripe_price_env_var: str | None = None
    # Optional annual price (per-month equivalent when billed yearly).
    # None = annual billing not offered for this tier. The total annual
    # charge is `annual_price_cents * 12`. Frontend shows it as a
    # secondary line under the monthly headline.
    annual_price_cents: int | None = None
    stripe_annual_price_env_var: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        """Shape sent to the frontend. No internal env-var names leak."""
        return {
            "slug": self.slug,
            "title": self.title,
            "monthly_price_cents": self.monthly_price_cents,
            "annual_price_cents": self.annual_price_cents,
            "description": self.description,
            "features": list(self.features),
            "limits": asdict(self.limits),
        }


# ---------------------------------------------------------------------------
# The catalog. Order matters — the frontend renders cards left-to-right in
# this order, and the pricing page copy follows the same sequence.
# ---------------------------------------------------------------------------
PLANS: list[Plan] = [
    Plan(
        slug="free",
        title="Free",
        monthly_price_cents=0,
        description="Enough to try Inspira seriously.",
        features=[
            "Up to 3 projects",
            "Up to 100 topics across all projects",
            "Daily planner budget: 50,000 tokens",
            "All core canvas features",
            "Export to Markdown + PDF",
        ],
        limits=Limits(
            max_projects=3,
            daily_token_budget=50_000,
            max_topics_total=100,
            allow_share_links=False,
            allow_team=False,
            allow_export_pdf=True,
            allow_advanced_exports=False,
            priority_planner=False,
        ),
        stripe_price_env_var=None,
    ),
    Plan(
        slug="pro",
        title="Pro",
        monthly_price_cents=2900,
        annual_price_cents=2400,  # per-month-equivalent; charged $288/yr
        description="For the projects that deserve your attention.",
        features=[
            "Unlimited projects",
            "Daily planner budget: 500,000 tokens",
            "Priority planner latency",
            "Read-only share links",
            "Advanced exports (HTML, JSON)",
        ],
        limits=Limits(
            max_projects=None,
            daily_token_budget=500_000,
            max_topics_total=None,
            allow_share_links=True,
            allow_team=False,
            allow_export_pdf=True,
            allow_advanced_exports=True,
            priority_planner=True,
            allow_business_plan=True,  # #094: Pro unlocks doc generator (1 doc/mo trial)
        ),
        stripe_price_env_var="STRIPE_PRICE_ID_PRO",
        stripe_annual_price_env_var="STRIPE_PRICE_ID_PRO_ANNUAL",
    ),
    Plan(
        # Slug stays "team" for backward-compat with existing
        # subscription rows + the STRIPE_PRICE_ID_TEAM env var. Only
        # user-facing display (title / description / features /
        # price) flips to the Frontier rebrand.
        slug="team",
        title="Frontier",
        monthly_price_cents=20000,  # $200/month
        description="For the most ambitious thinking.",
        features=[
            "Everything in Pro",
            "Top-tier reasoning model on every turn (gpt-5.5)",
            "Generous monthly usage* — designed so real users never see the limit",
            "Up to 100 business plans per month*",
            "Priority support",
        ],
        limits=Limits(
            max_projects=None,
            daily_token_budget=500_000,
            max_topics_total=None,
            allow_share_links=True,
            allow_team=True,
            allow_export_pdf=True,
            allow_advanced_exports=True,
            priority_planner=True,
            allow_business_plan=True,  # #094: Frontier unlocks doc generator (100/mo cap)
        ),
        stripe_price_env_var="STRIPE_PRICE_ID_TEAM",
        stripe_annual_price_env_var="STRIPE_PRICE_ID_TEAM_ANNUAL",
    ),
]


_PLANS_BY_SLUG: dict[str, Plan] = {plan.slug: plan for plan in PLANS}


def get_plan(slug: str | None) -> Plan | None:
    """Look up a plan by slug; ``None`` / unknown slug returns ``None``.

    Callers that need a safe default should fall back to ``get_plan("free")``.
    Keeping the null-return explicit lets the caller distinguish "user has no
    subscription" from "user is on Free", which matters for things like "show
    a 'no payment on file' empty state" vs "show a 'you are on Free' banner".
    """
    if not slug:
        return None
    return _PLANS_BY_SLUG.get(slug)


def plan_catalog_json() -> list[dict[str, Any]]:
    """Public JSON for ``GET /api/v2/billing/plans``."""
    return [plan.to_public_dict() for plan in PLANS]


# Default free tier. Users without a subscriptions row are treated as Free;
# this helper lets callers treat "missing" and "free" uniformly.
def free_plan() -> Plan:
    plan = get_plan("free")
    if plan is None:  # pragma: no cover — PLANS always contains 'free'
        raise RuntimeError("Plan catalog missing 'free' — misconfiguration")
    return plan
