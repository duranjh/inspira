"""Classifier rule tests (W2 F5).

Covers the lexical classifier in feedback_items/classify.py.
The rules are deterministic — these tests pin the partner-facing
behaviour so a future LLM swap can compare against the same
expectations.
"""
from __future__ import annotations

import unittest

from planning_studio_service.feedback_items.classify import (
    ALLOWED_HINT_VALUES,
    classify,
)


class ClassifyHintTakesPrecedenceTests(unittest.TestCase):

    def test_recognized_hint_always_wins(self) -> None:
        self.assertEqual(
            classify(title="anything", hint="bug"), "bug"
        )
        self.assertEqual(
            classify(title="loving the new layout!", hint="complaint"),
            "complaint",
        )

    def test_unknown_hint_falls_through_to_classifier(self) -> None:
        # 'critical' isn't in the allowed set; classifier still picks
        # based on title content.
        self.assertEqual(
            classify(
                title="App crashes on Safari",
                hint="critical",
            ),
            "bug",
        )

    def test_blank_hint_falls_through_to_classifier(self) -> None:
        self.assertEqual(
            classify(title="App crashes on Safari", hint=""),
            "bug",
        )

    def test_allowed_hint_set_complete(self) -> None:
        self.assertEqual(
            ALLOWED_HINT_VALUES,
            {"bug", "feature", "complaint", "praise", "question", "noise"},
        )


class ClassifyByContentTests(unittest.TestCase):

    def test_bug_phrases(self) -> None:
        for title in [
            "Login crashes on Safari",
            "Kanban board won't load",
            "Lost my work after dragging",
            "Spinner forever",
            "Throws an error after submitting",
        ]:
            self.assertEqual(
                classify(title=title), "bug", f"missed bug: {title!r}"
            )

    def test_feature_phrases(self) -> None:
        for title in [
            "Can we add bulk export to the kanban board?",
            "Please add keyboard shortcuts for the canvas",
            "Add a 'duplicate' option in the context menu",
            "Should support markdown",
        ]:
            self.assertEqual(
                classify(title=title), "feature",
                f"missed feature: {title!r}",
            )

    def test_complaint_phrases(self) -> None:
        for title in [
            "Pricing is too expensive for what I get",
            "Hate the new layout",
            "Onboarding is rough — no idea what to do first",
        ]:
            self.assertEqual(
                classify(title=title), "complaint",
                f"missed complaint: {title!r}",
            )

    def test_praise_phrases(self) -> None:
        for title in [
            "Love the new dashboard!",
            "Just discovered the canvas — game changer",
            "Take my money",
            "The way the kanban handles dragging is *chef's kiss*",
        ]:
            self.assertEqual(
                classify(title=title), "praise",
                f"missed praise: {title!r}",
            )

    def test_question_phrases(self) -> None:
        for title in [
            "How do I switch workspaces?",
            "Is there a way to bulk-rename?",
            "When will the kanban support iPad?",
        ]:
            self.assertEqual(
                classify(title=title), "question",
                f"missed question: {title!r}",
            )

    def test_short_input_is_noise(self) -> None:
        for title in ["?", "test", "ok", "...", "👍", "nvm"]:
            self.assertEqual(
                classify(title=title), "noise",
                f"missed noise: {title!r}",
            )

    def test_unrecognizable_long_input_falls_to_noise(self) -> None:
        # Long but with zero classifier signal — ends up in noise
        # rather than a wrong bucket.
        self.assertEqual(
            classify(
                title="weather is nice today, totally unrelated content",
            ),
            "noise",
        )

    def test_bug_wins_over_question_when_both_match(self) -> None:
        # "How do I prevent the crash?" is a question on a bug —
        # bug is more actionable so it wins per the docstring.
        self.assertEqual(
            classify(title="How do I prevent the crash on Safari?"),
            "bug",
        )


if __name__ == "__main__":
    unittest.main()
