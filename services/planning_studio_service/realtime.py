"""Real-time collaboration backend — presence, cursors, topic locks,
and the WebSocket endpoint that ties them all together.

This is a single-user-at-a-time app in terms of authentication, but
multiple authed users can be present on the same project canvas
simultaneously. This module keeps ephemeral per-project state (who's
connected, where their cursor is, who holds the Q&A lock on a topic)
and broadcasts changes to every connected peer.

Fan-out across Fly's two-machine deployment happens via Postgres
LISTEN/NOTIFY — every broadcast writes to ``pg_notify`` AND to local
asyncio queues; each machine listens on a dedicated connection and
re-fans messages from OTHER machines onto its own local queues. The
``origin`` field on every envelope lets a machine skip its own
messages to avoid echo. SQLite-backed local dev skips pg_notify
entirely; single-machine only.

Nothing here persists — the moment the server restarts, presence is
gone. That's fine: lock state has a 60s TTL anyway (if the owner
disconnects mid-answer, we don't want to leak a permanent lock).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from .store import PlanningStudioStore

_log = logging.getLogger(__name__)

# 8-color palette used for cursor + avatar rings. Mirrored client-side
# in app/src/features/inspira/realtime.ts — keep in sync.
PALETTE: tuple[str, ...] = (
    "#E8634D",  # rust
    "#5BA77F",  # sage
    "#E6B547",  # gold
    "#5F8FD6",  # indigo
    "#B369C8",  # orchid
    "#E58BB0",  # rose
    "#4FB4B4",  # teal
    "#9C8F3F",  # olive
)

# This machine's id — used to tag outgoing pg_notify envelopes so
# incoming notifies can be filtered to only those from OTHER machines.
# Falls back to a random uuid in local dev.
MACHINE_ID: str = os.environ.get("FLY_MACHINE_ID") or f"local-{uuid.uuid4().hex[:8]}"

# TTL + cadence knobs. Kept small so a disconnected user's lock clears
# quickly and the UI doesn't feel "stuck".
_HEARTBEAT_INTERVAL_S = 15.0
_SESSION_STALE_AFTER_S = 45.0
_LOCK_TTL_AFTER_S = 60.0
_JANITOR_TICK_S = 10.0


@dataclass
class SessionSnapshot:
    """Per-WebSocket presence record. One per connected tab."""

    session_id: str
    user_id: str
    display_name: str
    color: str
    cursor: dict[str, float] | None = None
    viewport: dict[str, float] | None = None
    active_topic_id: str | None = None
    last_seen: float = field(default_factory=time.monotonic)
    following_session_id: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "color": self.color,
            "cursor": self.cursor,
            "viewport": self.viewport,
            "active_topic_id": self.active_topic_id,
            "following_session_id": self.following_session_id,
        }


@dataclass
class LockEntry:
    """Exclusive Q&A lock on a topic. The session_id owns it until it
    closes the drawer, disconnects, or goes silent for > TTL."""

    topic_id: str
    owner_session_id: str
    owner_user_id: str
    owner_color: str
    owner_display_name: str
    acquired_at: float = field(default_factory=time.monotonic)

    def to_public(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "owner_session_id": self.owner_session_id,
            "owner_user_id": self.owner_user_id,
            "owner_color": self.owner_color,
            "owner_display_name": self.owner_display_name,
        }


@dataclass
class ProjectState:
    """All the state for one project's connected users."""

    project_id: str
    sessions: dict[str, SessionSnapshot] = field(default_factory=dict)
    # topic_id → LockEntry
    topic_locks: dict[str, LockEntry] = field(default_factory=dict)
    # Local outgoing queues — one per connected WS. The WS writer coroutine
    # reads from its own queue and forwards to the socket.
    local_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    # Map session_id → its queue so we can push to ONE specific session
    # (used for lock_denied + contradiction events).
    queue_by_session: dict[str, asyncio.Queue[dict[str, Any]]] = field(default_factory=dict)
    janitor_task: asyncio.Task | None = None
    listen_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Module-level state. One process, many projects.
