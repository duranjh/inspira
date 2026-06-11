/**
 * Tests for src/components/ShelfErrorBoundary.tsx.
 *
 * Coverage:
 *   - Children render unchanged when they don't throw.
 *   - Fallback renders when a child throws + Sentry / console logging fires.
 *   - "Dismiss" clears state AND calls the onDismiss callback.
 *   - resetKey change clears a captured error without user interaction.
 *   - Regression: ProjectsListPage's flat-grid → shelves-view transition
 *     is stable. Historically the `useMemo` that computes `visible` sat
 *     BELOW an early return for `shelvesEnabled`, so flipping shelves
 *     from empty to non-empty changed the hook count mid-lifecycle and
 *     React threw "Rendered fewer hooks than expected." That manifested
 *     as: user creates their first shelf → app crashes → reload stays
 *     stuck on the error page (same transition fires again). We simulate
 *     the transition here and assert no render throws escape the tree.
 *
 * We render with `react-dom/client` + `act` from react — no
 * testing-library dependency on this codebase.
 */

import React, { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShelfErrorBoundary } from "./ShelfErrorBoundary";
import { ProjectsListPage } from "../features/projects/ProjectsListPage";
import type { Shelf, V2Project } from "../features/inspira/api";

// ---------------------------------------------------------------------------
// Harness
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
  container.id = "__sbound";
  document.body.appendChild(container);
  root = createRoot(container);
  // Silence noisy React-caught-error logging. We still spy so tests that
  // care about our own explicit log lines can assert them.
  consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  consoleErrorSpy.mockRestore();
});

function Thrower({ shouldThrow }: { shouldThrow: boolean }): React.ReactElement {
  if (shouldThrow) throw new Error("shelf-boom");
  return <div data-testid="thrower-ok">ok</div>;
}

function findByText(text: string): HTMLElement | null {
  const all = container.querySelectorAll<HTMLElement>("*");
  for (const el of all) {
    if (el.textContent?.trim() === text) return el;
  }
  return null;
}

// Minimal fixtures for the integration test.
function makeProject(id: string, title: string): V2Project {
  const now = new Date().toISOString();
  return {
    project_id: id,
    user_id: "u1",
    title,
    created_at: now,
    updated_at: now,
  };
}

