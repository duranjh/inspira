"""LLM model-tier catalog and access policy.

The user-facing LLM picker routes each request to one of three tiers:

- ``base``     — default for everyone, cheapest turn cost (gpt-5-mini)
- ``pro``      — stronger reasoning for Pro/Team plans (gpt-5)
- ``frontier`` — premium/experimental for Team only (gpt-5 today; Claude
  Sonnet 4.5 once the Claude adapter is wired)

A user's plan gates which tiers they can pick. Every paid call uses the
same accounting hooks; the tier multiplier (1.0 / 3.0 / 5.0) is applied
to the credit charge so a ``pro`` turn costs 3x the ``base`` turn, etc.

Design choices
--------------

- Hard-coded catalog. The three tiers are a product decision, not a
  customer-configurable setting, so there's no reason to pay the SQL
  cost on every render.
- Separation from ``billing/plans.py``. The plan tells us *what a user
  can buy*; this module tells us *which models their plan unlocks*. The
  two evolve at different cadences (plans rarely, models often).
- No network side effects. Everything here is pure data + helpers; the
  only interaction with the outside world is ``tier_to_openai_model``
  which returns a string the adapter then hands to OpenAI.
"""
from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import PlanningInterviewer

logger = logging.getLogger(__name__)


class ModelTier(str, enum.Enum):
    """Canonical tier identifier used across the API + UI.

    String-valued so the enum serialises cleanly to JSON for the
    ``/api/v2/model-tiers`` response and round-trips through PATCH
    ``preferred_model_tier`` without a custom encoder.
    """

    BASE = "base"
    PRO = "pro"
    FRONTIER = "frontier"
    ENTERPRISE = "enterprise"


# ---------------------------------------------------------------------------
# Plan gating. Keep the keys in sync with ``billing/plans.py``.
# ---------------------------------------------------------------------------

ALLOWED_TIERS_BY_PLAN: dict[str, set[ModelTier]] = {
    "free": {ModelTier.BASE},
    "pro": {ModelTier.BASE, ModelTier.PRO},
    "team": {ModelTier.BASE, ModelTier.PRO, ModelTier.FRONTIER},
    "enterprise": {
        ModelTier.BASE,
        ModelTier.PRO,
        ModelTier.FRONTIER,
        ModelTier.ENTERPRISE,
    },
}


# Per-plan default tier applied when the user hasn't picked one. Team users
# default to ``pro`` (not ``frontier``) so a casual turn doesn't silently
# run at 5x cost — frontier is opt-in.
DEFAULT_TIER_BY_PLAN: dict[str, ModelTier] = {
    "free": ModelTier.BASE,
    "pro": ModelTier.PRO,
    "team": ModelTier.PRO,
    # Enterprise users default to ``frontier`` (not ``enterprise``) so a
    # casual turn doesn't silently run at 8x cost — enterprise is opt-in,
    # mirroring the team→pro default rationale.
    "enterprise": ModelTier.FRONTIER,
}


# Credit-charge multiplier applied on top of the base turn cost. A
# ``base`` turn charges 1x; a ``pro`` turn 3x; a ``frontier`` turn 5x.
# Floats so future fractional tiers (e.g. 1.5x for a "mid" tier) are
# possible without a schema change.
CREDIT_MULTIPLIER_BY_TIER: dict[ModelTier, float] = {
    ModelTier.BASE: 1.0,
    ModelTier.PRO: 3.0,
    ModelTier.FRONTIER: 5.0,
    ModelTier.ENTERPRISE: 8.0,
}


