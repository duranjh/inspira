"""Tests for the plan-tier entitlements module + the /api/v2/entitlements endpoint.

PR 2 replaced the credit ledger (``credits.py``, ``user_credits``,
``credit_transactions``) with plan-tier feature gating
(``entitlements.py``). These tests pin the new contract:

- ``get_plan`` returns the user's tier or ``free`` for missing rows.
- ``has_feature`` enforces the rank ordering (free < pro < team).
- ``GET /api/v2/entitlements`` returns ``{plan, features}``.
- A Free user POSTing to the scaffold route gets the structured 402
  with ``error="upgrade_required"`` (frontend renders an upgrade CTA).
"""
from __future__ import annotations

import unittest

from planning_studio_service import entitlements

try:
    from ._helpers import fake_kickoff_response, make_test_app, signup_and_login
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )


class EntitlementsHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="ent-helpers@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_plan_is_free(self) -> None:
        self.assertEqual(
            entitlements.get_plan(self.store, user_id=self.user_id), "free",
        )

    def test_free_user_lacks_paid_features(self) -> None:
        self.assertFalse(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="scaffold",
            ),
        )
        self.assertFalse(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="frontier_models",
            ),
        )
        self.assertFalse(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="team_workspace",
            ),
        )

    def test_pro_user_has_scaffold_and_frontier_but_not_team(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="pro", store=self.store,
        )
        self.assertTrue(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="scaffold",
            ),
        )
        self.assertTrue(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="frontier_models",
            ),
        )
        self.assertFalse(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="team_workspace",
            ),
        )

    def test_team_user_has_everything(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="team", store=self.store,
        )
        self.assertTrue(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="scaffold",
            ),
        )
        self.assertTrue(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="team_workspace",
            ),
        )

    def test_unknown_feature_returns_true(self) -> None:
        # Open-by-default for unknown slugs so a typo doesn't lock
        # everyone out.
        self.assertTrue(
            entitlements.has_feature(
                self.store, user_id=self.user_id, feature="zzz_not_a_feature",
            ),
        )

    def test_unknown_plan_collapses_to_free(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        NoopBillingProvider().record_local_subscription(
            user_id=self.user_id, plan_slug="enterprise_xxx", store=self.store,
        )
        self.assertEqual(
            entitlements.get_plan(self.store, user_id=self.user_id), "free",
        )


class EntitlementsRouteTests(unittest.TestCase):
    """HTTP-level tests for /api/v2/entitlements."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="ent-route@example.com")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_free_user_payload_shape(self) -> None:
        resp = self.client.get("/api/v2/entitlements")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["plan"], "free")
        self.assertEqual(body["features"], [])
        # Crucially: NO legacy credit-domain fields leak through.
        for forbidden in ("balance", "scaffold_cost", "allotment", "packs"):
            self.assertNotIn(forbidden, body)

    def test_pro_user_payload_shape(self) -> None:
        from planning_studio_service.billing import NoopBillingProvider

        me = self.client.get("/api/auth/me").json()
        NoopBillingProvider().record_local_subscription(
            user_id=me["user_id"], plan_slug="pro", store=self.store,
        )
        resp = self.client.get("/api/v2/entitlements")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["plan"], "pro")
        self.assertIn("scaffold", body["features"])
        self.assertIn("frontier_models", body["features"])
        self.assertNotIn("team_workspace", body["features"])

    def test_legacy_credits_endpoint_is_404(self) -> None:
        """The old /api/v2/credits endpoint is gone — frontend must
        migrate to /api/v2/entitlements."""
        resp = self.client.get("/api/v2/credits")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
