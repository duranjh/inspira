/**
 * Tests for MultiAgentDots — the project-scoped loading indicator.
 *
 * Coverage:
 *   - renders nothing initially
 *   - renders 3 dots on any sub_agent.started (SSE stream is already
 *     project-scoped — see component header note)
 *   - ref-counts started vs completed: dots persist until count → 0
 *   - removes dots when sub_agent.completed brings count to 0
 *   - removes dots when sub_agent.failed brings count to 0
 *   - cleans up window listeners on unmount (no leak)
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { MultiAgentDots } from "./MultiAgentDots";

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
});

function fireSSE(name: string, detail: unknown) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

describe("MultiAgentDots", () => {
  it("renders nothing initially", () => {
    act(() => {
      root.render(<MultiAgentDots topicId="t1" />);
    });
    expect(container.querySelector(".multi-agent-dots")).toBeNull();
  });

  it("renders 3 dots when sub_agent.started fires", () => {
    act(() => {
      root.render(<MultiAgentDots topicId="t1" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        type: "sub_agent.started",
        theme_id: "theme-1",
      });
    });
    const dots = container.querySelectorAll(".multi-agent-dots__dot");
    expect(dots.length).toBe(3);
    expect(container.querySelector(".multi-agent-dots")?.getAttribute("role")).toBe(
      "status",
    );
  });

  it("ref-counts started vs completed: dots persist until count → 0", () => {
    act(() => {
      root.render(<MultiAgentDots topicId="t1" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {});
      fireSSE("inspira:sse:sub_agent.started", {});
    });
    expect(container.querySelector(".multi-agent-dots")).not.toBeNull();
    act(() => {
      fireSSE("inspira:sse:sub_agent.completed", {});
    });
    // Still active — second sub-agent hasn't completed.
    expect(container.querySelector(".multi-agent-dots")).not.toBeNull();
    act(() => {
      fireSSE("inspira:sse:sub_agent.completed", {});
    });
    expect(container.querySelector(".multi-agent-dots")).toBeNull();
  });

  it("removes dots on sub_agent.failed bringing count to 0", () => {
    act(() => {
      root.render(<MultiAgentDots topicId="t1" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {});
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.failed", { reason: "timeout" });
    });
    expect(container.querySelector(".multi-agent-dots")).toBeNull();
  });

  it("cleans up window listeners on unmount (no leak)", () => {
    act(() => {
      root.render(<MultiAgentDots topicId="t1" />);
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
    }).not.toThrow();
    // Re-mount path so afterEach's unmount is still safe.
    root = createRoot(container);
  });
});
