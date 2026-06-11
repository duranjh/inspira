"""W2-θ cascade scope-computation tests.

Exercises ``cascade.compute_affected_scope`` against a real store —
no LLM, no FastAPI client. Validates the topic + one-hop-relationships
algorithm and the banner-state thresholds.
"""
from __future__ import annotations

import unittest

from planning_studio_service.agents import cascade

try:
    from ._helpers import make_test_app
except ImportError:
    from _helpers import make_test_app  # type: ignore[no-redef]


class CascadeScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.project_id = "proj-scope-test"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _topic(self, title: str) -> str:
        return self.store.create_topic(
            project_id=self.project_id, title=title, icon="flag",
        )["topic_id"]

    def _decision(self, *, topic_id: str, statement: str = "...") -> str:
        return self.store.create_decision(
            topic_id=topic_id,
            project_id=self.project_id,
            statement=statement,
            proposed_by="orchestrator",
        )["decision_id"]

    def _relationship(
        self, *, source_topic_id: str, target_topic_id: str, label: str = "depends_on",
    ) -> None:
        from planning_studio_service.store import now_timestamp
        import uuid
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO relationships (
                    relationship_id, project_id, source_topic_id,
                    target_topic_id, label, origin, strength,
                    created_at, deleted_at
                )
                VALUES (?, ?, ?, ?, ?, 'planner', 1.0, ?, NULL)
                """,
                (
                    f"rel-{uuid.uuid4().hex[:8]}", self.project_id,
                    source_topic_id, target_topic_id, label,
                    now_timestamp(),
                ),
            )
            conn.commit()

    # ----- basic shape ---------------------------------------------

    def test_local_mode_short_circuits_to_none(self) -> None:
        topic = self._topic("Auth")
        d_main = self._decision(topic_id=topic, statement="Use JWT")
        # Even with siblings, local mode returns count=0
        self._decision(topic_id=topic, statement="Sibling A")
        self._decision(topic_id=topic, statement="Sibling B")
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="local",
        )
        self.assertEqual(scope["count"], 0)
        self.assertEqual(scope["banner_state"], "none")
        self.assertEqual(scope["decision_ids"], [])

    def test_unknown_scope_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            cascade.compute_affected_scope(
                self.store, project_id=self.project_id,
                commented_decision_ids=["d1"], scope_mode="bogus",
            )

    # ----- cascade-mode sibling expansion --------------------------

    def test_no_siblings_returns_none(self) -> None:
        topic = self._topic("Solo")
        d_main = self._decision(topic_id=topic)
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="cascade",
        )
        self.assertEqual(scope["count"], 0)
        self.assertEqual(scope["banner_state"], "none")

    def test_two_siblings_narrow(self) -> None:
        topic = self._topic("Auth")
        d_main = self._decision(topic_id=topic, statement="Use JWT")
        s1 = self._decision(topic_id=topic, statement="Refresh tokens")
        s2 = self._decision(topic_id=topic, statement="MFA required")
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="cascade",
        )
        self.assertEqual(scope["count"], 2)
        self.assertEqual(scope["banner_state"], "narrow")
        self.assertEqual(set(scope["decision_ids"]), {s1, s2})
        self.assertNotIn(d_main, scope["decision_ids"])

    def test_four_siblings_wide(self) -> None:
        topic = self._topic("Auth")
        d_main = self._decision(topic_id=topic)
        for i in range(4):
            self._decision(topic_id=topic, statement=f"Sibling {i}")
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="cascade",
        )
        self.assertEqual(scope["count"], 4)
        self.assertEqual(scope["banner_state"], "wide")

    # ----- one-hop relationships -----------------------------------

    def test_one_hop_relationship_includes_neighbor_topic_decisions(self) -> None:
        t_auth = self._topic("Auth")
        t_billing = self._topic("Billing")
        d_main = self._decision(topic_id=t_auth, statement="Use JWT")
        d_neighbor = self._decision(topic_id=t_billing, statement="Stripe Identity")
        self._relationship(source_topic_id=t_auth, target_topic_id=t_billing)
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="cascade",
        )
        self.assertIn(d_neighbor, scope["decision_ids"])
        self.assertEqual(scope["count"], 1)
        self.assertEqual(scope["banner_state"], "narrow")

    def test_one_hop_relationship_is_bidirectional(self) -> None:
        t_a = self._topic("A")
        t_b = self._topic("B")
        d_in_a = self._decision(topic_id=t_a)
        d_in_b = self._decision(topic_id=t_b)
        # Edge in only one direction.
        self._relationship(source_topic_id=t_b, target_topic_id=t_a)
        # Commenting on a's decision should still pull b's decision in.
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_in_a],
            scope_mode="cascade",
        )
        self.assertIn(d_in_b, scope["decision_ids"])

    # ----- exclusions ----------------------------------------------

    def test_excludes_retracted_decisions(self) -> None:
        topic = self._topic("Auth")
        d_main = self._decision(topic_id=topic)
        d_retracted = self._decision(topic_id=topic, statement="Old idea")
        self.store.delete_decision(d_retracted)  # soft-delete via status=retracted
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="cascade",
        )
        self.assertEqual(scope["count"], 0)

    def test_commented_self_never_in_affected(self) -> None:
        topic = self._topic("Solo")
        d_main = self._decision(topic_id=topic, statement="Lone")
        scope = cascade.compute_affected_scope(
            self.store,
            project_id=self.project_id,
            commented_decision_ids=[d_main],
            scope_mode="cascade",
        )
        self.assertNotIn(d_main, scope["decision_ids"])


if __name__ == "__main__":
    unittest.main()