# ---------------------------------------------------------------------------
# Monthly token caps per (plan, tier). Founder-locked 2026-04-28 (#080).
# ---------------------------------------------------------------------------
# Output-token ceilings per calendar-month window. The application layer
# (api.v2_topic_turn) checks ``tier_usage.output_tokens_used >= cap`` before
# the OpenAI call and falls back to the next-cheaper tier (or 429s with
# ``errors.monthly_cap_reached`` once BASE is exhausted on Free).
#
# Sizing rationale (per-tier model cost, 3k output tokens/turn assumption):
# - Free BASE 2M = ~22 turns/day = ~$5/mo cost ceiling on gpt-5-mini.
# - Pro PRO 2.25M = ~750 turns/mo = ~$24 cost ceiling on gpt-5.
#   Pro plan max combined (BASE + PRO) ≈ $29 = break-even.
# - Frontier PRO 1.5M = ~500 turns. Frontier FRONTIER 4.5M = ~1500 turns.
#   Frontier max combined (BASE + PRO + FRONTIER) ≈ $171 on a $200 plan
#   = +$29 worst-case margin.
#
# Why "team" (not "frontier") on the plan slug: backward-compat with
# existing subscription rows + Stripe ``STRIPE_PRICE_ID_TEAM`` env var.
# Only the user-facing display flipped to "Frontier" in #081's rebrand.
CAPS_BY_PLAN_AND_TIER: dict[str, dict[ModelTier, int]] = {
    "free": {
        ModelTier.BASE: 2_000_000,
    },
    "pro": {
        ModelTier.BASE: 2_000_000,
        ModelTier.PRO: 2_250_000,
    },
    "team": {  # Frontier (slug stays "team" per #081)
        ModelTier.BASE: 2_000_000,
        ModelTier.PRO: 1_500_000,
        ModelTier.FRONTIER: 4_500_000,
    },
    "enterprise": {
        ModelTier.BASE: 2_000_000,
        ModelTier.PRO: 1_500_000,
        ModelTier.FRONTIER: 4_500_000,
        ModelTier.ENTERPRISE: 8_000_000,
    },
}


# Concurrent sub-agent caps per plan. Caps how many sub_agent_runs
# rows can be in status='running' at once for a workspace. The Kanban
# enforces this at drag-to-spawn time and at auto-spawn time.
# Founder direction 2026-05-04 — Free 1 / Pro 3 / Frontier 50 /
# Enterprise 100.
CONCURRENT_SUBAGENTS_BY_PLAN: dict[str, int] = {
    "free": 1,
    "pro": 3,
    "team": 50,  # Frontier (slug stays "team" per #081)
    "enterprise": 100,
}


# Per-month business plan generation cap. Pro gets a 1/mo free trial
# (sales hook into Frontier upgrade); Frontier has a high soft cap with
# fair-use disclaimer (#081's "Up to 100 business plans per month*").
# The Free plan has no business-plan generation at all (returns 0; the
# UI hides the feature for that plan via #081's gating).
BUSINESS_PLAN_CAPS_BY_PLAN: dict[str, int] = {
    "free": 0,
    "pro": 1,
    "team": 100,  # Frontier
    "enterprise": 100,
}


# Per-plan Kanban auto-promote cap (#172). Caps how many feedback clusters
# auto-promote into v2_projects rows on CSV/connector import, preventing
# the Kanban auto-spawn storm we hit on 2026-05-05 (179 cards from a
# 200-row import × Enterprise concurrent-subagent cap 100 = crash).
# Remaining clusters stay in the Inbox archive — visible, manually
# promotable — they just don't pile onto the Kanban.
#
# Keyed by ModelTier (matches CREDIT_MULTIPLIER_BY_TIER shape). The
# plan_slug → ModelTier mapping for cap-lookup lives in
# ``kanban_tier_for_plan`` — it intentionally differs from
# ``DEFAULT_TIER_BY_PLAN`` (which returns the user-facing *default*
# runtime tier for cost reasons, e.g. team→PRO) because here we want
# each plan's MAX tier so Frontier customers get the 50-card cap
# regardless of which model tier they currently run topic_turn at.
KANBAN_CARD_CAPS_BY_PLAN: dict[ModelTier, int] = {
    ModelTier.BASE: 10,
    ModelTier.PRO: 25,
    ModelTier.FRONTIER: 50,
    ModelTier.ENTERPRISE: 200,
}


