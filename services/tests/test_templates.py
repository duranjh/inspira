"""HTTP-layer tests for the starter-template endpoints.

Covers:
- ``GET /api/v2/templates`` returns the ten built-in templates in the
  summary shape the frontend gallery consumes (slug + title + counts,
  without the full topics/relationships payload).
- ``POST /api/v2/projects/from-template`` seeds topics + relationships
  into a real v2 project and returns the kickoff envelope shape so the
  frontend can open the canvas without a second round trip.
- The unit-level invariants that protect the ten templates from
  editorial drift (unique slugs, no emojis, every relationship endpoint
  resolves to a real topic title).
- Unknown slugs surface as a clean 404 (not a 500 or 422).

These tests use the shared ``make_test_app`` helper so they run against
a real FastAPI TestClient + an isolated SQLite store — no mocking of the
HTTP boundary — while skipping OpenAI entirely (templates are static).
"""
from __future__ import annotations

import unittest
from unittest import mock

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]

from planning_studio_service.templates import (
    DOC_TYPE_ORPHAN_SLUGS,
    TEMPLATES,
    Template,
    get_template,
)


# Ten is the product contract — if someone adds an 11th template without
# reading the feature-ideas memo, this test asks them to confirm the
# editorial rule ("Ten, curated, each with a hand-written one-sentence
# pitch. Not thirty.") still holds.
_EXPECTED_TEMPLATE_COUNT = 10

# Five visible after filtering out the doc-type-orphan slugs (the
# orphans' document tabs dead-end in production until each gets a
# proper domain mapping or doc-type generator). The visible set is
# what the kickoff picker shows.
_EXPECTED_VISIBLE_COUNT = _EXPECTED_TEMPLATE_COUNT - len(DOC_TYPE_ORPHAN_SLUGS)


class TemplateDefinitionInvariants(unittest.TestCase):
    """Structural checks that run without the HTTP stack.

    Kept separate from the route tests because they surface regressions
    in the template content itself (a typo in a relationship endpoint, a
    duplicate slug, an emoji that snuck into a tagline) before any HTTP
    test would catch them.
    """

    def test_ten_templates_shipped(self) -> None:
        self.assertEqual(len(TEMPLATES), _EXPECTED_TEMPLATE_COUNT)

    def test_slugs_are_unique_and_url_safe(self) -> None:
        slugs = [t.slug for t in TEMPLATES]
        self.assertEqual(len(slugs), len(set(slugs)), "duplicate slugs")
        for slug in slugs:
            self.assertRegex(slug, r"^[a-z0-9][a-z0-9-]*$", slug)

    def test_each_template_has_five_to_eight_topics(self) -> None:
        # The product memo calls for 5-7 topics; we allow 8 because the
        # Business plan needs it (Financials + Milestones pair is load-
        # bearing). Anything outside 5-8 is a signal someone let a
        # template bloat into sales collateral.
        for t in TEMPLATES:
            self.assertGreaterEqual(len(t.topics), 5, t.slug)
            self.assertLessEqual(len(t.topics), 8, t.slug)

    def test_relationships_reference_real_topics(self) -> None:
        for t in TEMPLATES:
            titles = {topic.title for topic in t.topics}
            for rel in t.relationships:
                self.assertIn(rel.from_title, titles, f"{t.slug}: {rel}")
                self.assertIn(rel.to_title, titles, f"{t.slug}: {rel}")
                self.assertNotEqual(
                    rel.from_title, rel.to_title,
                    f"{t.slug}: self-loop {rel}",
                )

    def test_no_emojis_in_content(self) -> None:
        # Cheap heuristic: reject any code point above U+2700 (covers
        # dingbats + emoji block). Our editorial tone is "no emojis"
        # and we want the test to flag a slip before review.
        def has_emoji(text: str) -> bool:
            return any(ord(ch) >= 0x2700 for ch in text)

        for t in TEMPLATES:
            fields = [t.title, t.tagline, t.description]
            for topic in t.topics:
                fields.extend([topic.title, topic.icon, topic.why_this_topic])
            for rel in t.relationships:
                fields.extend([rel.from_title, rel.to_title, rel.label])
            for value in fields:
                self.assertFalse(has_emoji(value), f"{t.slug}: emoji in {value!r}")

    def test_get_template_round_trips(self) -> None:
        for t in TEMPLATES:
            resolved = get_template(t.slug)
            self.assertIsInstance(resolved, Template)
            assert resolved is not None  # for mypy readers
            self.assertEqual(resolved.slug, t.slug)

    def test_get_template_unknown_returns_none(self) -> None:
        self.assertIsNone(get_template("does-not-exist"))


