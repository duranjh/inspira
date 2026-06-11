"""Tests for planning_studio_service.markdown_import.

~6 scenarios covering parsing + instantiation:
  1. Basic H1 + H2s → project with matching topics.
  2. H2 with H3 bullets → decisions on that topic.
  3. No H1 → falls back to title_override, or "Imported project".
  4. Empty markdown → ValueError.
  5. Markdown with frontmatter → parsed fine, unknown fields ignored.
  6. Very long markdown (50 k chars) → accepted, topics capped at 20.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, call

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.markdown_import import (
    MarkdownImportBody,
    ParsedImport,
    instantiate_from_markdown,
    parse_markdown,
)

ensure_loaded()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> MagicMock:
    """Return a mock store that records calls and returns plausible shapes."""
    store = MagicMock()
    store.create_v2_project.return_value = {
        "project_id": "project-testtest0001",
        "user_id": "user-abc",
        "title": "Test",
        "metadata": {},
        "created_at": "2026-04-21T00:00:00+00:00",
        "updated_at": "2026-04-21T00:00:00+00:00",
    }
    topic_counter = [0]

    def _create_topic(**kwargs):  # noqa: ANN202
        topic_counter[0] += 1
        return {
            "topic_id": f"topic-{topic_counter[0]:04d}",
            "project_id": kwargs.get("project_id"),
            "title": kwargs.get("title"),
            "icon": kwargs.get("icon", "flag"),
            "position_x": kwargs.get("position_x", 0.0),
            "position_y": kwargs.get("position_y", 0.0),
            "status": "empty",
            "order_index": kwargs.get("order_index", 0),
            "origin": kwargs.get("origin", "planner_initial"),
            "metadata": kwargs.get("metadata", {}),
            "created_at": "2026-04-21T00:00:00+00:00",
            "updated_at": "2026-04-21T00:00:00+00:00",
            "deleted_at": None,
        }

    store.create_topic.side_effect = _create_topic

    decision_counter = [0]

    def _create_decision(**kwargs):  # noqa: ANN202
        decision_counter[0] += 1
        return {
            "decision_id": f"dec-{decision_counter[0]:04d}",
            "topic_id": kwargs.get("topic_id"),
            "project_id": kwargs.get("project_id"),
            "statement": kwargs.get("statement"),
            "rationale": kwargs.get("rationale"),
            "status": kwargs.get("status", "proposed"),
            "source_turn_id": None,
            "proposed_by": kwargs.get("proposed_by", "planner"),
            "confirmed_by_user_id": None,
            "created_at": "2026-04-21T00:00:00+00:00",
            "updated_at": "2026-04-21T00:00:00+00:00",
            "retracted_at": None,
        }

    store.create_decision.side_effect = _create_decision
    return store


# ---------------------------------------------------------------------------
# 1. Basic H1 + H2s → project with matching topics
# ---------------------------------------------------------------------------
class TestBasicH1H2(unittest.TestCase):
    MD = """\
# My Novel

## Characters

## Plot Structure

## Setting
"""

    def test_parse_title(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertEqual(parsed.title, "My Novel")

    def test_parse_topic_count(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertEqual(len(parsed.topics), 3)

    def test_parse_topic_titles(self) -> None:
        parsed = parse_markdown(self.MD)
        titles = [t.title for t in parsed.topics]
        self.assertEqual(titles, ["Characters", "Plot Structure", "Setting"])

    def test_instantiate_creates_project(self) -> None:
        parsed = parse_markdown(self.MD)
        store = _make_store()
        project = instantiate_from_markdown(
            store, user_id="user-abc", parsed=parsed,
        )
        store.create_v2_project.assert_called_once_with(
            user_id="user-abc", title="My Novel",
        )
        self.assertEqual(project["project_id"], "project-testtest0001")

    def test_instantiate_creates_correct_topic_count(self) -> None:
        parsed = parse_markdown(self.MD)
        store = _make_store()
        instantiate_from_markdown(store, user_id="user-abc", parsed=parsed)
        self.assertEqual(store.create_topic.call_count, 3)


# ---------------------------------------------------------------------------
# 2. H2 with H3 bullets → decisions on that topic
# ---------------------------------------------------------------------------
class TestH3Decisions(unittest.TestCase):
    MD = """\
# Launch Plan

## Marketing
Some prose about marketing.

### Run paid ads
### Partner with creators
### Product Hunt launch

