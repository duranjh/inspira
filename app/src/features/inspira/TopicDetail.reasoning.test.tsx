/**
 * Integration smoke for the reasoning + live-stream layer in TopicDetail.
 *
 * Goal: verify that the new SSE event filter wired into TopicDetail
 * (a) appends `decision.drafted` events that match `theme_id +
 * topic.order_index` to the live stream, and (b) drops events whose
 * theme_id or topic_index don't match.
 *
 * The full TopicDetail mount path makes many side-effecting API calls
 * (turns, decisions, model tier catalog, etc.) — those are mocked to
 * resolved promises with empty data. JSDOM doesn't load CSS, so visual
 * checks are skipped here; the design is covered by the per-component
 * unit tests + manual browser smoke.
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listTurns: vi.fn().mockResolvedValue({ turns: [] }),
      listDecisions: vi.fn().mockResolvedValue({ decisions: [] }),
      listTopicProvenance: vi.fn().mockResolvedValue({ provenance: [] }),
      topicTurnStream: vi.fn().mockResolvedValue({
        kickoff: null,
        turns: [],
        latest_planner_result: null,
        usage: null,
      }),
      topicTurn: vi.fn().mockResolvedValue({
        kickoff: null,
        turns: [],
        latest_planner_result: null,
        usage: null,
      }),
      modelTierCatalog: vi.fn().mockResolvedValue({ tiers: [], default: null }),
      getUsageView: vi.fn().mockResolvedValue(null),
      listConflictResolutions: vi.fn().mockResolvedValue({ resolutions: [] }),
    },
    getLastLlmMode: vi.fn().mockReturnValue(null),
    subscribeLlmMode: vi.fn().mockReturnValue(() => {}),
  };
});

import { TopicDetail } from "./TopicDetail";
import type { Topic, V2Project } from "./api";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // JSDOM doesn't implement Element.scrollTo; TopicDetail's auto-scroll
  // effect uses it. Stub once globally so renders don't throw.
  if (!Element.prototype.scrollTo) {
    Element.prototype.scrollTo = function () {};
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.clearAllMocks();
});

const topic: Topic = {
  topic_id: "t1",
  project_id: "p1",
  title: "Reproduce the bug",
  icon: "flag",
  position_x: 0,
  position_y: 0,
  status: "in_progress",
  order_index: 2,
  origin: "planner_initial",
  metadata: { why_this_topic: "Why" },
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
};

const project: V2Project = {
  project_id: "p1",
  user_id: "u1",
  title: "T",
  metadata: { theme_id: "theme-X" },
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
};

function fireSSE(name: string, detail: unknown) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

async function flushAsync() {
  // Two ticks: one to drain microtasks for the api.listTurns +
  // api.listDecisions Promise.all, one for any state updates dispatched
  // inside `then` continuations.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function renderDrawer() {
  act(() => {
    root.render(
      <TopicDetail
        topic={topic}
        allTopics={[topic]}
        relationships={[]}
        originRect={null}
        onClose={() => {}}
        project={project}
      />,
    );
  });
}

describe("TopicDetail reasoning integration", () => {
  it("renders the reasoning expander above the (skeleton) thread", async () => {
    renderDrawer();
    await flushAsync();
    const reasoning = container.querySelector(".topic-detail__reasoning");
    expect(reasoning).not.toBeNull();
  });

  it("appends decision.drafted to the live stream when theme_id + topic_index match", async () => {
    renderDrawer();
    await flushAsync();
    act(() => {
      fireSSE("inspira:sse:decision.drafted", {
        sub_agent_run_id: "sa-1",
        theme_id: "theme-X",
        topic_index: 2,
        decision: {
          decision_id: "d-100",
          statement: "Use venue X",
          rationale: null,
          subject: "venue",
        },
        provenance: [],
      });
    });
    // Open the stream so rows render.
    const trigger = container.querySelector(
      ".topic-detail__stream-trigger",
    ) as HTMLButtonElement | null;
    expect(trigger).not.toBeNull();
    act(() => {
      trigger?.click();
    });
    const rows = container.querySelectorAll(".topic-detail__stream-row");
    expect(rows.length).toBe(1);
    expect(
      container.querySelector(".topic-detail__stream-text")?.textContent,
    ).toBe("Use venue X");
  });

  it("drops decision.drafted events whose topic_index doesn't match", async () => {
    renderDrawer();
    await flushAsync();
    act(() => {
      fireSSE("inspira:sse:decision.drafted", {
        sub_agent_run_id: "sa-2",
        theme_id: "theme-X",
        topic_index: 99,
        decision: {
          decision_id: "d-200",
          statement: "Wrong topic",
          rationale: null,
          subject: "x",
        },
        provenance: [],
      });
    });
    expect(
      container.querySelector(".topic-detail__stream-trigger"),
    ).toBeNull();
  });

  it("drops decision.drafted events whose theme_id doesn't match", async () => {
    renderDrawer();
    await flushAsync();
    act(() => {
      fireSSE("inspira:sse:decision.drafted", {
        sub_agent_run_id: "sa-3",
        theme_id: "theme-OTHER",
        topic_index: 2,
        decision: {
          decision_id: "d-300",
          statement: "Other theme",
          rationale: null,
          subject: "x",
        },
        provenance: [],
      });
    });
    expect(
      container.querySelector(".topic-detail__stream-trigger"),
    ).toBeNull();
  });

  it("renders the TopicSubAgentPulse only when sub_agent.started fires for the matching theme", async () => {
    renderDrawer();
    await flushAsync();
    expect(
      container.querySelector(".topic-detail__sub-agent-pulse"),
    ).toBeNull();
    act(() => {
      fireSSE("inspira:sse:sub_agent.started", {
        sub_agent_run_id: "sa-4",
        theme_id: "theme-X",
        project_id: null,
      });
    });
    expect(
      container.querySelector(".topic-detail__sub-agent-pulse"),
    ).not.toBeNull();
  });
});