class TemplateListEndpointTests(unittest.TestCase):
    """GET /api/v2/templates."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Signed-in or not, the list endpoint is identical — but signing
        # in matches how real browsers will hit it, and avoids the
        # system-user bootstrap path masking any session-cookie bug.
        signup_and_login(
            self.client, email="g@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_returns_visible_templates_only(self) -> None:
        response = self.client.get("/api/v2/templates")
        self.assertEqual(response.status_code, 200, response.text)
        templates = response.json().get("templates") or []
        self.assertEqual(len(templates), _EXPECTED_VISIBLE_COUNT)

        # YC v4 reframe: all 10 templates are currently hidden. The
        # endpoint still has its summary contract for any future
        # un-hidden slugs, but the response array is empty by design.
        # If this test's expected count drops below 0 someone removed a
        # template entirely — re-evaluate whether the source TEMPLATES
        # tuple still matches the orphan set.
        self.assertGreaterEqual(_EXPECTED_VISIBLE_COUNT, 0)

        # When templates ARE visible, validate the summary shape and
        # that no orphan slug leaked through. The branch is no-op while
        # all 10 are hidden but stays as a regression guard for when a
        # template gets re-enabled post-batch.
        for summary in templates:
            self.assertIn("slug", summary)
            self.assertIn("title", summary)
            self.assertIn("description", summary)
            self.assertIn("tagline", summary)
            self.assertIn("topic_count", summary)
            self.assertIn("relationship_count", summary)
            self.assertIn("domain_framing", summary)
            self.assertNotIn("topics", summary)
            self.assertNotIn("relationships", summary)

        visible_slugs = {t["slug"] for t in templates}
        self.assertTrue(
            visible_slugs.isdisjoint(DOC_TYPE_ORPHAN_SLUGS),
            f"orphan slug leaked into picker: "
            f"{visible_slugs & DOC_TYPE_ORPHAN_SLUGS}",
        )


class CreateProjectFromTemplateTests(unittest.TestCase):
    """POST /api/v2/projects/from-template."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="c@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creates_project_with_correct_topics_and_relationships(self) -> None:
        # YC v4 reframe: every shipping slug is in DOC_TYPE_ORPHAN_SLUGS,
        # so /from-template 404s every real slug. To still exercise the
        # CREATE endpoint's topic+relationship seeding behavior (and
        # protect the contract for any post-batch un-hidden slug), patch
        # the orphan set to an empty frozenset for the duration of this
        # test. The real handler imports the constant by name at request
        # time, so this patch lands on the live lookup.
        with mock.patch(
            "planning_studio_service.templates.DOC_TYPE_ORPHAN_SLUGS",
            frozenset(),
        ):
            response = self.client.post(
                "/api/v2/projects/from-template", json={"slug": "event"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()

        # Envelope shape mirrors the kickoff route (project + topics +
        # relationships) so the frontend can open the canvas directly.
        self.assertIn("project", payload)
        self.assertIn("topics", payload)
        self.assertIn("relationships", payload)
        self.assertIn("template", payload)

        # The Event template ships 7 topics and 7 relationships — if the
        # definitions change, this test needs to be updated deliberately.
        event = get_template("event")
        assert event is not None
        self.assertEqual(len(payload["topics"]), len(event.topics))
        self.assertEqual(
            len(payload["relationships"]), len(event.relationships),
        )

        persisted_titles = {t["title"] for t in payload["topics"]}
        self.assertEqual(
            persisted_titles, {topic.title for topic in event.topics},
        )

        # Every persisted relationship must resolve to real topic IDs
        # (the title-to-id mapping succeeded for every edge).
        topic_ids = {t["topic_id"] for t in payload["topics"]}
        for rel in payload["relationships"]:
            self.assertIn(rel["source_topic_id"], topic_ids)
            self.assertIn(rel["target_topic_id"], topic_ids)
            self.assertIsNotNone(rel["label"])

        # Title is seeded from the template; the user will see "Event"
        # as the top-bar chip until they rename.
        project = payload["project"]
        self.assertEqual(project["title"], event.title)

        # And the project landed in the user's project list — the next
        # GET /api/v2/projects returns it.
        listed = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["project_id"], project["project_id"])

    def test_rejects_unknown_slug_with_404(self) -> None:
        response = self.client.post(
            "/api/v2/projects/from-template",
            json={"slug": "alien-invasion-planner"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "template_not_found")
        # And no stray project was created.
        listed = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(listed, [])

    def test_rejects_orphan_slug_with_404(self) -> None:
        # Defense in depth: a stale frontend cache or direct API call
        # could still hit /from-template with a hidden orphan slug. The
        # response mirrors unknown-slug to avoid leaking that the
        # template exists but is gated.
        for orphan_slug in DOC_TYPE_ORPHAN_SLUGS:
            with self.subTest(slug=orphan_slug):
                response = self.client.post(
                    "/api/v2/projects/from-template",
                    json={"slug": orphan_slug},
                )
                self.assertEqual(
                    response.status_code, 404, response.text,
                )
                detail = response.json().get("detail") or {}
                self.assertEqual(detail.get("error"), "template_not_found")
        # And no stray projects were created.
        listed = self.client.get("/api/v2/projects").json()["projects"]
        self.assertEqual(listed, [])

    def test_rejects_empty_slug_with_400(self) -> None:
        response = self.client.post(
            "/api/v2/projects/from-template", json={"slug": ""},
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "slug_required")


if __name__ == "__main__":
    unittest.main()
