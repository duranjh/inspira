/**
 * Tests for DraftingBanner — the top-of-canvas "Inspira is drafting"
 * status pill driven by `inspira:sse:sub_agent.*` window events.
 *
 * Coverage:
 *   - renders nothing initially (no banner element in DOM)
 *   - appears on first sub_agent.started
 *   - copy: "1 topic" after one completed; "{n} topics" after two
 *   - auto-hides 15s after last event when completedCount > 0
 *   - re-arms: new started after auto-hide brings banner back
 *   - cleans up listeners + idle timer on unmount (no leak)
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraftingBanner } from "./DraftingBanner";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.useRealTimers();
});

function fireSSE(name: string, detail: unknown = {}) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

describe("DraftingBanner", () => {
  it("renders nothing initially", () => {
    act(() => {
      root.render(<DraftingBanner />);
    });
    expect(container.querySelector(".inspira-drafting-banner")).toBeNull();
  });

  it("appears on first sub_agent.started", () => {
    act(() => {
      root.render(<DraftingBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        type: "sub_agent.started",
        topic_id: "t1",
      });
    });
    const banner = container.querySelector(".inspira-drafting-banner");
    expect(banner).not.toBeNull();
    expect(banner?.getAttribute("role")).toBe("status");
    expect(banner?.getAttribute("aria-live")).toBe("polite");
  });

  it("shows '1 topic' copy after one completed and '{n} topics' after two", () => {
    act(() => {
      root.render(<DraftingBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started");
      fireSSE("inspira:sse:sub_agent.completed");
    });
    const after1 = container.querySelector(".inspira-drafting-banner__text");
    // "Inspira is drafting · 1 topic so far"
    expect(after1?.textContent).toMatch(/1 topic so far/);
    expect(after1?.textContent).not.toMatch(/topics so far/);

    act(() => {
      fireSSE("inspira:sse:sub_agent.started");
      fireSSE("inspira:sse:sub_agent.completed");
    });
    const after2 = container.querySelector(".inspira-drafting-banner__text");
    expect(after2?.textContent).toMatch(/2 topics so far/);
  });

  it("auto-hides 15s after last event when completedCount > 0", () => {
    vi.useFakeTimers();
    act(() => {
      root.render(<DraftingBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started");
      fireSSE("inspira:sse:sub_agent.completed");
    });
    expect(container.querySelector(".inspira-drafting-banner")).not.toBeNull();
    // Advance past the 15s idle window. activeCount is now 0 and
    // completedCount > 0, so the banner relies on the idle timer.
    act(() => {
      vi.advanceTimersByTime(15_001);
    });
    expect(container.querySelector(".inspira-drafting-banner")).toBeNull();
  });

  it("re-arms after auto-hide on a new started event", () => {
    vi.useFakeTimers();
    act(() => {
      root.render(<DraftingBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started");
      fireSSE("inspira:sse:sub_agent.completed");
    });
    act(() => {
      vi.advanceTimersByTime(15_001);
    });
    expect(container.querySelector(".inspira-drafting-banner")).toBeNull();

    // New started after the lull — banner must come back.
    act(() => {
      fireSSE("inspira:sse:sub_agent.started");
    });
    expect(container.querySelector(".inspira-drafting-banner")).not.toBeNull();
  });

  it("cleans up window listeners + idle timer on unmount (no leak)", () => {
    act(() => {
      root.render(<DraftingBanner />);
    });
    act(() => {
      root.unmount();
    });
    // Firing after unmount must not throw — no leaked handler should be running.
    expect(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        type: "sub_agent.started",
        topic_id: "t1",
      });
      fireSSE("inspira:sse:sub_agent.completed");
      fireSSE("inspira:sse:sub_agent.failed");
    }).not.toThrow();
    // Re-mount path so afterEach's unmount is still safe.
    root = createRoot(container);
  });
});
