/**
 * Tests for ManualOverrideDialog (W5).
 *
 * Coverage:
 *   - Confirm button is disabled until a non-empty note is typed.
 *   - Whitespace-only notes don't satisfy the requirement.
 *   - Cancel button calls onCancel.
 *   - Escape key cancels.
 *   - Backdrop click cancels but a click on the panel does not.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { ManualOverrideDialog } from "./ManualOverrideDialog";

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

/**
 * React's controlled inputs swallow direct ``el.value = ...`` assignments
 * unless we go through the native setter. Without this helper, the
 * synthetic ``input`` event fires but React's onChange never picks up
 * the new value (the descriptor was already overridden by React's
 * controlled-input wrapper).
 */
function setTextareaValue(
  textarea: HTMLTextAreaElement, value: string,
): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype, "value",
  )?.set;
  setter?.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function render(props: {
  onConfirm?: (note: string, rerun: boolean) => void;
  onCancel?: () => void;
} = {}) {
  const onConfirm = props.onConfirm ?? vi.fn();
  const onCancel = props.onCancel ?? vi.fn();
  act(() => {
    root.render(
      <ManualOverrideDialog
        projectTitle="Demo project"
        fromColumn="queue"
        toColumn="review"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
  });
  return { onConfirm, onCancel };
}

describe("ManualOverrideDialog — note is optional", () => {
  it("confirm button is enabled even when note is empty (audit logs actor regardless)", () => {
    render();
    const btn = container.querySelector(
      ".kb-override-modal__primary",
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("clicking Confirm with an empty note calls onConfirm with empty string", () => {
    const { onConfirm } = render();
    const btn = container.querySelector(
      ".kb-override-modal__primary",
    ) as HTMLButtonElement;
    act(() => {
      btn.click();
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("", false);
  });

  it("trims whitespace-only notes to empty string", () => {
    const { onConfirm } = render();
    const textarea = container.querySelector(
      ".kb-override-modal__textarea",
    ) as HTMLTextAreaElement;
    act(() => {
      setTextareaValue(textarea, "    \n\t  ");
    });
    const btn = container.querySelector(
      ".kb-override-modal__primary",
    ) as HTMLButtonElement;
    act(() => {
      btn.click();
    });
    expect(onConfirm).toHaveBeenCalledWith("", false);
  });
});

describe("ManualOverrideDialog — actions", () => {
  it("clicking Cancel calls onCancel", () => {
    const { onCancel } = render();
    const btn = container.querySelector(
      ".kb-override-modal__cancel",
    ) as HTMLButtonElement;
    act(() => {
      btn.click();
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("clicking Confirm with a typed note calls onConfirm with the trimmed value", () => {
    const { onConfirm } = render();
    const textarea = container.querySelector(
      ".kb-override-modal__textarea",
    ) as HTMLTextAreaElement;
    act(() => {
      setTextareaValue(textarea, "  override reason  ");
    });
    const btn = container.querySelector(
      ".kb-override-modal__primary",
    ) as HTMLButtonElement;
    act(() => {
      btn.click();
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("override reason", false);
  });

  it("Escape key cancels the dialog", () => {
    const { onCancel } = render();
    act(() => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape" }),
      );
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("clicking the backdrop cancels", () => {
    const { onCancel } = render();
    const backdrop = container.querySelector(
      ".kb-override-backdrop",
    ) as HTMLElement;
    act(() => {
      backdrop.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("clicking inside the modal panel does NOT cancel", () => {
    const { onCancel } = render();
    const modal = container.querySelector(
      ".kb-override-modal",
    ) as HTMLElement;
    act(() => {
      modal.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(onCancel).not.toHaveBeenCalled();
  });
});
