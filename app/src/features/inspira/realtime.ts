// Real-time collaboration — WebSocket client hook.
//
// Single source of truth for presence on a canvas: connects to
// /ws/projects/{id}, tracks peers + topic locks + my own color, and
// exposes throttled/debounced send methods for cursor + viewport +
// focus-topic + follow.
//
// The server is authoritative. We never mutate local state based on
// the user's own cursor move — we only render what the server
// broadcasts. (Our cursor is drawn client-side using the local
// pointer event; we just SEND our position to the server so peers
// see us.)
//
// Reconnects with exponential backoff when the socket drops. A single
// React re-render fires per frame at most — the hook coalesces state
// writes via a reducer.

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

// Kept in sync with realtime.py PALETTE.
export const PALETTE: readonly string[] = [
  "#E8634D",
  "#5BA77F",
  "#E6B547",
  "#5F8FD6",
  "#B369C8",
  "#E58BB0",
  "#4FB4B4",
  "#9C8F3F",
];

export type PeerCursor = { x: number; y: number };
export type PeerViewport = { x: number; y: number; zoom: number };

export type PeerSession = {
  sessionId: string;
  userId: string;
  displayName: string;
  color: string;
  cursor?: PeerCursor;
  viewport?: PeerViewport;
  activeTopicId?: string | null;
  followingSessionId?: string | null;
};

export type LockEntry = {
  topicId: string;
  ownerSessionId: string;
  ownerUserId: string;
  ownerColor: string;
  ownerDisplayName: string;
};

export type ContradictionPayload = {
  decisionId: string;
  conflictingDecisionId: string;
  conflictingStatement: string;
  conflictingAuthorDisplayName: string;
  conflictingAuthorColor?: string;
  conflictingCreatedAt: string;
  reason?: string;
};

type RtState = {
  mySessionId: string | null;
  myUserId: string | null;
  myColor: string | null;
  myDisplayName: string | null;
  peers: Record<string, PeerSession>;
  locks: Record<string, LockEntry>;
  contradictionEvent: ContradictionPayload | null;
  lockDenied: { topicId: string; ownerDisplayName: string; ownerColor: string } | null;
  status: "connecting" | "open" | "closed";
};

type RtAction =
  | { type: "connecting" }
  | {
      type: "hello";
      sessionId: string;
      userId: string;
      displayName: string;
      color: string;
      peers: PeerSession[];
      locks: LockEntry[];
    }
  | { type: "peer_join"; session: PeerSession }
  | { type: "peer_leave"; sessionId: string }
  | { type: "peer_cursor"; sessionId: string; cursor: PeerCursor }
  | { type: "peer_viewport"; sessionId: string; viewport: PeerViewport }
  | { type: "peer_following"; sessionId: string; followingSessionId: string | null }
  | { type: "lock_state"; topicId: string; entry: LockEntry | null }
  | {
      type: "lock_denied";
      topicId: string;
      ownerDisplayName: string;
      ownerColor: string;
    }
  | { type: "contradiction"; payload: ContradictionPayload }
  | { type: "clear_contradiction" }
  | { type: "clear_lock_denied" }
  | { type: "closed" };

