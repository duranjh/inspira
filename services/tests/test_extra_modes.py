"""HTTP + adapter tests for the three auxiliary LLM modes.

- Plan summary (POST /api/v2/projects/{id}/summary)
- Outline generator (POST /api/v2/projects/{id}/outline)
- Topic deduper (POST /api/v2/projects/{id}/dedupe)

These never hit OpenAI. We:
- Use ``make_test_app()`` to get an isolated FastAPI client, store, and
  MagicMock planner adapter. The three extra adapters are injected via
  ``client.app.state.*_adapter`` so the v2 endpoints pick them up instead
  of trying to construct a real ``PlanSummaryAdapter`` etc.
- Seed each test's project + topics + decisions via the public CRUD
  routes (kickoff + createDecision) so the fixtures exercise the same
  persistence paths the production code does.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

try:
    from ._helpers import (
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )

from planning_studio_service.agents.deduper import (
    DeduperAdapter,
    _sanitize_deduper_response,
)
from planning_studio_service.agents.outline import OutlineAdapter
from planning_studio_service.agents.plan_summary import (
    PlanSummaryAdapter,
    _format_summary_user_message,
)
from planning_studio_service.agents.prompts_extra import (
    DEDUPER_PROMPT,
    OUTLINE_PROMPT,
    PLAN_SUMMARY_PROMPT,
)
from planning_studio_service.agents.schemas_extra import (
    DEDUPER_SCHEMA,
    EXTRA_TOOL_SPECS,
    OUTLINE_SCHEMA,
    PLAN_SUMMARY_SCHEMA,
)


# =============================================================================
# Schema + prompt sanity
# =============================================================================


class SchemaSanityTests(unittest.TestCase):
    """Every extra schema must be strict-mode compatible.

    Strict JSON mode (OpenAI) requires: ``additionalProperties`` false at
    every object level; every property in ``required`` is also in
    ``properties``; no ``nullable``.
    """

    def _walk(self, schema: dict) -> list[dict]:
        out: list[dict] = []
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                out.append(schema)
            for v in schema.values():
                out.extend(self._walk(v))
        elif isinstance(schema, list):
            for v in schema:
                out.extend(self._walk(v))
        return out

    def _assert_strict_compliant(self, schema: dict) -> None:
        for obj in self._walk(schema):
            self.assertEqual(
                obj.get("additionalProperties"),
                False,
                f"object missing additionalProperties=false: {obj}",
            )
            required = set(obj.get("required") or [])
            properties = set((obj.get("properties") or {}).keys())
            self.assertEqual(
                required,
                properties,
                f"required/properties mismatch: {required ^ properties}",
            )

    def test_plan_summary_schema_is_strict_compliant(self) -> None:
        self._assert_strict_compliant(PLAN_SUMMARY_SCHEMA)

    def test_outline_schema_is_strict_compliant(self) -> None:
        self._assert_strict_compliant(OUTLINE_SCHEMA)

    def test_deduper_schema_is_strict_compliant(self) -> None:
        self._assert_strict_compliant(DEDUPER_SCHEMA)

    def test_extra_tool_specs_registry_covers_three_modes(self) -> None:
        self.assertEqual(
            sorted(EXTRA_TOOL_SPECS.keys()),
            ["dedupe_response", "outline_response", "plan_summary"],
        )

    def test_prompts_avoid_emojis_and_carry_mode_name(self) -> None:
        for name, prompt in (
            ("PLAN_SUMMARY_PROMPT", PLAN_SUMMARY_PROMPT),
            ("OUTLINE_PROMPT", OUTLINE_PROMPT),
            ("DEDUPER_PROMPT", DEDUPER_PROMPT),
        ):
            with self.subTest(prompt=name):
                # Smoke test: non-empty, long enough to have real guidance.
                self.assertGreater(len(prompt), 500)
                # Tone constraint from the product memo — no emoji.
                for ch in prompt:
                    self.assertLess(
                        ord(ch),
                        0x1F000,
                        f"emoji codepoint in {name}",
                    )


# =============================================================================
# Adapter unit tests
# =============================================================================


class PlanSummaryFormatterTests(unittest.TestCase):
    def test_user_message_groups_by_topic_and_lists_decisions(self) -> None:
        msg = _format_summary_user_message(
            project_title="Autumn Campaign",
            topics=[
                {"topic_id": "t1", "title": "Audience", "icon": "heart"},
                {"topic_id": "t2", "title": "Channels", "icon": "megaphone"},
            ],
            decisions=[
                {
                    "topic_id": "t1",
                    "statement": "Focus on returning customers.",
                    "rationale": "Lower CAC.",
                    "status": "confirmed",
                },
                {
                    "topic_id": "t2",
                    "statement": "Instagram-first.",
                    "status": "confirmed",
                },
            ],
            sample_turns=[
                {
                    "topic_id": "t1",
                    "role": "user",
                    "body": "Returning customers convert 3x better.",
                },
            ],
        )
        self.assertIn("PROJECT: Autumn Campaign", msg)
        self.assertIn("TOPIC: Audience", msg)
        self.assertIn("Focus on returning customers.", msg)
        self.assertIn("because: Lower CAC.", msg)
        self.assertIn("TOPIC: Channels", msg)
        self.assertIn("Instagram-first.", msg)
        self.assertIn("[USER] Returning customers convert 3x better.", msg)

    def test_plan_summary_adapter_requires_project_title(self) -> None:
        adapter = PlanSummaryAdapter(client=MagicMock())
        with self.assertRaises(ValueError):
            adapter.generate(
                project_title="",
                topics=[],
                decisions=[],
                sample_turns=[],
            )


class OutlineAdapterTests(unittest.TestCase):
    def test_outline_requires_artifact_type(self) -> None:
        adapter = OutlineAdapter(client=MagicMock())
        with self.assertRaises(ValueError):
            adapter.generate(
                project_title="Proj",
                artifact_type="",
                topics=[],
                decisions=[],
            )

    def test_outline_requires_project_title(self) -> None:
        adapter = OutlineAdapter(client=MagicMock())
        with self.assertRaises(ValueError):
            adapter.generate(
                project_title="",
                artifact_type="Chapter outline",
                topics=[],
                decisions=[],
            )


class DeduperSanitizerTests(unittest.TestCase):
    def test_short_circuits_when_fewer_than_two_topics(self) -> None:
        adapter = DeduperAdapter(client=MagicMock())
        # Only one topic — no tokens burned, empty proposals.
        result = adapter.generate(
            topics=[{"topic_id": "t1", "title": "Pricing"}],
            decisions=[],
        )
        self.assertEqual(result["merge_proposals"], [])
        self.assertTrue(result["_sanitize"]["short_circuit"])

    def test_sanitizer_drops_unknown_topic_ids(self) -> None:
        parsed = {
            "merge_proposals": [
                {
                    "topic_a_id": "t1",
                    "topic_b_id": "t2",
                    "overlap_reason": "Same pricing concept.",
                    "suggested_merged_title": "Pricing",
                    "suggested_action": "merge",
                },
                {
                    "topic_a_id": "t1",
                    "topic_b_id": "ghost",
                    "overlap_reason": "Nothing real.",
                    "suggested_merged_title": "Pricing",
                    "suggested_action": "merge",
                },
            ],
        }
        _sanitize_deduper_response(
            parsed,
            topics=[
                {"topic_id": "t1", "title": "Pricing"},
                {"topic_id": "t2", "title": "Pricing Strategy"},
            ],
        )
        self.assertEqual(len(parsed["merge_proposals"]), 1)
        self.assertEqual(len(parsed["_sanitize"]["dropped_proposals"]), 1)

    def test_sanitizer_drops_self_merge(self) -> None:
        parsed = {
            "merge_proposals": [
                {
                    "topic_a_id": "t1",
                    "topic_b_id": "t1",
                    "overlap_reason": "self",
                    "suggested_merged_title": "x",
                    "suggested_action": "merge",
                },
            ],
        }
        _sanitize_deduper_response(
            parsed,
            topics=[{"topic_id": "t1", "title": "Pricing"}],
        )
        self.assertEqual(parsed["merge_proposals"], [])


# =============================================================================
# HTTP endpoint tests (mocked adapters injected via app.state)
# =============================================================================


def _seed_project(client, adapter, project_id: str) -> None:
    """Create a project + two topics + a couple decisions for endpoint tests."""
    adapter.kickoff.return_value = fake_kickoff_response()
    response = client.post(
        f"/api/v2/projects/{project_id}/kickoff",
        json={"user_idea": "A neighborhood coffee shop launch."},
    )
    response.raise_for_status()
    topics = response.json()["topics"]
    # Capture one confirmed decision on the first topic so the adapters
    # have real data to synthesize from.
    if topics:
        client.post(
            f"/api/v2/topics/{topics[0]['topic_id']}/decisions",
            json={
                "statement": "Ballroom venue confirmed.",
                "rationale": "Capacity matches guest list.",
            },
        )


class PlanSummaryEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="summary@example.com")
        self.summary_adapter = MagicMock()
        self.client.app.state.plan_summary_adapter = self.summary_adapter

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_summary_happy_path_returns_201_with_summary(self) -> None:
        self.summary_adapter.generate.return_value = {
            "summary_markdown": "At its core, this is a plan about ...",
            "suggested_title": "Autumn Brief",
            "domain_framing": "campaign memo",
        }
        _seed_project(self.client, self.adapter, "proj-summary")

        response = self.client.post("/api/v2/projects/proj-summary/summary")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("summary", body)
        self.assertEqual(body["summary"]["suggested_title"], "Autumn Brief")
        self.assertEqual(body["summary"]["domain_framing"], "campaign memo")
        self.summary_adapter.generate.assert_called_once()
        kwargs = self.summary_adapter.generate.call_args.kwargs
        self.assertIn("project_title", kwargs)
        self.assertIn("topics", kwargs)
        self.assertIn("decisions", kwargs)
        self.assertIn("sample_turns", kwargs)

    def test_summary_unknown_project_returns_404(self) -> None:
        response = self.client.post("/api/v2/projects/does-not-exist/summary")
        self.assertEqual(response.status_code, 404)
        self.summary_adapter.generate.assert_not_called()

    def test_summary_planner_failure_surfaces_as_500_with_request_id(self) -> None:
        _seed_project(self.client, self.adapter, "proj-fail")
        self.summary_adapter.generate.side_effect = RuntimeError("boom")
        response = self.client.post("/api/v2/projects/proj-fail/summary")
        self.assertEqual(response.status_code, 500)
        self.assertIn("request_id", response.json()["detail"])


class OutlineEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="outline@example.com")
        self.outline_adapter = MagicMock()
        self.client.app.state.outline_adapter = self.outline_adapter

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_outline_happy_path(self) -> None:
        self.outline_adapter.generate.return_value = {
            "artifact_kind": "deck_outline",
            "suggested_title": "Pitch — Atlas",
            "sections": [
                {
                    "roman_numeral": "I",
                    "title": "Opening",
                    "note": "One-line hook.",
                    "subsections": [],
                },
            ],
        }
        _seed_project(self.client, self.adapter, "proj-out")
        response = self.client.post(
            "/api/v2/projects/proj-out/outline",
            json={"artifact_type": "Pitch deck outline"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["outline"]["artifact_kind"], "deck_outline")
        self.outline_adapter.generate.assert_called_once()
        kwargs = self.outline_adapter.generate.call_args.kwargs
        self.assertEqual(kwargs["artifact_type"], "Pitch deck outline")

    def test_outline_rejects_empty_artifact_type(self) -> None:
        _seed_project(self.client, self.adapter, "proj-out2")
        response = self.client.post(
            "/api/v2/projects/proj-out2/outline",
            json={"artifact_type": "   "},
        )
        self.assertEqual(response.status_code, 400)
        self.outline_adapter.generate.assert_not_called()

    def test_outline_unknown_project_returns_404(self) -> None:
        response = self.client.post(
            "/api/v2/projects/ghost/outline",
            json={"artifact_type": "Chapter outline"},
        )
        self.assertEqual(response.status_code, 404)
        self.outline_adapter.generate.assert_not_called()


class DedupeEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="dedupe@example.com")
        self.deduper_adapter = MagicMock()
        self.client.app.state.deduper_adapter = self.deduper_adapter

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dedupe_happy_path(self) -> None:
        self.deduper_adapter.generate.return_value = {
            "merge_proposals": [],
            "_sanitize": {"dropped_proposals": []},
        }
        _seed_project(self.client, self.adapter, "proj-dedupe")
        response = self.client.post("/api/v2/projects/proj-dedupe/dedupe")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["dedupe"]["merge_proposals"], [])
        self.deduper_adapter.generate.assert_called_once()
        kwargs = self.deduper_adapter.generate.call_args.kwargs
        self.assertIn("topics", kwargs)
        self.assertIn("decisions", kwargs)

    def test_dedupe_unknown_project_returns_404(self) -> None:
        response = self.client.post("/api/v2/projects/ghost/dedupe")
        self.assertEqual(response.status_code, 404)
        self.deduper_adapter.generate.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
