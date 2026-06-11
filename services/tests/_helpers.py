"""Shared fixtures for the FastAPI + auth test suite.

Centralises the boilerplate that ``test_api_fastapi.py``,
``test_auth_routes.py`` and ``test_ownership.py`` all need:

- ``make_test_app()`` — spin up an isolated store in a temp dir, mock
  the planner adapter, and hand back a ``TestClient`` bound to a
  freshly-built ``FastAPI`` app. Each test gets its own SQLite file and
  its own instance of the app so the ``itsdangerous`` session secret,
  CORS config, and bootstrapped system user are cleanly isolated.
- ``signup_and_login(client, email, password)`` — mutates the passed-in
  ``TestClient`` in place so subsequent requests carry the session
  cookie. Returns the signup response payload for assertions.
- ``fake_kickoff_response()`` — canonical valid-shaped planner kickoff
  response used to stub the adapter whenever a test needs a project
  to exist (topics/relationships get persisted in the store as a side
  effect).

Design choices:
- The mocked adapter is injected via ``create_app(store=..., adapter=...)``
  so no route ever reaches the real OpenAI client during tests.
- ``ignore_cleanup_errors=True`` is passed to ``TemporaryDirectory``
  because SQLite on Windows holds file handles open until GC, and the
  test cleanup runs before that. See ``test_service.py`` for the same
  workaround.
- The returned ``temp_dir`` object is kept alive by the caller's
  ``tearDown`` — if it gets GC'd mid-test the temp directory disappears
  and SQLite errors with ``unable to open database file``.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.api import create_app
from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore


ensure_loaded()

# BYOK tests + anything that exercises the users.{openai,anthropic}_api_key
# columns needs a valid Fernet key in the env. Picking a deterministic
# test-only key keeps encrypt/decrypt reproducible across runs; production
# deploys set their own via ``INSPIRA_BYOK_SECRET``. We only seed when the
# var is unset so a caller that set their own key on purpose still wins.
os.environ.setdefault(
    "INSPIRA_BYOK_SECRET", "v832Ysb_Eb5rkU4n6jIIz2oGoQueIDcyK5Ny8XJbZHg=",
)

# PR 2 deleted the voice surface entirely; the INSPIRA_VOICE_SWEEP_*
# env-vars are no longer read by anything.


def make_test_app() -> tuple[TestClient, PlanningStudioStore, MagicMock, Any]:
    """Build a fresh FastAPI app wired to an isolated store + mocked adapter.

    Returns ``(client, store, adapter, temp_dir)``. The caller owns the
    ``temp_dir`` handle — keep it on ``self`` in setUp and call
    ``temp_dir.cleanup()`` in tearDown. The TestClient is ready to hit
    any route; auth routes start as the system user until the test
    signs someone in.
    """
    temp_dir = tempfile.TemporaryDirectory(
        prefix="inspira-fastapi-test-", ignore_cleanup_errors=True,
    )
    os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = temp_dir.name
    store = PlanningStudioStore(load_config())
    adapter = MagicMock()
    app = create_app(store=store, adapter=adapter)
    client = TestClient(app)
    return client, store, adapter, temp_dir


def signup_and_login(
    client: TestClient,
    email: str = "a@example.com",
    password: str = "password123",
    display_name: str = "",
    terms_accepted: bool = True,
) -> dict[str, Any]:
    """Sign the user up on this client so subsequent calls are authenticated.

    The session cookie is set on ``client.cookies`` by ``TestClient`` as
    a side effect of the signup response; httpx persists it across
    later requests on the same client. We return the signup payload so
    tests that want to assert on ``user_id`` / ``email`` can do so.

    Uses signup (not login) by default — a dedicated test for login is
    in ``test_auth_routes.py``. ``terms_accepted`` defaults to True so
    the happy-path helper stays a one-liner; tests that exercise the
    terms gate can pass ``terms_accepted=False`` to hit the 400.
    """
    response = client.post(
        "/api/auth/signup",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
            "terms_accepted": terms_accepted,
        },
    )
    response.raise_for_status()
    return response.json()


def fake_kickoff_response() -> dict[str, Any]:
    """Canonical planner kickoff response used by route-level tests.

    Shape matches what ``OpenAIPlanningInterviewer.kickoff`` returns —
    a domain label, an opening card, a list of topics, a list of
    relationships, and the sanitize bookkeeping. The HTTP layer
    persists the topics and relationships into the store; it doesn't
    look at the other fields, but we include them so the payload is
    representative of real traffic.
    """
    return {
        "domain": "event",
        "domain_confidence": "high",
        "opening_card": {"body": "Five topics. Start with Venue."},
        "topics": [
            # B1 (YC v4): every topic must include a `q_and_a` array
            # because OpenAI strict schema requires it. Empty array
            # exercises the legacy on-demand topic_turn flow — same
            # behavior as before B1 shipped.
            {"title": "Venue", "icon": "map-pin", "why_this_topic": "The space.", "q_and_a": []},
            {"title": "Budget", "icon": "chart", "why_this_topic": "What we have.", "q_and_a": []},
            {"title": "Audience", "icon": "heart", "why_this_topic": "Who it's for.", "q_and_a": []},
            {"title": "Timing", "icon": "clock", "why_this_topic": "When.", "q_and_a": []},
            {"title": "Safety", "icon": "flag", "why_this_topic": "Compliance.", "q_and_a": []},
        ],
        "relationships": [
            {"from_topic_title": "Venue", "to_topic_title": "Safety", "label": "requires"},
            {"from_topic_title": "Budget", "to_topic_title": "Venue", "label": "bounds"},
        ],
        "suggested_first_topic": "Venue",
        "clarifying_question_if_too_vague": None,
        "_sanitize": {"dropped_relationships": [], "suggested_first_fallback": None},
    }


def fake_kickoff_response_with_qa() -> dict[str, Any]:
    """B1 kickoff response variant: topics include pre-populated Q&A.

    Mirrors what the planner produces under the YC v4 reframe (kickoff
    prompt instructs the LLM to generate 2-3 Q&A turns + a decision per
    topic). Used by tests verifying that the kickoff handler persists
    these into the qna_turns + decisions tables.
    """
    base = fake_kickoff_response()
    base["topics"] = [
        {
            "title": "Venue",
            "icon": "map-pin",
            "why_this_topic": "The space.",
            "q_and_a": [
                {
                    "question": "Indoor or outdoor venue?",
                    "answer": "Indoor — easier to control sound and weather.",
                    "decision": "Venue is indoor.",
                },
                {
                    "question": "Capacity target?",
                    "answer": "120 guests, with cocktail-hour overflow space.",
                    "decision": "Capacity locked at 120 guests.",
                },
            ],
        },
        {
            "title": "Budget",
            "icon": "chart",
            "why_this_topic": "What we have.",
            "q_and_a": [
                {
                    "question": "Total budget?",
                    "answer": "$45k all-in including venue, catering, A/V.",
                    "decision": "Budget cap is $45k all-in.",
                },
            ],
        },
        # Topic with empty q_and_a — exercises the no-inserts path.
        {"title": "Audience", "icon": "heart", "why_this_topic": "Who it's for.", "q_and_a": []},
    ]
    return base


def fake_turn_response(
    action: str = "ask",
    *,
    planned_checkpoints: "list[dict] | None" = None,
    checkpoint_updates: "list[dict] | None" = None,
) -> dict[str, Any]:
    """Canonical planner topic_turn response for test_api_fastapi.

    ``action="ask"`` is the common case — a planner question the HTTP
    layer will persist as a planner turn. ``action="suggest_close"``
    flips the branch that skips persisting the planner turn.

    ``planned_checkpoints`` and ``checkpoint_updates`` default to None
    (first-turn / no-update paths respectively).
    """
    is_suggest_close = action == "suggest_close"
    return {
        "action": action,
        "question": (
            "You've touched everything I planned to ask about here. Want to keep exploring, or close this topic?"
            if is_suggest_close
            else "Which line items are non-negotiable?"
        ),
        "why_this_matters": "Pre-deciding cuts saves negotiation later." if not is_suggest_close else None,
        "suggested_responses": (
            [
                {"label": "Close the topic \u2192", "intent": "close"},
                {"label": "I want to keep going \u2192", "intent": "continue"},
            ]
            if is_suggest_close
            else [
                {"label": "Safety and insurance.", "intent": "conservative"},
                {"label": "Talent flexes first.", "intent": "ambitious"},
            ]
        ),
        "proposed_decisions": [],
        "consistency_flags": [],
        "new_topic_proposal": None,
        "topic_deletion_suggestion": None,
        "close_recommendation_reason": "All decisions captured." if is_suggest_close else None,
        "conflict_resolution": None,
        "planned_checkpoints": planned_checkpoints,
        "checkpoint_updates": checkpoint_updates,
        "_sanitize": {
            "dropped_consistency_flags": [],
            "dropped_target_topic_titles": [],
            "resolve_conflict_downgrades": [],
            "dropped_new_topic_proposal": None,
            "dropped_deletion_suggestion": None,
        },
    }