_project_state: dict[str, ProjectState] = {}
_global_lock: asyncio.Lock = asyncio.Lock()


async def _get_or_create_project_state(project_id: str) -> ProjectState:
    async with _global_lock:
        state = _project_state.get(project_id)
        if state is None:
            state = ProjectState(project_id=project_id)
            _project_state[project_id] = state
        return state


# ---------------------------------------------------------------------------
# pg_notify wiring (cross-machine fanout)
# ---------------------------------------------------------------------------


def _channel_name(project_id: str) -> str:
    """Postgres channel name — stripped to [a-z0-9_] and prefixed so we
    can't accidentally collide with other app channels. Postgres allows
    up to 63 chars for unquoted identifiers."""
    safe = re.sub(r"[^a-z0-9]", "", project_id.lower())[:50]
    return f"inspira_rt_{safe}"


class _NotifyChannel:
    """Process-wide Postgres NOTIFY emitter.

    Before PR 1: every broadcast opened a fresh psycopg connection to
    Neon (~30/sec/user at cursor throttle) which pegged the connection
    pool and caused visible instability. Now: one long-lived autocommit
    connection is reused across all NOTIFY calls, serialized by an
    asyncio.Lock (NOTIFY is microsecond-fast, so contention is a
    non-issue). On any error we close the connection; the next call
    lazy-reconnects.

    The connection is lazy so module import doesn't touch the network,
    and so SQLite-only dev stays cleanly a no-op path (callers check
    ``store._is_postgres`` before reaching this class).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conn: Any = None

    async def notify(self, db_url: str, channel: str, payload: str) -> None:
        """Send one NOTIFY. Best-effort: logged and swallowed on failure."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._send_sync, db_url, channel, payload)
                return
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "pg_notify failed on %s: %s — reconnecting and retrying once",
                    channel, exc,
                )
                self._close()
            # One-shot retry after reconnect. Still best-effort; local
            # fanout already ran so users on THIS machine see the update.
            try:
                await asyncio.to_thread(self._send_sync, db_url, channel, payload)
            except Exception as exc:  # noqa: BLE001
                _log.warning("pg_notify retry failed on %s: %s", channel, exc)
                self._close()

    def _send_sync(self, db_url: str, channel: str, payload: str) -> None:
        """Invoked on a thread — the per-call path after the lock is held."""
        import psycopg  # type: ignore[import]
        if self._conn is None or getattr(self._conn, "closed", True):
            self._conn = psycopg.connect(db_url, autocommit=True)
        self._conn.execute("SELECT pg_notify(%s, %s)", (channel, payload))

    def _close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    async def aclose(self) -> None:
        """Close at app shutdown. Called from FastAPI lifespan."""
        async with self._lock:
            self._close()


_notify_channel: _NotifyChannel | None = None


def _get_notify_channel() -> _NotifyChannel:
    global _notify_channel
    if _notify_channel is None:
        _notify_channel = _NotifyChannel()
    return _notify_channel


async def shutdown_notify_channel() -> None:
    """Close the process-wide NOTIFY connection. Wired into lifespan."""
    global _notify_channel
    if _notify_channel is not None:
        await _notify_channel.aclose()
        _notify_channel = None


async def _pg_notify(store: "PlanningStudioStore", project_id: str, msg: dict[str, Any]) -> None:
    """Send a NOTIFY via the pooled channel. SQLite dev: no-op."""
    if not getattr(store, "_is_postgres", False):
        return
    envelope = {"origin": MACHINE_ID, "msg": msg}
    payload = json.dumps(envelope)
    channel = _channel_name(project_id)
    await _get_notify_channel().notify(store.config.database_url, channel, payload)


