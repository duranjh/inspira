"""Tests for the homepage AI suggestions feature.

Five tests:
1. user with 0 or 1 projects → empty array returned, no LLM call made.
2. user with 2+ projects → 3 suggestions returned from mocked adapter.
3. suggestions are non-empty strings, each under 150 chars.
4. user_id isolation — cross-user project data never leaks.
5. locale hint flows through — mock adapter inspects the locale arg.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]

from planning_studio_service.homepage import generate_suggestions


class TestHomepageSuggestionsThreshold(unittest.TestCase):
    """Users with 0 or 1 projects get an empty list; no LLM is called."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.tmp = make_test_app()
        signup_and_login(self.client)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _user_id(self) -> str:
        me = self.client.get("/api/auth/me")
        me.raise_for_status()
        return me.json()["user_id"]

    def test_zero_projects_no_call(self) -> None:
        adapter = MagicMock()
        result = generate_suggestions(self.store, user_id=self._user_id(), adapter=adapter)
        self.assertEqual(result, [])
        adapter.generate_homepage_suggestions.assert_not_called()

    def test_one_project_no_call(self) -> None:
        self.client.post("/api/v2/projects", json={"title": "Solo project"})
        adapter = MagicMock()
        result = generate_suggestions(self.store, user_id=self._user_id(), adapter=adapter)
        self.assertEqual(result, [])
        adapter.generate_homepage_suggestions.assert_not_called()


class TestHomepageSuggestionsWithProjects(unittest.TestCase):
    """Users with 2+ projects get 3 suggestions from the mocked adapter."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.tmp = make_test_app()
        signup_and_login(self.client)
        self.client.post("/api/v2/projects", json={"title": "Design Retreat"})
        self.client.post("/api/v2/projects", json={"title": "Clothing Brand Pitch"})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _user_id(self) -> str:
        me = self.client.get("/api/auth/me")
        me.raise_for_status()
        return me.json()["user_id"]

    def test_returns_three_suggestions(self) -> None:
        fake_suggestions = [
            "Plan a retreat for the product team in the mountains",
            "Write the pitch deck for your sustainable clothing line",
            "Design the onboarding flow for your first digital product",
        ]
        adapter = MagicMock()
        adapter.generate_homepage_suggestions.return_value = fake_suggestions

        result = generate_suggestions(self.store, user_id=self._user_id(), adapter=adapter)

        self.assertEqual(result, fake_suggestions)
        adapter.generate_homepage_suggestions.assert_called_once()

    def test_suggestions_are_short_strings(self) -> None:
        """Each suggestion must be a non-empty string under 150 chars."""
        fake_suggestions = [
            "Plan a design retreat for the team",
            "Write a pitch deck for your clothing brand",
            "Map out the arc of your next podcast season",
        ]
        adapter = MagicMock()
        adapter.generate_homepage_suggestions.return_value = fake_suggestions

        result = generate_suggestions(self.store, user_id=self._user_id(), adapter=adapter)

        for suggestion in result:
            self.assertIsInstance(suggestion, str)
            self.assertGreater(len(suggestion.strip()), 0)
            self.assertLessEqual(len(suggestion), 150)

    def test_adapter_failure_returns_empty(self) -> None:
        """LLM failure must not propagate — returns empty list silently."""
        adapter = MagicMock()
        adapter.generate_homepage_suggestions.side_effect = RuntimeError("LLM unavailable")

        result = generate_suggestions(self.store, user_id=self._user_id(), adapter=adapter)

        self.assertEqual(result, [])


class TestHomepageUserIsolation(unittest.TestCase):
    """Cross-user project data must never leak between users."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.tmp = make_test_app()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_user_a_data_not_visible_to_user_b(self) -> None:
        # Sign up user A and create projects.
        signup_and_login(self.client, email="a@example.com", password="password123")
        self.client.post("/api/v2/projects", json={"title": "User A Secret Project"})
        self.client.post("/api/v2/projects", json={"title": "User A Second Project"})
        user_a_id = self.client.get("/api/auth/me").json()["user_id"]

        # Sign up user B — has no projects.
        self.client.post("/api/auth/logout", json={})
        signup_and_login(self.client, email="b@example.com", password="password456")
        user_b_id = self.client.get("/api/auth/me").json()["user_id"]

        adapter = MagicMock()
        # User B has 0 projects → empty, no LLM call.
        result_b = generate_suggestions(self.store, user_id=user_b_id, adapter=adapter)
        self.assertEqual(result_b, [])
        adapter.generate_homepage_suggestions.assert_not_called()

        # User A's projects must still be scoped to user_a_id only.
        adapter2 = MagicMock()
        adapter2.generate_homepage_suggestions.return_value = ["idea1", "idea2", "idea3"]

        # The context dict passed to the adapter must only contain user A's projects.
        captured_context: dict = {}

        def _capture(ctx: dict, loc: str | None) -> list[str]:
            captured_context.update(ctx)
            return ["idea1", "idea2", "idea3"]

        adapter2.generate_homepage_suggestions.side_effect = _capture

        generate_suggestions(self.store, user_id=user_a_id, adapter=adapter2, locale=None)
        project_titles = [p["title"] for p in captured_context.get("projects", [])]
        self.assertIn("User A Secret Project", project_titles)
        # User B's ID would find no projects — double-check no cross-contamination.
        for title in project_titles:
            self.assertNotIn("User B", title)


class TestHomepageLocaleFlowthrough(unittest.TestCase):
    """locale arg must be forwarded to the adapter unchanged."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.tmp = make_test_app()
        signup_and_login(self.client)
        self.client.post("/api/v2/projects", json={"title": "Proyecto uno"})
        self.client.post("/api/v2/projects", json={"title": "Proyecto dos"})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _user_id(self) -> str:
        return self.client.get("/api/auth/me").json()["user_id"]

    def test_locale_forwarded(self) -> None:
        received_locale: list = []

        def _capture(ctx: dict, loc: str | None) -> list[str]:
            received_locale.append(loc)
            return ["sugerencia uno", "sugerencia dos", "sugerencia tres"]

        adapter = MagicMock()
        adapter.generate_homepage_suggestions.side_effect = _capture

        generate_suggestions(
            self.store, user_id=self._user_id(), adapter=adapter, locale="es"
        )

        self.assertEqual(len(received_locale), 1)
        self.assertEqual(received_locale[0], "es")

    def test_none_locale_forwarded(self) -> None:
        received_locale: list = []

        def _capture(ctx: dict, loc: str | None) -> list[str]:
            received_locale.append(loc)
            return ["idea one", "idea two", "idea three"]

        adapter = MagicMock()
        adapter.generate_homepage_suggestions.side_effect = _capture

        generate_suggestions(
            self.store, user_id=self._user_id(), adapter=adapter, locale=None
        )

        self.assertEqual(received_locale[0], None)


if __name__ == "__main__":
    unittest.main()
