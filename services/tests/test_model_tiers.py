"""Tests for the subscription-tier-gated LLM model picker.

Two layers:

1. Pure-function tests of ``agents.tiers.resolve_tier_for_user`` and the
   store's ``set_preferred_model_tier`` validation.
2. HTTP-level tests of the three endpoints that come with the feature:
   ``GET /api/v2/model-tiers``, ``PATCH /api/v2/auth/me/preferred-model-tier``,
   and the ``model_tier`` override body field on ``POST /api/v2/topics/{id}/turn``.
"""
from __future__ import annotations

import os
import unittest

try:
    from ._helpers import (
        fake_kickoff_response,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )

from planning_studio_service.agents.tiers import (
    ALLOWED_TIERS_BY_PLAN,
    CLAUDE_CODEGEN_MODEL,
    CLAUDE_FRONTIER_MODEL,
    CREDIT_MULTIPLIER_BY_TIER,
    DEFAULT_TIER_BY_PLAN,
    ModelTier,
    parse_tier,
    resolve_tier_for_user,
    tier_to_adapter,
    tier_to_claude_model,
    tier_to_openai_model,
    tier_to_reasoning_effort,
    tier_to_timeout_s,
)
from planning_studio_service.billing import NoopBillingProvider


class TierCatalogTests(unittest.TestCase):
    """Sanity checks on the static catalog constants."""

    def test_every_plan_has_an_allowlist(self) -> None:
        self.assertEqual(
            set(ALLOWED_TIERS_BY_PLAN.keys()),
            {"free", "pro", "team", "enterprise"},
        )

    def test_every_plan_has_a_default(self) -> None:
        self.assertEqual(
            set(DEFAULT_TIER_BY_PLAN.keys()),
            {"free", "pro", "team", "enterprise"},
        )

    def test_plan_defaults_are_in_allowlist(self) -> None:
        for slug, default in DEFAULT_TIER_BY_PLAN.items():
            self.assertIn(default, ALLOWED_TIERS_BY_PLAN[slug])

    def test_enterprise_plan_unlocks_all_four_tiers(self) -> None:
        self.assertEqual(
            ALLOWED_TIERS_BY_PLAN["enterprise"],
            {
                ModelTier.BASE,
                ModelTier.PRO,
                ModelTier.FRONTIER,
                ModelTier.ENTERPRISE,
            },
        )

    def test_credit_multipliers(self) -> None:
        self.assertEqual(CREDIT_MULTIPLIER_BY_TIER[ModelTier.BASE], 1.0)
        self.assertEqual(CREDIT_MULTIPLIER_BY_TIER[ModelTier.PRO], 3.0)
        self.assertEqual(CREDIT_MULTIPLIER_BY_TIER[ModelTier.FRONTIER], 5.0)
        self.assertEqual(CREDIT_MULTIPLIER_BY_TIER[ModelTier.ENTERPRISE], 8.0)

    def test_tier_to_openai_model_mapping(self) -> None:
        # Per-tier mapping locked in 2026-04-28 LLM-tier overhaul.
        # Output-cost ratio 1 : 5 : 15 across BASE / PRO / FRONTIER.
        # FRONTIER + ENTERPRISE OpenAI fallback only fires when
        # ANTHROPIC_API_KEY is absent — real frontier/enterprise
        # ``topic_turn`` goes Claude via ``tier_to_adapter``.
        self.assertEqual(tier_to_openai_model(ModelTier.BASE), "gpt-5-mini")
        self.assertEqual(tier_to_openai_model(ModelTier.PRO), "gpt-5")
        self.assertEqual(tier_to_openai_model(ModelTier.FRONTIER), "gpt-5.5")
        self.assertEqual(tier_to_openai_model(ModelTier.ENTERPRISE), "gpt-5.5")

    def test_tier_to_reasoning_effort(self) -> None:
        # BASE pins "low" so gpt-5-mini fits the 15s budget.
        # PRO/FRONTIER/ENTERPRISE return None so the adapter omits the
        # param and OpenAI uses its default ("medium") — paid users opt
        # into longer waits for higher-quality output. See #075
        # iteration-4 fix.
        self.assertEqual(tier_to_reasoning_effort(ModelTier.BASE), "low")
        self.assertIsNone(tier_to_reasoning_effort(ModelTier.PRO))
        self.assertIsNone(tier_to_reasoning_effort(ModelTier.FRONTIER))
        self.assertIsNone(tier_to_reasoning_effort(ModelTier.ENTERPRISE))

    def test_tier_to_timeout_s(self) -> None:
        # BASE returns None (use OpenAIConfig.timeout_s default = 15s)
        # to keep the interactive budget. PRO/FRONTIER/ENTERPRISE return
        # 60.0 so default medium reasoning effort completes without
        # tripping the user-visible timeout error.
        self.assertIsNone(tier_to_timeout_s(ModelTier.BASE))
        self.assertEqual(tier_to_timeout_s(ModelTier.PRO), 60.0)
        self.assertEqual(tier_to_timeout_s(ModelTier.FRONTIER), 60.0)
        self.assertEqual(tier_to_timeout_s(ModelTier.ENTERPRISE), 60.0)

    def test_tier_to_claude_model_default_is_sonnet_for_paid_tiers(self) -> None:
        # Per the "Claude only for code-gen on Frontier/Enterprise" rule,
        # the global ``tier_to_claude_model`` mapping must keep Sonnet as
        # the default for both FRONTIER and ENTERPRISE. The artifact
        # endpoint pins ``CLAUDE_CODEGEN_MODEL`` (Opus 4.7) per-call via
        # ``model_override`` instead of mutating this default.
        self.assertIsNone(tier_to_claude_model(ModelTier.BASE))
        self.assertIsNone(tier_to_claude_model(ModelTier.PRO))
        self.assertEqual(
            tier_to_claude_model(ModelTier.FRONTIER), CLAUDE_FRONTIER_MODEL,
        )
        self.assertEqual(
            tier_to_claude_model(ModelTier.ENTERPRISE), CLAUDE_FRONTIER_MODEL,
        )

    def test_claude_codegen_model_constant_is_opus_4_7(self) -> None:
        # Pinned for the artifact-viewer code-gen endpoint. Bump only
        # after live-validating the new Opus version against
        # ``sanitize_scaffold_manifest``.
        self.assertEqual(CLAUDE_CODEGEN_MODEL, "claude-opus-4-7")


