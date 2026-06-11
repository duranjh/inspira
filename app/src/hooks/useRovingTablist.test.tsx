// Tests for useRovingTablist — roving-tabindex helper for ARIA
// tablists (closes #133). Wires up file-tabs + av-mode tablist in
// CodeEditor.tsx but is fully generic so the test exercises a
// minimal harness.

import React, { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useRovingTablist } from "./useRovingTablist";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const TAB_IDS = ["a", "b", "c", "d"] as const;
type TabId = (typeof TAB_IDS)[number];

function Harness({
  initial = "a",
  onSelectSpy,
}: {
  initial?: TabId;
  onSelectSpy?: (id: TabId) => void;
}) {
  const [active, setActive] = useState<TabId>(initial);
  const tablist = useRovingTablist<TabId>({
    ids: TAB_IDS,
    activeId: active,
    onSelect: (id) => {
      onSelectSpy?.(id);
      setActive(id);
    },
  });
  return (
    <div role="tablist" aria-label="Test tabs">
      {TAB_IDS.map((id) => (
        <button
          key={id}
          ref={tablist.registerRef(id)}
          type="button"
          role="tab"
          data-id={id}
          aria-selected={id === active}
          tabIndex={tablist.tabIndex(id)}
          onClick={() => setActive(id)}
          onKeyDown={tablist.onKeyDown(id)}
        >
          {id}
        </button>
      ))}
    </div>
  );
}

function fireKey(el: HTMLElement, key: string) {
  el.dispatchEvent(
    new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
  );
}

function queryTabs(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("[role=tab]"));
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

describe("useRovingTablist", () => {
  it("only the active tab is in the Tab sequence (tabIndex=0)", () => {
    act(() => {
      root.render(<Harness initial="b" />);
    });
    const tabs = queryTabs();
    expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0, -1, -1]);
  });

  it("ArrowRight advances + selects + focuses next tab", async () => {
    act(() => {
      root.render(<Harness initial="a" />);
    });
    let tabs = queryTabs();
    tabs[0].focus();
    act(() => {
      fireKey(tabs[0], "ArrowRight");
    });
    // requestAnimationFrame is used to defer focus — wait for it.
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
    tabs = queryTabs();
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[1]);
  });

  it("ArrowLeft wraps backward at the start", async () => {
    act(() => {
      root.render(<Harness initial="a" />);
    });
    let tabs = queryTabs();
    tabs[0].focus();
    act(() => {
      fireKey(tabs[0], "ArrowLeft");
    });
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
    tabs = queryTabs();
    expect(tabs[3].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[3]);
  });

  it("ArrowRight wraps forward at the end", async () => {
    act(() => {
      root.render(<Harness initial="d" />);
    });
    let tabs = queryTabs();
    tabs[3].focus();
    act(() => {
      fireKey(tabs[3], "ArrowRight");
    });
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
    tabs = queryTabs();
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("Home jumps to the first tab; End to the last", async () => {
    act(() => {
      root.render(<Harness initial="b" />);
    });
    let tabs = queryTabs();
    tabs[1].focus();
    act(() => {
      fireKey(tabs[1], "End");
    });
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
    tabs = queryTabs();
    expect(tabs[3].getAttribute("aria-selected")).toBe("true");
    act(() => {
      fireKey(tabs[3], "Home");
    });
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
    tabs = queryTabs();
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  });

  it("ignores unrelated keys (Tab, letter keys, etc.)", () => {
    act(() => {
      root.render(<Harness initial="a" />);
    });
    const tabs = queryTabs();
    tabs[0].focus();
    act(() => {
      fireKey(tabs[0], "Tab");
      fireKey(tabs[0], "a");
    });
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[0]);
  });
});