# ---------------------------------------------------------------------------
# Public labels + descriptions. The API echoes these back so the frontend
# doesn't have to duplicate the catalog string table.
# ---------------------------------------------------------------------------

_TIER_PUBLIC_META: dict[ModelTier, dict[str, str]] = {
    ModelTier.BASE: {
        "label": "Base",
        "description": "Fast, dependable. Good for most turns.",
    },
    ModelTier.PRO: {
        "label": "Pro",
        "description": "Stronger reasoning. Slower but more thoughtful.",
    },
    ModelTier.FRONTIER: {
        "label": "Frontier",
        "description": "Our most capable model. Reserve for hard turns.",
    },
    ModelTier.ENTERPRISE: {
        "label": "Enterprise",
        "description": "Highest-capability tier for enterprise plans.",
    },
}


def _plan_slug_or_free(plan_slug: str | None) -> str:
    """Normalise a plan slug, falling back to ``free``.

    Keeps the rest of the module from sprinkling ``or "free"``
    everywhere and gives us one place to log unexpected slugs.
    """
    if not plan_slug:
        return "free"
    if plan_slug not in ALLOWED_TIERS_BY_PLAN:
        logger.warning(
            "Unknown plan slug %r passed to tiers module; treating as free",
            plan_slug,
        )
        return "free"
    return plan_slug


def tier_to_openai_model(tier: ModelTier) -> str:
    """Return the OpenAI model id to use for this tier.

    We keep the mapping in Python (not env) because swapping a model is a
    product + pricing decision that warrants a code review, not an ops
    toggle.

    Per-tier mapping (locked in 2026-04-28 per the LLM-tier overhaul):

    - BASE     → ``gpt-5-mini`` ($0.25 input / $2 output per 1M tokens)
    - PRO      → ``gpt-5``      ($1.25 input / $10 output per 1M tokens)
    - FRONTIER → ``gpt-5.5``    ($5 input / $30 output per 1M tokens),
                 served only as the OpenAI fallback when
                 ``ANTHROPIC_API_KEY`` is absent — the real frontier
                 ``topic_turn`` routes through Claude Sonnet via
                 ``tier_to_adapter``.

    Output-cost ratio is 1 : 5 : 15, which roughly matches the
    intended user value-prop scaling (Free → Pro 5x credit
    multiplier → Frontier ~15x). The current
    ``CREDIT_MULTIPLIER_BY_TIER`` of 1.0 / 3.0 / 5.0 undercharges the
    paid tiers vs raw cost, but the per-tier monthly-token caps
    (#080) backstop the absolute monthly cost so this isn't a
    runaway risk.

    Model-name strings track the *latest pointer* (no dated suffix).
    OpenAI rolls the underlying snapshot forward over time; we get
    the improvements automatically. If a snapshot regresses
    structured-output adherence or latency, pin a dated snapshot
    here as a hotfix and revert when fixed upstream.

    Watch for latency regression: reasoning-model output tokens
    include hidden thinking tokens, which can balloon turn duration
    beyond the 5-10s interactive budget. If topic_turn p95 climbs
    past 10s, pin ``reasoning_effort="minimal"`` adapter-side.
    """
    if tier is ModelTier.BASE:
        return "gpt-5-mini"
    if tier is ModelTier.PRO:
        return "gpt-5"
    if tier is ModelTier.FRONTIER or tier is ModelTier.ENTERPRISE:
        # Fallback when ANTHROPIC_API_KEY is absent. Real frontier/enterprise
        # ``topic_turn`` is served by Claude Sonnet via ``tier_to_adapter``;
        # the artifact code-gen endpoint pins ``CLAUDE_CODEGEN_MODEL`` instead.
        return "gpt-5.5"
    raise ValueError(f"Unknown ModelTier: {tier!r}")


