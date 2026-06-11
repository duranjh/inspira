"""Regression tests for ``POST /api/v2/topics/{id}/update`` status whitelisting.

Background: ``TopicUpdateBody.status`` used to be typed ``str | None`` with
only a 40-char cap, so a client could POST ``{"status": "anything"}`` and
the value would be written through to the ``topics.status`` column. QA
flagged this because the frontend only knows how to render three values
(``empty`` / ``in_progress`` / ``fleshed_out``) and rows with other
statuses would silently break the canvas.

These tests lock in the new contract:

- Each of the three valid statuses is accepted and persisted.
- Null ``status`` is accepted as "no change" and leaves the row's status
  untouched (other fields on the same body still apply).
- An unknown status returns ``400 {"error": "invalid_status", "allowed": [...]}``
  and DOES NOT mutate the row — neither the bogus status nor any other
  field on the same body lands.

The 400 shape matters: the frontend renders the ``allowed`` list verbatim
when surfacing the error, so the contract is part of the public API.
"""
from __future__ import annotations

import unittest

try:
    from ._helpers import fake_kickoff_response, make_test_app, signup_and_login
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )


class TopicStatusValidationTests(unittest.TestCase):
    """HTTP surface: POST /api/v2/topics/{id}/update with {status: ...}."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="status@example.com", password="password123",
        )
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.project_id = "proj-status"
        kickoff = self.client.post(
            f"/api/v2/projects/{self.project_id}/kickoff",
            json={"user_idea": "Status enum regression."},
        )
        kickoff.raise_for_status()
        self.topic_id = kickoff.json()["topics"][0]["topic_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------
    # Happy path — each of the three valid statuses
    # -------------------------------------------------------------------

    def test_accepts_status_empty(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "empty"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["topic"]["status"], "empty")

    def test_accepts_status_in_progress(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "in_progress"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["topic"]["status"], "in_progress")

    def test_accepts_status_fleshed_out(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "fleshed_out"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["topic"]["status"], "fleshed_out")

    # -------------------------------------------------------------------
    # Null accepted — "no change"
    # -------------------------------------------------------------------

    def test_null_status_is_accepted_and_leaves_row_unchanged(self) -> None:
        # First move to a known state so we can detect a silent overwrite.
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "in_progress"},
        )

        # POST with status=null plus another legitimate field. The other
        # field should apply; the status should stay "in_progress".
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": None, "position_x": 420.0},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["topic"]
        self.assertEqual(body["status"], "in_progress")
        self.assertEqual(body["position_x"], 420.0)

    def test_null_status_alone_is_accepted(self) -> None:
        # First set status so we can prove a null POST leaves it alone.
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "fleshed_out"},
        )
        # Body with ONLY status=null collapses to "no updates" (null is
        # filtered by ``exclude_none=True``). That's a 400 "no valid
        # fields to update" — but the important thing is it's NOT a 4xx
        # from the status whitelist, i.e. null alone doesn't surface as
        # invalid_status.
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": None},
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json().get("detail")
        self.assertNotEqual(
            (detail or {}).get("error") if isinstance(detail, dict) else None,
            "invalid_status",
        )
        # And the status on disk is still fleshed_out.
        topics = self.store.list_topics(project_id=self.project_id)
        row = next(t for t in topics if t["topic_id"] == self.topic_id)
        self.assertEqual(row["status"], "fleshed_out")

    # -------------------------------------------------------------------
    # Invalid → 400 with the precise shape the frontend expects
    # -------------------------------------------------------------------

    def test_unknown_status_returns_400_invalid_status(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "anything"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error"], "invalid_status")
        self.assertEqual(
            sorted(detail["allowed"]),
            ["empty", "fleshed_out", "in_progress"],
        )

    def test_unknown_status_does_not_mutate_row(self) -> None:
        # Start from a known state.
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "empty"},
        )

        # Attempt to sneak a bad status AND a legitimate-looking
        # position_x update through the same body. The whole request
        # must be rejected — neither field should land.
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "done", "position_x": 999.0},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_status")

        topics = self.store.list_topics(project_id=self.project_id)
        row = next(t for t in topics if t["topic_id"] == self.topic_id)
        self.assertEqual(row["status"], "empty")
        # position_x was whatever kickoff seeded, not 999.
        self.assertNotEqual(row["position_x"], 999.0)

    def test_empty_string_status_is_rejected(self) -> None:
        """Empty string is NOT one of the three allowed tokens."""
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_status")

    def test_case_variant_status_is_rejected(self) -> None:
        """Case-sensitive match — ``IN_PROGRESS`` is not ``in_progress``."""
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/update",
            json={"status": "IN_PROGRESS"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_status")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
