"""Tests for per-topic color coding.

Coverage:

- Update flow: POST /api/v2/topics/{id}/color persists the color slug and
  returns the updated topic row with ``color`` as a top-level field.
- Invalid slug rejection: a color outside the five-slug allowlist returns
  400 and does NOT mutate the row.
- Null clears: ``color=null`` drops the color key from metadata and the
  returned topic has ``color=None``.
- IDOR: a different user cannot read or overwrite a topic's color
  (returns 404, same shape as missing topic).
- Color surfaces through the list endpoint so the canvas load path sees
  it (not just the single-topic update response).

The 5-slug allowlist is ``sage``, ``rust``, ``gold``, ``ink``, ``paper``
— pulled from the existing theme vars in ``App.css``. The slug is stored
under ``metadata_json["color"]`` (no schema migration) and surfaced as
``topic["color"]`` by the store's ``_with_topic_color`` helper.
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


# ---------------------------------------------------------------------------
# Test fixture helper
# ---------------------------------------------------------------------------


def _provision_topic(client: Any, adapter: Any) -> str:
    """Run kickoff so a real project + topics exist. Return the first topic id."""
    adapter.kickoff.return_value = fake_kickoff_response()
    response = client.post(
        "/api/v2/projects/proj-color/kickoff",
        json={"user_idea": "Coloring test."},
    )
    response.raise_for_status()
    payload = response.json()
    return payload["topics"][0]["topic_id"]


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------


class TopicColorEndpointTests(unittest.TestCase):
    """POST /api/v2/topics/{id}/color HTTP surface."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="owner@example.com", password="password123")
        self.topic_id = _provision_topic(self.client, self.adapter)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_update_persists_color_and_returns_topic(self) -> None:
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": "sage"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["topic"]["topic_id"], self.topic_id)
        self.assertEqual(body["topic"]["color"], "sage")
        # Color also lands in metadata so downstream readers that inspect
        # the raw blob still see it.
        self.assertEqual(body["topic"]["metadata"].get("color"), "sage")

    def test_all_five_allowlisted_colors_accepted(self) -> None:
        for color in ("sage", "rust", "gold", "ink", "paper"):
            response = self.client.post(
                f"/api/v2/topics/{self.topic_id}/color",
                json={"color": color},
            )
            self.assertEqual(response.status_code, 200, f"{color}: {response.text}")
            self.assertEqual(response.json()["topic"]["color"], color)

    def test_invalid_color_returns_400(self) -> None:
        # First, set a known-good color so we can assert the bad update
        # did not overwrite it.
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": "gold"},
        )
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": "fuchsia"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        # The previous color is untouched.
        listed = self.client.get(
            "/api/v2/projects/proj-color/topics",
        ).json()
        topic = next(
            t for t in listed["topics"] if t["topic_id"] == self.topic_id
        )
        self.assertEqual(topic["color"], "gold")

    def test_null_clears_color(self) -> None:
        # Populate first.
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": "rust"},
        )
        response = self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["topic"]["color"])
        # And the metadata key is gone (not just set to null).
        self.assertNotIn("color", response.json()["topic"]["metadata"])

    def test_missing_topic_returns_404(self) -> None:
        response = self.client.post(
            "/api/v2/topics/topic-does-not-exist/color",
            json={"color": "sage"},
        )
        self.assertEqual(response.status_code, 404)

    def test_idor_other_user_cannot_write(self) -> None:
        """A signed-in user on a different account must see 404, not 200."""
        # Populate first as the owner.
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": "ink"},
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
                f"/api/v2/topics/{self.topic_id}/color",
                json={"color": "rust"},
            )
            self.assertEqual(response.status_code, 404, response.text)
        finally:
            intruder_temp.cleanup()

        # Owner's color is still intact.
        listed = self.client.get(
            "/api/v2/projects/proj-color/topics",
        ).json()
        topic = next(
            t for t in listed["topics"] if t["topic_id"] == self.topic_id
        )
        self.assertEqual(topic["color"], "ink")

    def test_list_topics_surfaces_color_field(self) -> None:
        """The canvas load path (`/api/v2/projects/{id}/topics`) must carry
        ``color`` on every topic in the list — without it the TopicNode
        can't render the visual tag."""
        self.client.post(
            f"/api/v2/topics/{self.topic_id}/color",
            json={"color": "paper"},
        )
        listed = self.client.get(
            "/api/v2/projects/proj-color/topics",
        ).json()
        # Every topic in the list has the ``color`` key (even topics that
        # were never tagged — they should carry ``None``).
        for topic in listed["topics"]:
            self.assertIn("color", topic)
        tagged = next(
            t for t in listed["topics"] if t["topic_id"] == self.topic_id
        )
        self.assertEqual(tagged["color"], "paper")
        untagged = next(
            t for t in listed["topics"] if t["topic_id"] != self.topic_id
        )
        self.assertIsNone(untagged["color"])


if __name__ == "__main__":
    unittest.main()