def tier_to_reasoning_effort(tier: ModelTier) -> str | None:
    """Reasoning effort to pass to OpenAI for this tier's topic_turn calls.

    OpenAI's gpt-5 family supports ``reasoning_effort`` ∈ {none, low,
    medium (default), high, xhigh}. The default ``medium`` produces
    high-quality structured output but its hidden thinking tokens
    routinely exceed the 15s adapter ``timeout_s`` on the BASE model
    (gpt-5-mini), tripping the user-visible "planner took too long"
    error (root cause of #075's iteration-3 verification failure).

    Per-tier policy (founder-locked 2026-04-28):
    - **BASE** → ``"low"``. Fits the 5-10s interactive budget on
      gpt-5-mini; still benefits from some reasoning for
      structured-output adherence. Free users expect responsiveness.
    - **PRO / FRONTIER** → ``None`` (use OpenAI's medium default).
      Paid users opt into longer wait times for higher-quality
      output. The ``OpenAIConfig.timeout_s`` override at the call
      site is bumped accordingly via ``tier_to_timeout_s``.

    Returns ``None`` rather than ``"medium"`` so the adapter can omit
    the param (cleaner request log + matches OpenAI's "default" semantics).
    """
    if tier is ModelTier.BASE:
        return "low"
    return None


def tier_to_timeout_s(tier: ModelTier) -> float | None:
    """Adapter timeout (seconds) for this tier's topic_turn calls.

    Returns ``None`` to use ``OpenAIConfig.timeout_s`` default
    (currently 15s).

    Per-tier policy (founder-locked 2026-04-28):
    - **BASE** → ``None`` (15s default). Pairs with
      ``reasoning_effort="low"`` to keep responsiveness on Free.
    - **PRO** → ``60.0``. Pairs with default ``medium`` reasoning effort
      on gpt-5; longer waits acceptable for paid quality.
    - **FRONTIER** → ``60.0``. Same shape as PRO but on gpt-5.5.

    Front-end already shows "STILL WORKING — THIS CAN TAKE A BIT…" after
    a few seconds and ramps to "Almost there…" — the longer budget for
    paid tiers stays under the FE's 90s hard-stop.
    """
    if tier is ModelTier.BASE:
        return None
    return 60.0


def kickoff_openai_model() -> str:
    """OpenAI model for the kickoff call, regardless of the user's tier.

    Kickoff produces a topic skeleton — the sanitiser enforces shape and
    topic-count bounds. gpt-4o-mini handles this in 2-4s; previously
    pinned to gpt-5-mini which routinely spent 30-60s on the production
    prompt and tripped the kickoff timeout.

    topic_turn dispatches per-tier via ``tier_to_openai_model``:
    ``gpt-5-mini`` (BASE), ``gpt-5`` (PRO), ``gpt-5.5`` (FRONTIER
    OpenAI fallback). All three are reasoning models with better
    structured-output adherence than ``gpt-4o-mini`` — the root
    cause of issues-log #075. Kickoff stays pinned to
    ``gpt-4o-mini`` because the topic-skeleton output is
    constrained by a JSON schema; reasoning depth has diminishing
    returns and the skeleton-shape sanitiser already enforces the
    bounds.
    """
    return "gpt-4o-mini"


# Frontier model for Claude. Keep the string here (not env) — swapping a
# model is a product + pricing decision, not an ops toggle. Bump when a
# newer Sonnet is validated against the TOPIC_TURN_SCHEMA sanitizer.
CLAUDE_FRONTIER_MODEL = "claude-sonnet-4-5-20250929"


# Code-generation pin for the artifact viewer endpoint. Per the founder rule
# ("Claude only for code-gen on Frontier/Enterprise; non-code LLM features
# always GPT regardless of tier"), the artifact endpoint passes this as
# ``model_override`` rather than mutating ``tier_to_claude_model`` — so
# every other Claude-backed call site keeps Sonnet by default and ENTERPRISE
# can't silently upgrade non-code calls to Opus.
CLAUDE_CODEGEN_MODEL = "claude-opus-4-7"