function rtReducer(state: RtState, action: RtAction): RtState {
  switch (action.type) {
    case "connecting":
      return { ...state, status: "connecting" };
    case "closed":
      return { ...state, status: "closed" };
    case "hello": {
      const peers: Record<string, PeerSession> = {};
      for (const p of action.peers) peers[p.sessionId] = p;
      const locks: Record<string, LockEntry> = {};
      for (const l of action.locks) locks[l.topicId] = l;
      return {
        ...state,
        status: "open",
        mySessionId: action.sessionId,
        myUserId: action.userId,
        myColor: action.color,
        myDisplayName: action.displayName,
        peers,
        locks,
      };
    }
    case "peer_join":
      return {
        ...state,
        peers: { ...state.peers, [action.session.sessionId]: action.session },
      };
    case "peer_leave": {
      if (!state.peers[action.sessionId]) return state;
      const next = { ...state.peers };
      delete next[action.sessionId];
      return { ...state, peers: next };
    }
    case "peer_cursor": {
      const p = state.peers[action.sessionId];
      if (!p) return state;
      return {
        ...state,
        peers: {
          ...state.peers,
          [action.sessionId]: { ...p, cursor: action.cursor },
        },
      };
    }
    case "peer_viewport": {
      const p = state.peers[action.sessionId];
      if (!p) return state;
      return {
        ...state,
        peers: {
          ...state.peers,
          [action.sessionId]: { ...p, viewport: action.viewport },
        },
      };
    }
    case "peer_following": {
      const p = state.peers[action.sessionId];
      if (!p) return state;
      return {
        ...state,
        peers: {
          ...state.peers,
          [action.sessionId]: {
            ...p,
            followingSessionId: action.followingSessionId,
          },
        },
      };
    }
    case "lock_state": {
      if (action.entry === null) {
        if (!state.locks[action.topicId]) return state;
        const next = { ...state.locks };
        delete next[action.topicId];
        return { ...state, locks: next };
      }
      return {
        ...state,
        locks: { ...state.locks, [action.topicId]: action.entry },
      };
    }
    case "lock_denied":
      return {
        ...state,
        lockDenied: {
          topicId: action.topicId,
          ownerDisplayName: action.ownerDisplayName,
          ownerColor: action.ownerColor,
        },
      };
    case "clear_lock_denied":
      return { ...state, lockDenied: null };
    case "contradiction":
      return { ...state, contradictionEvent: action.payload };
    case "clear_contradiction":
      return { ...state, contradictionEvent: null };
    default:
      return state;
  }
}

function wsUrlFor(
  apiBase: string,
  projectId: string,
  ticket: string | null,
): string {
  const u = new URL(apiBase);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  // Strip trailing slash; append path.
  const path = u.pathname.replace(/\/$/, "");
  u.pathname = `${path}/ws/projects/${encodeURIComponent(projectId)}`;
  if (ticket) u.searchParams.set("auth", ticket);
  return u.toString();
}

