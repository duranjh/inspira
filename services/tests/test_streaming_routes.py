"""Tests for the Phase 1 SSE streaming routes.

Covers ``POST /api/v2/projects/{id}/kickoff/stream`` and
``POST /api/v2/topics/{id}/turn/stream``. The non-streaming originals
have their own dedicated coverage in ``test_api_fastapi.py`` /
``test_topic_turn.py`` — these tests focus on the streaming-specific
shape of the response: heartbeat-first ordering, complete event payload
matching the original envelope, error handling without crashing the
server, and the credit-refund path on non-planner exceptions.

Test transport notes:
- We toggle ``INSPIRA_ENABLE_STREAM_KICKOFF=1`` for the duration of these
  tests because the routes default to 503 when the flag is off (staged
  rollout). A separate test flips the flag back off and confirms the
  503 short-circuit.
- Starlette's ``TestClient`` (httpx wrapper) doesn't expose a streaming
  iterator by default. We use ``client.stream("POST", ...)`` and read
  ``response.iter_text()`` to assemble the full body — sufficient for
  asserting frame ordering since the test client buffers locally.
"""
from __future__ import annotations

import json
import os
import unittest

from ._helpers import (
    fake_kickoff_response,
    fake_turn_response,
    make_test_app,
    signup_and_login,
)


def _parse_sse_frames(body: str) -> list[tuple[str, dict]]:
    """Split an SSE response body into (event_name, json_payload) pairs."""
    frames: list[tuple[str, dict]] = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in raw.split("\n"):
            if not line or line.startswith(":"):
                continue
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)
        if data_lines:
            frames.append((event, json.loads("\n".join(data_lines))))
    return frames


class _StreamingFlagOnMixin:
    """Flip on the streaming feature flag for the duration of the test."""

    @classmethod
    def setUpClass(cls) -> None:  # type: ignore[override]
        cls._prev_flag = os.environ.get("INSPIRA_ENABLE_STREAM_KICKOFF")
        os.environ["INSPIRA_ENABLE_STREAM_KICKOFF"] = "1"

    @classmethod
    def tearDownClass(cls) -> None:  # type: ignore[override]
        if cls._prev_flag is None:
            os.environ.pop("INSPIRA_ENABLE_STREAM_KICKOFF", None)
        else:
            os.environ["INSPIRA_ENABLE_STREAM_KICKOFF"] = cls._prev_flag


