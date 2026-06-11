import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileTree } from "./FileTree";

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

const SAMPLE_FILES = [
  { path: "README.md", content: "# Notes\n\nA small app.\n" },
  { path: "src/main.tsx", content: "console.log('hi')\nconst x = 1\n" },
  { path: "src/App.tsx", content: "export default function App() { return null }\n" },
  { path: "tests/main.test.ts", content: "test('ok', () => {})\n" },
];

describe("FileTree", () => {
  it("renders one entry per file plus directory headers", () => {
    act(() => {
      root.render(
        <FileTree
          files={SAMPLE_FILES}
          selectedPath={null}
          onSelect={() => {}}
        />,
      );
    });
    // Two directory rows ("src/", "tests/") + four file rows.
    expect(container.querySelectorAll(".av-nav__dir").length).toBe(2);
    expect(container.querySelectorAll(".av-nav__file").length).toBe(4);
  });

  it("highlights the selected file", () => {
    act(() => {
      root.render(
        <FileTree
          files={SAMPLE_FILES}
          selectedPath="src/main.tsx"
          onSelect={() => {}}
        />,
      );
    });
    const active = container.querySelectorAll(".av-nav__file--active");
    expect(active.length).toBe(1);
    const btn = active[0]?.querySelector(".av-nav__file-btn");
    expect(btn?.getAttribute("title")).toBe("src/main.tsx");
  });

  it("calls onSelect once with the clicked path", () => {
    const onSelect = vi.fn();
    act(() => {
      root.render(
        <FileTree
          files={SAMPLE_FILES}
          selectedPath={null}
          onSelect={onSelect}
        />,
      );
    });
    const buttons = container.querySelectorAll<HTMLButtonElement>(
      ".av-nav__file-btn",
    );
    // Click the README row.
    const readmeBtn = Array.from(buttons).find(
      (b) => b.getAttribute("title") === "README.md",
    );
    expect(readmeBtn).toBeTruthy();
    act(() => {
      readmeBtn!.click();
    });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("README.md");
  });

  it("renders the MOD chip when statusByPath flags it modified", () => {
    act(() => {
      root.render(
        <FileTree
          files={SAMPLE_FILES}
          selectedPath={null}
          onSelect={() => {}}
          statusByPath={{ "src/main.tsx": "MOD" }}
        />,
      );
    });
    const chip = container.querySelector(".av-chip--mod");
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toBe("MOD");
  });

  it("renders a Thinking… chip for THINKING status (pulse animation)", () => {
    act(() => {
      root.render(
        <FileTree
          files={SAMPLE_FILES}
          selectedPath={null}
          onSelect={() => {}}
          statusByPath={{ "src/App.tsx": "THINKING" }}
        />,
      );
    });
    const chip = container.querySelector(".av-chip--thinking");
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toBe("Thinking…");
  });
});
