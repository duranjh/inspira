/**
 * Tests for ConflictBanner — gold-wash pill + resolution modal.
 *
 * Coverage:
 *   - not rendered initially
 *   - renders on inspira:sse:conflict.detected with topic titles
 *   - inspira:sse:conflict.resolved unmounts it
 *   - View resolution → opens the modal
 *   - modal shows TOPIC A / TOPIC B / VS layout
 *   - modal shows ORCHESTRATOR RESOLUTION section
 *   - matching conflict_id resolves; mismatching does NOT
 */

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConflictBanner } from "./ConflictBanner";

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

const sampleConflict = {
  conflict_id: "c-1",
  topics: [
    {
      topic_id: "t-1",
      title: "Service-worker rollback",
      statement: "Disable the service worker on Safari iOS.",
    },
    {
      topic_id: "t-2",
      title: "Cookie migration",
      statement: "Migrate session cookies to first-party storage.",
    },
  ],
  resolution: "Cookie migration takes precedence; service worker stays disabled.",
};

describe("ConflictBanner", () => {
  it("renders nothing initially", () => {
    act(() => {
      root.render(<ConflictBanner />);
    });
    expect(container.querySelector(".conflict-banner")).toBeNull();
  });

  it("renders on conflict.detected with both topic titles", () => {
    act(() => {
      root.render(<ConflictBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:conflict.detected", sampleConflict);
    });
    const banner = container.querySelector(".conflict-banner");
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain("Service-worker rollback");
    expect(banner?.textContent).toContain("Cookie migration");
    expect(banner?.textContent).toContain("Orchestrator resolving conflict");
  });

  it("conflict.resolved with matching id unmounts banner", () => {
    act(() => {
      root.render(<ConflictBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:conflict.detected", sampleConflict);
    });
    expect(container.querySelector(".conflict-banner")).not.toBeNull();
    act(() => {
      fireSSE("inspira:sse:conflict.resolved", { conflict_id: "c-1" });
    });
    expect(container.querySelector(".conflict-banner")).toBeNull();
  });

  it("conflict.resolved with MISMATCHING id leaves banner alone", () => {
    act(() => {
      root.render(<ConflictBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:conflict.detected", sampleConflict);
    });
    act(() => {
      fireSSE("inspira:sse:conflict.resolved", { conflict_id: "c-other" });
    });
    expect(container.querySelector(".conflict-banner")).not.toBeNull();
  });

  it("View resolution → opens modal with VS layout and resolution section", () => {
    act(() => {
      root.render(<ConflictBanner />);
    });
    act(() => {
      fireSSE("inspira:sse:conflict.detected", sampleConflict);
    });
    const link = container.querySelector<HTMLButtonElement>(
      ".conflict-banner__link",
    );
    expect(link).not.toBeNull();
    act(() => {
      link!.click();
    });
    // Modal mounts via base Dialog under document.body — query the wider
    // document, not just our test container.
    const modal = document.querySelector("[role='dialog']");
    expect(modal).not.toBeNull();
    const labels = Array.from(
      document.querySelectorAll(".conflict-modal__label"),
    ).map((el) => el.textContent?.trim());
    expect(labels).toContain("TOPIC A");
    expect(labels).toContain("TOPIC B");
    expect(labels).toContain("ORCHESTRATOR RESOLUTION");
    const vs = document.querySelector(".conflict-modal__vs");
    expect(vs?.textContent).toBe("VS");
    const resolutionText = document.querySelector(
      ".conflict-modal__resolution-text",
    );
    expect(resolutionText?.textContent).toContain(
      "Cookie migration takes precedence",
    );
  });
});
