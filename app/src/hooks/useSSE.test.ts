/**
 * Tests for useSSE — SSE → window CustomEvent bridge.
 *
 * Coverage:
 *   - opens an EventSource at the right URL when projectId is provided
 *   - skips entirely when projectId is null/undefined
 *   - dispatches `inspira:sse:<name>` for each subscribed orchestrator event type
 *   - ignores unsubscribed event types (decision.drafted / orchestrator.completed)
 *   - closes the EventSource on unmount (no leak)
 *
 * The global `EventSource` is replaced with a Mock implementation that
 * captures handlers and lets tests synthesize incoming messages. This
 * avoids any network access in jsdom.
 */

import React, { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSSE } from "./useSSE";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  // Map of "channel name" → array of listener callbacks.
  listeners: Map<string, Array<(e: MessageEvent) => void>> = new Map();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(
    name: string,
    cb: EventListenerOrEventListenerObject,
  ): void {
    const list = this.listeners.get(name) ?? [];
    list.push(cb as (e: MessageEvent) => void);
    this.listeners.set(name, list);
  }

  removeEventListener(
    name: string,
    cb: EventListenerOrEventListenerObject,
  ): void {
    const list = this.listeners.get(name);
    if (!list) return;
    const idx = list.indexOf(cb as (e: MessageEvent) => void);
    if (idx !== -1) list.splice(idx, 1);
  }

  close(): void {
    this.closed = true;
  }

  emit(name: string, data: unknown): void {
    const evt = new MessageEvent(name, { data: JSON.stringify(data) });
    for (const cb of this.listeners.get(name) ?? []) cb(evt);
  }
}

let container: HTMLDivElement;
let root: Root;
let originalEventSource: typeof EventSource | undefined;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  originalEventSource = (globalThis as { EventSource?: typeof EventSource }).EventSource;
  (globalThis as { EventSource?: unknown }).EventSource =
    MockEventSource as unknown as typeof EventSource;
  MockEventSource.instances = [];
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  if (originalEventSource) {
    (globalThis as { EventSource?: typeof EventSource }).EventSource =
      originalEventSource;
  } else {
    delete (globalThis as { EventSource?: typeof EventSource }).EventSource;
  }
});

function Probe({ projectId }: { projectId: string | null }) {
  useSSE(projectId);
  return null;
}

describe("useSSE", () => {
  it("opens an EventSource at the project events URL", () => {
    act(() => {
      root.render(React.createElement(Probe, { projectId: "p_42" }));
    });
    expect(MockEventSource.instances.length).toBe(1);
    expect(MockEventSource.instances[0].url).toBe(
      "/api/v2/projects/p_42/events",
    );
  });

  it("skips when projectId is null", () => {
    act(() => {
      root.render(React.createElement(Probe, { projectId: null }));
    });
    expect(MockEventSource.instances.length).toBe(0);
  });

  it("dispatches inspira:sse:<name> for each subscribed orchestrator event", () => {
    act(() => {
      root.render(React.createElement(Probe, { projectId: "p_42" }));
    });
    const es = MockEventSource.instances[0];

    const events: Array<{ type: string; detail: unknown }> = [];
    const listener = (e: Event) => {
      events.push({
        type: e.type,
        detail: (e as CustomEvent).detail,
      });
    };
    for (const name of [
      "inspira:sse:sub_agent.started",
      "inspira:sse:sub_agent.completed",
      "inspira:sse:sub_agent.failed",
      "inspira:sse:conflict.detected",
      "inspira:sse:conflict.resolved",
      "inspira:sse:decision.drafted",
    ]) {
      window.addEventListener(name, listener);
    }

    act(() => {
      es.emit("sub_agent.started", { type: "sub_agent.started", topic_id: "t1" });
      es.emit("sub_agent.completed", { type: "sub_agent.completed", topic_id: "t1" });
      es.emit("sub_agent.failed", { type: "sub_agent.failed", topic_id: "t2" });
      es.emit("conflict.detected", { type: "conflict.detected", conflict_id: "c1", topics: [] });
      es.emit("conflict.resolved", { type: "conflict.resolved", conflict_id: "c1", topics: [] });
      es.emit("decision.drafted", {
        type: "decision.drafted",
        sub_agent_run_id: "sa1",
        theme_id: "th1",
        topic_index: 0,
        decision: { decision_id: "d1", statement: "x", rationale: null, subject: "y" },
        provenance: [],
      });
    });

    for (const name of [
      "inspira:sse:sub_agent.started",
      "inspira:sse:sub_agent.completed",
      "inspira:sse:sub_agent.failed",
      "inspira:sse:conflict.detected",
      "inspira:sse:conflict.resolved",
      "inspira:sse:decision.drafted",
    ]) {
      window.removeEventListener(name, listener);
    }

    expect(events.map((e) => e.type)).toEqual([
      "inspira:sse:sub_agent.started",
      "inspira:sse:sub_agent.completed",
      "inspira:sse:sub_agent.failed",
      "inspira:sse:conflict.detected",
      "inspira:sse:conflict.resolved",
      "inspira:sse:decision.drafted",
    ]);
  });

  it("dispatches decision.drafted with the full payload (reasoning expander needs provenance)", () => {
    act(() => {
      root.render(React.createElement(Probe, { projectId: "p_42" }));
    });
    const es = MockEventSource.instances[0];

    let captured: { detail: unknown } | null = null;
    const listener = (e: Event) => {
      captured = { detail: (e as CustomEvent).detail };
    };
    window.addEventListener("inspira:sse:decision.drafted", listener);

    const payload = {
      type: "decision.drafted",
      sub_agent_run_id: "sa-run-1",
      theme_id: "theme-7",
      topic_index: 2,
      decision: {
        decision_id: "dec-9",
        statement: "Pick venue X",
        rationale: "Cheaper.",
        subject: "venue",
      },
      provenance: [{ feedback_item_id: "fi-1", weight: 0.5 }],
    };

    act(() => {
      es.emit("decision.drafted", payload);
    });

    window.removeEventListener("inspira:sse:decision.drafted", listener);

    expect(captured).not.toBeNull();
    expect((captured as { detail: typeof payload }).detail).toEqual(payload);
  });

  it("ignores events outside the subscribed set on the default `message` channel", () => {
    act(() => {
      root.render(React.createElement(Probe, { projectId: "p_42" }));
    });
    const es = MockEventSource.instances[0];

    const seen: string[] = [];
    const listener = (e: Event) => seen.push(e.type);
    // orchestrator.completed is consumed elsewhere and not subscribed by this hook.
    window.addEventListener("inspira:sse:orchestrator.completed", listener);

    act(() => {
      es.emit("message", { type: "orchestrator.completed" });
    });

    window.removeEventListener("inspira:sse:orchestrator.completed", listener);

    expect(seen.length).toBe(0);
  });

  it("closes the EventSource on unmount (full subscribe → unsubscribe lifecycle)", () => {
    act(() => {
      root.render(React.createElement(Probe, { projectId: "p_42" }));
    });
    const es = MockEventSource.instances[0];
    expect(es.closed).toBe(false);
    act(() => {
      root.unmount();
    });
    expect(es.closed).toBe(true);
    // Re-mount path so afterEach's unmount is still safe.
    root = createRoot(container);
  });
});
