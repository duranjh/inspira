"""Endpoint integration tests for the Document API (#094 / Item 3 redesign).

Covers the 4 new handlers + 1 BackgroundTask landed in part 3 (commits
3a/3b/3c). Mirrors ``test_business_plan_endpoints.py`` (sync BP) and
``test_next_steps_endpoints.py`` (async + BG-task pattern).

Specifically:

- Plan gate: Free → 402 on POST/PATCH; GET works for everyone past
  ownership (404 if no completed doc yet).
- Domain mapping: 422 when project.metadata.domain is missing /
  unmapped (career, personal in v1) / unknown.
- Strict cap: 429 blocks ANY
  POST when at cap, regenerates included.
- Cap counting: increment ONLY on first generation of a NEW
  ``(project_id, doc_type)`` pair; regenerates of an existing
  completed doc don't count. Failed generations don't increment.
- In-flight idempotency: a second POST while a row is already
  in_progress returns the existing document_id + already_in_flight=
  True without scheduling a duplicate BackgroundTask.
- BackgroundTask happy-path: POST → 202 → BG runs synchronously under
  TestClient → row flips to completed with content + token estimate.
- BackgroundTask failure: adapter raise → row flips to failed with
  ``error_message``; cap NOT incremented.
- PATCH inline edit: title-only / prose-only / both / unknown
  section_id / empty body. No LLM, no cap, no advisory lock.
- Cross-user / cross-project isolation: 404 on all 4 endpoints.
- Sanity: tier_usage is NOT touched by Document (separate counter).
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from planning_studio_service.agents.tiers import (
    BUSINESS_PLAN_CAPS_BY_PLAN,
    ModelTier,
)

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _flip_to_plan(store: Any, user_id: str, plan_slug: str) -> None:
    """Stamp the user as Pro/Frontier in subscriptions."""
    store.upsert_subscription(
        user_id=user_id, plan=plan_slug, status="active",
    )


def _create_project_with_topics(
    store: Any,
    user_id: str,
    project_id: str,
    *,
    domain: str = "business_plan",
) -> None:
    """Build a minimum project + 3 topics + 1 decision + project domain.

    The project's ``metadata_json.domain`` is set so the document
    endpoints can derive a doc_type via ``DOMAIN_TO_DOC_TYPE``.
    """
    store.ensure_project(
        project_id=project_id, user_id=user_id, title="Test Project",
    )
    store.set_project_domain(project_id=project_id, domain=domain)
    t1 = store.create_topic(
        project_id=project_id, user_id=user_id,
        title="Audience", icon="heart",
    )
    store.create_topic(
        project_id=project_id, user_id=user_id,
        title="Channels", icon="megaphone",
    )
    store.create_topic(
        project_id=project_id, user_id=user_id,
        title="Budget", icon="banknote",
    )
    # Plant one decision so the BG task's project-state load surfaces
    # something to fence into the prompt — the adapter is mocked so
    # the actual content doesn't matter, but we want exercise the
    # decisions-by-topic build path.
    topic_id = (
        t1["topic_id"] if isinstance(t1, dict) and "topic_id" in t1 else None
    )
    if topic_id:
        store.create_decision(
            topic_id=topic_id,
            project_id=project_id,
            statement="Target millennials in urban tech hubs.",
            proposed_by="test",
            user_id=user_id,
        )


def _fake_document_response(doc_type: str = "business_plan") -> dict[str, Any]:
    """Canonical sanitized doc-type response (matches sanitizer output).

    The shape mirrors what ``adapter.business_plan(...)`` and friends
    return after the per-doc-type sanitizer runs in part 2c.
    """
    return {
        "doc_type": doc_type,
        "sections": [
            {
                "section_id": "executive-summary",
                "title": "Executive Summary",
                "prose_markdown": "We are the X for Y. Mocked.",
                "key_points": ["A", "B", "C"],
                "cited_topics": ["Audience"],
            },
            {
                "section_id": "purpose",
                "title": "Mission",
                "prose_markdown": "To bring clarity to ambitious thinking.",
                "key_points": ["P1", "P2"],
                "cited_topics": [],
            },
        ],
    }


def _adapter_method(adapter: Any, doc_type: str) -> Any:
    """Pick the bound MagicMock attribute for a given doc_type."""
    return {
        "business_plan": adapter.business_plan,
        "prd": adapter.prd,
        "story_outline": adapter.story_outline,
        "event_plan": adapter.event_plan,
        "marketing_plan": adapter.marketing_plan,
        "research_proposal": adapter.research_proposal,
        "course_outline": adapter.course_outline,
    }[doc_type]


# ---------------------------------------------------------------------------
# DocumentGetTests — GET /document and GET /document/{document_id}
# ---------------------------------------------------------------------------
class DocumentGetTests(unittest.TestCase):
    """GET endpoints for tab open + FE poller."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="get@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-get-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)
        _flip_to_plan(self.store, self.user_id, "pro")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_latest_no_completed_returns_404(self) -> None:
        """No completed document yet → 404 with doc_type label."""
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}/document",
        )
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["detail"]["error"], "document_not_found")
        self.assertEqual(payload["detail"]["doc_type"], "business_plan")

    def test_get_latest_returns_completed_document(self) -> None:
        """A completed document seeded in the store surfaces on tab open."""
        doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="business_plan", plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=doc_id,
            content_json=json.dumps(_fake_document_response("business_plan")),
            output_tokens_estimate=200,
        )
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}/document",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["document_id"], doc_id)
        self.assertEqual(payload["doc_type"], "business_plan")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["content"]["doc_type"], "business_plan")
        self.assertEqual(len(payload["content"]["sections"]), 2)

    def test_get_by_id_cross_user_returns_404(self) -> None:
        """Another user's session sees document_not_found."""
        doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="business_plan", plan_tier="pro", model_id="gpt-5.5",
        )
        client2, _store2, _adapter2, tmp2 = make_test_app()
        signup_and_login(client2, email="other@example.com")
        try:
            response = client2.get(
                f"/api/v2/projects/{self.project_id}/document/{doc_id}",
            )
            self.assertEqual(response.status_code, 404)
        finally:
            tmp2.cleanup()

    def test_get_latest_with_explicit_doc_type_query(self) -> None:
        """?doc_type=prd overrides the domain-derived doc_type."""
        # Seed a PRD doc, but the project's domain is business_plan.
        # Without the query param, GET /document derives business_plan
        # → 404. With ?doc_type=prd, it finds the seeded row.
        doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="prd", plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=doc_id,
            content_json=json.dumps(_fake_document_response("prd")),
            output_tokens_estimate=120,
        )
        response = self.client.get(
            f"/api/v2/projects/{self.project_id}/document?doc_type=prd",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["doc_type"], "prd")


