// Context wrapper for the realtime hook. Lets TopicNode + TopicDetail
// read live peers/locks without prop-drilling through ProjectCanvas.

import { createContext, useContext, type ReactNode } from "react";

import type { UseRealtimeReturn } from "./realtime";

const RealtimeContext = createContext<UseRealtimeReturn | null>(null);

export function RealtimeProvider({
  value,
  children,
}: {
  value: UseRealtimeReturn;
  children: ReactNode;
}) {
  return (
    <RealtimeContext.Provider value={value}>
      {children}
    </RealtimeContext.Provider>
  );
}

/** Returns the realtime API, or a disconnected stub when no provider
 *  is mounted (e.g., in tests). Callers should guard on `status` if
 *  they need to know whether the socket is live. */
export function useRealtimeContext(): UseRealtimeReturn {
  const ctx = useContext(RealtimeContext);
  if (ctx) return ctx;
  return {
    mySessionId: null,
    myUserId: null,
    myColor: null,
    myDisplayName: null,
    peers: [],
    locks: {},
    followingSessionId: null,
    setFollowing: () => {},
    sendCursor: () => {},
    sendViewport: () => {},
    sendFocusTopic: () => {},
    contradictionEvent: null,
    clearContradiction: () => {},
    lockDenied: null,
    clearLockDenied: () => {},
    status: "closed",
  };
}
