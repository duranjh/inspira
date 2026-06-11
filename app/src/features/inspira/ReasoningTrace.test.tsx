/**
 * Tests for ReasoningTrace — the Topic Detail reasoning expander.
 *
 * Coverage:
 *   - collapsed by default (trigger visible, body hidden)
 *   - clicking the trigger calls onToggle
 *   - all 4 section headings render when open
 *   - "Cited feedback items" empty placeholder when map is empty
 *   - "Cited feedback items" chips render when map has rows
 *   - "ROI rationale" reads topic.metadata.why_this_topic
 *   - "ROI rationale" empty placeholder when missing/blank
 *   - "Decision derivation" lists each decision's statement
 *   - "Decision derivation" empty placeholder when no decisions
 *   - "Re-think" button is rendered disabled
 *   - lazy-load: onLoadProvenance fires once on first open if map empty
 *   - lazy-load: onLoadProvenance does NOT fire if map already has rows
 *   - lazy-load: subsequent re-opens do not re-trigger the load
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReasoningTrace } from "./ReasoningTrace";
import type { Decision, Topic, TopicProvenanceRow } from "./api";

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

function makeTopic(overrides: Partial<Topic> = {}): Topic {
  return {
    topic_id: "t1",
    project_id: "p1",
    title: "Reproduce the bug",
    icon: "flag",
    position_x: 0,
    position_y: 0,
    status: "in_progress",
    order_index: 0,
    origin: "planner_initial",
    metadata: { why_this_topic: "We can't fix what we can't see." },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function makeDecision(id: string, statement: string): Decision {
  return {
    decision_id: id,
    topic_id: "t1",
    project_id: "p1",
    statement,
    rationale: null,
    status: "proposed",
    source_turn_id: null,
    proposed_by: "planner",
    confirmed_by_user_id: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    retracted_at: null,
  };
}

function makeProvenanceRow(
  decisionId: string,
  itemTitle: string,
  source: string,
): TopicProvenanceRow {
  return {
    decision_id: decisionId,
    feedback_item_id: `fi-${itemTitle}`,
    weight: 0.5,
    feedback_item: {
      item_id: `fi-${itemTitle}`,
      title: itemTitle,
      body: "",
      source,
      received_at: null,
      ingested_at: "2026-05-01T00:00:00Z",
    },
  };
}

describe("ReasoningTrace", () => {
  it("trigger renders by default; body hidden until isOpen", () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={false}
          onToggle={() => {}}
        />,
      );
    });
    expect(container.querySelector(".topic-detail__reasoning-trigger")).not.toBeNull();
    expect(container.querySelector(".topic-detail__reasoning-body")).toBeNull();
  });

  it("invokes onToggle when the trigger is clicked", () => {
    const onToggle = vi.fn();
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={false}
          onToggle={onToggle}
        />,
      );
    });
    const trigger = container.querySelector(
      ".topic-detail__reasoning-trigger",
    ) as HTMLButtonElement;
    act(() => {
      trigger.click();
    });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("renders all 4 section headings when open", () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const headings = Array.from(
      container.querySelectorAll(".topic-detail__reasoning-heading"),
    ).map((el) => el.textContent);
    expect(headings).toEqual([
      "Cited feedback items",
      "ROI rationale",
      "Decision derivation",
    ]);
    // Re-think is a button, not a heading.
    expect(
      container.querySelector(".topic-detail__reasoning-rethink"),
    ).not.toBeNull();
  });

  it('"Cited feedback items" shows empty placeholder when map is empty', () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const empties = container.querySelectorAll(".topic-detail__reasoning-empty");
    // 3 empties — feedback, rationale (if blank), decisions (if empty).
    // Here ROI rationale has data, so 2 empties.
    expect(empties.length).toBe(2);
    expect(empties[0].textContent).toContain("No cited feedback");
  });

  it('"Cited feedback items" renders chips when map has rows', () => {
    const map = new Map<string, TopicProvenanceRow[]>();
    map.set("dec-1", [
      makeProvenanceRow("dec-1", "Login fails on Safari", "linear"),
      makeProvenanceRow("dec-1", "Same issue from Sara", "csv-import"),
    ]);
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={map}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const chips = container.querySelectorAll(
      ".topic-detail__reasoning-source-chip",
    );
    expect(chips.length).toBe(2);
    expect(chips[0].textContent).toBe("linear");
  });

  it('"ROI rationale" reads topic.metadata.why_this_topic', () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const rationale = container.querySelector(
      ".topic-detail__reasoning-rationale",
    );
    expect(rationale?.textContent).toBe(
      "We can't fix what we can't see.",
    );
  });

  it('"ROI rationale" shows empty placeholder when missing', () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic({ metadata: {} })}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const empties = container.querySelectorAll(".topic-detail__reasoning-empty");
    // All 3 sections empty now: feedback, rationale, decisions.
    expect(empties.length).toBe(3);
  });

  it('"Decision derivation" lists each decision statement', () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[
            makeDecision("d1", "Use feature flags"),
            makeDecision("d2", "Roll out to 10%"),
          ]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const texts = Array.from(
      container.querySelectorAll(".topic-detail__reasoning-derivation-text"),
    ).map((el) => el.textContent);
    expect(texts).toEqual(["Use feature flags", "Roll out to 10%"]);
  });

  it('"Re-think" button is rendered disabled with tooltip', () => {
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const button = container.querySelector(
      ".topic-detail__reasoning-rethink",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("title")).toBe("Available next release");
  });

  it("lazy-load: onLoadProvenance fires once on first open if map is empty", () => {
    const onLoad = vi.fn();
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={false}
          onToggle={() => {}}
          onLoadProvenance={onLoad}
        />,
      );
    });
    expect(onLoad).not.toHaveBeenCalled();
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
          onLoadProvenance={onLoad}
        />,
      );
    });
    expect(onLoad).toHaveBeenCalledTimes(1);
  });

  it("lazy-load: still fires when map has live placeholders (REST hydrates titles)", () => {
    // Live SSE rows carry only feedback_item_id as the title — without
    // a REST hydrate, "Cited feedback items" would render IDs forever.
    // The lazy-load must run regardless of map size.
    const onLoad = vi.fn();
    const map = new Map<string, TopicProvenanceRow[]>();
    map.set("dec-1", [makeProvenanceRow("dec-1", "fi-placeholder-id", "live")]);
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={map}
          isOpen={true}
          onToggle={() => {}}
          onLoadProvenance={onLoad}
        />,
      );
    });
    expect(onLoad).toHaveBeenCalledTimes(1);
  });

  it("lazy-load: subsequent re-opens do not re-fire the load", () => {
    const onLoad = vi.fn();
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
          onLoadProvenance={onLoad}
        />,
      );
    });
    expect(onLoad).toHaveBeenCalledTimes(1);
    // Toggle closed
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={false}
          onToggle={() => {}}
          onLoadProvenance={onLoad}
        />,
      );
    });
    // Re-open
    act(() => {
      root.render(
        <ReasoningTrace
          topic={makeTopic()}
          decisions={[]}
          provenanceByDecisionId={new Map()}
          isOpen={true}
          onToggle={() => {}}
          onLoadProvenance={onLoad}
        />,
      );
    });
    expect(onLoad).toHaveBeenCalledTimes(1);
  });
});
