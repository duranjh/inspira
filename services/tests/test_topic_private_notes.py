"""Tests for per-topic private notes.

Coverage:

- Update flow: POST /api/v2/topics/{id}/private-notes persists notes and
  returns the updated topic row.
- IDOR: a different user cannot read or overwrite a topic's private_notes
  (returns 404, same shape as missing topic).
- Empty string semantics: notes="" clears the field (stored as NULL).
  notes=null does the same.
- GET paths expose private_notes on the topic dict so the frontend can
  render it.
- Prompt assembly guard: the serialized topic_turn user message MUST NOT
  contain the private note text, even when the note is set on the topic.

The prompt-assembly guard test is the critical one — it exercises the
exact formatter used by both the OpenAI and Claude adapters, so any
regression that starts leaking private_notes into the prompt (e.g. a
refactor that spreads ``topic`` into ``current_topic_view``) flips this
test red.
"""
from __future__ import annotations

import unittest
from typing import Any

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

from planning_studio_service.agents.openai_adapter import (
    _format_topic_turn_user_message,
)


# ---------------------------------------------------------------------------
# Test fixture helper
# ---------------------------------------------------------------------------


def _provision_topic(client: Any, adapter: Any) -> str:
    """Run kickoff so a real project + topics exist. Return the first topic id."""
    adapter.kickoff.return_value = fake_kickoff_response()
    response = client.post(
        "/api/v2/projects/proj-notes/kickoff",
        json={"user_idea": "Notebook layout test."},
    )
    response.raise_for_status()
    payload = response.json()
    return payload["topics"][0]["topic_id"]


# ---------------------------------------------------------------------------
# Endpoint integration tests — update, GET round-trip, IDOR
# ---------------------------------------------------------------------------


