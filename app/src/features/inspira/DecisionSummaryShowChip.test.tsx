import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DecisionSummaryShowChip } from "./DecisionSummaryShowChip";

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

describe("DecisionSummaryShowChip", () => {
  it("renders nothing when visible is false", () => {
    act(() => {
      root.render(
        <DecisionSummaryShowChip visible={false} onClick={() => undefined} />,
      );
    });
    expect(container.querySelector(".decision-summary-show-chip")).toBeNull();
  });

  it("renders the labeled button when visible is true", () => {
    act(() => {
      root.render(
        <DecisionSummaryShowChip visible onClick={() => undefined} />,
      );
    });
    const btn = container.querySelector<HTMLButtonElement>(
      ".decision-summary-show-chip",
    );
    expect(btn).not.toBeNull();
    expect(btn?.getAttribute("aria-label")).toBe(
      "Open Inspira's decision summary",
    );
    expect(btn?.textContent).toContain("Show summary");
  });

  it("invokes onClick when clicked", () => {
    const onClick = vi.fn();
    act(() => {
      root.render(<DecisionSummaryShowChip visible onClick={onClick} />);
    });
    act(() => {
      container.querySelector<HTMLButtonElement>(
        ".decision-summary-show-chip",
      )!.click();
    });
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
