"""Tests for the NUL-byte / C0 control-character sanitizer.

Verifies that:
- ``sanitize_text`` strips NUL bytes (\\x00).
- ``sanitize_text`` strips other non-whitespace C0 control characters
  (\\x01–\\x08, \\x0b, \\x0c, \\x0e–\\x1f, \\x7f).
- ``sanitize_text`` preserves tab (\\t), newline (\\n), carriage-return (\\r).
- ``sanitize_text`` is a no-op on clean text and on non-string inputs.
- The ``SanitizedStr`` annotated type fires via Pydantic model validation,
  specifically for ``KickoffBody.user_idea`` (highest-priority field per spec).
- All other priority models (TopicTurnBody, DecisionCreateBody, etc.) also
  strip NUL bytes via ``SanitizedStr``.
"""
from __future__ import annotations

import unittest

from planning_studio_service.validators import SanitizedStr, sanitize_text
from planning_studio_service.api import (
    AttachedSource,
    DecisionCreateBody,
    KickoffBody,
    ProjectCreateBody,
    ProjectUpdateBody,
    ShelfCreateBody,
    ShelfUpdateBody,
    TopicCreateBody,
    TopicPrivateNotesBody,
    TopicTurnBody,
    TopicUpdateBody,
)


class SanitizeTextUnitTests(unittest.TestCase):
    """Unit tests for the standalone ``sanitize_text`` function."""

    def test_strips_nul_byte(self) -> None:
        assert sanitize_text("hel\x00lo") == "hello"

    def test_strips_nul_at_start(self) -> None:
        assert sanitize_text("\x00hello") == "hello"

    def test_strips_nul_at_end(self) -> None:
        assert sanitize_text("hello\x00") == "hello"

    def test_strips_multiple_nul_bytes(self) -> None:
        assert sanitize_text("a\x00b\x00c") == "abc"

    def test_strips_c0_control_chars(self) -> None:
        # \x01 through \x08 — SOH, STX, ETX, EOT, ENQ, ACK, BEL, BS
        for code in range(0x01, 0x09):
            assert sanitize_text(f"x{chr(code)}y") == "xy", f"code {code:#04x} should be stripped"
        # \x0b (VT) and \x0c (FF)
        assert sanitize_text("a\x0bb") == "ab"
        assert sanitize_text("a\x0cb") == "ab"
        # \x0e through \x1f
        for code in range(0x0E, 0x20):
            assert sanitize_text(f"x{chr(code)}y") == "xy", f"code {code:#04x} should be stripped"
        # \x7f (DEL)
        assert sanitize_text("a\x7fb") == "ab"

    def test_preserves_tab(self) -> None:
        assert sanitize_text("col1\tcol2") == "col1\tcol2"

    def test_preserves_newline(self) -> None:
        assert sanitize_text("line1\nline2") == "line1\nline2"

    def test_preserves_carriage_return(self) -> None:
        assert sanitize_text("line1\r\nline2") == "line1\r\nline2"

    def test_clean_text_unchanged(self) -> None:
        text = "Hello, world! Café résumé 日本語."
        assert sanitize_text(text) == text

    def test_empty_string_unchanged(self) -> None:
        assert sanitize_text("") == ""

    def test_non_string_passthrough(self) -> None:
        # sanitize_text should not crash on None or int (belt-and-suspenders
        # for Optional fields that might pass None through BeforeValidator).
        assert sanitize_text(None) is None  # type: ignore[arg-type]
        assert sanitize_text(42) == 42  # type: ignore[arg-type]


class PydanticModelSanitizationTests(unittest.TestCase):
    """Confirm NUL bytes are stripped by Pydantic at parse time for each
    high-priority write model."""

    # --- KickoffBody: user_idea (highest priority per spec) ---

    def test_kickoff_user_idea_nul_stripped(self) -> None:
        body = KickoffBody(user_idea="plan\x00ning")
        assert "\x00" not in body.user_idea
        assert body.user_idea == "planning"

    def test_kickoff_user_idea_clean_unchanged(self) -> None:
        body = KickoffBody(user_idea="plan a concert tour")
        assert body.user_idea == "plan a concert tour"

    # --- TopicTurnBody: user_answer ---

    def test_topic_turn_user_answer_nul_stripped(self) -> None:
        body = TopicTurnBody(user_answer="ans\x00wer")
        assert body.user_answer == "answer"

    # --- TopicCreateBody: title ---

    def test_topic_create_title_nul_stripped(self) -> None:
        body = TopicCreateBody(title="Venue\x00 Search")
        assert body.title == "Venue Search"

    # --- TopicUpdateBody: title ---

    def test_topic_update_title_nul_stripped(self) -> None:
        body = TopicUpdateBody(title="Budget\x00")
        assert body.title == "Budget"

    def test_topic_update_title_none_passthrough(self) -> None:
        body = TopicUpdateBody(title=None)
        assert body.title is None

    # --- TopicPrivateNotesBody: notes ---

    def test_topic_private_notes_nul_stripped(self) -> None:
        body = TopicPrivateNotesBody(notes="private\x00note")
        assert body.notes == "privatenote"

    # --- DecisionCreateBody: statement, rationale ---

    def test_decision_statement_nul_stripped(self) -> None:
        body = DecisionCreateBody(statement="Hire\x00 in Q3")
        assert body.statement == "Hire in Q3"

    def test_decision_rationale_nul_stripped(self) -> None:
        body = DecisionCreateBody(statement="ok", rationale="because\x00reasons")
        assert body.rationale == "becausereasons"

    # --- ProjectCreateBody / ProjectUpdateBody: title ---

    def test_project_create_title_nul_stripped(self) -> None:
        body = ProjectCreateBody(title="My\x00 Project")
        assert body.title == "My Project"

    def test_project_update_title_nul_stripped(self) -> None:
        body = ProjectUpdateBody(title="New\x00Title")
        assert body.title == "NewTitle"

    # --- ShelfCreateBody / ShelfUpdateBody: name ---

    def test_shelf_create_name_nul_stripped(self) -> None:
        body = ShelfCreateBody(name="Work\x00Stuff")
        assert body.name == "WorkStuff"

    def test_shelf_update_name_nul_stripped(self) -> None:
        body = ShelfUpdateBody(name="Ideas\x00")
        assert body.name == "Ideas"

    # --- AttachedSource: excerpt ---

    def test_attached_source_excerpt_nul_stripped(self) -> None:
        src = AttachedSource(excerpt="Some\x00text from a doc")
        assert src.excerpt == "Sometext from a doc"


if __name__ == "__main__":
    unittest.main()
