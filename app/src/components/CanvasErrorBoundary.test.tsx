/**
 * Tests for src/components/CanvasErrorBoundary.tsx.
 *
 * Coverage:
 *   - child throws → fallback renders with the heading
 *   - "Reload canvas" click → children re-mount (toggling child increments)
 *   - componentDidCatch logs via console.error
 *   - projectTitle prop flows into the heading
 *
 * We render with `react-dom/client` + `act` from react — no testing-library
 * dependency on this codebase. React 19 exposes `act` directly from `react`.
 */

import React, { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CanvasErrorBoundary } from "./CanvasErrorBoundary";

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------
//
// Each test mounts its own <div id="__cbound"> under document.body, creates
// a root, and tears both down in afterEach. IS_REACT_ACT_ENVIRONMENT silences
// React's "not wrapped in act" warning in strict mode.
// ---------------------------------------------------------------------------

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  container.id = "__cbound";
  document.body.appendChild(container);
  root = createRoot(container);
  // Silence the console.error noise React emits for caught render errors.
  // We still spy so we can assert our own error log fires.
  consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  consoleErrorSpy.mockRestore();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Child component that throws on its first render when `shouldThrow` is
 * true. Used to trigger the boundary's catch path deterministically.
 */
function Thrower({ shouldThrow }: { shouldThrow: boolean }): React.ReactElement {
  if (shouldThrow) {
    throw new Error("boom");
  }
  return <div data-testid="thrower-ok">ok</div>;
}

/**
 * A toggling child: renders a div that increments a counter each time it's
 * remounted. Used to prove the "Reload canvas" button actually re-mounts the
 * children — not just flips an internal flag without re-creating the subtree.
 *
 * We keep the mount counter outside React state so it survives the unmount
 * that happens when the boundary swaps its subtree for the fallback UI.
 */
let mountCounter = 0;

function TogglingChild({ shouldThrow }: { shouldThrow: boolean }): React.ReactElement {
  const [mountedCount] = useState(() => {
    mountCounter += 1;
    return mountCounter;
  });
  if (shouldThrow) throw new Error("boom");
  return (
    <div data-testid="toggling-child" data-mount-count={mountedCount}>
      mount #{mountedCount}
    </div>
  );
}

function findByText(text: string): HTMLElement | null {
  const all = container.querySelectorAll<HTMLElement>("*");
  for (const el of all) {
    // textContent includes nested children; we want direct text matches
    // to avoid the wrapping div masking a button lookup.
    if (el.textContent?.trim() === text) return el;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CanvasErrorBoundary", () => {
  it("renders children unchanged when they don't throw", () => {
    act(() => {
      root.render(
        <CanvasErrorBoundary>
          <Thrower shouldThrow={false} />
        </CanvasErrorBoundary>,
      );
    });
    expect(container.querySelector("[data-testid=thrower-ok]")).not.toBeNull();
    // The fallback heading must NOT be present.
    expect(findByText("This canvas hit a snag.")).toBeNull();
  });

  it("renders the fallback heading when a child throws", () => {
    act(() => {
      root.render(
        <CanvasErrorBoundary>
          <Thrower shouldThrow={true} />
        </CanvasErrorBoundary>,
      );
    });
    const heading = findByText("This canvas hit a snag.");
    expect(heading).not.toBeNull();
    expect(heading?.tagName).toBe("H2");
    // The role=alert wrapper makes screen readers announce the crash.
    expect(container.querySelector("[role=alert]")).not.toBeNull();
  });

  it("flows projectTitle into the heading", () => {
    act(() => {
      root.render(
        <CanvasErrorBoundary projectTitle="Sunday Garden Plan">
          <Thrower shouldThrow={true} />
        </CanvasErrorBoundary>,
      );
    });
    expect(
      findByText("This canvas (Sunday Garden Plan) hit a snag."),
    ).not.toBeNull();
    // Generic variant must NOT also be present.
    expect(findByText("This canvas hit a snag.")).toBeNull();
  });

  it("calls console.error when componentDidCatch fires", () => {
    act(() => {
      root.render(
        <CanvasErrorBoundary>
          <Thrower shouldThrow={true} />
        </CanvasErrorBoundary>,
      );
    });
    // Our explicit log tag should show up somewhere in the spy calls.
    const calls = consoleErrorSpy.mock.calls as unknown[][];
    const hasOurLog = calls.some((call) =>
      call.some(
        (arg) =>
          typeof arg === "string" &&
          arg.includes("[CanvasErrorBoundary] caught render error:"),
      ),
    );
    expect(hasOurLog).toBe(true);
  });

  it('"Reload canvas" re-mounts children (toggling counter increments)', () => {
    // Parent wrapper that controls whether the child throws, so we can
    // simulate a crash, click Reload, and then have the child stop
    // throwing on re-mount.
    function Harness(): React.ReactElement {
      const [fail, setFail] = useState(true);
      return (
        <div>
          <button
            type="button"
            data-testid="harness-fix"
            onClick={() => setFail(false)}
          >
            fix
          </button>
          <CanvasErrorBoundary>
            <TogglingChild shouldThrow={fail} />
          </CanvasErrorBoundary>
        </div>
      );
    }

    mountCounter = 0;
    act(() => {
      root.render(<Harness />);
    });

    // Initial attempt threw — fallback rendered. React's render phase may
    // run a child more than once before the boundary catches (strict-mode
    // rerender + speculative render), so rather than pinning the exact
    // counter value we just snapshot it here and assert the delta later.
    expect(findByText("This canvas hit a snag.")).not.toBeNull();
    const countBeforeReload = mountCounter;
    expect(countBeforeReload).toBeGreaterThanOrEqual(1);

    // "Fix" the harness so the next render of TogglingChild succeeds.
    const fixBtn = container.querySelector<HTMLButtonElement>(
      "[data-testid=harness-fix]",
    );
    expect(fixBtn).not.toBeNull();
    act(() => {
      fixBtn!.click();
    });

    // Now click the boundary's "Reload canvas" button. It clears state →
    // children re-mount → TogglingChild's counter bumps.
    const reloadBtn = findByText("Reload canvas") as HTMLButtonElement | null;
    expect(reloadBtn).not.toBeNull();
    act(() => {
      reloadBtn!.click();
    });

    // The toggling child is back in the tree with a fresh mount counter
    // strictly greater than what we had at the point of the crash. That's
    // the real proof of a re-mount — the exact delta depends on React's
    // internal dev-time double-invokes.
    const toggled = container.querySelector<HTMLElement>(
      "[data-testid=toggling-child]",
    );
    expect(toggled).not.toBeNull();
    const countAfterReload = Number(toggled!.getAttribute("data-mount-count"));
    expect(countAfterReload).toBeGreaterThan(countBeforeReload);
    // Fallback heading is gone.
    expect(findByText("This canvas hit a snag.")).toBeNull();
  });
});
