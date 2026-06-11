"""Per-tier Kanban auto-promote cap + ranking tests (#172).

Pins three behaviours of ``ensure_v2_projects_for_clusters`` after
the 2026-05-12 refactor:

1. Free tier caps at 10 (cluster overflow → Inbox archive).
2. Enterprise tier caps at 200.
3. Ranking score (``item_count × recency × severity``) decides which
   clusters cross the cap — bugs beat features beat noise.

Plus a fourth pure-function test pinning the plan_slug → ModelTier
mapping that lives in ``tiers.kanban_tier_for_plan``. The mapping
intentionally differs from ``DEFAULT_TIER_BY_PLAN`` (which is the
user-facing default *runtime* tier, not the plan's max tier), so a
future refactor of either can't silently break the cap lookup.
"""
from __future__ import annotations

import hashlib
import secrets
import unittest

from planning_studio_service.agents.tiers import (
    ModelTier,
    kanban_tier_for_plan,
)
from planning_studio_service.feedback_items import cluster as fc
from planning_studio_service.store import now_timestamp

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        make_test_app,
        signup_and_login,
    )


class _CapTestBase(unittest.TestCase):
    """Shared scaffolding: builds the store + a workspace_id + a helper to
    seed feedback_clusters + feedback_items in bulk.

    We bypass ``feedback_store.upsert_item`` here because the production
    helper computes content_hash from canonical content + UPSERTs on
    UNIQUE(workspace_id, content_hash) which is more work than these
    tests need. We're driving the ranking function directly — raw INSERTs
    keep the test fixture obvious.
    """

    workspace_id = "ws-cap-test"
    user_id = "user-cap-test"

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        del self.temp_dir

    def _seed_cluster(
        self,
        *,
        item_count: int = 1,
        type_hint: str | None = "bug",
        received_at: str | None = None,
    ) -> str:
        """Insert one feedback_clusters row + ``item_count`` feedback_items
        rows pointing at it. Returns the cluster_id.

        ``received_at`` defaults to ``now_timestamp()`` so the recency
        weight is the maximum (1.0). Callers override to test older
        recency buckets.
        """
        cluster_id = f"cl-test-{secrets.token_hex(4)}"
        now = now_timestamp()
        rcv = received_at if received_at is not None else now
        with self.store._connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback_clusters
                    (cluster_id, workspace_id, centroid_json, theme,
                     item_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cluster_id,
                    self.workspace_id,
                    "[]",
                    None,
                    item_count,
                    now,
                    now,
                ),
            )
            for i in range(item_count):
                item_id = f"it-{secrets.token_hex(4)}"
                content = f"{cluster_id}-{i}"
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                connection.execute(
                    """
                    INSERT INTO feedback_items
                        (item_id, workspace_id, source, external_id,
                         content_hash, title, body, received_at,
                         ingested_at, type_hint, cluster_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        self.workspace_id,
                        "csv-import",
                        None,
                        content_hash,
                        f"item {i} of {cluster_id}",
                        "",
                        rcv,
                        now,
                        type_hint,
                        cluster_id,
                    ),
                )
            connection.commit()
        return cluster_id

    def _count_v2_projects(self) -> int:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM v2_projects WHERE workspace_id = ? "
                "AND deleted_at IS NULL",
                (self.workspace_id,),
            ).fetchone()
        return int(row[0])

    def _promoted_cluster_ids(self) -> set[str]:
        """Read back the cluster_ids that auto-promoted to v2_projects."""
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT json_extract(metadata_json, '$.cluster_id') "
                "FROM v2_projects WHERE workspace_id = ? "
                "AND deleted_at IS NULL",
                (self.workspace_id,),
            ).fetchall()
        return {r[0] for r in rows if r and r[0]}


class FreeTierCapsAt10Tests(_CapTestBase):

    def test_free_tier_caps_at_10(self) -> None:
        # 50 clusters, 1 item each, all type_hint=bug + fresh received_at
        # → identical score across all → tie-break by cluster_id asc.
        # Cap (BASE) = 10.
        cluster_ids = {self._seed_cluster() for _ in range(50)}

        promoted, deferred = fc.ensure_v2_projects_for_clusters(
            self.store,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            cluster_ids=cluster_ids,
            plan_tier=ModelTier.BASE,
        )

        self.assertEqual(promoted, 10)
        self.assertEqual(deferred, 40)
        self.assertEqual(self._count_v2_projects(), 10)


class EnterpriseTierCapsAt200Tests(_CapTestBase):

    def test_enterprise_caps_at_200(self) -> None:
        # 250 clusters → 200 promoted, 50 deferred. Enterprise cap.
        cluster_ids = {self._seed_cluster() for _ in range(250)}

        promoted, deferred = fc.ensure_v2_projects_for_clusters(
            self.store,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            cluster_ids=cluster_ids,
            plan_tier=ModelTier.ENTERPRISE,
        )

        self.assertEqual(promoted, 200)
        self.assertEqual(deferred, 50)
        self.assertEqual(self._count_v2_projects(), 200)


class RankingOrderTests(_CapTestBase):

    def test_ranking_promotes_high_score_first(self) -> None:
        # 5 bug clusters @ 10 items each + 15 feature clusters @ 1 item.
        # Cap (BASE) = 10.
        # bug score   = 10 × 1.0 × 1.0 = 10.0
        # feature score = 1 × 1.0 × 0.5 = 0.5
        # All 5 bugs land; cap is 10, so 5 of the 15 features also land
        # (tie-broken by cluster_id asc). 10 features deferred.
        bug_ids = {
            self._seed_cluster(item_count=10, type_hint="bug")
            for _ in range(5)
        }
        feature_ids = {
            self._seed_cluster(item_count=1, type_hint="feature")
            for _ in range(15)
        }
        all_ids = bug_ids | feature_ids

        promoted, deferred = fc.ensure_v2_projects_for_clusters(
            self.store,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            cluster_ids=all_ids,
            plan_tier=ModelTier.BASE,
        )

        self.assertEqual(promoted, 10)
        self.assertEqual(deferred, 10)

        promoted_ids = self._promoted_cluster_ids()
        # Every bug cluster must be on the Kanban (highest score).
        self.assertEqual(
            bug_ids & promoted_ids,
            bug_ids,
            msg="all bug clusters must outrank features",
        )
        # Exactly 5 features made the cut (10 promoted - 5 bugs).
        self.assertEqual(len(promoted_ids & feature_ids), 5)


class KanbanTierForPlanMappingTests(unittest.TestCase):
    """Pure-function test — no store, no fixture. Pins the four canonical
    slug → ModelTier mappings + the unknown-slug → BASE default so a
    future refactor of DEFAULT_TIER_BY_PLAN can't silently break the cap
    lookup. The two helpers map differently (DEFAULT: team→PRO, here:
    team→FRONTIER) and we want to keep that drift visible."""

    def test_canonical_slugs(self) -> None:
        self.assertEqual(kanban_tier_for_plan("free"), ModelTier.BASE)
        self.assertEqual(kanban_tier_for_plan("pro"), ModelTier.PRO)
        self.assertEqual(kanban_tier_for_plan("team"), ModelTier.FRONTIER)
        self.assertEqual(
            kanban_tier_for_plan("enterprise"), ModelTier.ENTERPRISE,
        )

    def test_defensive_defaults(self) -> None:
        self.assertEqual(kanban_tier_for_plan(None), ModelTier.BASE)
        self.assertEqual(kanban_tier_for_plan("nonsense"), ModelTier.BASE)
        self.assertEqual(kanban_tier_for_plan(""), ModelTier.BASE)


class CsvImportRouterTierCapTests(unittest.TestCase):
    """End-to-end check on the ``/api/v2/connectors/csv/import`` route:
    after the #172 refactor, a default (Free) workspace that imports
    enough distinct titles to create > 10 clusters must see at most 10
    cards auto-promote, and the response must carry both
    ``auto_promoted`` and the new ``deferred`` field.
    """

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        signup_and_login(
            self.client,
            email="member@cap-test.com",
            password="password123",
            display_name="Cap Member",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "cap-corp", "name": "Cap Corp"},
        )
        self.workspace_id = ws.json()["workspace"]["workspace_id"]

    def tearDown(self) -> None:
        del self.temp_dir

    def test_free_plan_route_caps_at_10_and_returns_deferred(self) -> None:
        # 50 rows in 25 title-pairs (embeddings flag is off in tests, so
        # the title-normalisation fallback runs — and it only forms a
        # cluster when a SECOND item with the same normalised title
        # appears). Each pair = one cluster → 25 clusters. Free cap = 10.
        #
        # Numeric tokens >= 10 stay in the normalised key (single-digit
        # tokens get filtered by the len(t) > 1 rule in normalize_title);
        # using cluster indices 10..34 keeps all 25 normalised keys
        # distinct.
        rows = []
        for cluster_idx in range(10, 35):  # 25 clusters
            rows.append(
                {"title": f"feature {cluster_idx} broken on safari",
                 "body": "first report"},
            )
            rows.append(
                {"title": f"feature {cluster_idx} broken on safari",
                 "body": "duplicate report"},
            )

        resp = self.client.post(
            "/api/v2/connectors/csv/import",
            json={"rows": rows},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["inserted"], 50)
        # Cap kicks in: default Free plan → 10 Kanban cards.
        self.assertEqual(body["auto_promoted"], 10)
        self.assertEqual(body["deferred"], 15)


if __name__ == "__main__":
    unittest.main()