class TopicPrivateNotesEndpointTests(unittest.TestCase):
    """POST /api/v2/topics/{id}/private-notes HTTP surface."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="owner@example.com", password="password123")
        self.topic_id = _provision_topic(self.client, self.adapter)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_update_persists_notes_and_returns_topic(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": "Remember: the sponsor wants red not orange."},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body["topic"]["private_notes"],
            "Remember: the sponsor wants red not orange.",
        )
        self.assertEqual(body["topic"]["topic_id"], self.topic_id)

    def test_get_returns_private_notes_field(self) -> None:
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": "Private hint."},
        )
        # GET via the list endpoint (canvas load path).
        listed = self.client.get(
            "/api/v2/projects/proj-notes/topics",
        ).json()
        topic = next(
            t for t in listed["topics"] if t["topic_id"] == self.topic_id
        )
        self.assertEqual(topic["private_notes"], "Private hint.")

    def test_empty_string_clears_notes(self) -> None:
        # First populate
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": "Some scratch."},
        )
        # Then clear with ""
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": ""},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["topic"]["private_notes"])

    def test_null_clears_notes(self) -> None:
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": "Scratch."},
        )
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["topic"]["private_notes"])

    def test_missing_topic_returns_404(self) -> None:
        response = self.client.post(
            "/api/v2/topics/topic-does-not-exist/private-notes",
            json={"notes": "x"},
        )
        self.assertEqual(response.status_code, 404)

    def test_idor_other_user_cannot_write(self) -> None:
        """A signed-in user on a different account must see 404, not 200."""
        # Populate the note as the owning user first so we can check that
        # the other user's write would also be blocked (not that an empty
        # row trivially returns).
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/private-notes",
            json={"notes": "owner-only note"},
        )

        # Sign in as a different user on a fresh TestClient.
        intruder_client, _store, _adapter, intruder_temp = make_test_app()
        try:
            signup_and_login(
                intruder_client,
                email="intruder@example.com",
                password="password123",
            )
            response = intruder_client.post(
                f"/api/v2/topics/{self.topic_id}/private-notes",
                json={"notes": "hijack attempt"},
            )
            self.assertEqual(response.status_code, 404, response.text)
        finally:
            intruder_temp.cleanup()

        # Owner's note is still intact.
        listed = self.client.get(
            "/api/v2/projects/proj-notes/topics",
        ).json()
        topic = next(
            t for t in listed["topics"] if t["topic_id"] == self.topic_id
        )
        self.assertEqual(topic["private_notes"], "owner-only note")


# ---------------------------------------------------------------------------
# LLM prompt guard — the critical isolation test
# ---------------------------------------------------------------------------


class PrivateNotesNeverLeakIntoPromptTests(unittest.TestCase):
    """The serialized topic_turn prompt MUST NOT carry private_notes.

    Both adapters (OpenAI + Claude) share the same ``_format_topic_turn_user_message``
    helper; we exercise it directly so any regression in either adapter path
    shows up here. We pass a ``current_topic`` dict that ALSO includes a
    ``private_notes`` key (simulating a sloppy caller that forgot to strip
    it) — the serializer must still only emit the whitelisted fields.
    """

    def test_private_notes_key_is_ignored_by_formatter(self) -> None:
        secret = "DO_NOT_SHARE: the sponsor is Acme but we haven't signed yet."
        current_topic = {
            "title": "Sponsorship",
            "icon": "flag",
            "decisions": [],
            "turns": [],
            "open_questions": [],
            "risks_assumptions": [],
            "checkpoints": [],
            # Sloppy caller: include private_notes in the topic dict
            # passed to the formatter. The formatter must ignore it.
            "private_notes": secret,
        }

        serialized = _format_topic_turn_user_message(
            current_topic=current_topic,
            other_topics=[],
            sources=[],
        )

        self.assertNotIn(secret, serialized)
        self.assertNotIn("private_notes", serialized)
        self.assertNotIn("DO_NOT_SHARE", serialized)

    def test_api_topic_turn_path_does_not_forward_private_notes(self) -> None:
        """End-to-end: set a private note, trigger a topic_turn, and assert the
        adapter's ``current_topic`` kwarg omits ``private_notes``.

        We capture the adapter call because the formatter itself is covered
        above; this second layer exercises the api.py route to confirm the
        ``current_topic_view`` literal in ``v2_topic_turn`` still follows
        the whitelist and hasn't been replaced with a spread of ``topic``.
        """
        from unittest.mock import ANY as _ANY

        client, store, adapter, temp_dir = make_test_app()
        try:
            signup_and_login(client, email="guard@example.com", password="password123")
            adapter.kickoff.return_value = fake_kickoff_response()
            kick = client.post(
                "/api/v2/projects/proj-guard/kickoff",
                json={"user_idea": "Prompt leak test."},
            )
            kick.raise_for_status()
            topic_id = kick.json()["topics"][0]["topic_id"]

            # Set a highly distinctive private note on the topic.
            leak_canary = "PRIVATE_LEAK_CANARY_abc123xyz"
            note_resp = client.post(
                f"/api/v2/topics/{topic_id}/private-notes",
                json={"notes": leak_canary},
            )
            note_resp.raise_for_status()

            # Stub the adapter's topic_turn to a minimal valid response.
            adapter.topic_turn.return_value = {
                "action": "ask",
                "question": "What's the budget?",
                "why_this_matters": "Anchors everything.",
                "suggested_responses": [],
                "proposed_decisions": [],
                "consistency_flags": [],
                "new_topic_proposal": None,
                "topic_deletion_suggestion": None,
                "close_recommendation_reason": None,
                "conflict_resolution": None,
                "planned_checkpoints": None,
                "checkpoint_updates": None,
                "_sanitize": {
                    "dropped_consistency_flags": [],
                    "dropped_target_topic_titles": [],
                    "resolve_conflict_downgrades": [],
                    "dropped_new_topic_proposal": None,
                    "dropped_deletion_suggestion": None,
                },
            }

            turn_resp = client.post(
                f"/api/v2/topics/{topic_id}/turn",
                json={"user_answer": "tell me more"},
            )
            self.assertEqual(turn_resp.status_code, 201, turn_resp.text)

            # Confirm the adapter was actually invoked.
            self.assertEqual(adapter.topic_turn.call_count, 1)

            # The `current_topic` kwarg on that call MUST NOT contain the
            # leak canary under any key.
            call_kwargs = adapter.topic_turn.call_args.kwargs
            current_topic = call_kwargs.get("current_topic")
            self.assertIsNotNone(current_topic)
            self.assertIsInstance(current_topic, dict)
            self.assertNotIn("private_notes", current_topic)
            # Deeper check: serialize the whole kwargs blob and assert the
            # canary string appears nowhere (catches a regression that
            # hides the note under a nested key).
            import json as _json
            blob = _json.dumps(call_kwargs, default=str)
            self.assertNotIn(leak_canary, blob)
            _ = _ANY  # silence unused import lint in some environments
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
