"""Tests for the monthly tier-cap counter store + helpers (#080).

Covers:
- ``store.get_tier_usage`` / ``increment_tier_usage`` happy path + lazy
  month-boundary reset.
- ``store.get_business_plan_usage`` / ``increment_business_plan_usage``
  happy path + lazy reset.
- ``_usage_window_is_stale`` boundary cases.
- ``agents.tiers.get_tier_cap`` per-plan-per-tier lookup with the
  locked cap policy.
- ``agents.tiers.get_business_plan_cap`` per-plan lookup.

The store-method tests use ``make_test_app()`` for an isolated SQLite
DB per test (mirrors the pattern in ``test_entitlements.py``).
"""
from __future__ import annotations

import datetime as _dt
import unittest
from unittest import mock

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]

from planning_studio_service.agents.tiers import (
    BUSINESS_PLAN_CAPS_BY_PLAN,
    CAPS_BY_PLAN_AND_TIER,
    ModelTier,
    get_business_plan_cap,
    get_tier_cap,
    select_tier_after_cap_check,
)
from planning_studio_service.billing import NoopBillingProvider
from planning_studio_service.store import (
    _usage_window_is_stale,
    now_timestamp,
)


class TierCapHelperTests(unittest.TestCase):
    """``get_tier_cap`` returns the locked cap-policy values."""

    def test_free_only_has_base_cap(self) -> None:
        self.assertEqual(get_tier_cap("free", ModelTier.BASE), 2_000_000)
        self.assertIsNone(get_tier_cap("free", ModelTier.PRO))
        self.assertIsNone(get_tier_cap("free", ModelTier.FRONTIER))

    def test_pro_has_base_and_pro_caps(self) -> None:
        self.assertEqual(get_tier_cap("pro", ModelTier.BASE), 2_000_000)
        self.assertEqual(get_tier_cap("pro", ModelTier.PRO), 2_250_000)
        self.assertIsNone(get_tier_cap("pro", ModelTier.FRONTIER))

    def test_team_frontier_plan_has_all_three_caps(self) -> None:
        # Slug stays "team" for backward-compat; Frontier is the
        # display name (#081). All three tiers have caps.
        self.assertEqual(get_tier_cap("team", ModelTier.BASE), 2_000_000)
        self.assertEqual(get_tier_cap("team", ModelTier.PRO), 1_500_000)
        self.assertEqual(get_tier_cap("team", ModelTier.FRONTIER), 4_500_000)

    def test_unknown_plan_collapses_to_free(self) -> None:
        # Defensive fallback: an unknown plan slug shouldn't unlock a
        # higher-tier cap. ``_plan_slug_or_free`` logs a warning + treats
        # as free. (``enterprise`` was the placeholder unknown here
        # before #118 added it as a real plan slug; switched to an
        # obviously-fake slug to keep the defensive test alive.)
        self.assertEqual(
            get_tier_cap("zorgon", ModelTier.BASE), 2_000_000,
        )
        self.assertIsNone(get_tier_cap("zorgon", ModelTier.PRO))

    def test_none_plan_collapses_to_free(self) -> None:
        self.assertEqual(get_tier_cap(None, ModelTier.BASE), 2_000_000)
        self.assertIsNone(get_tier_cap(None, ModelTier.PRO))

    def test_constants_are_consistent_with_helpers(self) -> None:
        # Catch silent drift if someone edits the dict but not the helper
        # (or vice versa).
        for plan, tier_caps in CAPS_BY_PLAN_AND_TIER.items():
            for tier, cap in tier_caps.items():
                self.assertEqual(get_tier_cap(plan, tier), cap)