class TierToAdapterTests(unittest.TestCase):
    """``tier_to_adapter`` routes FRONTIER + ENTERPRISE to Claude (closes #118)."""

    def setUp(self) -> None:
        # Sentinel adapters — identity is all that matters here.
        self.openai_adapter = object()
        self.claude_adapter = object()

    def test_base_routes_to_openai(self) -> None:
        chosen = tier_to_adapter(
            ModelTier.BASE,
            openai_adapter=self.openai_adapter,
            claude_adapter=self.claude_adapter,
        )
        self.assertIs(chosen, self.openai_adapter)

    def test_pro_routes_to_openai(self) -> None:
        chosen = tier_to_adapter(
            ModelTier.PRO,
            openai_adapter=self.openai_adapter,
            claude_adapter=self.claude_adapter,
        )
        self.assertIs(chosen, self.openai_adapter)

    def test_frontier_routes_to_claude_when_wired(self) -> None:
        chosen = tier_to_adapter(
            ModelTier.FRONTIER,
            openai_adapter=self.openai_adapter,
            claude_adapter=self.claude_adapter,
        )
        self.assertIs(chosen, self.claude_adapter)

    def test_frontier_falls_back_to_openai_when_claude_not_wired(self) -> None:
        chosen = tier_to_adapter(
            ModelTier.FRONTIER,
            openai_adapter=self.openai_adapter,
            claude_adapter=None,
        )
        self.assertIs(chosen, self.openai_adapter)

    def test_tier_to_adapter_enterprise_routes_to_claude(self) -> None:
        # Visible #118 closure: ENTERPRISE must route to Claude exactly
        # like FRONTIER. If this test ever flips, a Claude-only paid
        # feature has silently stopped working for enterprise users.
        chosen = tier_to_adapter(
            ModelTier.ENTERPRISE,
            openai_adapter=self.openai_adapter,
            claude_adapter=self.claude_adapter,
        )
        self.assertIs(chosen, self.claude_adapter)

    def test_enterprise_falls_back_to_openai_when_claude_not_wired(self) -> None:
        # Mirrors the FRONTIER fallback — dev environments without an
        # Anthropic key still serve enterprise turns rather than 500'ing.
        chosen = tier_to_adapter(
            ModelTier.ENTERPRISE,
            openai_adapter=self.openai_adapter,
            claude_adapter=None,
        )
        self.assertIs(chosen, self.openai_adapter)