async def _listen_loop(store: "PlanningStudioStore", project_id: str) -> None:
    """Long-lived LISTEN coroutine. Reads notifies from OTHER machines
    and fans them to local queues. Uses psycopg3's async interface so
    cancellation is responsive and a dropped connection auto-reconnects
    with exponential backoff. Runs until cancelled.
    """
    if not getattr(store, "_is_postgres", False):
        return
    channel = _channel_name(project_id)
    state = _project_state.get(project_id)
    if state is None:
        return

    # Channel name is sanitized to [a-z0-9_] in _channel_name; safe to
    # interpolate. Postgres disallows parameter binding on LISTEN.
    listen_sql = f"LISTEN {channel}"
    # Neon recommends the direct URL for LISTEN — pooled connections
    # can silently drop subscriptions when the pooler cycles backends.
    db_url = os.environ.get("INSPIRA_DATABASE_DIRECT_URL") or store.config.database_url

    backoff_s = 1.0
    while True:
        try:
            import psycopg  # type: ignore[import]
            async with await psycopg.AsyncConnection.connect(
                db_url, autocommit=True,
            ) as aconn:
                await aconn.execute(listen_sql)
                _log.info("LISTEN started on %s", channel)
                backoff_s = 1.0
                async for notify in aconn.notifies():
                    try:
                        envelope = json.loads(notify.payload)
                    except Exception:  # noqa: BLE001
                        continue
                    if envelope.get("origin") == MACHINE_ID:
                        continue  # skip our own
                    msg = envelope.get("msg")
                    if not isinstance(msg, dict):
                        continue
                    async with state.lock:
                        queues = list(state.local_queues)
                    for q in queues:
                        try:
                            q.put_nowait(msg)
                        except asyncio.QueueFull:
                            pass
        except asyncio.CancelledError:
            _log.info("LISTEN stopped on %s (cancelled)", channel)
            raise
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "LISTEN %s failed: %s — reconnecting in %.1fs",
                channel, exc, backoff_s,
            )
            try:
                await asyncio.sleep(backoff_s)
            except asyncio.CancelledError:
                raise
            backoff_s = min(backoff_s * 2.0, 30.0)


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------


async def _broadcast_local(state: ProjectState, msg: dict[str, Any],
                           exclude_session_id: str | None = None) -> None:
    """Write msg to every local queue except ``exclude_session_id``."""
    async with state.lock:
        items = list(state.queue_by_session.items())
    for sid, q in items:
        if sid == exclude_session_id:
            continue
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


async def _broadcast(store: "PlanningStudioStore", state: ProjectState,
                     msg: dict[str, Any],
                     exclude_session_id: str | None = None,
                     cross_machine: bool = True) -> None:
    """Send to local sessions; optionally fan via pg_notify.

    ``cross_machine=False`` skips the NOTIFY round-trip — use this for
    high-frequency ephemera (cursor, viewport) where a peer on a sibling
    Fly machine being 200ms stale is invisible, but keeping the NOTIFY
    rate up would peg the Neon connection pool. Sticky events
    (peer_join, peer_leave, lock_state, contradiction, peer_following)
    keep the default so reconnecting peers on a sibling machine see a
    consistent world.
    """
    await _broadcast_local(state, msg, exclude_session_id=exclude_session_id)
    if cross_machine:
        await _pg_notify(store, state.project_id, msg)


async def _push_to_session(state: ProjectState, session_id: str,
                           msg: dict[str, Any]) -> None:
    """Target one specific local session (lock_denied, contradiction)."""
    q = state.queue_by_session.get(session_id)
    if q is None:
        return
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:
        pass


async def push_to_user_sessions(store: "PlanningStudioStore", project_id: str,
                                user_id: str, msg: dict[str, Any]) -> None:
    """Public: push to all sessions belonging to a specific user on a project.

    Used by non-WS code (e.g., the decision-create route after it detects
    a contradiction) to nudge the user's own tabs. Any machine can call
    this; we local-fan AND pg_notify so the user's tab on a sibling
    machine also gets the nudge.
    """
    state = _project_state.get(project_id)
    if state is not None:
        async with state.lock:
            matches = [
                sid for sid, sess in state.sessions.items()
                if sess.user_id == user_id
            ]
        for sid in matches:
            await _push_to_session(state, sid, msg)
    # Cross-machine — envelope with a target_user_id marker so remote
    # listeners know to push to only that user's sessions.
    await _pg_notify(store, project_id, {**msg, "_target_user_id": user_id})