class UsageWindowStaleTests(unittest.TestCase):
    """``_usage_window_is_stale`` boundary semantics."""

    def test_now_is_not_stale(self) -> None:
        self.assertFalse(_usage_window_is_stale(now_timestamp()))

    def test_empty_string_is_stale(self) -> None:
        self.assertTrue(_usage_window_is_stale(""))

    def test_malformed_iso_is_stale(self) -> None:
        # Fail-safe: unparseable values reset the counter rather
        # than silently keep a dirty value.
        self.assertTrue(_usage_window_is_stale("garbage"))
        self.assertTrue(_usage_window_is_stale("2026-99-99"))

    def test_naive_iso_assumed_utc(self) -> None:
        # Pre-tz-aware writes assume UTC. Yesterday's date should be stale
        # only if last calendar month; recent same-month should not.
        now = _dt.datetime.now(_dt.timezone.utc)
        same_month_naive = now.replace(tzinfo=None).isoformat()
        self.assertFalse(_usage_window_is_stale(same_month_naive))

    def test_last_calendar_month_is_stale(self) -> None:
        # Pick a fixed last-month date that's safely before this month's start.
        prev = "2024-01-15T12:00:00+00:00"
        self.assertTrue(_usage_window_is_stale(prev))

    def test_first_of_current_month_is_not_stale(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        first = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        self.assertFalse(_usage_window_is_stale(first.isoformat()))

    def test_microsecond_before_month_start_is_stale(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        first = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        # Just before the calendar month boundary.
        before = first - _dt.timedelta(seconds=1)
        self.assertTrue(_usage_window_is_stale(before.isoformat()))


class TierUsageStoreTests(unittest.TestCase):
    """``store.get_tier_usage`` / ``increment_tier_usage`` round-trip."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="capstest@example.com")
        self.user_id = self.client.get("/api/auth/me").json()["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initial_read_returns_zero(self) -> None:
        usage = self.store.get_tier_usage(user_id=self.user_id, tier="base")
        self.assertEqual(usage["output_tokens_used"], 0)
        self.assertEqual(usage["tier"], "base")
        self.assertIn("window_started_at", usage)

    def test_first_increment_creates_row(self) -> None:
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=500,
        )
        usage = self.store.get_tier_usage(user_id=self.user_id, tier="base")
        self.assertEqual(usage["output_tokens_used"], 500)

    def test_subsequent_increments_accumulate(self) -> None:
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=300,
        )
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=200,
        )
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=100,
        )
        usage = self.store.get_tier_usage(user_id=self.user_id, tier="base")
        self.assertEqual(usage["output_tokens_used"], 600)

    def test_tiers_are_independent(self) -> None:
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=100,
        )
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="pro", tokens=200,
        )
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="frontier", tokens=300,
        )
        self.assertEqual(
            self.store.get_tier_usage(
                user_id=self.user_id, tier="base",
            )["output_tokens_used"], 100,
        )
        self.assertEqual(
            self.store.get_tier_usage(
                user_id=self.user_id, tier="pro",
            )["output_tokens_used"], 200,
        )
        self.assertEqual(
            self.store.get_tier_usage(
                user_id=self.user_id, tier="frontier",
            )["output_tokens_used"], 300,
        )

    def test_zero_tokens_is_a_noop(self) -> None:
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=0,
        )
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=-5,
        )
        usage = self.store.get_tier_usage(user_id=self.user_id, tier="base")
        self.assertEqual(usage["output_tokens_used"], 0)

    def test_lazy_reset_when_window_is_stale(self) -> None:
        # Seed a row with a stale window directly via the connection
        # (we don't expose a "force-stale" API).
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO tier_usage "
                "(user_id, tier, output_tokens_used, window_started_at) "
                "VALUES (?, ?, ?, ?)",
                (self.user_id, "base", 999_999, "2024-01-15T00:00:00+00:00"),
            )
            conn.commit()
        # Read returns zero (lazy reset).
        usage = self.store.get_tier_usage(user_id=self.user_id, tier="base")
        self.assertEqual(usage["output_tokens_used"], 0)
        # Increment overwrites the stale value (not adds on top).
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=100,
        )
        usage = self.store.get_tier_usage(user_id=self.user_id, tier="base")
        self.assertEqual(usage["output_tokens_used"], 100)


class BusinessPlanUsageStoreTests(unittest.TestCase):
    """``store.get_business_plan_usage`` / ``increment_business_plan_usage``."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="bptest@example.com")
        self.user_id = self.client.get("/api/auth/me").json()["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initial_read_is_zero(self) -> None:
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 0)

    def test_increment_creates_row(self) -> None:
        self.store.increment_business_plan_usage(user_id=self.user_id)
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 1)

    def test_repeated_increments_accumulate(self) -> None:
        for _ in range(5):
            self.store.increment_business_plan_usage(user_id=self.user_id)
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 5)

    def test_lazy_reset_on_stale_window(self) -> None:
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO business_plan_usage "
                "(user_id, plans_used_this_month, window_started_at) "
                "VALUES (?, ?, ?)",
                (self.user_id, 87, "2024-01-15T00:00:00+00:00"),
            )
            conn.commit()
        # Read returns 0 (stale window → lazy reset).
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 0)
        # Increment overwrites the stale 87 with 1.
        self.store.increment_business_plan_usage(user_id=self.user_id)
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 1)