class ResolveTierTests(unittest.TestCase):
    """``resolve_tier_for_user`` clamps disallowed tiers to the plan default."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="tierres@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _upgrade_to(self, plan_slug: str) -> None:
        noop = NoopBillingProvider()
        noop.record_local_subscription(
            user_id=self.user_id, plan_slug=plan_slug, store=self.store,
        )

    # ---- Free plan ----------------------------------------------------------

    def test_free_user_requesting_frontier_clamps_to_base(self) -> None:
        resolved = resolve_tier_for_user(
            self.store, self.user_id, ModelTier.FRONTIER,
        )
        self.assertEqual(resolved, ModelTier.BASE)

    def test_free_user_requesting_pro_clamps_to_base(self) -> None:
        resolved = resolve_tier_for_user(
            self.store, self.user_id, ModelTier.PRO,
        )
        self.assertEqual(resolved, ModelTier.BASE)

    def test_free_user_no_request_returns_base(self) -> None:
        resolved = resolve_tier_for_user(self.store, self.user_id, None)
        self.assertEqual(resolved, ModelTier.BASE)

    # ---- Pro plan -----------------------------------------------------------

    def test_pro_user_requesting_pro_returns_pro(self) -> None:
        self._upgrade_to("pro")
        resolved = resolve_tier_for_user(
            self.store, self.user_id, ModelTier.PRO,
        )
        self.assertEqual(resolved, ModelTier.PRO)

    def test_pro_user_requesting_frontier_clamps_to_plan_default(self) -> None:
        self._upgrade_to("pro")
        resolved = resolve_tier_for_user(
            self.store, self.user_id, ModelTier.FRONTIER,
        )
        # Pro plan default is PRO (not BASE).
        self.assertEqual(resolved, ModelTier.PRO)

    def test_pro_user_no_request_returns_pro_default(self) -> None:
        self._upgrade_to("pro")
        resolved = resolve_tier_for_user(self.store, self.user_id, None)
        self.assertEqual(resolved, ModelTier.PRO)

    # ---- Team plan ----------------------------------------------------------

    def test_team_user_can_request_frontier(self) -> None:
        self._upgrade_to("team")
        resolved = resolve_tier_for_user(
            self.store, self.user_id, ModelTier.FRONTIER,
        )
        self.assertEqual(resolved, ModelTier.FRONTIER)

    def test_team_user_no_request_returns_pro_not_frontier(self) -> None:
        """Team default is PRO, not FRONTIER — see DEFAULT_TIER_BY_PLAN."""
        self._upgrade_to("team")
        resolved = resolve_tier_for_user(self.store, self.user_id, None)
        self.assertEqual(resolved, ModelTier.PRO)

    # ---- Persisted preference ----------------------------------------------

    def test_persisted_preference_wins_over_plan_default(self) -> None:
        self._upgrade_to("team")
        self.store.set_preferred_model_tier(self.user_id, "frontier")
        resolved = resolve_tier_for_user(self.store, self.user_id, None)
        self.assertEqual(resolved, ModelTier.FRONTIER)

    def test_persisted_preference_is_clamped_to_plan(self) -> None:
        # Persist "frontier" while on free plan — the store write is OK
        # (we don't re-validate against plan at write-time) but the
        # resolver still clamps it down at read time.
        self.store.set_preferred_model_tier(self.user_id, "frontier")
        resolved = resolve_tier_for_user(self.store, self.user_id, None)
        self.assertEqual(resolved, ModelTier.BASE)


class StoreValidationTests(unittest.TestCase):
    """``set_preferred_model_tier`` validates the slug."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="storeval@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_set_rejects_unknown_slug(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_preferred_model_tier(self.user_id, "super-ultra")

    def test_set_accepts_known_slugs(self) -> None:
        for slug in ("base", "pro", "frontier", "enterprise"):
            self.store.set_preferred_model_tier(self.user_id, slug)
            self.assertEqual(
                self.store.get_preferred_model_tier(self.user_id), slug,
            )

    def test_set_accepts_none_to_clear(self) -> None:
        self.store.set_preferred_model_tier(self.user_id, "pro")
        self.store.set_preferred_model_tier(self.user_id, None)
        self.assertIsNone(self.store.get_preferred_model_tier(self.user_id))

    def test_parse_tier_returns_none_on_garbage(self) -> None:
        self.assertIsNone(parse_tier(""))
        self.assertIsNone(parse_tier(None))
        self.assertIsNone(parse_tier("nope"))
        self.assertEqual(parse_tier("pro"), ModelTier.PRO)


class ModelTiersEndpointTests(unittest.TestCase):
    """``GET /api/v2/model-tiers`` flags availability per plan."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="catendpt@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _avail_map(self, response: dict) -> dict[str, bool]:
        return {t["slug"]: t["available"] for t in response["tiers"]}

    def test_free_user_sees_base_only_available(self) -> None:
        response = self.client.get("/api/v2/model-tiers").json()
        self.assertEqual(
            self._avail_map(response),
            {
                "base": True,
                "pro": False,
                "frontier": False,
                "enterprise": False,
            },
        )
        self.assertEqual(response["current_default"], "base")
        self.assertIsNone(response["persisted_default"])
        self.assertEqual(response["plan_slug"], "free")

    def test_pro_user_sees_base_and_pro_available(self) -> None:
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        response = self.client.get("/api/v2/model-tiers").json()
        self.assertEqual(
            self._avail_map(response),
            {
                "base": True,
                "pro": True,
                "frontier": False,
                "enterprise": False,
            },
        )
        self.assertEqual(response["current_default"], "pro")

    def test_team_user_sees_all_but_enterprise_available(self) -> None:
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        response = self.client.get("/api/v2/model-tiers").json()
        self.assertEqual(
            self._avail_map(response),
            {
                "base": True,
                "pro": True,
                "frontier": True,
                "enterprise": False,
            },
        )
        # Team default is PRO, not FRONTIER.
        self.assertEqual(response["current_default"], "pro")

    # NOTE: an end-to-end "enterprise user sees all four tiers" test
    # requires a corresponding ``enterprise`` plan in
    # ``billing/plans.py`` and a Stripe price id. That's a separate
    # product change — adding the purchasable plan is out of scope for
    # the #118 closure (which is about the tier enum + dispatch).
    # ``ALLOWED_TIERS_BY_PLAN["enterprise"]`` and ``ENTERPRISE`` tier
    # plumbing land in this PR; the catalog endpoint integration test
    # follows when the plan ships.

    def test_catalog_carries_label_and_multiplier(self) -> None:
        response = self.client.get("/api/v2/model-tiers").json()
        slugs = {t["slug"]: t for t in response["tiers"]}
        self.assertEqual(slugs["pro"]["credit_multiplier"], 3.0)
        self.assertEqual(slugs["frontier"]["credit_multiplier"], 5.0)
        self.assertIn("label", slugs["base"])
        self.assertIn("description", slugs["base"])


class PatchPreferredTierTests(unittest.TestCase):
    """``PATCH /api/v2/auth/me/preferred-model-tier`` validates + persists."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="patchuser@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_free_user_can_persist_base(self) -> None:
        response = self.client.patch(
            "/api/v2/auth/me/preferred-model-tier",
            json={"tier": "base"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tier"], "base")

    def test_free_user_cannot_persist_pro_or_frontier(self) -> None:
        for tier in ("pro", "frontier"):
            response = self.client.patch(
                "/api/v2/auth/me/preferred-model-tier",
                json={"tier": tier},
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(
                response.json()["detail"]["error"], "tier_not_in_plan",
            )

    def test_unknown_slug_rejected(self) -> None:
        response = self.client.patch(
            "/api/v2/auth/me/preferred-model-tier",
            json={"tier": "ultra-frontier"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"], "unknown_tier")

    def test_null_clears_the_override(self) -> None:
        # Persist then clear.
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        self.client.patch(
            "/api/v2/auth/me/preferred-model-tier",
            json={"tier": "pro"},
        )
        self.assertEqual(self.store.get_preferred_model_tier(self.user_id), "pro")
        response = self.client.patch(
            "/api/v2/auth/me/preferred-model-tier",
            json={"tier": None},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cleared"])
        self.assertIsNone(self.store.get_preferred_model_tier(self.user_id))


class TopicTurnTierRoutingTests(unittest.TestCase):
    """``model_tier`` body field threads through to the adapter."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="turntier@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

        # Seed a project + topic via the kickoff fixture so we have an
        # actual topic_id to hit.
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-tierturn/kickoff",
            json={"user_idea": "A small wine festival."},
        ).json()
        self.project_id = "proj-tierturn"
        self.venue_id = next(
            t["topic_id"] for t in kick["topics"] if t["title"] == "Venue"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _take_adapter_kwargs(self) -> dict:
        """Return the kwargs from the last ``adapter.topic_turn`` call."""
        self.assertTrue(
            self.adapter.topic_turn.called,
            "adapter.topic_turn was never called",
        )
        # kwargs is always the second arg of call_args.
        return dict(self.adapter.topic_turn.call_args.kwargs)

    def test_free_user_requesting_pro_clamps_to_base(self) -> None:
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes.", "model_tier": "pro"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        kwargs = self._take_adapter_kwargs()
        # Free user requesting pro clamps to base. BASE → gpt-5-mini.
        self.assertEqual(kwargs["model_override"], "gpt-5-mini")

    def test_pro_user_requesting_pro_uses_topic_turn_model(self) -> None:
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes.", "model_tier": "pro"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        kwargs = self._take_adapter_kwargs()
        # Pro user explicit pro → PRO → gpt-5.
        self.assertEqual(kwargs["model_override"], "gpt-5")

    def test_pro_user_no_override_uses_pro_default(self) -> None:
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes."},
        )
        self.assertEqual(response.status_code, 201, response.text)
        kwargs = self._take_adapter_kwargs()
        # Pro plan default tier is PRO → gpt-5.
        self.assertEqual(kwargs["model_override"], "gpt-5")

    def test_free_user_no_override_uses_base_default(self) -> None:
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes."},
        )
        self.assertEqual(response.status_code, 201, response.text)
        kwargs = self._take_adapter_kwargs()
        # Free plan default tier is BASE → gpt-5-mini.
        self.assertEqual(kwargs["model_override"], "gpt-5-mini")

    def test_base_passes_low_reasoning_effort_and_default_timeout(self) -> None:
        # Free user → BASE → gpt-5-mini with reasoning_effort="low" so
        # latency fits the 15s adapter timeout (#075 iteration-4 fix).
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes."},
        )
        self.assertEqual(response.status_code, 201, response.text)
        kwargs = self._take_adapter_kwargs()
        self.assertEqual(kwargs["reasoning_effort"], "low")
        # BASE returns None to use the adapter's config timeout default.
        self.assertIsNone(kwargs["timeout_s"])

    def test_pro_passes_default_reasoning_effort_and_extended_timeout(self) -> None:
        # Pro user → PRO → gpt-5 with reasoning_effort=None (OpenAI
        # default = medium) and timeout_s=60.0 to accommodate the
        # paid-tier "longer waits OK" tradeoff.
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        response = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Yes."},
        )
        self.assertEqual(response.status_code, 201, response.text)
        kwargs = self._take_adapter_kwargs()
        self.assertIsNone(kwargs["reasoning_effort"])
        self.assertEqual(kwargs["timeout_s"], 60.0)



if __name__ == "__main__":
    unittest.main()