# ---------------------------------------------------------------------------
# Lock state transitions
# ---------------------------------------------------------------------------


async def _acquire_topic_lock(
    state: ProjectState, session: SessionSnapshot, topic_id: str,
    store: "PlanningStudioStore",
) -> bool:
    """Try to claim the lock on a topic. Returns True if granted."""
    async with state.lock:
        existing = state.topic_locks.get(topic_id)
        now = time.monotonic()
        if existing is not None:
            # Already held — grant only if it's us, OR the owner is stale.
            if existing.owner_session_id == session.session_id:
                existing.acquired_at = now
                return True
            owner_sess = state.sessions.get(existing.owner_session_id)
            owner_alive = (
                owner_sess is not None
                and (now - owner_sess.last_seen) <= _LOCK_TTL_AFTER_S
            )
            if owner_alive:
                return False
            # Stale — release + fall through to take it ourselves.
            state.topic_locks.pop(topic_id, None)
        entry = LockEntry(
            topic_id=topic_id,
            owner_session_id=session.session_id,
            owner_user_id=session.user_id,
            owner_color=session.color,
            owner_display_name=session.display_name,
            acquired_at=now,
        )
        state.topic_locks[topic_id] = entry
        session.active_topic_id = topic_id
    await _broadcast(store, state, {"t": "lock_state", **entry.to_public()})
    return True


async def _release_topic_lock(
    state: ProjectState, session: SessionSnapshot, topic_id: str,
    store: "PlanningStudioStore",
) -> None:
    async with state.lock:
        existing = state.topic_locks.get(topic_id)
        if existing is None:
            return
        if existing.owner_session_id != session.session_id:
            # Not our lock — no-op. Avoids a misbehaving client stealing.
            return
        state.topic_locks.pop(topic_id, None)
        if session.active_topic_id == topic_id:
            session.active_topic_id = None
    await _broadcast(store, state, {
        "t": "lock_state",
        "topic_id": topic_id,
        "owner_session_id": None,
        "owner_user_id": None,
        "owner_color": None,
        "owner_display_name": None,
    })


async def _release_all_locks_for(
    state: ProjectState, session_id: str, store: "PlanningStudioStore",
) -> None:
    """Called on disconnect / janitor sweep."""
    to_release: list[str] = []
    async with state.lock:
        for topic_id, entry in list(state.topic_locks.items()):
            if entry.owner_session_id == session_id:
                state.topic_locks.pop(topic_id, None)
                to_release.append(topic_id)
    for topic_id in to_release:
        await _broadcast(store, state, {
            "t": "lock_state", "topic_id": topic_id,
            "owner_session_id": None, "owner_user_id": None,
            "owner_color": None, "owner_display_name": None,
        })


# ---------------------------------------------------------------------------
# Janitor — sweeps stale sessions / expired locks
# ---------------------------------------------------------------------------


async def _janitor(state: ProjectState, store: "PlanningStudioStore") -> None:
    try:
        while True:
            await asyncio.sleep(_JANITOR_TICK_S)
            now = time.monotonic()
            stale_sids: list[str] = []
            async with state.lock:
                for sid, sess in list(state.sessions.items()):
                    if (now - sess.last_seen) > _SESSION_STALE_AFTER_S:
                        stale_sids.append(sid)
            for sid in stale_sids:
                await _release_all_locks_for(state, sid, store)
                async with state.lock:
                    state.sessions.pop(sid, None)
                    q = state.queue_by_session.pop(sid, None)
                    if q is not None and q in state.local_queues:
                        state.local_queues.remove(q)
                await _broadcast(store, state, {"t": "peer_leave", "session_id": sid})
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        _log.error("janitor crashed for %s: %s", state.project_id, exc)


