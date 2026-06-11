"""Plan-tier entitlements — the post-credits gate model.

Replaces the old ``credits`` module. Inspira no longer meters per-call
spend; features are gated as booleans on the user's plan tier:

- Free: read-only-ish — kickoff + canvas + base models.
- Pro:  scaffold, frontier models, all kickoff/turn modes.
- Team: same as Pro, plus seats and team-shared shelves.

Voice was scrapped in PR 2 — the voice surface no longer exists. If a
metered feature returns later (overage minutes on top of a Pro plan,
say), reintroduce metering as a *narrow* primitive on that single
feature (a ``voice_minutes_remaining`` counter, etc.) rather than a
generic credit ledger.

Why no credit ledger:
- The pack-purchase flow was never wired to a real billing provider —
  ``CREDIT_PACKS`` routed through the Noop provider returning 501.
- Per-call charging coupled product behavior to a runtime ledger
  without revenue mechanics, which meant free users hit a paywall the
  product couldn't actually charge them out of.
- Plan-gating is one boolean per request; auditing "can this user
  scaffold?" is a single read of ``users.plan_tier`` + the
  ``subscriptions`` row.

This module is a thin wrapper over the existing ``store`` subscription
helper. The public surface is intentionally tiny: ``get_plan`` and
``has_feature``. Anything else composes on top.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Tier capabilities
# ---------------------------------------------------------------------------


# Plan slugs in canonical order. Mirror this list to the marketing site
# locale strings; if the slugs drift the pricing page renders empty.
#
# "team" is the slug; the user-facing display label flipped to "Frontier"
# in #081's rebrand. "enterprise" is wired through agents/tiers.py
# (ALLOWED_TIERS_BY_PLAN, DEFAULT_TIER_BY_PLAN, CAPS_BY_PLAN_AND_TIER) but
# was missing here — set_subscription with plan="enterprise" used to
# silently degrade to "free" via the validation at line 107. Adding
# "enterprise" closes that gap (ref #159 — enterprise enum gap).
PLAN_TIERS: tuple[str, ...] = ("free", "pro", "team", "enterprise")
DEFAULT_PLAN: str = "free"


# Feature → minimum plan that unlocks it. The check is "user's plan is
# at or above the listed tier" — see ``has_feature`` for the ordering.
#
# To add a new feature: add the slug here, gate its route via
# ``has_feature``, and surface the slug in the pricing-page locale
# copy. No DB migration, no code in the routes beyond the boolean
# check.
_FEATURE_MIN_TIER: dict[str, str] = {
    # Code-scaffold generator. Pro-and-above.
    "scaffold": "pro",
    # Frontier-tier LLM models (Claude Sonnet, GPT-5, etc.) on
    # kickoff / topic_turn. Pro-and-above.
    "frontier_models": "pro",
    # Team workspace + shared shelves + member invites. Team-only.
    "team_workspace": "team",
}


def _tier_rank(plan_slug: str | None) -> int:
    """Numeric rank for tier comparison. Unknown slugs collapse to Free
    (0) so a misspelled subscription can't accidentally unlock paid
    features."""
    if not plan_slug:
        return 0
    try:
        return PLAN_TIERS.index(plan_slug)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_plan(store: Any, *, user_id: str) -> str:
    """Resolve the user's current plan slug.

    Missing subscriptions row → Free. Mirrors the convention the old
    credits module used so safe-by-default holds for users who never
    touched billing.
    """
    try:
        row = store.get_subscription(user_id=user_id)
    except Exception:  # noqa: BLE001 — never block route on subscription read
        return DEFAULT_PLAN
    if not row:
        return DEFAULT_PLAN
    plan = row.get("plan") or DEFAULT_PLAN
    return plan if plan in PLAN_TIERS else DEFAULT_PLAN


def has_feature(store: Any, *, user_id: str, feature: str) -> bool:
    """True iff the user's plan is at or above the tier required for
    ``feature``. Unknown features default to True (open path) so a
    typo here doesn't accidentally lock everyone out — the route
    layer is still the source of truth for what's gated."""
    min_tier = _FEATURE_MIN_TIER.get(feature)
    if min_tier is None:
        return True
    return _tier_rank(get_plan(store, user_id=user_id)) >= _tier_rank(min_tier)


def features_for(store: Any, *, user_id: str) -> list[str]:
    """All gated feature slugs the user has access to. Used by the
    /api/v2/entitlements response so the frontend can render the
    canvas with the right Pro/Team UI without making N round-trips."""
    plan = get_plan(store, user_id=user_id)
    rank = _tier_rank(plan)
    return [
        slug for slug, min_tier in _FEATURE_MIN_TIER.items()
        if rank >= _tier_rank(min_tier)
    ]


def entitlements_payload(store: Any, *, user_id: str) -> dict[str, Any]:
    """Single-source payload for /api/v2/entitlements.

    Shape (stable contract — frontend depends on these exact keys):
        {
            "plan": "free" | "pro" | "team",
            "features": ["scaffold", "frontier_models", ...],
        }
    """
    plan = get_plan(store, user_id=user_id)
    return {
        "plan": plan,
        "features": features_for(store, user_id=user_id),
    }