class V2KickoffStreamTests(_StreamingFlagOnMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="kstream@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_kickoff_stream_emits_heartbeat_then_complete(self) -> None:
        self.adapter.kickoff.return_value = fake_kickoff_response()
        with self.client.stream(
            "POST",
            "/api/v2/projects/proj-stream-1/kickoff/stream",
            json={"user_idea": "A small outdoor wine festival."},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            # Critical anti-buffering header for Fly.io's HTTP/2 proxy.
            self.assertEqual(response.headers.get("x-accel-buffering"), "no")
            body = "".join(response.iter_text())

        frames = _parse_sse_frames(body)
        self.assertGreaterEqual(len(frames), 2)
        # Heartbeat must arrive first — the entire point of Phase 1.
        self.assertEqual(frames[0][0], "heartbeat")
        self.assertEqual(frames[0][1]["status"], "thinking")
        # Complete event has the same envelope shape as the non-streaming
        # /kickoff route.
        complete = next(f for f in frames if f[0] == "complete")
        envelope = complete[1]
        self.assertIn("kickoff", envelope)
        self.assertIn("topics", envelope)
        self.assertIn("relationships", envelope)
        self.assertEqual(len(envelope["topics"]), 5)
        self.assertEqual(len(envelope["relationships"]), 2)

    def test_kickoff_stream_4xx_returns_plain_json(self) -> None:
        # Empty user_idea triggers the same 400 as the non-streaming route.
        # The stream must NOT start — pre-call gates fire before any
        # heartbeat is yielded.
        response = self.client.post(
            "/api/v2/projects/proj-stream-2/kickoff/stream",
            json={"user_idea": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("text/event-stream", response.headers["content-type"])

    def test_kickoff_stream_adapter_failure_emits_error_event(self) -> None:
        # Simulate a non-planner exception inside adapter.kickoff. The
        # route must yield an `error` event without crashing or 500'ing.
        self.adapter.kickoff.side_effect = ValueError("boom")
        with self.client.stream(
            "POST",
            "/api/v2/projects/proj-stream-3/kickoff/stream",
            json={"user_idea": "Something."},
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())

        frames = _parse_sse_frames(body)
        names = [f[0] for f in frames]
        self.assertIn("heartbeat", names)
        self.assertIn("error", names)
        # No `complete` should be emitted on failure.
        self.assertNotIn("complete", names)
        err_frame = next(f for f in frames if f[0] == "error")
        self.assertEqual(err_frame[1]["code"], "planner_error")

    def test_kickoff_stream_disabled_returns_503(self) -> None:
        os.environ.pop("INSPIRA_ENABLE_STREAM_KICKOFF", None)
        try:
            response = self.client.post(
                "/api/v2/projects/proj-stream-4/kickoff/stream",
                json={"user_idea": "x"},
            )
            self.assertEqual(response.status_code, 503)
            payload = response.json()
            self.assertEqual(payload["detail"]["error"], "streaming_disabled")
        finally:
            os.environ["INSPIRA_ENABLE_STREAM_KICKOFF"] = "1"


class V2TopicTurnStreamTests(_StreamingFlagOnMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client, email="tstream@example.com", password="password123",
        )
        self.adapter.kickoff.return_value = fake_kickoff_response()
        # Bootstrap a project + topics so we have a topic_id to turn on.
        kick = self.client.post(
            "/api/v2/projects/proj-tstream/kickoff",
            json={"user_idea": "A team offsite."},
        )
        self.assertEqual(kick.status_code, 201)
        self.topic_id = kick.json()["topics"][0]["topic_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_turn_stream_emits_heartbeat_then_complete(self) -> None:
        self.adapter.topic_turn.return_value = fake_turn_response()
        with self.client.stream(
            "POST",
            f"/api/v2/topics/{self.topic_id}/turn/stream",
            json={"user_answer": "Off-site at the lake."},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            body = "".join(response.iter_text())

        frames = _parse_sse_frames(body)
        self.assertGreaterEqual(len(frames), 2)
        self.assertEqual(frames[0][0], "heartbeat")
        complete = next(f for f in frames if f[0] == "complete")
        envelope = complete[1]
        # Same envelope shape as the non-streaming /turn route.
        self.assertIn("turn_result", envelope)
        self.assertIn("planner_turn", envelope)
        self.assertIn("rerouted_decisions", envelope)
        self.assertIn("checkpoints", envelope)

    def test_turn_stream_4xx_for_unknown_topic(self) -> None:
        # Unknown topic returns 404 BEFORE the stream starts.
        response = self.client.post(
            "/api/v2/topics/does-not-exist/turn/stream",
            json={"user_answer": ""},
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("text/event-stream", response.headers["content-type"])

    def test_turn_stream_emits_error_frame_on_non_planner_failure(self) -> None:
        # PR 2 deleted credits, so the original "credits get refunded"
        # contract is gone. The remaining contract: a non-planner
        # exception inside the stream surfaces an error frame and
        # never emits a complete frame.
        self.adapter.topic_turn.side_effect = ValueError("oops")
        with self.client.stream(
            "POST",
            f"/api/v2/topics/{self.topic_id}/turn/stream",
            json={"user_answer": "kaboom"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())

        frames = _parse_sse_frames(body)
        self.assertEqual(frames[0][0], "heartbeat")
        self.assertTrue(any(f[0] == "error" for f in frames))
        self.assertFalse(any(f[0] == "complete" for f in frames))


if __name__ == "__main__":
    unittest.main()