async function fetchWsTicket(apiBase: string): Promise<string | null> {
  try {
    const res = await fetch(`${apiBase}/api/auth/ws-ticket`, {
      credentials: "include",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { ticket?: string };
    return body.ticket || null;
  } catch {
    return null;
  }
}

export type UseRealtimeReturn = {
  mySessionId: string | null;
  myUserId: string | null;
  myColor: string | null;
  myDisplayName: string | null;
  peers: PeerSession[];
  locks: Record<string, LockEntry>;
  followingSessionId: string | null;
  setFollowing: (sid: string | null) => void;
  sendCursor: (x: number, y: number) => void;
  sendViewport: (x: number, y: number, zoom: number) => void;
  sendFocusTopic: (topicId: string | null) => void;
  contradictionEvent: ContradictionPayload | null;
  clearContradiction: () => void;
  lockDenied: RtState["lockDenied"];
  clearLockDenied: () => void;
  status: RtState["status"];
};

export function useRealtime(
  projectId: string | null,
  apiBase: string,
): UseRealtimeReturn {
  const [state, dispatch] = useReducer(rtReducer, {
    mySessionId: null,
    myUserId: null,
    myColor: null,
    myDisplayName: null,
    peers: {},
    locks: {},
    contradictionEvent: null,
    lockDenied: null,
    status: "connecting",
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<number | null>(null);
  const unmountedRef = useRef(false);
  const followingRef = useRef<string | null>(null);
  const [followVersion, forceFollow] = useReducer((x: number) => x + 1, 0);

  // Cursor throttle — never more than once per 33ms.
  const cursorThrottle = useRef<{
    lastSent: number;
    pending: { x: number; y: number } | null;
    timer: number | null;
  }>({ lastSent: 0, pending: null, timer: null });

  // Viewport debounce — only send 100ms after the last change.
  const viewportDebounce = useRef<{
    pending: { x: number; y: number; zoom: number } | null;
    timer: number | null;
  }>({ pending: null, timer: null });

  const sendRaw = useCallback((msg: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify(msg));
    } catch {
      /* socket closed mid-send — reconnect loop handles it */
    }
  }, []);

  const sendCursor = useCallback(
    (x: number, y: number) => {
      const now = performance.now();
      const s = cursorThrottle.current;
      const since = now - s.lastSent;
      if (since >= 33) {
        s.lastSent = now;
        s.pending = null;
        if (s.timer !== null) {
          window.clearTimeout(s.timer);
          s.timer = null;
        }
        sendRaw({ t: "cursor", x, y });
        return;
      }
      s.pending = { x, y };
      if (s.timer === null) {
        s.timer = window.setTimeout(() => {
          const p = s.pending;
          s.pending = null;
          s.timer = null;
          if (p) {
            s.lastSent = performance.now();
            sendRaw({ t: "cursor", x: p.x, y: p.y });
          }
        }, 33 - since);
      }
    },
    [sendRaw],
  );

  const sendViewport = useCallback(
    (x: number, y: number, zoom: number) => {
      const s = viewportDebounce.current;
      s.pending = { x, y, zoom };
      if (s.timer !== null) {
        window.clearTimeout(s.timer);
      }
      s.timer = window.setTimeout(() => {
        const p = s.pending;
        s.pending = null;
        s.timer = null;
        if (p) sendRaw({ t: "viewport", x: p.x, y: p.y, zoom: p.zoom });
      }, 100);
    },
    [sendRaw],
  );

  const sendFocusTopic = useCallback(
    (topicId: string | null) => {
      sendRaw({ t: "focus_topic", topic_id: topicId });
    },
    [sendRaw],
  );

  const setFollowing = useCallback(
    (sid: string | null) => {
      followingRef.current = sid;
      forceFollow();
      sendRaw({ t: "follow", target_session_id: sid });
    },
    [sendRaw],
  );

  const clearContradiction = useCallback(() => {
    dispatch({ type: "clear_contradiction" });
  }, []);

  const clearLockDenied = useCallback(() => {
    dispatch({ type: "clear_lock_denied" });
  }, []);

  // ---- Open socket + reconnect loop ----------------------------------
  useEffect(() => {
    unmountedRef.current = false;
    if (!projectId) return;

    const connect = async () => {
      if (unmountedRef.current) return;
      dispatch({ type: "connecting" });
      // Always re-fetch a ticket before opening — tickets expire in
      // 90s and a reconnect might come long after the initial mount.
      // Also: after a logout/re-auth, the ticket changes.
      const ticket = await fetchWsTicket(apiBase);
      if (unmountedRef.current) return;
      if (!ticket) {
        // No valid session — no point opening the WS. Retry later in
        // case the user signs in during the session.
        scheduleReconnect();
        return;
      }
      const url = wsUrlFor(apiBase, projectId, ticket);
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        console.warn("[realtime] WS ctor failed", err);
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempt.current = 0;
      };

      ws.onmessage = (ev) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        const t = msg.t as string | undefined;
        switch (t) {
          case "hello":
            dispatch({
              type: "hello",
              sessionId: String(msg.session_id),
              userId: String(msg.user_id),
              displayName: String(msg.display_name),
              color: String(msg.color),
              peers: Array.isArray(msg.peers)
                ? (msg.peers as Array<Record<string, unknown>>).map((p) => ({
                    sessionId: String(p.session_id),
                    userId: String(p.user_id),
                    displayName: String(p.display_name),
                    color: String(p.color),
                    cursor: (p.cursor as PeerCursor | undefined) ?? undefined,
                    viewport:
                      (p.viewport as PeerViewport | undefined) ?? undefined,
                    activeTopicId:
                      (p.active_topic_id as string | null | undefined) ??
                      null,
                    followingSessionId:
                      (p.following_session_id as string | null | undefined) ??
                      null,
                  }))
                : [],
              locks: Object.values(
                (msg.locks as Record<string, Record<string, unknown>>) ?? {},
              ).map((l) => ({
                topicId: String(l.topic_id),
                ownerSessionId: String(l.owner_session_id),
                ownerUserId: String(l.owner_user_id),
                ownerColor: String(l.owner_color),
                ownerDisplayName: String(l.owner_display_name),
              })),
            });
            break;
          case "peer_join": {
            const p = msg.session as Record<string, unknown>;
            dispatch({
              type: "peer_join",
              session: {
                sessionId: String(p.session_id),
                userId: String(p.user_id),
                displayName: String(p.display_name),
                color: String(p.color),
                cursor: p.cursor as PeerCursor | undefined,
                viewport: p.viewport as PeerViewport | undefined,
                activeTopicId: (p.active_topic_id as string | null) ?? null,
                followingSessionId:
                  (p.following_session_id as string | null) ?? null,
              },
            });
            break;
          }
          case "peer_leave":
            dispatch({ type: "peer_leave", sessionId: String(msg.session_id) });
            break;
          case "peer_cursor":
            dispatch({
              type: "peer_cursor",
              sessionId: String(msg.session_id),
              cursor: { x: Number(msg.x), y: Number(msg.y) },
            });
            break;
          case "peer_viewport":
            dispatch({
              type: "peer_viewport",
              sessionId: String(msg.session_id),
              viewport: {
                x: Number(msg.x),
                y: Number(msg.y),
                zoom: Number(msg.zoom),
              },
            });
            break;
          case "peer_following":
            dispatch({
              type: "peer_following",
              sessionId: String(msg.session_id),
              followingSessionId:
                (msg.following_session_id as string | null) ?? null,
            });
            break;
          case "lock_state":
            if (msg.owner_session_id) {
              dispatch({
                type: "lock_state",
                topicId: String(msg.topic_id),
                entry: {
                  topicId: String(msg.topic_id),
                  ownerSessionId: String(msg.owner_session_id),
                  ownerUserId: String(msg.owner_user_id),
                  ownerColor: String(msg.owner_color),
                  ownerDisplayName: String(msg.owner_display_name),
                },
              });
            } else {
              dispatch({
                type: "lock_state",
                topicId: String(msg.topic_id),
                entry: null,
              });
            }
            break;
          case "lock_denied":
            dispatch({
              type: "lock_denied",
              topicId: String(msg.topic_id),
              ownerDisplayName: String(msg.owner_display_name ?? ""),
              ownerColor: String(msg.owner_color ?? ""),
            });
            break;
          case "contradiction": {
            const payload = msg as Record<string, unknown>;
            dispatch({
              type: "contradiction",
              payload: {
                decisionId: String(payload.decision_id),
                conflictingDecisionId: String(payload.conflicting_decision_id),
                conflictingStatement: String(payload.conflicting_statement),
                conflictingAuthorDisplayName: String(
                  payload.conflicting_author_display_name ?? "Someone",
                ),
                conflictingAuthorColor:
                  (payload.conflicting_author_color as string | undefined) ??
                  undefined,
                conflictingCreatedAt: String(
                  payload.conflicting_created_at ?? "",
                ),
                reason: (payload.reason as string | undefined) ?? undefined,
              },
            });
            break;
          }
          case "ping":
            sendRaw({ t: "pong" });
            break;
          default:
            break;
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        dispatch({ type: "closed" });
        scheduleReconnect();
      };

      ws.onerror = () => {
        // onclose will fire; reconnect handled there.
      };
    };

    const scheduleReconnect = () => {
      if (unmountedRef.current) return;
      const attempt = reconnectAttempt.current;
      reconnectAttempt.current = Math.min(attempt + 1, 5);
      const delay = Math.min(15000, 1000 * Math.pow(2, attempt));
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current);
      }
      reconnectTimer.current = window.setTimeout(() => {
        reconnectTimer.current = null;
        connect();
      }, delay);
    };

    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [projectId, apiBase, sendRaw]);

  const peersArr = useMemo(() => Object.values(state.peers), [state.peers]);

  // followVersion is referenced to force re-render when local follow
  // target changes; returning followingRef.current is otherwise stable.
  void followVersion;

  return {
    mySessionId: state.mySessionId,
    myUserId: state.myUserId,
    myColor: state.myColor,
    myDisplayName: state.myDisplayName,
    peers: peersArr,
    locks: state.locks,
    followingSessionId: followingRef.current,
    setFollowing,
    sendCursor,
    sendViewport,
    sendFocusTopic,
    contradictionEvent: state.contradictionEvent,
    clearContradiction,
    lockDenied: state.lockDenied,
    clearLockDenied,
    status: state.status,
  };
}
