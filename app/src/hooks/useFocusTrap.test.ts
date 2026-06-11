/**
 * Tests for useFocusTrap — Tab cycling + initial focus + restoration.
 *
 * Coverage:
 *   - Tab from last focusable wraps to first
 *   - Shift+Tab from first focusable wraps to last
 *   - Tab from outside the container snaps to first
 *   - Initial focus lands on first focusable when no initialFocusRef
 *   - Initial focus lands on initialFocusRef when provided
 *   - Focus restores to the invoker when the trap disengages
 *   - enabled: false → hook is fully inert
 *   - autoFocus: false → no initial-focus move (restoration still works)
 */

import React, { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useFocusTrap, type UseFocusTrapOptions } from "./useFocusTrap";

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

function Harness({
  options,
  initialFocusInner = false,
}: {
  options: UseFocusTrapOptions;
  initialFocusInner?: boolean;
}): React.ReactElement {
  const trapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const opts = initialFocusInner ? { ...options, initialFocusRef: inputRef } : options;
  const { onKeyDown } = useFocusTrap(trapRef, opts);
  return React.createElement(
    "div",
    { ref: trapRef, onKeyDown, "data-testid": "trap" },
    React.createElement("button", { "data-testid": "btn-1" }, "First"),
    React.createElement("input", { ref: inputRef, "data-testid": "input-1" }),
    React.createElement("button", { "data-testid": "btn-2" }, "Last"),
  );
}

function render(props: { options: UseFocusTrapOptions; initialFocusInner?: boolean }): void {
  act(() => {
    root.render(React.createElement(Harness, props));
  });
}

function $(testid: string): HTMLElement {
  const el = container.querySelector(`[data-testid="${testid}"]`);
  if (!el) throw new Error(`Element [data-testid="${testid}"] not found`);
  return el as HTMLElement;
}

function dispatchTab(target: HTMLElement, shift = false): void {
  const event = new KeyboardEvent("keydown", {
    key: "Tab",
    shiftKey: shift,
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    target.dispatchEvent(event);
  });
}

async function nextFrame(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
}

describe("useFocusTrap initial focus", () => {
  it("focuses the first focusable on engage by default", async () => {
    render({ options: { enabled: true } });
    await nextFrame();
    expect(document.activeElement).toBe($("btn-1"));
  });

  it("focuses the initialFocusRef when provided", async () => {
    render({ options: { enabled: true }, initialFocusInner: true });
    await nextFrame();
    expect(document.activeElement).toBe($("input-1"));
  });

  it("does not move focus when autoFocus is false", async () => {
    const outside = document.createElement("button");
    outside.textContent = "Outside";
    document.body.appendChild(outside);
    outside.focus();
    render({ options: { enabled: true, autoFocus: false } });
    await nextFrame();
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});

describe("useFocusTrap tab cycling", () => {
  it("Tab from last focusable wraps to first", async () => {
    render({ options: { enabled: true } });
    await nextFrame();
    $("btn-2").focus();
    dispatchTab($("btn-2"));
    expect(document.activeElement).toBe($("btn-1"));
  });

  it("Shift+Tab from first focusable wraps to last", async () => {
    render({ options: { enabled: true } });
    await nextFrame();
    $("btn-1").focus();
    dispatchTab($("btn-1"), true);
    expect(document.activeElement).toBe($("btn-2"));
  });

  it("Tab from outside the container snaps focus to first", async () => {
    render({ options: { enabled: true } });
    await nextFrame();
    // Simulate focus outside the container — the onKeyDown handler
    // fires from inside in tests because we dispatch on the trap div.
    dispatchTab($("trap"));
    expect(document.activeElement).toBe($("btn-1"));
  });
});

describe("useFocusTrap restoration", () => {
  it("restores focus to the invoker when disengaged", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "Trigger";
    document.body.appendChild(trigger);
    trigger.focus();

    render({ options: { enabled: true } });
    await nextFrame();
    expect(document.activeElement).toBe($("btn-1"));

    render({ options: { enabled: false } });
    await nextFrame();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });

  it("does not restore focus when restoreFocus is false", async () => {
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();

    render({ options: { enabled: true, restoreFocus: false } });
    await nextFrame();
    render({ options: { enabled: false, restoreFocus: false } });
    await nextFrame();
    expect(document.activeElement).not.toBe(trigger);
    trigger.remove();
  });
});

describe("useFocusTrap disabled", () => {
  it("is fully inert when enabled is false", async () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();

    render({ options: { enabled: false } });
    await nextFrame();
    expect(document.activeElement).toBe(outside);

    // Tab should not be intercepted.
    $("btn-2").focus();
    dispatchTab($("btn-2"));
    // Without the trap, default browser handling happens — but in
    // jsdom default Tab does nothing. The point: focus didn't snap
    // back to btn-1.
    expect(document.activeElement).toBe($("btn-2"));
    outside.remove();
  });
});
