"""Tests for search.search_all — cross-project full-text search.

Covers:
  1. Matches across all four kinds (project, topic, decision, turn).
  2. Cross-user leakage protection (user B never sees user A's data).
  3. Case-insensitive matching.
  4. Empty query → empty result, no full-dump.
  5. Truncation flag when the hit count hits the limit.
  6. LIKE special-char escaping (``%``, ``_`` in the query are literal).

Each test spins up a real isolated SQLite store so we get actual SQL
behaviour — no mocking of the query layer.
"""
from __future__ import annotations

import os
import tempfile
import unittest

try:
    from planning_studio_service._env_bootstrap import ensure_loaded
    ensure_loaded()
except Exception:
    pass  # Bootstrap is best-effort in the test runner.

from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore
from planning_studio_service.search import search_all


def _make_store() -> tuple[PlanningStudioStore, tempfile.TemporaryDirectory]:  # type: ignore[type-arg]
    tmp = tempfile.TemporaryDirectory(
        prefix="inspira-search-test-", ignore_cleanup_errors=True,
    )
    os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = tmp.name
    store = PlanningStudioStore(load_config())
    return store, tmp


def _create_user(store: PlanningStudioStore, email: str) -> str:
    """Create a user and return their user_id."""
    user = store.create_user(email=email, password_hash=None)
    return str(user["user_id"])


def _create_project(store: PlanningStudioStore, user_id: str, title: str) -> str:
    proj = store.create_v2_project(user_id=user_id, title=title)
    return str(proj["project_id"])


def _create_topic(store: PlanningStudioStore, project_id: str, title: str) -> str:
    topic = store.create_topic(
        project_id=project_id,
        title=title,
        icon="circle",
        position_x=0.0,
        position_y=0.0,
        order_index=0,
        origin="user_manual",
    )
    return str(topic["topic_id"])


def _create_decision(
    store: PlanningStudioStore,
    topic_id: str,
    project_id: str,
    statement: str,
    rationale: str | None = None,
) -> str:
    decision = store.create_decision(
        topic_id=topic_id,
        project_id=project_id,
        statement=statement,
        rationale=rationale,
        status="confirmed",
        proposed_by="user",
    )
    return str(decision["decision_id"])


def _create_turn(
    store: PlanningStudioStore,
    topic_id: str,
    project_id: str,
    body: str,
    role: str = "user",
) -> str:
    turn = store.append_qna_turn(
        topic_id=topic_id,
        project_id=project_id,
        role=role,
        body=body,
        status="answered",
    )
    return str(turn["turn_id"])


class TestSearchAllKinds(unittest.TestCase):
    """Matches span all four kinds."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.user_id = _create_user(self.store, "alpha@example.com")
        proj_id = _create_project(self.store, self.user_id, "ProjectAlpha unique-marker")
        topic_id = _create_topic(self.store, proj_id, "TopicAlpha unique-marker")
        _create_decision(
            self.store, topic_id, proj_id,
            "DecisionAlpha unique-marker",
            rationale="RationaleAlpha unique-marker",
        )
        _create_turn(self.store, topic_id, proj_id, "TurnBodyAlpha unique-marker", role="user")
        _create_turn(self.store, topic_id, proj_id, "PlannerBodyAlpha unique-marker", role="planner")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_project_title_match(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="ProjectAlpha")
        kinds = {h.kind for h in results.hits}
        self.assertIn("project", kinds)

    def test_topic_title_match(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="TopicAlpha")
        kinds = {h.kind for h in results.hits}
        self.assertIn("topic", kinds)

    def test_decision_statement_match(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="DecisionAlpha")
        kinds = {h.kind for h in results.hits}
        self.assertIn("decision", kinds)

    def test_turn_body_match_user(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="TurnBodyAlpha")
        kinds = {h.kind for h in results.hits}
        self.assertIn("turn", kinds)

    def test_turn_body_match_planner(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="PlannerBodyAlpha")
        kinds = {h.kind for h in results.hits}
        self.assertIn("turn", kinds)

    def test_all_four_kinds_found_with_shared_token(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="unique-marker")
        kinds = {h.kind for h in results.hits}
        expected = {"project", "topic", "decision", "turn"}
        missing = expected - kinds
        self.assertTrue(kinds.issuperset(expected),
                        f"Missing kinds: {missing}; got {kinds}")


class TestCrossUserLeakage(unittest.TestCase):
    """User B must never see user A's data."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.user_a = _create_user(self.store, "userA@example.com")
        self.user_b = _create_user(self.store, "userB@example.com")
        proj_a = _create_project(self.store, self.user_a, "SecretAlpha project")
        topic_a = _create_topic(self.store, proj_a, "SecretAlpha topic")
        _create_decision(self.store, topic_a, proj_a, "SecretAlpha decision")
        _create_turn(self.store, topic_a, proj_a, "SecretAlpha turn")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_user_b_sees_nothing_from_user_a(self) -> None:
        results = search_all(self.store, user_id=self.user_b, query="SecretAlpha")
        self.assertEqual(results.hits, [], f"Leaked hits: {results.hits}")

    def test_user_a_sees_own_data(self) -> None:
        results = search_all(self.store, user_id=self.user_a, query="SecretAlpha")
        self.assertGreater(len(results.hits), 0)


