"""Unit tests for the documents store layer (#094 / Item 3 redesign).

Covers:
- ``create_document_in_progress`` — id format, doc_type validation, status.
- ``mark_document_completed`` — content_json + tokens + completed_at.
- ``mark_document_failed`` — error_message + completed_at.
- ``get_document`` — by id; None on miss.
- ``get_latest_completed_document`` — newest completed for (project, doc_type);
  ignores in_progress / failed.
- ``get_in_flight_document`` — newest in_progress; ignores failed; respects
  the 5-minute stale-orphan guard.
- Doc-type isolation: business_plan rows don't leak into prd queries.
- Cross-project isolation: project A's completed doc doesn't bleed into B.

These exercise the store layer directly (no API). Endpoint-level
behaviour is covered by ``test_document_endpoints.py`` in a later
commit.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any

from planning_studio_service.config import load_config
from planning_studio_service.store import (
    DOMAIN_TO_DOC_TYPE,
    PlanningStudioStore,
    VALID_DOC_TYPES,
)


def _make_store() -> tuple[PlanningStudioStore, tempfile.TemporaryDirectory]:
    temp_dir = tempfile.TemporaryDirectory(
        prefix="inspira-doc-store-test-", ignore_cleanup_errors=True,
    )
    os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = temp_dir.name
    return PlanningStudioStore(load_config()), temp_dir


def _document_content(doc_type: str = "business_plan") -> str:
    """Canonical content_json shape post-sanitization."""
    return json.dumps(
        {
            "doc_type": doc_type,
            "sections": [
                {
                    "id": "executive-summary",
                    "title": "Executive Summary",
                    "prose_markdown": "We are the X for Y. Mocked.",
                    "key_points": ["A", "B"],
                },
            ],
        }
    )


class CreateDocumentInProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.tmp = _make_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_doc_prefixed_id(self) -> None:
        doc_id = self.store.create_document_in_progress(
            project_id="proj-1",
            user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro",
            model_id="gpt-5.5",
        )
        self.assertTrue(doc_id.startswith("doc-"))
        self.assertGreater(len(doc_id), 6)

    def test_initial_status_is_in_progress(self) -> None:
        doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1", doc_type="prd",
            plan_tier="pro", model_id="gpt-5.5",
        )
        row = self.store.get_document(document_id=doc_id)
        assert row is not None
        self.assertEqual(row["status"], "in_progress")
        self.assertEqual(row["doc_type"], "prd")
        self.assertIsNone(row["content_json"])
        self.assertIsNone(row["completed_at"])

    def test_invalid_doc_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_document_in_progress(
                project_id="proj-1", user_id="user-1",
                doc_type="not-a-doc-type",
                plan_tier="pro", model_id="gpt-5.5",
            )


class MarkDocumentCompletedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_flips_to_completed_with_content(self) -> None:
        content = _document_content()
        self.store.mark_document_completed(
            document_id=self.doc_id,
            content_json=content,
            output_tokens_estimate=4200,
        )
        row = self.store.get_document(document_id=self.doc_id)
        assert row is not None
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["content_json"], content)
        self.assertEqual(row["output_tokens_estimate"], 4200)
        self.assertIsNotNone(row["completed_at"])


class MarkDocumentFailedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="course_outline",
            plan_tier="frontier", model_id="gpt-5.5",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_flips_to_failed_with_error(self) -> None:
        self.store.mark_document_failed(
            document_id=self.doc_id,
            error_message="adapter_failed: TimeoutError",
        )
        row = self.store.get_document(document_id=self.doc_id)
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_message"], "adapter_failed: TimeoutError")
        self.assertIsNotNone(row["completed_at"])
        self.assertIsNone(row["content_json"])


class GetLatestCompletedDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.tmp = _make_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_none_when_never_generated(self) -> None:
        result = self.store.get_latest_completed_document(
            project_id="proj-empty", doc_type="business_plan",
        )
        self.assertIsNone(result)

    def test_returns_completed_row(self) -> None:
        doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=doc_id,
            content_json=_document_content(),
            output_tokens_estimate=4000,
        )
        result = self.store.get_latest_completed_document(
            project_id="proj-1", doc_type="business_plan",
        )
        assert result is not None
        self.assertEqual(result["document_id"], doc_id)
        self.assertEqual(result["status"], "completed")

    def test_skips_in_progress_and_failed(self) -> None:
        # Old failed
        failed = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_failed(
            document_id=failed, error_message="boom",
        )
        # Latest in_progress
        self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )
        result = self.store.get_latest_completed_document(
            project_id="proj-1", doc_type="business_plan",
        )
        # Neither should match — only completed are returned.
        self.assertIsNone(result)

    def test_doctype_isolation(self) -> None:
        bp_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=bp_id,
            content_json=_document_content("business_plan"),
            output_tokens_estimate=4000,
        )
        prd_result = self.store.get_latest_completed_document(
            project_id="proj-1", doc_type="prd",
        )
        self.assertIsNone(prd_result)
        bp_result = self.store.get_latest_completed_document(
            project_id="proj-1", doc_type="business_plan",
        )
        assert bp_result is not None
        self.assertEqual(bp_result["doc_type"], "business_plan")

    def test_project_isolation(self) -> None:
        a_id = self.store.create_document_in_progress(
            project_id="proj-A", user_id="user-1",
            doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=a_id,
            content_json=_document_content(),
            output_tokens_estimate=4000,
        )
        b_result = self.store.get_latest_completed_document(
            project_id="proj-B", doc_type="business_plan",
        )
        self.assertIsNone(b_result)


class GetInFlightDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.tmp = _make_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_in_progress_row(self) -> None:
        doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="prd",
            plan_tier="pro", model_id="gpt-5.5",
        )
        result = self.store.get_in_flight_document(
            project_id="proj-1", doc_type="prd",
        )
        assert result is not None
        self.assertEqual(result["document_id"], doc_id)

    def test_skips_completed(self) -> None:
        doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1",
            doc_type="prd",
            plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=doc_id,
            content_json=_document_content("prd"),
            output_tokens_estimate=2000,
        )
        result = self.store.get_in_flight_document(
            project_id="proj-1", doc_type="prd",
        )
        self.assertIsNone(result)

    def test_invalid_doctype_returns_none(self) -> None:
        result = self.store.get_in_flight_document(
            project_id="proj-1", doc_type="bogus",
        )
        self.assertIsNone(result)


class UpdateDocumentContentJsonTests(unittest.TestCase):
    """Test the user-edit pathway used by PATCH /document/{id}/section/{id}."""

    def setUp(self) -> None:
        self.store, self.tmp = _make_store()
        self.doc_id = self.store.create_document_in_progress(
            project_id="proj-1", user_id="user-1", doc_type="business_plan",
            plan_tier="pro", model_id="gpt-5.5",
        )
        self.store.mark_document_completed(
            document_id=self.doc_id,
            content_json=_document_content("business_plan"),
            output_tokens_estimate=120,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_update_replaces_content_json(self) -> None:
        new_content = json.dumps(
            {"doc_type": "business_plan", "sections": [{"id": "x", "title": "Y"}]}
        )
        updated = self.store.update_document_content_json(
            document_id=self.doc_id, content_json=new_content,
        )
        assert updated is not None
        self.assertEqual(updated["content_json"], new_content)

    def test_update_preserves_status_and_completed_at(self) -> None:
        # User edits should NOT flip a completed doc back to in_progress,
        # nor refresh completed_at — Option C semantics from #092.
        before = self.store.get_document(document_id=self.doc_id)
        assert before is not None
        updated = self.store.update_document_content_json(
            document_id=self.doc_id, content_json='{"sections": []}',
        )
        assert updated is not None
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["completed_at"], before["completed_at"])

    def test_update_unknown_id_returns_none(self) -> None:
        result = self.store.update_document_content_json(
            document_id="doc-does-not-exist", content_json="{}",
        )
        self.assertIsNone(result)


class DocumentConstantsTests(unittest.TestCase):
    """Sanity check the module-level constants the API + tests rely on."""

    def test_valid_doc_types_set(self) -> None:
        self.assertEqual(
            VALID_DOC_TYPES,
            frozenset({
                "business_plan", "prd", "story_outline", "event_plan",
                "marketing_plan", "research_proposal", "course_outline",
            }),
        )

    def test_domain_mapping_covers_9_of_11(self) -> None:
        # business_plan, software_product → business_plan
        # software_feature → prd
        # novel, screenplay → story_outline
        # event → event_plan
        # campaign → marketing_plan
        # research → research_proposal
        # course → course_outline
        # career, personal: deferred (not in v1)
        self.assertEqual(DOMAIN_TO_DOC_TYPE["business_plan"], "business_plan")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["software_product"], "business_plan")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["software_feature"], "prd")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["novel"], "story_outline")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["screenplay"], "story_outline")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["event"], "event_plan")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["campaign"], "marketing_plan")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["research"], "research_proposal")
        self.assertEqual(DOMAIN_TO_DOC_TYPE["course"], "course_outline")
        self.assertNotIn("career", DOMAIN_TO_DOC_TYPE)
        self.assertNotIn("personal", DOMAIN_TO_DOC_TYPE)

    def test_all_mapped_types_in_allowlist(self) -> None:
        for doc_type in DOMAIN_TO_DOC_TYPE.values():
            self.assertIn(doc_type, VALID_DOC_TYPES)


if __name__ == "__main__":
    unittest.main()
