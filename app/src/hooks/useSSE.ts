// B1.2 — SSE bridge for the canvas-review chrome.
//
// Opens an EventSource against the orchestrator's per-project event stream
// and re-emits each typed message as a `window` CustomEvent so any
// component on the canvas (TopicNode, ConflictBanner, ReviewActionsPanel)
// can subscribe without prop drilling.
//
// Uses the orchestrator's verbatim event vocabulary — DO NOT invent names:
//   run.started, sub_agent.started, sub_agent.completed, sub_agent.failed,
//   decision.drafted, conflict.detected, conflict.resolved,
//   decision_summary.ready, orchestrator.completed, error
//
// This hook only subscribes to the events it actually consumes (see
// SUBSCRIBED_EVENTS below). The others (orchestrator.completed,
// decision_summary.ready) have their own listeners elsewhere —
// re-emitting them would create silent listener fan-out across
// modules that don't need them. decision.drafted is included because
// the Topic Detail reasoning expander needs the live append.
//
// Tests `vi.mock` the global `EventSource`. There is no
// `window.__INSPIRA_*` debug global by design (it's a code smell —
// production builds would carry the indirection forever). Dev fixtures
// gate via `import.meta.env.DEV` if/when added.

import { useEffect } from "react";

export type SSESubAgentEvent = {
  type: "sub_agent.started" | "sub_agent.completed" | "sub_agent.failed";
  topic_id: string;
  sub_agent_id?: string;
  reason?: string;
};

export type SSEConflictEvent = {
  type: "conflict.detected" | "conflict.resolved";
  conflict_id: string;
  topics: Array<{ topic_id: string; title: string; statement: string }>;
  resolution?: string;
};

export type SSEDecisionDraftedEvent = {
  type: "decision.drafted";
  sub_agent_run_id: string;
  theme_id: string;
  topic_index: number;
  decision: {
    decision_id: string;
    statement: string;
    rationale: string | null;
    subject: string;
  };
  provenance: Array<{ feedback_item_id: string; weight: number }>;
};

export type SSEEvent =
  | SSESubAgentEvent
  | SSEConflictEvent
  | SSEDecisionDraftedEvent;

const SUBSCRIBED_EVENTS = [
  "sub_agent.started",
  "sub_agent.completed",
  "sub_agent.failed",
  "conflict.detected",
  "conflict.resolved",
  "decision.drafted",
] as const;

export type SubscribedEventType = (typeof SUBSCRIBED_EVENTS)[number];

/**
 * Subscribes the current canvas to its project's SSE stream and re-emits
 * each subscribed event as a `window` CustomEvent named
 * `inspira:sse:<event-name>` with the parsed payload as `detail`.
 *
 * Pass a falsy `projectId` to opt out (e.g. before the project loads).
 *
 * Closes the EventSource on unmount.
 */
export function useSSE(projectId: string | null | undefined): void {
  useEffect(() => {
    if (!projectId) return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return;
    }

    const url = `/api/v2/projects/${projectId}/events`;
    const es = new EventSource(url);

    const dispatch = (event: SubscribedEventType, payload: unknown) => {
      window.dispatchEvent(
        new CustomEvent(`inspira:sse:${event}`, { detail: payload }),
      );
    };

    const onMessage = (e: MessageEvent) => {
      let parsed: SSEEvent | null = null;
      try {
        parsed = JSON.parse(e.data) as SSEEvent;
      } catch {
        return;
      }
      if (!parsed || typeof parsed.type !== "string") return;
      if (
        (SUBSCRIBED_EVENTS as readonly string[]).includes(parsed.type) === false
      ) {
        return;
      }
      dispatch(parsed.type as SubscribedEventType, parsed);
    };

    es.addEventListener("message", onMessage);

    // The backend may emit each event on its named channel rather than the default
    // `message` channel — register a listener per subscribed type so we
    // catch both encodings. The handler shape is identical.
    const namedHandlers: Array<[SubscribedEventType, (e: MessageEvent) => void]> = [];
    for (const name of SUBSCRIBED_EVENTS) {
      const handler = (e: MessageEvent) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(e.data);
        } catch {
          return;
        }
        dispatch(name, parsed);
      };
      es.addEventListener(name, handler as EventListener);
      namedHandlers.push([name, handler]);
    }

    return () => {
      es.removeEventListener("message", onMessage);
      for (const [name, handler] of namedHandlers) {
        es.removeEventListener(name, handler as EventListener);
      }
      es.close();
    };
  }, [projectId]);
}