# ---------------------------------------------------------------------------
# Verify project ownership — reuse the store's IDOR guard
# ---------------------------------------------------------------------------


def _verify_project_membership(
    store: "PlanningStudioStore", project_id: str, user_id: str,
) -> bool:
    """Return True if user can connect to this project's realtime
    channel. Reuses the store's ownership check; future collab rosters
    plug in here.
    """
    try:
        return store.verify_project_ownership(
            project_id=project_id, user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# The main WS handler
# ---------------------------------------------------------------------------


async def handle_ws(
    websocket: WebSocket,
    project_id: str,
    store: "PlanningStudioStore",
) -> None:
    """Upgrade + auth + pump. One coroutine per connected tab."""
    # Import here to avoid a circular import with auth at module load.
    from .auth import SESSION_COOKIE_NAME, _resolve_user, resolve_ws_ticket

    # Auth resolution order:
    #   1. `?auth=<ticket>`  — short-lived WS ticket (preferred path,
    #      browser can't forward httpOnly cookie on cross-subdomain WS).
    #   2. session cookie    — works in same-origin dev setups.
    user = None
    auth_path = "none"
    ticket = websocket.query_params.get("auth")
    if ticket:
        uid_from_ticket = resolve_ws_ticket(ticket)
        # debug-level: every legit WS connect logs this; keep it out of
        # the default INFO stream. Audit 2026-04-25 flagged the prior
        # WARNING level as noise that drowned real signals.
        _log.debug("ws: ticket decoded to uid=%r", uid_from_ticket)
        if uid_from_ticket:
            user = store.get_user_by_id(uid_from_ticket)
            _log.debug("ws: get_user_by_id(%r) => %s",
                       uid_from_ticket, "found" if user else "None")
            if user:
                auth_path = "ticket"
    if user is None:
        cookie_val = websocket.cookies.get(SESSION_COOKIE_NAME)
        try:
            user = _resolve_user(store, cookie_val) if cookie_val else None
            if user:
                auth_path = "cookie"
        except Exception:  # noqa: BLE001
            user = None
    if user is None:
        _log.info("ws: no auth — closing 4401")
        await websocket.close(code=4401)
        return
    user_id = str(user.get("user_id") or "").strip()
    if not user_id:
        await websocket.close(code=4401)
        return

    # Project membership.
    ok = _verify_project_membership(store, project_id, user_id)
    _log.info("ws: auth=%s user=%s project=%s membership=%s",
              auth_path, user_id, project_id, ok)
    if not ok:
        await websocket.close(code=4403)
        return

    await websocket.accept()

    session_id = f"sid-{uuid.uuid4().hex[:10]}"
    color = random.choice(PALETTE)
    display_name = (
        (user.get("display_name") or "").strip()
        or (user.get("email") or "").split("@")[0]
        or "Guest"
    )
    session = SessionSnapshot(
        session_id=session_id, user_id=user_id,
        display_name=display_name, color=color,
    )

    state = await _get_or_create_project_state(project_id)
    out_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)

    async with state.lock:
        state.sessions[session_id] = session
        state.local_queues.append(out_queue)
        state.queue_by_session[session_id] = out_queue
        if state.janitor_task is None or state.janitor_task.done():
            state.janitor_task = asyncio.create_task(_janitor(state, store))
        if state.listen_task is None or state.listen_task.done():
            state.listen_task = asyncio.create_task(_listen_loop(store, project_id))

    # Hello — tell the new client its session + a snapshot of peers/locks.
    peers_snapshot = [s.to_public() for s in state.sessions.values() if s.session_id != session_id]
    locks_snapshot = {lid: l.to_public() for lid, l in state.topic_locks.items()}
    await websocket.send_json({
        "t": "hello",
        "session_id": session_id,
        "color": color,
        "display_name": display_name,
        "user_id": user_id,
        "peers": peers_snapshot,
        "locks": locks_snapshot,
    })

    # Announce the join to peers.
    await _broadcast(store, state, {"t": "peer_join", "session": session.to_public()},
                     exclude_session_id=session_id)

    async def _writer() -> None:
        """Pump queue → websocket. Also emits ping heartbeats."""
        last_ping = time.monotonic()
        while True:
            try:
                timeout = max(0.1, _HEARTBEAT_INTERVAL_S - (time.monotonic() - last_ping))
                msg = await asyncio.wait_for(out_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                await websocket.send_json({"t": "ping"})
                last_ping = time.monotonic()
                continue
            # Filter out messages targeted at a different user (from pg_notify).
            target_uid = msg.get("_target_user_id")
            if target_uid and target_uid != user_id:
                continue
            # Strip the internal marker before sending to client.
            if target_uid:
                msg = {k: v for k, v in msg.items() if k != "_target_user_id"}
            await websocket.send_json(msg)

    writer_task = asyncio.create_task(_writer())

    try:
        while True:
            raw = await websocket.receive_json()
            if not isinstance(raw, dict):
                continue
            t = raw.get("t")
            session.last_seen = time.monotonic()
            if t == "cursor":
                x = float(raw.get("x", 0.0))
                y = float(raw.get("y", 0.0))
                session.cursor = {"x": x, "y": y}
                # cross_machine=False: cursor frames are ephemeral (30/s
                # throttled). Paying the Postgres NOTIFY round-trip on
                # every one was what caused the prod instability.
                await _broadcast(
                    store, state,
                    {"t": "peer_cursor", "session_id": session_id, "x": x, "y": y},
                    exclude_session_id=session_id,
                    cross_machine=False,
                )
            elif t == "viewport":
                vp = {
                    "x": float(raw.get("x", 0.0)),
                    "y": float(raw.get("y", 0.0)),
                    "zoom": float(raw.get("zoom", 1.0)),
                }
                session.viewport = vp
                # cross_machine=False: see cursor rationale above.
                await _broadcast(
                    store, state,
                    {"t": "peer_viewport", "session_id": session_id, **vp},
                    exclude_session_id=session_id,
                    cross_machine=False,
                )
            elif t == "focus_topic":
                topic_id = raw.get("topic_id")
                if topic_id is None:
                    prev = session.active_topic_id
                    if prev is not None:
                        await _release_topic_lock(state, session, prev, store)
                elif isinstance(topic_id, str) and topic_id:
                    granted = await _acquire_topic_lock(state, session, topic_id, store)
                    if not granted:
                        existing = state.topic_locks.get(topic_id)
                        await _push_to_session(state, session_id, {
                            "t": "lock_denied", "topic_id": topic_id,
                            "owner_display_name": existing.owner_display_name if existing else "",
                            "owner_color": existing.owner_color if existing else "",
                        })
            elif t == "follow":
                target = raw.get("target_session_id")
                session.following_session_id = target if isinstance(target, str) else None
                await _broadcast(
                    store, state,
                    {"t": "peer_following", "session_id": session_id,
                     "following_session_id": session.following_session_id},
                    exclude_session_id=session_id,
                )
            elif t == "pong":
                # heartbeat — last_seen already bumped above.
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        _log.warning("ws error for session %s: %s", session_id, exc)
    finally:
        writer_task.cancel()
        try:
            await writer_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        await _release_all_locks_for(state, session_id, store)
        async with state.lock:
            state.sessions.pop(session_id, None)
            q = state.queue_by_session.pop(session_id, None)
            if q is not None and q in state.local_queues:
                state.local_queues.remove(q)
            empty = not state.sessions
        await _broadcast(store, state, {"t": "peer_leave", "session_id": session_id})
        # Last one out: cancel per-project background tasks.
        if empty:
            async with _global_lock:
                if state.janitor_task is not None:
                    state.janitor_task.cancel()
                    state.janitor_task = None
                if state.listen_task is not None:
                    state.listen_task.cancel()
                    state.listen_task = None
                _project_state.pop(project_id, None)
