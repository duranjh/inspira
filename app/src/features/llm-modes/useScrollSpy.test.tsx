// Tests for useScrollSpy (#094 part 4b).
//
// Coverage:
//   - SSR safety: returns first id when IntersectionObserver missing.
//   - No intersection observed → activeId stays at the initial first id.
//   - Single intersection → returns that id.
//   - Multi-intersection → returns the FIRST in DOM order (lowest index
//     in sectionIds), which matches the rootMargin band semantics.
//
// We mock IntersectionObserver since jsdom doesn't ship one. Each test
// captures the observer callback so we can fire synthetic entries.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";

import { useScrollSpy } from "./useScrollSpy";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

type IOCallback = (
  entries: IntersectionObserverEntry[],
  observer: IntersectionObserver,
) => void;

let container: HTMLDivElement;
let root: Root;
let lastObserverCallback: IOCallback | null = null;
let observedElements: Element[] = [];

class FakeIntersectionObserver {
  callback: IOCallback;
  constructor(cb: IOCallback) {
    this.callback = cb;
    lastObserverCallback = cb;
  }
  observe(el: Element): void {
    observedElements.push(el);
  }
  unobserve(): void {
    // no-op for tests
  }
  disconnect(): void {
    observedElements = [];
  }
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  root = null;
  rootMargin = "";
  thresholds = [0];
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  lastObserverCallback = null;
  observedElements = [];
  // @ts-expect-error stub global IO for test
  globalThis.IntersectionObserver = FakeIntersectionObserver;
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.restoreAllMocks();
});

/** Test harness component that exposes the hook's return value via a
 *  data attribute on the root div, so tests can read activeId without
 *  needing a hook tester util. */
function Harness({
  ids,
}: {
  ids: readonly string[];
}): React.ReactElement {
  const ref = useRef<HTMLDivElement | null>(null);
  const active = useScrollSpy(ids, ref);
  return (
    <div ref={ref} data-active={active ?? "(none)"}>
      {ids.map((id) => (
        <section key={id} id={id}>
          {id}
        </section>
      ))}
    </div>
  );
}

function fireEntries(entries: { id: string; intersecting: boolean }[]): void {
  if (!lastObserverCallback) throw new Error("observer not set up");
  const synthetic = entries.map((e) => {
    const el = document.getElementById(e.id);
    if (!el) throw new Error(`section #${e.id} not in DOM`);
    return {
      isIntersecting: e.intersecting,
      target: el,
      intersectionRatio: e.intersecting ? 1 : 0,
      boundingClientRect: {} as DOMRect,
      intersectionRect: {} as DOMRect,
      rootBounds: null,
      time: 0,
    } as IntersectionObserverEntry;
  });
  act(() => {
    lastObserverCallback!(
      synthetic,
      {} as IntersectionObserver,
    );
  });
}

describe("useScrollSpy", () => {
  it("initial render returns the first id (no IO transitions yet)", () => {
    act(() => {
      root.render(<Harness ids={["a", "b", "c"]} />);
    });
    const div = container.querySelector("[data-active]") as HTMLElement;
    expect(div.getAttribute("data-active")).toBe("a");
  });

  it("single intersection updates activeId to that section", () => {
    act(() => {
      root.render(<Harness ids={["a", "b", "c"]} />);
    });
    fireEntries([{ id: "b", intersecting: true }]);
    const div = container.querySelector("[data-active]") as HTMLElement;
    expect(div.getAttribute("data-active")).toBe("b");
  });

  it("multi-intersection picks first in DOM order (lowest index)", () => {
    act(() => {
      root.render(<Harness ids={["a", "b", "c"]} />);
    });
    // c and b both intersect. b has lower index → wins.
    fireEntries([
      { id: "c", intersecting: true },
      { id: "b", intersecting: true },
    ]);
    const div = container.querySelector("[data-active]") as HTMLElement;
    expect(div.getAttribute("data-active")).toBe("b");
  });

  it("section exit doesn't bounce activeId — keeps the last good one", () => {
    act(() => {
      root.render(<Harness ids={["a", "b", "c"]} />);
    });
    // b enters → active=b
    fireEntries([{ id: "b", intersecting: true }]);
    // b exits with no other intersection — activeId should NOT flip
    // back to "a"; instead stays at "b" until another section enters.
    fireEntries([{ id: "b", intersecting: false }]);
    const div = container.querySelector("[data-active]") as HTMLElement;
    expect(div.getAttribute("data-active")).toBe("b");
  });
});
