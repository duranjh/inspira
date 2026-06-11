// DiffBadge — three age states drive distinct classes.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DiffBadge } from "./DiffBadge";

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

describe("DiffBadge", () => {
  it("fresh age adds the pulse class", () => {
    act(() => {
      root.render(
        <DiffBadge age="fresh" lastChangedAt={new Date().toISOString()} />,
      );
    });
    const el = container.querySelector(".cc-diff-badge");
    expect(el?.classList.contains("cc-diff-badge--fresh")).toBe(true);
  });

  it("recent age uses static fill", () => {
    act(() => {
      root.render(
        <DiffBadge age="recent" lastChangedAt={new Date().toISOString()} />,
      );
    });
    const el = container.querySelector(".cc-diff-badge");
    expect(el?.classList.contains("cc-diff-badge--recent")).toBe(true);
    expect(el?.classList.contains("cc-diff-badge--fresh")).toBe(false);
  });

  it("stale age uses muted outline", () => {
    act(() => {
      root.render(
        <DiffBadge age="stale" lastChangedAt={new Date().toISOString()} />,
      );
    });
    const el = container.querySelector(".cc-diff-badge");
    expect(el?.classList.contains("cc-diff-badge--stale")).toBe(true);
  });

  it("formats time-ago in seconds for recent timestamps", () => {
    const tenSecondsAgo = new Date(Date.now() - 10_000).toISOString();
    act(() => {
      root.render(<DiffBadge age="fresh" lastChangedAt={tenSecondsAgo} />);
    });
    const time = container.querySelector(".cc-diff-badge__time");
    expect(time?.textContent).toMatch(/\d+s ago/);
  });

  it("renders changeNote as title attribute", () => {
    act(() => {
      root.render(
        <DiffBadge
          age="fresh"
          lastChangedAt={new Date().toISOString()}
          changeNote="Switched to opaque tokens."
        />,
      );
    });
    const el = container.querySelector(".cc-diff-badge");
    expect(el?.getAttribute("title")).toBe("Switched to opaque tokens.");
  });
});