class SelectTierAfterCapCheckTests(unittest.TestCase):
    """Auto-fallback chain when a tier's monthly cap is exhausted."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="capcheck@example.com")
        self.user_id = self.client.get("/api/auth/me").json()["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _exhaust(self, tier: ModelTier, plan_slug: str) -> None:
        """Push the user's tier_usage to its full cap."""
        cap = get_tier_cap(plan_slug, tier)
        assert cap is not None, "test bug: exhausting a None-cap tier"
        self.store.increment_tier_usage(
            user_id=self.user_id, tier=tier.value, tokens=cap,
        )

    def test_no_usage_returns_requested_tier(self) -> None:
        effective, fell_back_from = select_tier_after_cap_check(
            self.store, self.user_id, "team", ModelTier.FRONTIER,
        )
        self.assertEqual(effective, ModelTier.FRONTIER)
        self.assertIsNone(fell_back_from)

    def test_frontier_exhausted_falls_back_to_pro(self) -> None:
        self._exhaust(ModelTier.FRONTIER, "team")
        effective, fell_back_from = select_tier_after_cap_check(
            self.store, self.user_id, "team", ModelTier.FRONTIER,
        )
        self.assertEqual(effective, ModelTier.PRO)
        self.assertEqual(fell_back_from, ModelTier.FRONTIER)

    def test_frontier_and_pro_exhausted_falls_back_to_base(self) -> None:
        self._exhaust(ModelTier.FRONTIER, "team")
        self._exhaust(ModelTier.PRO, "team")
        effective, fell_back_from = select_tier_after_cap_check(
            self.store, self.user_id, "team", ModelTier.FRONTIER,
        )
        self.assertEqual(effective, ModelTier.BASE)
        self.assertEqual(fell_back_from, ModelTier.FRONTIER)

    def test_all_tiers_exhausted_returns_none(self) -> None:
        self._exhaust(ModelTier.FRONTIER, "team")
        self._exhaust(ModelTier.PRO, "team")
        self._exhaust(ModelTier.BASE, "team")
        effective, fell_back_from = select_tier_after_cap_check(
            self.store, self.user_id, "team", ModelTier.FRONTIER,
        )
        self.assertIsNone(effective)
        self.assertEqual(fell_back_from, ModelTier.FRONTIER)

    def test_pro_user_pro_exhausted_falls_back_to_base(self) -> None:
        # Pro plan: PRO chain is PRO → BASE.
        self._exhaust(ModelTier.PRO, "pro")
        effective, fell_back_from = select_tier_after_cap_check(
            self.store, self.user_id, "pro", ModelTier.PRO,
        )
        self.assertEqual(effective, ModelTier.BASE)
        self.assertEqual(fell_back_from, ModelTier.PRO)

    def test_free_user_base_exhausted_returns_none(self) -> None:
        # Free plan: BASE chain has no fallback below BASE.
        self._exhaust(ModelTier.BASE, "free")
        effective, fell_back_from = select_tier_after_cap_check(
            self.store, self.user_id, "free", ModelTier.BASE,
        )
        self.assertIsNone(effective)
        self.assertEqual(fell_back_from, ModelTier.BASE)


class V2GetUsageEndpointTests(unittest.TestCase):
    """``GET /api/v2/auth/me/usage`` round-trip."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="usageendpt@example.com")
        self.user_id = self.client.get("/api/auth/me").json()["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_free_user_sees_base_only(self) -> None:
        response = self.client.get("/api/v2/auth/me/usage")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["plan_slug"], "free")
        self.assertEqual(len(body["tiers"]), 1)
        self.assertEqual(body["tiers"][0]["tier"], "base")
        self.assertEqual(body["tiers"][0]["used"], 0)
        self.assertEqual(body["tiers"][0]["cap"], 2_000_000)
        self.assertEqual(body["tiers"][0]["percent"], 0.0)
        # Free users have no business-plan cap (0).
        self.assertEqual(body["business_plan"]["cap"], 0)

    def test_pro_user_sees_base_and_pro(self) -> None:
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        response = self.client.get("/api/v2/auth/me/usage")
        body = response.json()
        self.assertEqual(body["plan_slug"], "pro")
        slugs = [t["tier"] for t in body["tiers"]]
        self.assertEqual(slugs, ["base", "pro"])
        self.assertEqual(body["business_plan"]["cap"], 1)

    def test_team_user_sees_all_three_tiers(self) -> None:
        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        response = self.client.get("/api/v2/auth/me/usage")
        body = response.json()
        self.assertEqual(body["plan_slug"], "team")
        slugs = [t["tier"] for t in body["tiers"]]
        self.assertEqual(slugs, ["base", "pro", "frontier"])
        # Frontier business-plan cap from #081.
        self.assertEqual(body["business_plan"]["cap"], 100)

    def test_used_count_reflects_increments(self) -> None:
        # Bump the counter then verify the endpoint reflects it.
        self.store.increment_tier_usage(
            user_id=self.user_id, tier="base", tokens=500_000,
        )
        response = self.client.get("/api/v2/auth/me/usage")
        body = response.json()
        base_row = next(t for t in body["tiers"] if t["tier"] == "base")
        self.assertEqual(base_row["used"], 500_000)
        self.assertEqual(base_row["cap"], 2_000_000)
        self.assertEqual(base_row["percent"], 0.25)


if __name__ == "__main__":
    unittest.main()
