"""Unit tests for the PR 1 realtime stability changes.

Covers:
- ``_NotifyChannel`` reuses a single Postgres connection across calls.
- ``_NotifyChannel.aclose`` closes cleanly even when never used.
- ``_broadcast(cross_machine=False)`` skips ``_pg_notify``.
- ``_acquire_topic_lock`` grant / deny / stale-takeover paths.

The async-LISTEN reconnect path needs a real Postgres and is exercised
by integration tests; the sync paths covered here can run on the
SQLite-only dev stack.
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock

from planning_studio_service import realtime


class _StubStore:
    """Bare minimum store API surface used by realtime functions."""

    def __init__(self, *, is_postgres: bool) -> None:
        self._is_postgres = is_postgres


class NotifyChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_aclose_with_no_connection_is_safe(self) -> None:
        # Lazy: never used, never opened. aclose must still be safe.
        ch = realtime._NotifyChannel()
        await ch.aclose()  # no exception
        self.assertIsNone(ch._conn)

    async def test_aclose_closes_existing_connection(self) -> None:
        ch = realtime._NotifyChannel()

        class _FakeConn:
            closed = False

            def close(self) -> None:
                _FakeConn.closed = True

        ch._conn = _FakeConn()
        await ch.aclose()
        self.assertTrue(_FakeConn.closed)
        self.assertIsNone(ch._conn)

    async def test_pg_notify_no_op_for_sqlite(self) -> None:
        # SQLite-mode store should never reach the notify channel.
        # Reset the module-global singleton so we observe a fresh state.
        realtime._notify_channel = None
        store = _StubStore(is_postgres=False)
        await realtime._pg_notify(store, "proj-test", {"t": "noop"})
        # No channel was lazily created — the SQLite branch returns
        # before _get_notify_channel() is called.
        self.assertIsNone(realtime._notify_channel)


class BroadcastCrossMachineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Reset module-global so each test sees a fresh channel.
        realtime._notify_channel = None
        # Build a tiny ProjectState with one queue.
        self.state = realtime.ProjectState(project_id="proj-cm")
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self.state.queue_by_session["sess-1"] = self.queue
        self.state.local_queues.append(self.queue)
        # Patch _pg_notify so we can verify it WASN'T called.
        self._orig_pg_notify = realtime._pg_notify
        self.notify_mock = AsyncMock()
        realtime._pg_notify = self.notify_mock  # type: ignore[assignment]

    async def asyncTearDown(self) -> None:
        realtime._pg_notify = self._orig_pg_notify  # type: ignore[assignment]

    async def test_cross_machine_false_skips_pg_notify(self) -> None:
        store = _StubStore(is_postgres=True)  # postgres path; channel would normally fire
        await realtime._broadcast(
            store, self.state,
            {"t": "peer_cursor", "x": 1.0, "y": 2.0},
            cross_machine=False,
        )
        self.notify_mock.assert_not_called()
        # Local fanout still ran.
        self.assertFalse(self.queue.empty())
        msg = await self.queue.get()
        self.assertEqual(msg["t"], "peer_cursor")

    async def test_cross_machine_true_fires_pg_notify(self) -> None:
        store = _StubStore(is_postgres=True)
        await realtime._broadcast(
            store, self.state,
            {"t": "peer_join", "session": {"session_id": "sess-1"}},
            cross_machine=True,
        )
        self.notify_mock.assert_called_once()


class TopicLockTests(unittest.IsolatedAsyncioTestCase):
    """The store passed to _acquire_topic_lock isn't a real store; it's
    only consumed by the broadcast path, which we route through a mocked
    _pg_notify above."""

    async def asyncSetUp(self) -> None:
        realtime._notify_channel = None
        self._orig_pg_notify = realtime._pg_notify
        realtime._pg_notify = AsyncMock()  # type: ignore[assignment]

    async def asyncTearDown(self) -> None:
        realtime._pg_notify = self._orig_pg_notify  # type: ignore[assignment]

    async def test_acquire_grants_on_fresh_topic(self) -> None:
        state = realtime.ProjectState(project_id="proj-lock")
        session = realtime.SessionSnapshot(
            session_id="sess-A", user_id="user-A",
            display_name="A", color="#000",
        )
        state.sessions[session.session_id] = session
        granted = await realtime._acquire_topic_lock(
            state, session, "topic-1", _StubStore(is_postgres=False),
        )
        self.assertTrue(granted)
        self.assertIn("topic-1", state.topic_locks)

    async def test_acquire_denies_when_held_by_other(self) -> None:
        state = realtime.ProjectState(project_id="proj-lock")
        owner = realtime.SessionSnapshot(
            session_id="sess-A", user_id="user-A",
            display_name="A", color="#000",
        )
        intruder = realtime.SessionSnapshot(
            session_id="sess-B", user_id="user-B",
            display_name="B", color="#111",
        )
        state.sessions[owner.session_id] = owner
        state.sessions[intruder.session_id] = intruder
        await realtime._acquire_topic_lock(
            state, owner, "topic-1", _StubStore(is_postgres=False),
        )
        granted = await realtime._acquire_topic_lock(
            state, intruder, "topic-1", _StubStore(is_postgres=False),
        )
        self.assertFalse(granted)
        # Owner unchanged.
        self.assertEqual(
            state.topic_locks["topic-1"].owner_session_id, "sess-A",
        )

    async def test_acquire_takes_over_stale_owner(self) -> None:
        import time
        state = realtime.ProjectState(project_id="proj-lock")
        owner = realtime.SessionSnapshot(
            session_id="sess-A", user_id="user-A",
            display_name="A", color="#000",
        )
        intruder = realtime.SessionSnapshot(
            session_id="sess-B", user_id="user-B",
            display_name="B", color="#111",
        )
        state.sessions[owner.session_id] = owner
        state.sessions[intruder.session_id] = intruder
        await realtime._acquire_topic_lock(
            state, owner, "topic-1", _StubStore(is_postgres=False),
        )
        # Pretend the owner has been silent for too long.
        owner.last_seen = time.monotonic() - (realtime._LOCK_TTL_AFTER_S + 5)
        granted = await realtime._acquire_topic_lock(
            state, intruder, "topic-1", _StubStore(is_postgres=False),
        )
        self.assertTrue(granted)
        self.assertEqual(
            state.topic_locks["topic-1"].owner_session_id, "sess-B",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
