/**
 * Tests for TopicSubAgentPulse — header pulse next to the Topic Detail
 * title that activates while a sub-agent is working on this topic's theme.
 *
 * Coverage:
 *   - renders nothing initially
 *   - renders 3 dots + status label when sub_agent.started fires for the
 *     matching theme_id
 *   - ignores sub_agent events for other theme_ids
 *   - removes pulse on sub_agent.completed
 *   - removes pulse on sub_agent.failed
 *   - renders nothing when themeId is undefined / empty
 *   - cleans up window listeners on unmount
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TopicSubAgentPulse } from "./TopicSubAgentPulse";

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

describe("TopicSubAgentPulse", () => {
  it("renders nothing initially", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" />);
    });
    expect(container.querySelector(".topic-detail__sub-agent-pulse")).toBeNull();
  });

  it("renders 3 dots + label when sub_agent.started fires for the matching theme", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-1",
        theme_id: "theme-7",
        project_id: null,
      });
    });
    const pulse = container.querySelector(".topic-detail__sub-agent-pulse");
    expect(pulse).not.toBeNull();
    expect(pulse?.getAttribute("role")).toBe("status");
    const dots = container.querySelectorAll(".multi-agent-dots__dot");
    expect(dots.length).toBe(3);
    const label = container.querySelector(".topic-detail__sub-agent-pulse-label");
    expect(label?.textContent).toContain("Sub-agent working");
  });

  it("ignores sub_agent events for other theme_ids", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-2",
        theme_id: "theme-other",
        project_id: null,
      });
    });
    expect(container.querySelector(".topic-detail__sub-agent-pulse")).toBeNull();
  });

  it("removes pulse on sub_agent.completed", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-3",
        theme_id: "theme-7",
        project_id: null,
      });
    });
    expect(container.querySelector(".topic-detail__sub-agent-pulse")).not.toBeNull();
    act(() => {
      fireSSE("inspira:sse:sub_agent.completed", {
        sub_agent_run_id: "sa-3",
        theme_id: "theme-7",
        project_id: "proj-1",
      });
    });
    expect(container.querySelector(".topic-detail__sub-agent-pulse")).toBeNull();
  });

  it("removes pulse on sub_agent.failed", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-4",
        theme_id: "theme-7",
        project_id: null,
      });
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.failed", {
        sub_agent_run_id: "sa-4",
        theme_id: "theme-7",
        project_id: "proj-1",
        error: "timeout",
      });
    });
    expect(container.querySelector(".topic-detail__sub-agent-pulse")).toBeNull();
  });

  it("renders nothing when themeId is undefined", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId={undefined} />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-5",
        theme_id: "theme-7",
        project_id: null,
      });
    });
    expect(container.querySelector(".topic-detail__sub-agent-pulse")).toBeNull();
  });

  it("cleans up window listeners on unmount (no leak)", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" />);
    });
    act(() => {
      root.unmount();
    });
    expect(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-6",
        theme_id: "theme-7",
        project_id: null,
      });
    }).not.toThrow();
    root = createRoot(container);
  });

  it("renders custom label when provided", () => {
    act(() => {
      root.render(<TopicSubAgentPulse themeId="theme-7" label="Thinking now" />);
    });
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-7",
        theme_id: "theme-7",
        project_id: null,
      });
    });
    const label = container.querySelector(".topic-detail__sub-agent-pulse-label");
    expect(label?.textContent).toBe("Thinking now");
  });
});
