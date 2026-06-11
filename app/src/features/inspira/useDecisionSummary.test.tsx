// Pattern matches WorkspaceContext.test.tsx — react-dom/client + React 19
// `act` (no testing-library dep on this codebase). A tiny Probe component
// captures the hook return value into a module-level variable so the
// surrounding test can assert against it.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mockDecisionSummary, type DecisionSummary } from "./decisionSummary";
import {
  ORCHESTRATOR_COMPLETED_EVENT,
  useDecisionSummary,
  type UseDecisionSummary,
} from "./useDecisionSummary";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let captured: UseDecisionSummary | null = null;

function Probe(): React.ReactElement {
  captured = useDecisionSummary();
  return <div data-testid="probe" />;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  captured = null;
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

function dispatchComplete(detail: DecisionSummary): void {
  act(() => {
    window.dispatchEvent(
      new CustomEvent(ORCHESTRATOR_COMPLETED_EVENT, { detail }),
    );
  });
}

describe("useDecisionSummary", () => {
  it("starts with summary=null and drawer closed", async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    expect(captured?.summary).toBeNull();
    expect(captured?.drawerOpen).toBe(false);
  });

  it("opens the drawer with the dispatched summary on the orchestrator event", async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    dispatchComplete(mockDecisionSummary);
    expect(captured?.summary).toBe(mockDecisionSummary);
    expect(captured?.drawerOpen).toBe(true);
  });

  it("close() retains the summary so the chip can re-open it", async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    dispatchComplete(mockDecisionSummary);

    act(() => {
      captured?.close();
    });
    expect(captured?.drawerOpen).toBe(false);
    expect(captured?.summary).toBe(mockDecisionSummary);

    act(() => {
      captured?.open();
    });
    expect(captured?.drawerOpen).toBe(true);
  });

  it("triggerMock dispatches the same event the production wiring uses", async () => {
    let received: unknown = null;
    const listener = (ev: Event) => {
      received = (ev as CustomEvent).detail;
    };
    window.addEventListener(ORCHESTRATOR_COMPLETED_EVENT, listener);

    await act(async () => {
      root.render(<Probe />);
    });
    act(() => {
      captured?.triggerMock();
    });

    expect(received).toBe(mockDecisionSummary);
    window.removeEventListener(ORCHESTRATOR_COMPLETED_EVENT, listener);
  });

  it("a second event while open replaces the summary silently", async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    dispatchComplete(mockDecisionSummary);
    expect(captured?.drawerOpen).toBe(true);

    const second: DecisionSummary = {
      ...mockDecisionSummary,
      summary_json: {
        ...mockDecisionSummary.summary_json,
        headline: "A different finding from a later orchestrator run.",
      },
    };
    dispatchComplete(second);
    expect(captured?.summary).toBe(second);
    expect(captured?.drawerOpen).toBe(true);
  });

  it("ignores events with no detail (defensive)", async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    act(() => {
      window.dispatchEvent(new CustomEvent(ORCHESTRATOR_COMPLETED_EVENT));
    });
    expect(captured?.summary).toBeNull();
    expect(captured?.drawerOpen).toBe(false);
  });

  it("removes its listener on unmount", async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    act(() => {
      root.unmount();
    });
    captured = null;

    act(() => {
      window.dispatchEvent(
        new CustomEvent(ORCHESTRATOR_COMPLETED_EVENT, {
          detail: mockDecisionSummary,
        }),
      );
    });
    expect(captured).toBeNull();

    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });
});