# ---------------------------------------------------------------------------
# DocumentPostGatingTests — auth + plan gate on POST /document/generate
# ---------------------------------------------------------------------------
class DocumentPostGatingTests(unittest.TestCase):
    """Plan gate enforcement on POST /document/generate."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="g@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-gate-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unauthenticated_post_returns_401_or_403(self) -> None:
        """No session cookie → 401 (or 403 — depends on auth middleware)."""
        # Build a fresh client with no signup so there's no session.
        from fastapi.testclient import TestClient

        # Create a brand-new app + client with no signed-in user.
        client2, _store2, _adapter2, tmp2 = make_test_app()
        try:
            response = client2.post(
                f"/api/v2/projects/{self.project_id}/document/generate",
            )
            self.assertIn(response.status_code, (401, 403, 404))
        finally:
            tmp2.cleanup()
        del TestClient  # quiet linter

    def test_free_user_post_returns_402(self) -> None:
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 402)
        payload = response.json()
        self.assertEqual(payload["detail"]["error"], "plan_required")
        self.assertEqual(payload["detail"]["feature"], "document")
        self.assertEqual(payload["detail"]["min_plan"], "pro")
        self.assertFalse(self.adapter.business_plan.called)

    def test_pro_user_can_post(self) -> None:
        _flip_to_plan(self.store, self.user_id, "pro")
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload["document_id"].startswith("doc-"))
        self.assertEqual(payload["status"], "in_progress")

    def test_frontier_user_can_post(self) -> None:
        _flip_to_plan(self.store, self.user_id, "team")
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)


# ---------------------------------------------------------------------------
# DocumentDomainMappingTests — 422 on unmapped domains
# ---------------------------------------------------------------------------
class DocumentDomainMappingTests(unittest.TestCase):
    """Domain → doc_type derivation with 422 on unmapped values."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="d@example.com")
        self.user_id = self.signup["user_id"]
        _flip_to_plan(self.store, self.user_id, "pro")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_career_domain_returns_422(self) -> None:
        """career is intentionally unmapped in v1."""
        proj_id = "proj-career"
        _create_project_with_topics(
            self.store, self.user_id, proj_id, domain="career",
        )
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{proj_id}/document/generate",
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["detail"]["error"], "domain_not_supported")
        self.assertEqual(payload["detail"]["domain"], "career")

    def test_personal_domain_returns_422(self) -> None:
        proj_id = "proj-personal"
        _create_project_with_topics(
            self.store, self.user_id, proj_id, domain="personal",
        )
        response = self.client.post(
            f"/api/v2/projects/{proj_id}/document/generate",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["error"], "domain_not_supported",
        )

    def test_software_feature_domain_maps_to_prd(self) -> None:
        """software_feature → prd. Adapter dispatched accordingly."""
        proj_id = "proj-prd"
        _create_project_with_topics(
            self.store, self.user_id, proj_id, domain="software_feature",
        )
        self.adapter.prd.return_value = _fake_document_response("prd")
        response = self.client.post(
            f"/api/v2/projects/{proj_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        # BG task ran synchronously under TestClient. The PRD adapter
        # method should have been called, NOT business_plan.
        self.assertTrue(self.adapter.prd.called)
        self.assertFalse(self.adapter.business_plan.called)

    def test_doc_type_override_takes_precedence_over_domain(self) -> None:
        """#094 follow-up: FE picker can override the auto-derived doc_type
        before generating, in case the kickoff inferred the wrong domain.
        Project domain is software_product (would map to business_plan)
        but the override forces course_outline; the course adapter method
        runs, business_plan does not."""
        proj_id = "proj-override"
        _create_project_with_topics(
            self.store, self.user_id, proj_id, domain="software_product",
        )
        self.adapter.course_outline.return_value = _fake_document_response(
            "course_outline",
        )
        response = self.client.post(
            f"/api/v2/projects/{proj_id}/document/generate",
            json={"doc_type": "course_outline"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(self.adapter.course_outline.called)
        self.assertFalse(self.adapter.business_plan.called)
        # Persisted document row reflects the override doc_type.
        document_id = response.json()["document_id"]
        doc = self.store.get_document(document_id=document_id)
        self.assertEqual(doc["doc_type"], "course_outline")

    def test_invalid_doc_type_override_returns_422(self) -> None:
        """A bogus doc_type in the body fails the VALID_DOC_TYPES
        allowlist check and surfaces 422 invalid_doc_type."""
        proj_id = "proj-invalid-override"
        _create_project_with_topics(
            self.store, self.user_id, proj_id, domain="software_product",
        )
        response = self.client.post(
            f"/api/v2/projects/{proj_id}/document/generate",
            json={"doc_type": "not_a_real_type"},
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["detail"]["error"], "invalid_doc_type")
        self.assertEqual(payload["detail"]["doc_type"], "not_a_real_type")
        # No adapter method should have been called.
        self.assertFalse(self.adapter.business_plan.called)
        self.assertFalse(self.adapter.course_outline.called)

    def test_doc_type_override_unblocks_unmapped_domain(self) -> None:
        """Override is the safety valve for career/personal projects:
        domain unmapped → without override 422, with override 202."""
        proj_id = "proj-career-override"
        _create_project_with_topics(
            self.store, self.user_id, proj_id, domain="career",
        )
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{proj_id}/document/generate",
            json={"doc_type": "business_plan"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(self.adapter.business_plan.called)


# ---------------------------------------------------------------------------
# DocumentCapTests — strict cap + Option C increment semantics
# ---------------------------------------------------------------------------
class DocumentCapTests(unittest.TestCase):
    """business_plan_usage strict cap + Option C increment counting."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="cap@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-cap-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)
        self.adapter.business_plan.return_value = _fake_document_response()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _generate(self) -> Any:
        return self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )

    def test_pro_first_generation_increments_to_one(self) -> None:
        _flip_to_plan(self.store, self.user_id, "pro")
        response = self._generate()
        self.assertEqual(response.status_code, 202)
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 1)

    def test_pro_at_cap_post_returns_429(self) -> None:
        """Pro at 1/1 → strict block on any new POST."""
        _flip_to_plan(self.store, self.user_id, "pro")
        self.store.increment_business_plan_usage(user_id=self.user_id)
        response = self._generate()
        self.assertEqual(response.status_code, 429)
        payload = response.json()
        self.assertEqual(
            payload["detail"]["error"], "document_limit_reached",
        )
        self.assertEqual(payload["detail"]["doc_type"], "business_plan")
        self.assertEqual(payload["detail"]["cap"], 1)

    def test_pro_regenerate_blocked_at_cap(self) -> None:
        """Pro regenerate of an existing completed doc is blocked at cap."""
        _flip_to_plan(self.store, self.user_id, "pro")
        # Seed a completed doc + increment usage to 1/1.
        doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="business_plan", plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=doc_id,
            content_json=json.dumps(_fake_document_response()),
            output_tokens_estimate=100,
        )
        self.store.increment_business_plan_usage(user_id=self.user_id)
        # Now any POST 429s — even though there's no in-flight row.
        response = self._generate()
        self.assertEqual(response.status_code, 429)

    def test_frontier_at_99_succeeds_increments_to_100(self) -> None:
        _flip_to_plan(self.store, self.user_id, "team")
        for _ in range(BUSINESS_PLAN_CAPS_BY_PLAN["team"] - 1):
            self.store.increment_business_plan_usage(user_id=self.user_id)
        response = self._generate()
        self.assertEqual(response.status_code, 202)
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 100)

    def test_frontier_at_100_returns_429(self) -> None:
        _flip_to_plan(self.store, self.user_id, "team")
        for _ in range(BUSINESS_PLAN_CAPS_BY_PLAN["team"]):
            self.store.increment_business_plan_usage(user_id=self.user_id)
        response = self._generate()
        self.assertEqual(response.status_code, 429)

    def test_failed_adapter_does_not_increment(self) -> None:
        """Adapter raise → status=failed; cap NOT incremented."""
        _flip_to_plan(self.store, self.user_id, "pro")
        self.adapter.business_plan.side_effect = RuntimeError("model said no")
        response = self._generate()
        self.assertEqual(response.status_code, 202)
        # BG task ran sync; row is now failed.
        doc_id = response.json()["document_id"]
        doc = self.store.get_document(document_id=doc_id)
        assert doc is not None
        self.assertEqual(doc["status"], "failed")
        self.assertIn("sanitizer_failed", doc["error_message"] or "")
        # Cap NOT incremented.
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 0)

    def test_frontier_regenerate_does_not_increment(self) -> None:
        """Frontier with headroom: regenerate of completed doc → no increment."""
        _flip_to_plan(self.store, self.user_id, "team")
        # Seed a completed doc (no usage bump — fixture, not real call).
        doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="business_plan", plan_tier="team", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=doc_id,
            content_json=json.dumps(_fake_document_response()),
            output_tokens_estimate=100,
        )
        self.store.increment_business_plan_usage(user_id=self.user_id)  # = 1
        # Now POST again — should run (headroom) but NOT increment (regenerate).
        response = self._generate()
        self.assertEqual(response.status_code, 202)
        usage = self.store.get_business_plan_usage(user_id=self.user_id)
        self.assertEqual(usage["plans_used_this_month"], 1)

    def test_document_does_not_count_against_tier_usage(self) -> None:
        """Sanity: business_plan_usage is separate from tier_usage (#080)."""
        _flip_to_plan(self.store, self.user_id, "team")
        before = self.store.get_tier_usage(
            user_id=self.user_id, tier=ModelTier.FRONTIER.value,
        )
        response = self._generate()
        self.assertEqual(response.status_code, 202)
        after = self.store.get_tier_usage(
            user_id=self.user_id, tier=ModelTier.FRONTIER.value,
        )
        self.assertEqual(
            int(after.get("output_tokens_used", 0)),
            int(before.get("output_tokens_used", 0)),
        )


# ---------------------------------------------------------------------------
# DocumentInFlightIdempotencyTests — duplicate POST returns same id
# ---------------------------------------------------------------------------
class DocumentInFlightIdempotencyTests(unittest.TestCase):
    """Concurrent POSTs resolve to the same document_id."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="idem@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-idem-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)
        _flip_to_plan(self.store, self.user_id, "pro")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_second_post_returns_existing_in_flight_document_id(self) -> None:
        """Two clicks → second POST returns the first's document_id."""
        # Insert an in_progress row directly so the second POST sees
        # it. (A first POST under TestClient runs the BG task sync,
        # which would flip the row to completed before the second POST
        # runs — making this test a no-op. So we simulate with a direct
        # store insert that matches what the endpoint would create.)
        existing_id = self.store.create_document_in_progress(
            project_id=self.project_id,
            user_id=self.user_id,
            doc_type="business_plan",
            plan_tier="pro",
            model_id="gpt-5.5",
        )
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["document_id"], existing_id)
        self.assertTrue(payload.get("already_in_flight"))
        # Adapter must NOT have been scheduled for a duplicate run.
        self.assertFalse(self.adapter.business_plan.called)

    def test_in_flight_for_different_doctype_does_not_block(self) -> None:
        """An in-flight PRD doesn't block a business_plan POST."""
        # PRD in-flight on the same project (different doc_type).
        self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="prd", plan_tier="pro", model_id="gpt-5.5",
        )
        # Project domain is business_plan, so POST → business_plan adapter.
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        # No idempotency hit — the in-flight is for a different doc_type.
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertFalse(payload.get("already_in_flight"))


# ---------------------------------------------------------------------------
# DocumentBackgroundTaskTests — end-to-end async happy + failure paths
# ---------------------------------------------------------------------------
class DocumentBackgroundTaskTests(unittest.TestCase):
    """End-to-end: 202 → BG runs → row flips to completed/failed."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="bg@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-bg-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)
        _flip_to_plan(self.store, self.user_id, "pro")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_completed_document_after_bg_runs(self) -> None:
        """TestClient runs BG synchronously; row should be completed."""
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        doc_id = response.json()["document_id"]

        doc = self.store.get_document(document_id=doc_id)
        assert doc is not None
        self.assertEqual(doc["status"], "completed")
        self.assertIsNotNone(doc["content_json"])
        content = json.loads(doc["content_json"])
        self.assertEqual(content["doc_type"], "business_plan")
        self.assertEqual(len(content["sections"]), 2)
        self.assertEqual(doc["plan_tier"], "pro")
        self.assertEqual(doc["model_id"], "gpt-5.5")
        self.assertIsNotNone(doc["completed_at"])
        self.assertGreater(doc["output_tokens_estimate"] or 0, 0)

    def test_adapter_runtime_error_marks_failed(self) -> None:
        """Adapter RuntimeError → status=failed/sanitizer_failed."""
        self.adapter.business_plan.side_effect = RuntimeError("sanitizer raised")
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        doc_id = response.json()["document_id"]
        doc = self.store.get_document(document_id=doc_id)
        assert doc is not None
        self.assertEqual(doc["status"], "failed")
        # Per security review's err-message-leak scope: type-name only,
        # no exception-message leak in the persisted error_message.
        self.assertIn("sanitizer_failed", doc["error_message"] or "")
        self.assertNotIn("sanitizer raised", doc["error_message"] or "")

    def test_adapter_generic_exception_marks_failed(self) -> None:
        """Non-RuntimeError adapter exception → status=failed/adapter_failed."""
        self.adapter.business_plan.side_effect = ValueError("bad arg")
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        doc_id = response.json()["document_id"]
        doc = self.store.get_document(document_id=doc_id)
        assert doc is not None
        self.assertEqual(doc["status"], "failed")
        self.assertIn("adapter_failed", doc["error_message"] or "")

    def test_empty_project_marks_failed(self) -> None:
        """Project with no topics → BG marks failed/empty_project_no_topics."""
        empty_pid = "proj-empty-bg"
        self.store.ensure_project(
            project_id=empty_pid, user_id=self.user_id, title="Empty",
        )
        self.store.set_project_domain(
            project_id=empty_pid, domain="business_plan",
        )
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{empty_pid}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        doc_id = response.json()["document_id"]
        doc = self.store.get_document(document_id=doc_id)
        assert doc is not None
        self.assertEqual(doc["status"], "failed")
        self.assertEqual(doc["error_message"], "empty_project_no_topics")
        # Adapter NOT called — BG bailed before the LLM step.
        self.assertFalse(self.adapter.business_plan.called)

    def test_adapter_apitimeout_marks_failed_with_typename_only(self) -> None:
        """#096 regression: simulated APITimeoutError on the adapter call
        must surface as failed with a type-name-only error_message
        (no message-text leak per #094 part-3 security review). Mock
        raises immediately so the failure plumbing is exercised without
        actually waiting 120s. Pre-#096 the same failure path took 6+
        minutes due to stacked SDK + transient retries; this test
        guards the failure shape, not the wall-clock budget (covered
        by the openai_adapter unit tests)."""
        class FakeAPITimeoutError(Exception):
            pass
        FakeAPITimeoutError.__name__ = "APITimeoutError"
        self.adapter.business_plan.side_effect = FakeAPITimeoutError(
            "simulated upstream timeout that should NOT leak into error_message"
        )
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        doc_id = response.json()["document_id"]
        doc = self.store.get_document(document_id=doc_id)
        assert doc is not None
        self.assertEqual(doc["status"], "failed")
        # Type-name only; exception message must NOT leak.
        self.assertIn("adapter_failed", doc["error_message"] or "")
        self.assertIn("APITimeoutError", doc["error_message"] or "")
        self.assertNotIn("simulated", doc["error_message"] or "")

    def test_documents_invoke_adapter_exactly_once(self) -> None:
        """#096 regression: documents force max_retries=0 at the
        _generate_document call site to _call_with_toolcall_retry, so
        a happy-path generate invokes the adapter method exactly once.
        Pins the no-retry invariant against future config-flip
        regressions."""
        self.adapter.business_plan.return_value = _fake_document_response()
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/document/generate",
        )
        self.assertEqual(response.status_code, 202)
        # The mocked adapter sits AT the dispatch boundary
        # (_generate_document is an internal method), so call_count == 1
        # means the BG task path didn't introduce any outer retry layer.
        self.assertEqual(self.adapter.business_plan.call_count, 1)


# ---------------------------------------------------------------------------
# DocumentPatchTests — user inline edits
# ---------------------------------------------------------------------------
class DocumentPatchTests(unittest.TestCase):
    """PATCH /document/{document_id}/section/{section_id}."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="edit@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-edit-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)
        _flip_to_plan(self.store, self.user_id, "pro")
        # Seed a completed document with two sections.
        self.doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="business_plan", plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=self.doc_id,
            content_json=json.dumps(_fake_document_response()),
            output_tokens_estimate=200,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _patch(self, section_id: str, **body: Any) -> Any:
        return self.client.patch(
            f"/api/v2/projects/{self.project_id}/document/{self.doc_id}"
            f"/section/{section_id}",
            json=body,
        )

    def test_patch_title_only(self) -> None:
        response = self._patch(
            "executive-summary", title="New Exec Title",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        target = next(
            s for s in payload["content"]["sections"] if s["section_id"] == "executive-summary"
        )
        self.assertEqual(target["title"], "New Exec Title")
        # prose_markdown unchanged.
        self.assertEqual(
            target["prose_markdown"], "We are the X for Y. Mocked.",
        )

    def test_patch_prose_only(self) -> None:
        response = self._patch(
            "executive-summary",
            prose_markdown="My better summary text.",
        )
        self.assertEqual(response.status_code, 200)
        target = next(
            s for s in response.json()["content"]["sections"]
            if s["section_id"] == "executive-summary"
        )
        self.assertEqual(target["prose_markdown"], "My better summary text.")
        self.assertEqual(target["title"], "Executive Summary")  # unchanged

    def test_patch_both_fields(self) -> None:
        response = self._patch(
            "purpose",
            title="Why We Exist",
            prose_markdown="To deliver clarity at scale.",
        )
        self.assertEqual(response.status_code, 200)
        target = next(
            s for s in response.json()["content"]["sections"]
            if s["section_id"] == "purpose"
        )
        self.assertEqual(target["title"], "Why We Exist")
        self.assertEqual(target["prose_markdown"], "To deliver clarity at scale.")

    def test_patch_unknown_section_returns_404(self) -> None:
        response = self._patch(
            "no-such-section", prose_markdown="ignored",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["error"], "section_not_found",
        )

    def test_patch_empty_body_returns_422(self) -> None:
        """Pydantic accepts {} but the handler enforces "at least one"."""
        response = self._patch("executive-summary")  # empty body
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["error"], "empty_patch",
        )


# ---------------------------------------------------------------------------
# DocumentOwnershipTests — cross-user isolation
# ---------------------------------------------------------------------------
class DocumentOwnershipTests(unittest.TestCase):
    """Cross-user / cross-project access returns 404."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(self.client, email="own@example.com")
        self.user_id = self.signup["user_id"]
        self.project_id = "proj-own-test"
        _create_project_with_topics(self.store, self.user_id, self.project_id)
        _flip_to_plan(self.store, self.user_id, "pro")
        # Seed a doc.
        self.doc_id = self.store.create_document_in_progress(
            project_id=self.project_id, user_id=self.user_id,
            doc_type="business_plan", plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=self.doc_id,
            content_json=json.dumps(_fake_document_response()),
            output_tokens_estimate=100,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_other_user_patch_returns_404(self) -> None:
        client2, _store2, _adapter2, tmp2 = make_test_app()
        signup_and_login(client2, email="ext@example.com")
        try:
            response = client2.patch(
                f"/api/v2/projects/{self.project_id}/document/{self.doc_id}"
                f"/section/executive-summary",
                json={"prose_markdown": "stolen"},
            )
            self.assertEqual(response.status_code, 404)
        finally:
            tmp2.cleanup()
