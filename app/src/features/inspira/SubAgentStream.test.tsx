/**
 * Tests for SubAgentStream — live decision feed for the Topic Detail.
 *
 * Coverage:
 *   - renders nothing when isActive=false (regardless of events)
 *   - renders trigger but not feed when isActive=true and isOpen=false
 *   - renders feed with all rows when isOpen=true
 *   - empty placeholder when isActive=true and events.length === 0
 *   - rows render in event order
 *   - decision count badge updates with events.length
 *   - caps DOM at MAX_VISIBLE (50) — 60 events render the latest 50
 *   - timestamp formatting falls back to raw string on parse failure
 *   - onToggle invoked on trigger click
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SubAgentStream,
  type LiveDecisionEvent,
} from "./SubAgentStream";

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

function makeEvent(i: number): LiveDecisionEvent {
  return {
    decision_id: `dec-${i}`,
    statement: `Decision ${i}`,
    rationale: null,
    subject: "subj",
    received_at: "2026-05-03T10:30:00Z",
  };
}

describe("SubAgentStream", () => {
  it("renders nothing when isActive=false", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[makeEvent(0)]}
          isActive={false}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    expect(container.querySelector(".topic-detail__stream")).toBeNull();
  });

  it("renders trigger but not feed when isOpen=false", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[makeEvent(0)]}
          isActive={true}
          isOpen={false}
          onToggle={() => {}}
        />,
      );
    });
    expect(container.querySelector(".topic-detail__stream")).not.toBeNull();
    expect(container.querySelector(".topic-detail__stream-trigger")).not.toBeNull();
    expect(container.querySelector(".topic-detail__stream-feed")).toBeNull();
  });

  it("renders feed rows when isOpen=true", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[makeEvent(0), makeEvent(1), makeEvent(2)]}
          isActive={true}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const rows = container.querySelectorAll(".topic-detail__stream-row");
    expect(rows.length).toBe(3);
  });

  it("shows empty placeholder when isActive but no events yet", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[]}
          isActive={true}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const empty = container.querySelector(".topic-detail__stream-empty");
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toContain("Waiting");
  });

  it("renders rows in event order", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[makeEvent(0), makeEvent(1), makeEvent(2)]}
          isActive={true}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const texts = Array.from(
      container.querySelectorAll(".topic-detail__stream-text"),
    ).map((el) => el.textContent);
    expect(texts).toEqual(["Decision 0", "Decision 1", "Decision 2"]);
  });

  it("count badge reflects events.length", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[makeEvent(0), makeEvent(1)]}
          isActive={true}
          isOpen={false}
          onToggle={() => {}}
        />,
      );
    });
    const count = container.querySelector(".topic-detail__stream-count");
    expect(count?.textContent).toBe("2 decisions");
  });

  it("caps visible rows at 50 even if 60 events arrived", () => {
    const events = Array.from({ length: 60 }, (_, i) => makeEvent(i));
    act(() => {
      root.render(
        <SubAgentStream
          events={events}
          isActive={true}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const rows = container.querySelectorAll(".topic-detail__stream-row");
    expect(rows.length).toBe(50);
    // The latest 50 are kept — first visible is event #10.
    expect(
      container.querySelector(".topic-detail__stream-text")?.textContent,
    ).toBe("Decision 10");
  });

  it("falls back to raw string when timestamp can't parse", () => {
    act(() => {
      root.render(
        <SubAgentStream
          events={[
            { ...makeEvent(0), received_at: "not-a-date" },
          ]}
          isActive={true}
          isOpen={true}
          onToggle={() => {}}
        />,
      );
    });
    const ts = container.querySelector(".topic-detail__stream-ts");
    expect(ts?.textContent).toBe("not-a-date");
  });

  it("invokes onToggle when the trigger is clicked", () => {
    const onToggle = vi.fn();
    act(() => {
      root.render(
        <SubAgentStream
          events={[]}
          isActive={true}
          isOpen={false}
          onToggle={onToggle}
        />,
      );
    });
    const trigger = container.querySelector(
      ".topic-detail__stream-trigger",
    ) as HTMLButtonElement;
    act(() => {
      trigger.click();
    });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
