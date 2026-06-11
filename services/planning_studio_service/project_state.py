"""Project state machine for the workspace Kanban (B3.3 / B1.1).

Single source of truth for legal state transitions on ``v2_projects``.
Imported by both:

- ``api.py`` — to validate transition POSTs and shape the 409 payload
- ``store.py`` — defensive enforcement at write so a misbehaving
  caller can't shove a project into an illegal state by talking
  directly to the store

States:
    pending_review → in_review → approved   (terminal)
                              → rejected    (terminal)

Both ``approved`` and ``rejected`` are terminal via ``/transition``.
Re-opening a terminal state is intentional friction: it requires the
``/manual-state-override`` endpoint with a non-empty ``note`` so the
audit trail captures *why* the human reversed the AI's last call.

The 5th state ``summary_ready`` is reserved for a post-W4 feature
(per B3.3 design notes). It exists in the SQL CHECK constraint and
in :data:`STATES` here so the schema is forward-compatible without
another migration when the time comes — but it has no incoming or
outgoing legal transitions yet, so attempts to enter or leave it
through ``/transition`` will 409. The ``/manual-state-override``
escape hatch can still produce/consume it for testing.
"""
from __future__ import annotations

from typing import Any, Literal


STATES: tuple[str, ...] = (
    "pending_review",
    "in_review",
    "approved",
    "rejected",
    "summary_ready",
)


# (current, target) tuples that the ``/transition`` endpoint will
# accept. Anything else 409s. Manual override bypasses this set.
LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("pending_review", "in_review"),
        ("in_review", "approved"),
        ("in_review", "rejected"),
    }
)


# Both terminal states block re-entry via /transition. Use this set
# rather than hardcoding the tuple in callers — the boundary may
# shift if ``summary_ready`` ever becomes a normal terminal.
TERMINAL_STATES: frozenset[str] = frozenset({"approved", "rejected"})


# Action verbs the API accepts in the transition body. Each maps to
# a single target state given the current state below.
ActionLiteral = Literal["start_review", "approve", "reject"]
_ACTION_TARGETS: dict[str, str] = {
    "start_review": "in_review",
    "approve": "approved",
    "reject": "rejected",
}


class IllegalTransitionError(Exception):
    """Raised when a transition is not in :data:`LEGAL_TRANSITIONS`.

    The ``payload`` attribute is the dict the API serialises into the
    409 body. Keeping it here rather than in the route handler means
    the message stays consistent across every caller (store-level
    defensive checks, the endpoint, future bulk-transition utilities).
    """

    def __init__(self, current: str, attempted: str) -> None:
        self.current = current
        self.attempted = attempted
        super().__init__(
            f"illegal transition: {current!r} -> {attempted!r}"
        )

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "error": "illegal_transition",
            "current": self.current,
            "attempted": self.attempted,
        }


class UnknownActionError(ValueError):
    """Raised when the request body's ``action`` is not in :data:`_ACTION_TARGETS`.

    The endpoint returns 400 for this — distinct from 409 illegal
    transition, since the request itself is malformed (no such verb)
    rather than the state machine refusing a known verb.
    """


def validate_transition(current: str, target: str) -> None:
    """Raise :class:`IllegalTransitionError` if (current, target) is not legal.

    Used by both the API endpoint and the store's defensive write
    path. Returns ``None`` on success; the caller proceeds with the
    UPDATE. Pass through this function rather than testing
    ``LEGAL_TRANSITIONS`` membership directly so callers always get
    the same payload shape on rejection.
    """
    if (current, target) not in LEGAL_TRANSITIONS:
        raise IllegalTransitionError(current=current, attempted=target)


def next_state_for_action(current: str, action: str) -> str:
    """Resolve ``(current, action)`` to a target state.

    Validates the transition before returning, so callers can write::

        target = next_state_for_action(current, body.action)
        store.update_v2_project_state(..., target_state=target)

    and trust the result is a legal target state. Wraps both
    :class:`UnknownActionError` (bad verb) and
    :class:`IllegalTransitionError` (good verb, wrong state).
    """
    if action not in _ACTION_TARGETS:
        raise UnknownActionError(
            f"unknown action {action!r}; "
            f"expected one of {sorted(_ACTION_TARGETS)}"
        )
    target = _ACTION_TARGETS[action]
    validate_transition(current, target)
    return target


def is_terminal(state: str) -> bool:
    """True for states that block re-entry via ``/transition``."""
    return state in TERMINAL_STATES
