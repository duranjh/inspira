/**
 * Tests for src/components/ProjectStateBadge.tsx.
 *
 * Coverage:
 *   - 5 states × 3 sizes = 15 distinct render shapes.
 *   - State-class suffix mirrors the snake_case → kebab-case mapping
 *     the CSS file relies on.
 *   - Compact size renders the abbreviation visibly + the full label
 *     to assistive tech.
 *   - Large size renders the attribution row when ``attribution`` is
 *     supplied, omits it when null/undefined.
 *
 * Mirrors ShelfErrorBoundary.test.tsx — react-dom/client + act, no
 * testing-library dependency. The component is purely declarative,
 * so we don't need event simulation.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  ProjectStateBadge,
  type ProjectState,
  type ProjectStateBadgeSize,
} from "./ProjectStateBadge";

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

const STATES: ProjectState[] = [
  "pending_review",
  "in_review",
  "approved",
  "rejected",
  "summary_ready",
];

const SIZES: ProjectStateBadgeSize[] = ["compact", "default", "large"];

const STATE_CLASS: Record<ProjectState, string> = {
  pending_review: "psb--pending-review",
  in_review: "psb--in-review",
  approved: "psb--approved",
  rejected: "psb--rejected",
  summary_ready: "psb--summary-ready",
};

const STATE_LABEL: Record<ProjectState, string> = {
  pending_review: "Pending review",
  in_review: "In review",
  approved: "Approved",
  rejected: "Rejected",
  summary_ready: "Summary ready",
};

describe("ProjectStateBadge — render matrix", () => {
  for (const state of STATES) {
    for (const size of SIZES) {
      it(`renders ${state} @ ${size} with the right state + size class`, () => {
        act(() => {
          root.render(<ProjectStateBadge state={state} size={size} />);
        });
        const badge = container.querySelector(".psb")!;
        expect(badge).toBeTruthy();
        expect(badge.classList.contains(STATE_CLASS[state])).toBe(true);
        expect(badge.classList.contains(`psb--${size}`)).toBe(true);
        // Data attributes pin the contract — Session δ may target these
        // for testing instead of class names.
        expect(badge.getAttribute("data-state")).toBe(state);
        expect(badge.getAttribute("data-size")).toBe(size);
      });
    }
  }
});

describe("ProjectStateBadge — size-specific markup", () => {
  it("compact size renders abbreviation visibly + full label sr-only", () => {
    act(() => {
      root.render(<ProjectStateBadge state="approved" size="compact" />);
    });
    const abbr = container.querySelector(".psb__abbr");
    expect(abbr?.textContent).toBe("A");
    const sr = container.querySelector(".psb__sr-only");
    expect(sr?.textContent).toBe("Approved");
    // Default-size markers should NOT appear.
    expect(container.querySelector(".psb__attribution")).toBeNull();
  });

  it("default size renders the full label, no abbreviation, no attribution", () => {
    act(() => {
      root.render(<ProjectStateBadge state="in_review" size="default" />);
    });
    const label = container.querySelector(".psb__label");
    expect(label?.textContent).toBe("In review");
    expect(container.querySelector(".psb__abbr")).toBeNull();
    expect(container.querySelector(".psb__attribution")).toBeNull();
  });

  it("large size renders the attribution row when supplied", () => {
    act(() => {
      root.render(
        <ProjectStateBadge
          state="approved"
          size="large"
          attribution={{
            actorName: "Maria",
            changedAt: "4 min ago",
          }}
        />,
      );
    });
    const attribution = container.querySelector(".psb__attribution");
    expect(attribution).toBeTruthy();
    expect(attribution?.textContent).toContain("Maria");
    expect(attribution?.textContent).toContain("4 min ago");
  });

  it("large size omits the attribution row when not supplied", () => {
    act(() => {
      root.render(
        <ProjectStateBadge state="approved" size="large" />,
      );
    });
    expect(container.querySelector(".psb__attribution")).toBeNull();
    // The label is still in the row, just no attribution under it.
    expect(container.querySelector(".psb__label")?.textContent).toBe(
      "Approved",
    );
  });

  it("summary_ready @ large surfaces the post-W4 future-tag", () => {
    act(() => {
      root.render(
        <ProjectStateBadge state="summary_ready" size="large" />,
      );
    });
    const tag = container.querySelector(".psb__future-tag");
    expect(tag?.textContent).toBe("post-W4");
  });

  it("summary_ready @ default does NOT show the future-tag (size-gated)", () => {
    act(() => {
      root.render(
        <ProjectStateBadge state="summary_ready" size="default" />,
      );
    });
    expect(container.querySelector(".psb__future-tag")).toBeNull();
  });
});

describe("ProjectStateBadge — defaults + edge cases", () => {
  it("size defaults to 'default' when omitted", () => {
    act(() => {
      root.render(<ProjectStateBadge state="pending_review" />);
    });
    const badge = container.querySelector(".psb")!;
    expect(badge.classList.contains("psb--default")).toBe(true);
  });

  it("compact rejected uses the X abbreviation, not '—'", () => {
    // ``—`` is the icon; the abbreviation has to be a letter so the
    // single-char compact view stays unambiguous against the dash.
    act(() => {
      root.render(<ProjectStateBadge state="rejected" size="compact" />);
    });
    expect(container.querySelector(".psb__abbr")?.textContent).toBe("X");
  });

  it("attribution is ignored when size is not 'large'", () => {
    act(() => {
      root.render(
        <ProjectStateBadge
          state="approved"
          size="default"
          attribution={{
            actorName: "Maria",
            changedAt: "4 min ago",
          }}
        />,
      );
    });
    expect(container.querySelector(".psb__attribution")).toBeNull();
  });
});
