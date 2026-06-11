/**
 * Tests for ProvenanceBadge — the per-decision gold dot + hover popover.
 *
 * Coverage:
 *   - dot renders when proposed_by === "planner"
 *   - nothing renders when proposed_by === "user"
 *   - half-fill class applied when proposed_by === "planner" && humanEditedAt set
 *   - popover lists ≤5 sources
 *   - popover shows "View all sources →" link when sources are present
 *   - empty-source popover shows fallback message
 *
 * Uses react-dom/client + react.act, matching CanvasErrorBoundary.test.tsx.
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Decision } from "../api";
import { ProvenanceBadge } from "./ProvenanceBadge";

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

function makeDecision(over: Partial<Decision> = {}): Decision {
  return {
    decision_id: "d1",
    topic_id: "t1",
    project_id: "p1",
    statement: "Pick a stable iOS Safari build for repro.",
    rationale: null,
    status: "proposed",
    source_turn_id: null,
    proposed_by: "planner",
    confirmed_by_user_id: null,
    created_at: "2026-05-03T12:00:00Z",
    updated_at: "2026-05-03T12:00:00Z",
    retracted_at: null,
    ...over,
  };
}

describe("ProvenanceBadge", () => {
  it("renders a gold dot when proposed_by is planner", () => {
    act(() => {
      root.render(<ProvenanceBadge decision={makeDecision()} />);
    });
    const dot = container.querySelector(".provenance-badge__dot");
    expect(dot).not.toBeNull();
    expect(dot?.classList.contains("provenance-badge__dot--edited")).toBe(false);
  });

  it("renders nothing when proposed_by is user", () => {
    act(() => {
      root.render(
        <ProvenanceBadge decision={makeDecision({ proposed_by: "user" })} />,
      );
    });
    expect(container.querySelector(".provenance-badge")).toBeNull();
    expect(container.querySelector(".provenance-badge__dot")).toBeNull();
  });

  it("applies half-fill class when planner-seeded then human-edited", () => {
    act(() => {
      root.render(
        <ProvenanceBadge
          decision={makeDecision({
            provenance: {
              aiSeededAt: "2026-05-03T12:00:00Z",
              humanEditedAt: "2026-05-03T12:30:00Z",
            },
          })}
        />,
      );
    });
    const dot = container.querySelector(".provenance-badge__dot");
    expect(dot?.classList.contains("provenance-badge__dot--edited")).toBe(true);
  });

  it("does NOT apply half-fill when only aiSeededAt is set (no human edit)", () => {
    act(() => {
      root.render(
        <ProvenanceBadge
          decision={makeDecision({
            provenance: { aiSeededAt: "2026-05-03T12:00:00Z" },
          })}
        />,
      );
    });
    const dot = container.querySelector(".provenance-badge__dot");
    expect(dot?.classList.contains("provenance-badge__dot--edited")).toBe(false);
  });

  it("opens popover and renders up to 5 sources", () => {
    const sources = Array.from({ length: 7 }, (_, i) => ({
      feedbackItemId: `fi-${i}`,
      severity: 4,
      excerpt: `Source ${i}`,
    }));
    act(() => {
      root.render(
        <ProvenanceBadge
          decision={makeDecision({ provenance: { sources } })}
        />,
      );
    });
    const dot = container.querySelector<HTMLButtonElement>(
      ".provenance-badge__dot",
    );
    expect(dot).not.toBeNull();
    act(() => {
      dot!.click();
    });
    const items = container.querySelectorAll(".provenance-badge__source");
    expect(items.length).toBe(5);
    const viewAll = container.querySelector(".provenance-badge__view-all");
    expect(viewAll).not.toBeNull();
    expect(viewAll?.textContent).toContain("View all sources");
  });

  it("popover shows fallback when no sources are recorded", () => {
    act(() => {
      root.render(
        <ProvenanceBadge decision={makeDecision({ provenance: {} })} />,
      );
    });
    const dot = container.querySelector<HTMLButtonElement>(
      ".provenance-badge__dot",
    );
    act(() => {
      dot!.click();
    });
    const empty = container.querySelector(".provenance-badge__empty");
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toContain("No source feedback recorded.");
    // No "View all sources" link when there are no sources.
    expect(container.querySelector(".provenance-badge__view-all")).toBeNull();
  });

  it("aria-label distinguishes edited from unedited", () => {
    act(() => {
      root.render(<ProvenanceBadge decision={makeDecision()} />);
    });
    expect(
      container.querySelector(".provenance-badge__dot")?.getAttribute("aria-label"),
    ).toBe("AI-drafted — view provenance");

    act(() => {
      root.unmount();
    });
    root = createRoot(container);
    act(() => {
      root.render(
        <ProvenanceBadge
          decision={makeDecision({
            provenance: {
              aiSeededAt: "2026-05-03T12:00:00Z",
              humanEditedAt: "2026-05-03T12:30:00Z",
            },
          })}
        />,
      );
    });
    expect(
      container.querySelector(".provenance-badge__dot")?.getAttribute("aria-label"),
    ).toBe("AI-drafted, human-edited — view provenance");
  });
});