class TestCaseInsensitive(unittest.TestCase):
    """Matching ignores case."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.user_id = _create_user(self.store, "case@example.com")
        proj_id = _create_project(self.store, self.user_id, "CamelCase Project")
        topic_id = _create_topic(self.store, proj_id, "CamelCase Topic")
        _create_decision(self.store, topic_id, proj_id, "CamelCase Decision")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_lowercase_query_matches_mixed_case_data(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="camelcase")
        self.assertGreater(len(results.hits), 0)

    def test_uppercase_query_matches_mixed_case_data(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="CAMELCASE")
        self.assertGreater(len(results.hits), 0)


class TestEmptyQuery(unittest.TestCase):
    """Empty or blank query returns zero hits without scanning the DB."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.user_id = _create_user(self.store, "empty@example.com")
        proj_id = _create_project(self.store, self.user_id, "Something interesting")
        _create_topic(self.store, proj_id, "A topic")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_string_returns_empty(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="")
        self.assertEqual(results.hits, [])
        self.assertFalse(results.truncated)

    def test_whitespace_only_returns_empty(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="   ")
        self.assertEqual(results.hits, [])
        self.assertFalse(results.truncated)


class TestTruncation(unittest.TestCase):
    """truncated=True when hits exceed the limit."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.user_id = _create_user(self.store, "trunc@example.com")
        # Create 6 projects all matching "haystack" — limit=5 should truncate.
        for i in range(6):
            _create_project(self.store, self.user_id, f"haystack project {i}")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_truncation_flag_set_when_over_limit(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="haystack", limit=5)
        self.assertTrue(results.truncated)
        self.assertEqual(len(results.hits), 5)

    def test_no_truncation_when_at_or_under_limit(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="haystack", limit=10)
        self.assertFalse(results.truncated)
        self.assertEqual(len(results.hits), 6)


class TestLikeSpecialChars(unittest.TestCase):
    """``%`` and ``_`` in the query match literally, not as SQL wildcards."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.user_id = _create_user(self.store, "special@example.com")
        proj_id = _create_project(self.store, self.user_id, "50%_off sale project")
        topic_id = _create_topic(self.store, proj_id, "regular topic")
        _create_decision(
            self.store, topic_id, proj_id,
            "50%_off is the discount applied at checkout",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_percent_underscore_match_literally(self) -> None:
        results = search_all(self.store, user_id=self.user_id, query="50%_off")
        # Should find the project title and the decision statement.
        matched_kinds = {h.kind for h in results.hits}
        self.assertIn("project", matched_kinds)
        self.assertIn("decision", matched_kinds)

    def test_wildcard_percent_does_not_dump_all_rows(self) -> None:
        # If ``%`` were treated as a SQL wildcard it would match EVERY row.
        # After escaping, the LIKE pattern is ``%\%%`` which only matches
        # rows containing a literal ``%`` character.  Our test setup has a
        # project titled "50%_off sale project" (contains ``%``) and a
        # topic titled "regular topic" (does not).  A wildcard ``%`` would
        # return BOTH; the escaped pattern should return only the project/decision.
        results_percent = search_all(self.store, user_id=self.user_id, query="%")
        # Verify none of the hits come from "regular topic" (no ``%`` in title).
        for hit in results_percent.hits:
            self.assertNotEqual(
                hit.snippet.strip(), "regular topic",
                f"Bare-% query dumped a row that doesn't contain '%': {hit}",
            )
        # Sanity: all returned snippets must actually contain a ``%`` character.
        for hit in results_percent.hits:
            clean = hit.snippet.replace("\u2026", "")
            self.assertIn("%", clean,
                          f"Hit snippet lacks literal '%%' but was returned: {hit}")