def tier_to_claude_model(tier: ModelTier) -> str | None:
    """Return the Claude model id for this tier, or ``None`` if not Claude-backed.

    Frontier and Enterprise both map to Sonnet here — the artifact endpoint
    pins ``CLAUDE_CODEGEN_MODEL`` (Opus 4.7) per-call via ``model_override``
    when generating code, so this default applies only to non-code Claude
    routes (currently ``topic_turn``). Base/Pro return ``None`` so the
    caller's dispatch logic stays a simple truthiness check:

        claude_model = tier_to_claude_model(tier)
        if claude_model and claude_adapter is not None:
            ...
    """
    if tier is ModelTier.FRONTIER or tier is ModelTier.ENTERPRISE:
        return CLAUDE_FRONTIER_MODEL
    return None


def tier_to_adapter(
    tier: ModelTier,
    *,
    openai_adapter: "PlanningInterviewer",
    claude_adapter: "PlanningInterviewer | None",
) -> "PlanningInterviewer":
    """Pick the adapter for this tier.

    The rule:
    - Frontier or Enterprise → Claude when ``claude_adapter`` is wired,
      else OpenAI.
    - Everything else → OpenAI.

    The caller (``api.py:v2_topic_turn``) resolves ``claude_adapter`` once
    per app lifetime and passes ``None`` when ``ANTHROPIC_API_KEY`` isn't
    set. The fallback is deliberate — dev environments without an
    Anthropic key should still serve frontier/enterprise requests via
    OpenAI rather than 500'ing on every frontier turn.

    TODO: kickoff is NOT yet dispatched through this helper. Only
    ``topic_turn`` routes by tier. Adding kickoff needs a short live
    validation pass against the kickoff sanitizer's topic-count bounds —
    the Claude adapter technically implements ``kickoff`` already but the
    output hasn't been exercised against real projects yet.
    """
    if (
        tier is ModelTier.FRONTIER or tier is ModelTier.ENTERPRISE
    ) and claude_adapter is not None:
        return claude_adapter
    return openai_adapter


def allowed_tiers_for_plan(plan_slug: str | None) -> set[ModelTier]:
    """Set of tiers this plan can select from.

    Unknown slugs collapse to the Free allowlist so a misconfigured
    subscription never leaks a higher tier.
    """
    return ALLOWED_TIERS_BY_PLAN[_plan_slug_or_free(plan_slug)]


def default_tier_for_plan(plan_slug: str | None) -> ModelTier:
    """Default tier when the user hasn't picked one.

    Centralised so ``resolve_tier_for_user`` and the public
    ``/api/v2/model-tiers`` endpoint agree on the default.
    """
    return DEFAULT_TIER_BY_PLAN[_plan_slug_or_free(plan_slug)]


def credit_multiplier(tier: ModelTier) -> float:
    """Credit charge multiplier for this tier. Never raises."""
    return CREDIT_MULTIPLIER_BY_TIER.get(tier, 1.0)


def get_tier_cap(plan_slug: str | None, tier: ModelTier) -> int | None:
    """Monthly output-token cap for (plan, tier). ``None`` = no cap.

    Returns ``None`` for combinations the plan doesn't allow (e.g.
    Free + PRO/FRONTIER) AND for combinations with no defined cap
    (defensive — currently every defined combination has a cap, but
    a future "enterprise" plan could legitimately have unlimited
    BASE/PRO).

    Caller (``api.v2_topic_turn``) interprets ``None`` as "skip the
    cap check, no monthly limit applies on this tier for this user".
    Returning ``None`` instead of ``math.inf`` keeps the type signature
    tight for the integer-comparison check at the call site.

    Unknown plan slugs collapse to Free's caps (matches
    ``allowed_tiers_for_plan`` and ``_plan_slug_or_free`` defensively).
    """
    plan = _plan_slug_or_free(plan_slug)
    return CAPS_BY_PLAN_AND_TIER.get(plan, {}).get(tier)