## Budget
"""

    def test_decisions_created_for_h3s(self) -> None:
        parsed = parse_markdown(self.MD)
        store = _make_store()
        instantiate_from_markdown(store, user_id="u", parsed=parsed)

        # The first topic (Marketing) should yield:
        #   1 context_note decision (prose) + 3 H3 decisions = 4 decisions
        # The second topic (Budget) has no H3s and no prose = 0 decisions
        total_decisions = store.create_decision.call_count
        self.assertEqual(total_decisions, 4)

    def test_h3_statements(self) -> None:
        parsed = parse_markdown(self.MD)
        marketing = parsed.topics[0]
        self.assertIn("Run paid ads", marketing.decisions)
        self.assertIn("Partner with creators", marketing.decisions)
        self.assertIn("Product Hunt launch", marketing.decisions)

    def test_context_note_captured(self) -> None:
        parsed = parse_markdown(self.MD)
        marketing = parsed.topics[0]
        self.assertIn("Some prose about marketing", marketing.context_note)


# ---------------------------------------------------------------------------
# 3. No H1 → title_override or "Imported project"
# ---------------------------------------------------------------------------
class TestNoH1Fallback(unittest.TestCase):
    MD = """\
## Goals
Be ambitious.

## Timeline
Three months.
"""

    def test_parse_returns_empty_title(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertEqual(parsed.title, "")

    def test_title_override_used(self) -> None:
        parsed = parse_markdown(self.MD)
        store = _make_store()
        instantiate_from_markdown(
            store, user_id="u", parsed=parsed, title_override="My Override",
        )
        store.create_v2_project.assert_called_once_with(
            user_id="u", title="My Override",
        )

    def test_default_fallback_title(self) -> None:
        parsed = parse_markdown(self.MD)
        store = _make_store()
        instantiate_from_markdown(store, user_id="u", parsed=parsed)
        store.create_v2_project.assert_called_once_with(
            user_id="u", title="Imported project",
        )


# ---------------------------------------------------------------------------
# 4. Empty markdown → ValueError
# ---------------------------------------------------------------------------
class TestEmptyMarkdown(unittest.TestCase):
    def test_empty_string_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_markdown("")

    def test_whitespace_only_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_markdown("   \n\t  ")


# ---------------------------------------------------------------------------
# 5. Frontmatter → known fields kept, unknown fields ignored
# ---------------------------------------------------------------------------
class TestFrontmatter(unittest.TestCase):
    MD = """\
---
title: Ignored title field
author: Jane Doe
source: Notion
unknown_field: should be dropped
another_unknown: also dropped
---

# Actual Title

## Topic One
Content here.
"""

    def test_frontmatter_does_not_clobber_h1(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertEqual(parsed.title, "Actual Title")

    def test_known_fields_kept(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertIn("author", parsed.metadata)
        self.assertEqual(parsed.metadata["author"], "Jane Doe")
        self.assertIn("source", parsed.metadata)

    def test_unknown_fields_dropped(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertNotIn("unknown_field", parsed.metadata)
        self.assertNotIn("another_unknown", parsed.metadata)

    def test_topics_still_parsed(self) -> None:
        parsed = parse_markdown(self.MD)
        self.assertEqual(len(parsed.topics), 1)
        self.assertEqual(parsed.topics[0].title, "Topic One")


# ---------------------------------------------------------------------------
# 6. Very long markdown → accepted, topics capped at 20
# ---------------------------------------------------------------------------
class TestTopicCap(unittest.TestCase):
    def _build_long_md(self, n_topics: int) -> str:
        lines = ["# Big Brain Dump\n"]
        for i in range(1, n_topics + 1):
            lines.append(f"## Topic {i}\n")
            # Add some filler prose to push toward 50 k chars
            lines.append("Lorem ipsum " * 80 + "\n")
        return "\n".join(lines)

    def test_50k_chars_accepted(self) -> None:
        md = self._build_long_md(30)
        # Make it at least 50 k chars
        md += "x" * max(0, 50_000 - len(md))
        # Should not raise
        parsed = parse_markdown(md)
        self.assertIsInstance(parsed, ParsedImport)

    def test_topics_capped_at_20(self) -> None:
        md = self._build_long_md(35)
        parsed = parse_markdown(md)
        self.assertLessEqual(len(parsed.topics), 20)

    def test_exactly_20_when_30_provided(self) -> None:
        md = self._build_long_md(30)
        parsed = parse_markdown(md)
        self.assertEqual(len(parsed.topics), 20)


# ---------------------------------------------------------------------------
# MarkdownImportBody model sanity
# ---------------------------------------------------------------------------
class TestMarkdownImportBody(unittest.TestCase):
    def test_markdown_required(self) -> None:
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            MarkdownImportBody()  # type: ignore[call-arg]

    def test_title_optional(self) -> None:
        body = MarkdownImportBody(markdown="# Hi\n## Topic")
        self.assertIsNone(body.title)

    def test_title_accepted(self) -> None:
        body = MarkdownImportBody(markdown="## Topic", title="Override")
        self.assertEqual(body.title, "Override")


if __name__ == "__main__":
    unittest.main()
