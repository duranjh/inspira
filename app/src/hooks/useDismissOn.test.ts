/**
 * Tests for useDismissOn — Esc + click-outside dismiss.
 *
 * Coverage:
 *   - Esc → onDismiss called
 *   - mousedown outside clickOutsideRef → onDismiss called
 *   - mousedown inside clickOutsideRef → onDismiss NOT called
 *   - enabled: false → no listeners attached (no dismiss)
 *   - esc: false → Esc is ignored
 *   - Listeners are removed on unmount (no leak)
 */

import React, { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDismissOn, type UseDismissOnOptions } from "./useDismissOn";

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

type HarnessOpts = Omit<UseDismissOnOptions, "clickOutsideRef"> & {
  withClickOutsideRef?: boolean;
};

function Harness(props: HarnessOpts): React.ReactElement {
  const ref = useRef<HTMLDivElement>(null);
  const { withClickOutsideRef, ...rest } = props;
  useDismissOn({
    ...rest,
    clickOutsideRef: withClickOutsideRef ? ref : undefined,
  });
  return React.createElement(
    "div",
    { ref, "data-testid": "panel" },
    React.createElement("span", { "data-testid": "inside" }, "inside"),
  );
}

function render(props: HarnessOpts): void {
  act(() => {
    root.render(React.createElement(Harness, props));
  });
}

function $(testid: string): HTMLElement {
  const el = container.querySelector(`[data-testid="${testid}"]`);
  if (!el) throw new Error(`Element [data-testid="${testid}"] not found`);
  return el as HTMLElement;
}

describe("useDismissOn — Escape", () => {
  it("calls onDismiss when Esc is pressed", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss });
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("ignores Esc when esc is false", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss, esc: false });
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("ignores Esc when enabled is false", () => {
    const onDismiss = vi.fn();
    render({ enabled: false, onDismiss });
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("ignores non-Esc keys", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss });
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
  });
});

describe("useDismissOn — click outside", () => {
  it("calls onDismiss on mousedown outside the ref", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss, withClickOutsideRef: true });
    const outside = document.createElement("div");
    document.body.appendChild(outside);
    act(() => {
      outside.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
    outside.remove();
  });

  it("does NOT call onDismiss on mousedown inside the ref", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss, withClickOutsideRef: true });
    act(() => {
      $("inside").dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("does not attach mousedown listener when no clickOutsideRef", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss });
    const outside = document.createElement("div");
    document.body.appendChild(outside);
    act(() => {
      outside.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
    outside.remove();
  });

  it("ignores mousedown when enabled is false", () => {
    const onDismiss = vi.fn();
    render({ enabled: false, onDismiss, withClickOutsideRef: true });
    const outside = document.createElement("div");
    document.body.appendChild(outside);
    act(() => {
      outside.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
    outside.remove();
  });
});

describe("useDismissOn — cleanup", () => {
  it("removes listeners on unmount", () => {
    const onDismiss = vi.fn();
    render({ enabled: true, onDismiss, withClickOutsideRef: true });
    act(() => {
      root.unmount();
    });
    // Re-create root so afterEach can call unmount again without error.
    root = createRoot(container);
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      document.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(onDismiss).not.toHaveBeenCalled();
  });
});