def select_tier_after_cap_check(
    store: Any,
    user_id: str,
    plan_slug: str | None,
    requested_tier: ModelTier,
) -> "tuple[ModelTier | None, ModelTier | None]":
    """Apply per-tier monthly-cap check + auto-fallback.

    Returns ``(effective_tier, fell_back_from)``:
    - ``effective_tier``: the tier that has remaining capacity. ``None``
      means all candidate tiers in the fallback chain are exhausted —
      caller should return 429 with ``errors.monthly_cap_reached``.
    - ``fell_back_from``: when non-None, the originally-requested
      tier (caller can surface a soft "switched to <tier>" toast in
      the response). ``None`` when the requested tier had capacity
      and no fallback occurred.

    Fall-through chain (founder-locked 2026-04-28):
    - ENTERPRISE → FRONTIER → PRO → BASE → 429
    - FRONTIER → PRO → BASE → 429
    - PRO → BASE → 429
    - BASE → 429 (no tier below BASE)

    A candidate tier with ``cap == None`` (plan doesn't allow it) is
    skipped — e.g., a Pro user requesting FRONTIER is already clamped
    to PRO by ``resolve_tier_for_user`` before this is called, so the
    chain above is for the *clamped* tier.

    Defensive: store-read errors are swallowed and the candidate is
    treated as 0-used. We'd rather let a user through than block on a
    counter-DB hiccup.
    """
    fallback_chain: dict[ModelTier, tuple[ModelTier, ...]] = {
        ModelTier.ENTERPRISE: (
            ModelTier.ENTERPRISE,
            ModelTier.FRONTIER,
            ModelTier.PRO,
            ModelTier.BASE,
        ),
        ModelTier.FRONTIER: (ModelTier.FRONTIER, ModelTier.PRO, ModelTier.BASE),
        ModelTier.PRO: (ModelTier.PRO, ModelTier.BASE),
        ModelTier.BASE: (ModelTier.BASE,),
    }
    chain = fallback_chain.get(requested_tier, (requested_tier,))
    for candidate in chain:
        cap = get_tier_cap(plan_slug, candidate)
        if cap is None:
            # Plan doesn't include this tier; skip in the chain.
            continue
        try:
            usage = store.get_tier_usage(user_id=user_id, tier=candidate.value)
            used = int(usage.get("output_tokens_used", 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "select_tier_after_cap_check: counter read failed "
                "user=%s tier=%s err=%s — letting through",
                user_id, candidate.value, exc,
            )
            return candidate, (
                requested_tier if candidate is not requested_tier else None
            )
        if used < cap:
            return candidate, (
                requested_tier if candidate is not requested_tier else None
            )
    return None, requested_tier


def get_business_plan_cap(plan_slug: str | None) -> int:
    """Monthly business-plan generation cap for this plan.

    Returns 0 for Free (feature not available — the UI hides the
    Generate button for that plan); 1 for Pro (free trial, sales hook);
    100 for Frontier (slug "team", per #081's "Up to 100 per month*"
    fair-use framing).

    Returns 0 (not None) for plans that don't have the feature so the
    cap-check call site can use a uniform integer comparison without
    branching on None.

    Unknown plan slugs collapse to Free (0).
    """
    plan = _plan_slug_or_free(plan_slug)
    return BUSINESS_PLAN_CAPS_BY_PLAN.get(plan, 0)


def kanban_card_cap_for_tier(tier: ModelTier) -> int:
    """Kanban auto-promote cap for this tier (#172).

    Falls back to the BASE cap on unknown tier — defensive default that
    matches the rest of this module's "missing → most-restrictive"
    posture.
    """
    return KANBAN_CARD_CAPS_BY_PLAN.get(
        tier, KANBAN_CARD_CAPS_BY_PLAN[ModelTier.BASE],
    )


def kanban_tier_for_plan(plan_slug: str | None) -> ModelTier:
    """Map a plan slug to its Kanban-cap tier (#172).

    Intentionally distinct from ``default_tier_for_plan``: that helper
    returns the *user-facing default* runtime tier (e.g. ``team`` →
    ``PRO`` so a casual turn doesn't silently cost 5x), but Kanban caps
    follow the plan itself, not the active model tier. A Frontier user
    running BASE turns should still get the 50-card cap, not the 10-card
    Free cap.
    """
    slug = _plan_slug_or_free(plan_slug)
    return {
        "free": ModelTier.BASE,
        "pro": ModelTier.PRO,
        "team": ModelTier.FRONTIER,
        "enterprise": ModelTier.ENTERPRISE,
    }.get(slug, ModelTier.BASE)


def parse_tier(value: str | None) -> ModelTier | None:
    """Coerce a free-text slug to a ``ModelTier``.

    Returns ``None`` for missing / unknown inputs rather than raising —
    callers that need strict validation do it explicitly at the API
    boundary so they can emit a structured 400 error.
    """
    if not value:
        return None
    try:
        return ModelTier(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Plan-lookup plumbing
# ---------------------------------------------------------------------------
# The store layer exposes ``get_subscription(user_id=...)`` already; we wrap
# it here so this module is the one place that knows the "missing row =
# free" convention mirrors ``billing/provider.py``.


def _plan_slug_for_user(store: Any, user_id: str) -> str:
    """Resolve the user's current plan slug from the subscriptions table.

    Missing subscription row → ``free`` (matches ``billing/provider.py``
    and ``credits._current_plan_slug_for``).
    """
    row = store.get_subscription(user_id=user_id)
    if not row:
        return "free"
    return _plan_slug_or_free(row.get("plan"))


def resolve_tier_for_user(
    store: Any,
    user_id: str,
    requested_tier: ModelTier | None,
) -> ModelTier:
    """Return the actual tier to run this turn under.

    Precedence:

    1. ``requested_tier`` when non-None AND permitted by the user's plan.
    2. The user's persisted ``preferred_model_tier`` when permitted.
    3. The plan default.

    Anything the user requests outside their plan is silently clamped
    down to the plan default. We don't surface a 403 here because the
    UI is the gate: the chip disables unavailable tiers, so a request
    with a disallowed tier is a stale client or a scripted caller, and
    the safe thing is to keep serving the turn at the lower tier.
    """
    plan_slug = _plan_slug_for_user(store, user_id)
    allowed = allowed_tiers_for_plan(plan_slug)

    if requested_tier is not None and requested_tier in allowed:
        return requested_tier

    # Fall back to the persisted default, if any.
    persisted = None
    try:
        persisted_slug = store.get_preferred_model_tier(user_id)
        persisted = parse_tier(persisted_slug)
    except AttributeError:
        # Backwards-compat: older stores without the method won't crash
        # the turn. They still resolve to the plan default.
        persisted = None
    if persisted is not None and persisted in allowed:
        return persisted

    return default_tier_for_plan(plan_slug)


def tier_catalog_for_plan(plan_slug: str | None) -> list[dict[str, Any]]:
    """Shape the tier list for ``GET /api/v2/model-tiers``.

    Each entry carries an ``available`` boolean so the UI can render
    disabled rows with an upgrade CTA without duplicating the plan
    catalog on the client.
    """
    allowed = allowed_tiers_for_plan(plan_slug)
    items: list[dict[str, Any]] = []
    # Iterate the enum in declaration order so the UI gets a stable order.
    for tier in (
        ModelTier.BASE,
        ModelTier.PRO,
        ModelTier.FRONTIER,
        ModelTier.ENTERPRISE,
    ):
        meta = _TIER_PUBLIC_META[tier]
        items.append(
            {
                "slug": tier.value,
                "label": meta["label"],
                "description": meta["description"],
                "credit_multiplier": CREDIT_MULTIPLIER_BY_TIER[tier],
                "available": tier in allowed,
            },
        )
    return items
