"""Pure-python coverage for the project state machine (no DB).

Mirrors the legal-transition table in
``services.planning_studio_service.project_state`` and the action
mapping. If a future session extends either, the tests here are the
canonical contract — update them deliberately, not opportunistically.
"""
from __future__ import annotations

import unittest

from planning_studio_service.project_state import (
    IllegalTransitionError,
    LEGAL_TRANSITIONS,
    STATES,
    TERMINAL_STATES,
    UnknownActionError,
    is_terminal,
    next_state_for_action,
    validate_transition,
)


class ValidateTransitionTests(unittest.TestCase):
    """Every ``(current, target)`` pair in the state cross-product
    is either explicitly legal or raises. No silent ``True``/``False``
    drift between :data:`LEGAL_TRANSITIONS` and the validator."""

    def test_legal_transitions_pass_silently(self) -> None:
        for current, target in LEGAL_TRANSITIONS:
            with self.subTest(transition=f"{current}->{target}"):
                self.assertIsNone(validate_transition(current, target))

    def test_every_illegal_pair_raises(self) -> None:
        all_pairs = {(c, t) for c in STATES for t in STATES}
        illegal_pairs = all_pairs - LEGAL_TRANSITIONS
        # Sanity — STATES has 5 entries, so 25 pairs total, 3 legal,
        # 22 illegal. Catches accidental shrinkage of LEGAL_TRANSITIONS.
        self.assertEqual(len(illegal_pairs), 22)
        for current, target in illegal_pairs:
            with self.subTest(transition=f"{current}->{target}"):
                with self.assertRaises(IllegalTransitionError) as ctx:
                    validate_transition(current, target)
                self.assertEqual(ctx.exception.current, current)
                self.assertEqual(ctx.exception.attempted, target)

    def test_self_loops_are_illegal(self) -> None:
        # Re-asserting the current state through /transition has no
        # legal use case; force callers to surface the no-op explicitly.
        for state in STATES:
            with self.subTest(state=state):
                with self.assertRaises(IllegalTransitionError):
                    validate_transition(state, state)

    def test_summary_ready_has_no_legal_transitions(self) -> None:
        # 5th-state forward-compat: summary_ready is reserved for a
        # post-W4 feature. It must not be reachable through /transition
        # in either direction until that feature lands.
        for other in STATES:
            with self.subTest(direction=f"summary_ready<->{other}"):
                if other != "summary_ready":
                    with self.assertRaises(IllegalTransitionError):
                        validate_transition(other, "summary_ready")
                with self.assertRaises(IllegalTransitionError):
                    validate_transition("summary_ready", other)

    def test_terminal_states_block_reentry(self) -> None:
        for terminal in TERMINAL_STATES:
            self.assertTrue(is_terminal(terminal))
            for target in STATES:
                with self.subTest(transition=f"{terminal}->{target}"):
                    with self.assertRaises(IllegalTransitionError):
                        validate_transition(terminal, target)

    def test_payload_shape_matches_api_contract(self) -> None:
        # The 409 response body promises a stable shape; pin it here.
        with self.assertRaises(IllegalTransitionError) as ctx:
            validate_transition("approved", "in_review")
        self.assertEqual(
            ctx.exception.payload,
            {
                "error": "illegal_transition",
                "current": "approved",
                "attempted": "in_review",
            },
        )


class NextStateForActionTests(unittest.TestCase):
    def test_start_review_from_pending(self) -> None:
        self.assertEqual(
            next_state_for_action("pending_review", "start_review"),
            "in_review",
        )

    def test_approve_from_in_review(self) -> None:
        self.assertEqual(
            next_state_for_action("in_review", "approve"), "approved"
        )

    def test_reject_from_in_review(self) -> None:
        self.assertEqual(
            next_state_for_action("in_review", "reject"), "rejected"
        )

    def test_unknown_action_raises_value_error(self) -> None:
        # Distinct from IllegalTransitionError — the verb itself is
        # unrecognised, so 400 (bad request) not 409 (state conflict).
        with self.assertRaises(UnknownActionError):
            next_state_for_action("pending_review", "ship_it")

    def test_known_action_in_wrong_state_raises_illegal_transition(
        self,
    ) -> None:
        # ``approve`` is a real verb but only legal from in_review;
        # from pending_review it must surface as 409 not 400.
        with self.assertRaises(IllegalTransitionError):
            next_state_for_action("pending_review", "approve")
        with self.assertRaises(IllegalTransitionError):
            next_state_for_action("approved", "approve")

    def test_start_review_from_terminal_is_illegal(self) -> None:
        # Re-opening through /transition is intentionally blocked —
        # the user must use /manual-state-override with a note.
        with self.assertRaises(IllegalTransitionError):
            next_state_for_action("approved", "start_review")
        with self.assertRaises(IllegalTransitionError):
            next_state_for_action("rejected", "start_review")


class IsTerminalTests(unittest.TestCase):
    def test_terminal_set(self) -> None:
        self.assertTrue(is_terminal("approved"))
        self.assertTrue(is_terminal("rejected"))

    def test_non_terminal_set(self) -> None:
        self.assertFalse(is_terminal("pending_review"))
        self.assertFalse(is_terminal("in_review"))
        # summary_ready is not a /transition target so it isn't
        # ``terminal`` in the state-machine sense — manual-override
        # can still leave it.
        self.assertFalse(is_terminal("summary_ready"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