function makeShelf(id: string, name: string): Shelf {
  const now = new Date().toISOString();
  return {
    shelf_id: id,
    user_id: "u1",
    name,
    sort_order: 0,
    created_at: now,
    updated_at: now,
    project_count: 0,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ShelfErrorBoundary", () => {
  it("renders children unchanged when they don't throw", () => {
    act(() => {
      root.render(
        <ShelfErrorBoundary>
          <Thrower shouldThrow={false} />
        </ShelfErrorBoundary>,
      );
    });
    expect(container.querySelector("[data-testid=thrower-ok]")).not.toBeNull();
    expect(findByText("Your shelves hit a snag.")).toBeNull();
  });

  it("renders the fallback heading when a child throws", () => {
    act(() => {
      root.render(
        <ShelfErrorBoundary>
          <Thrower shouldThrow={true} />
        </ShelfErrorBoundary>,
      );
    });
    const heading = findByText("Your shelves hit a snag.");
    expect(heading).not.toBeNull();
    expect(heading?.tagName).toBe("H2");
    expect(container.querySelector("[role=alert]")).not.toBeNull();
  });

  it("logs to console.error with a [ShelfErrorBoundary] tag", () => {
    act(() => {
      root.render(
        <ShelfErrorBoundary>
          <Thrower shouldThrow={true} />
        </ShelfErrorBoundary>,
      );
    });
    const calls = consoleErrorSpy.mock.calls as unknown[][];
    const hasOurLog = calls.some((call) =>
      call.some(
        (arg) =>
          typeof arg === "string" &&
          arg.includes("[ShelfErrorBoundary] caught render error:"),
      ),
    );
    expect(hasOurLog).toBe(true);
  });

  it("Dismiss clears captured state and fires onDismiss", () => {
    const onDismiss = vi.fn();

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
          <ShelfErrorBoundary onDismiss={onDismiss}>
            <Thrower shouldThrow={fail} />
          </ShelfErrorBoundary>
        </div>
      );
    }

    act(() => {
      root.render(<Harness />);
    });
    // Fallback visible.
    expect(findByText("Your shelves hit a snag.")).not.toBeNull();

    // "Fix" the child so the next render succeeds post-dismiss.
    const fixBtn = container.querySelector<HTMLButtonElement>(
      "[data-testid=harness-fix]",
    );
    expect(fixBtn).not.toBeNull();
    act(() => {
      fixBtn!.click();
    });

    // Click Dismiss. We reach for the button by class to sidestep a
    // `findByText` edge case where the <p> body contains the word
    // "Dismiss" and outranks the button in document order.
    const dismissBtn = container.querySelector<HTMLButtonElement>(
      ".inspira-boundary__btn--primary",
    );
    expect(dismissBtn).not.toBeNull();
    expect(dismissBtn?.textContent?.trim()).toBe("Dismiss");
    act(() => {
      dismissBtn!.click();
    });

    // onDismiss fired + children are back.
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(container.querySelector("[data-testid=thrower-ok]")).not.toBeNull();
    expect(findByText("Your shelves hit a snag.")).toBeNull();
  });

  it("clears the captured error when resetKey changes", () => {
    function Harness({
      resetKey,
      fail,
    }: {
      resetKey: number;
      fail: boolean;
    }): React.ReactElement {
      return (
        <ShelfErrorBoundary resetKey={resetKey}>
          <Thrower shouldThrow={fail} />
        </ShelfErrorBoundary>
      );
    }

    act(() => {
      root.render(<Harness resetKey={1} fail={true} />);
    });
    expect(findByText("Your shelves hit a snag.")).not.toBeNull();

    // Reset key changes AND the child stops throwing — boundary should
    // re-render children on the next pass.
    act(() => {
      root.render(<Harness resetKey={2} fail={false} />);
    });
    expect(findByText("Your shelves hit a snag.")).toBeNull();
    expect(container.querySelector("[data-testid=thrower-ok]")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Regression: ProjectsListPage hook-count stability on shelves-enabled flip.
// ---------------------------------------------------------------------------

describe("ProjectsListPage — shelves-enabled transition (regression)", () => {
  it("does not throw a Rules-of-Hooks error when shelves flip from empty to non-empty", () => {
    // Track any render throw that escapes. Before the fix, flipping
    // shelves from `[]` to `[shelf]` skipped a useMemo call below the
    // early return, and React threw from the dispatcher. We re-render
    // with both states and assert neither path throws.
    const shelfHandlerNoop = async () => {};
    const moveNoop = async () => {};
    const renameNoop = async () => {};
    const deleteNoop = async () => {};

    const commonProps = {
      projects: [
        makeProject("p1", "First project"),
        makeProject("p2", "Second project"),
      ] as V2Project[],
      onOpenProject: () => {},
      onCreateNew: () => {},
      onRename: renameNoop,
      onDelete: deleteNoop,
      onCreateNewShelf: shelfHandlerNoop,
      onRenameShelf: async (_id: string, _name: string) => {},
      onDeleteShelf: async (_id: string) => {},
      onMoveProjectToShelf: moveNoop,
    };

    // First render: zero shelves → flat grid. All hooks run.
    act(() => {
      root.render(<ProjectsListPage {...commonProps} shelves={[]} />);
    });
    // Sanity: flat-grid header is on screen.
    expect(container.querySelector(".projects-list")).not.toBeNull();

    // Now flip: one shelf appears. Before the fix this render threw
    // because the `useMemo` under the early-return was skipped, changing
    // the hook count. After the fix, all hooks stay above the early
    // return, so this transition is clean.
    expect(() => {
      act(() => {
        root.render(
          <ProjectsListPage
            {...commonProps}
            shelves={[makeShelf("s1", "Research")]}
          />,
        );
      });
    }).not.toThrow();

    // The ShelvesView surface is on screen — not the flat-grid toolbar.
    expect(container.querySelector(".shelves-view")).not.toBeNull();

    // Flip back to zero shelves (e.g. user deletes the only shelf). This
    // is the reverse transition; also must not throw.
    expect(() => {
      act(() => {
        root.render(<ProjectsListPage {...commonProps} shelves={[]} />);
      });
    }).not.toThrow();
    expect(container.querySelector(".projects-list")).not.toBeNull();
  });
});
