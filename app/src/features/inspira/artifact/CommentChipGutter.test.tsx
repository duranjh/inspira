/**
 * Wave F.4 — CommentChipGutter component tests.
 *
 * Coverage:
 *   - renders saved chips at correct lines
 *   - clicking ghost-add opens popover with "question" default
 *   - clicking saved chip expands thread with body + reply input
 *   - resolved comments render hollow chip + "Resolved" badge
 *
 * The CodeMirror EditorView is too heavy + DOM-coupled to mount inside
 * jsdom; we hand the component a duck-typed stub that supplies just the
 * surface it touches (state.doc.line / state.doc.lineAt / coordsAtPos /
 * posAtCoords). All tests measure DOM output, not internal state.
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
import type { EditorView } from "@codemirror/view";

/** Flush the async staleness effect's microtasks so the second render
 *  (which is when chips first appear — the first render has a null
 *  hostRef) lands inside ``act``. */
async function flushAsync(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

import { CommentChipGutter } from "./CommentChipGutter";
import type { ArtifactComment } from "../api";

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

function makeComment(overrides: Partial<ArtifactComment>): ArtifactComment {
  return {
    comment_id: overrides.comment_id ?? "comment-aaa",
    project_id: overrides.project_id ?? "proj-1",
    file_path: overrides.file_path ?? "src/main.py",
    line_number: overrides.line_number ?? 7,
    line_content_hash: overrides.line_content_hash ?? "deadbeefcafebabe",
    category: overrides.category ?? "question",
    body: overrides.body ?? "Why 42?",
    author_user_id: overrides.author_user_id ?? "user-1",
    parent_comment_id: overrides.parent_comment_id ?? null,
    resolved_at: overrides.resolved_at ?? null,
    created_at: overrides.created_at ?? "2026-05-13T22:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-13T22:00:00Z",
  };
}

/** Minimal duck-typed EditorView for tests.
 *
 *  Each "line" is the same text repeated; the component only reads
 *  ``line.text``/``line.from`` and ``coordsAtPos`` so we can fake the
 *  rest. Y coordinates: each line at 20px increments to mimic the
 *  editor's lineHeight, anchored to the container's top.
 */
function makeFakeView(lines: string[]): EditorView {
  const doc = {
    lines: lines.length,
    line(n: number): { number: number; from: number; to: number; text: string } {
      if (n < 1 || n > lines.length) {
        throw new RangeError(`line out of range: ${n}`);
      }
      const text = lines[n - 1];
      // Cumulative offset; close enough for the test's posAtCoords stub
      // which never reads it.
      const from = lines.slice(0, n - 1).reduce((acc, l) => acc + l.length + 1, 0);
      return { number: n, from, to: from + text.length, text };
    },
    lineAt(_pos: number): { number: number } {
      return { number: 1 };
    },
  };
  return {
    state: { doc },
    coordsAtPos(pos: number) {
      // Each line spans 20px; pos is the cumulative offset so derive
      // the line index from the doc.
      let acc = 0;
      for (let i = 0; i < lines.length; i += 1) {
        const len = lines[i].length;
        if (pos <= acc + len) {
          return { left: 40, right: 50, top: i * 20, bottom: i * 20 + 18 };
        }
        acc += len + 1;
      }
      return { left: 40, right: 50, top: 0, bottom: 18 };
    },
    posAtCoords(coords: { y: number }) {
      const i = Math.max(0, Math.min(lines.length - 1, Math.floor(coords.y / 20)));
      return doc.line(i + 1).from;
    },
  } as unknown as EditorView;
}

describe("CommentChipGutter", () => {
  it("renders saved chips at correct lines", async () => {
    const view = makeFakeView(["line 1", "line 2", "line 3"]);
    const c = makeComment({
      comment_id: "comment-1",
      line_number: 2,
      body: "needs guard",
    });
    await act(async () => {
      root.render(
        <CommentChipGutter
          view={view}
          viewTick={0}
          filePath="src/main.py"
          comments={[c]}
          loading={false}
          createComment={vi.fn()}
          updateComment={vi.fn()}
          fileContent="line 1\nline 2\nline 3"
        />,
      );
    });
    await flushAsync();
    const chips = container.querySelectorAll(".av-comment-chip--saved");
    expect(chips.length).toBe(1);
    expect((chips[0] as HTMLElement).style.top).toBe("20px");
  });

  it("clicking ghost-add opens popover with question default", async () => {
    const view = makeFakeView(["line 1", "line 2", "line 3"]);
    await act(async () => {
      root.render(
        <CommentChipGutter
          view={view}
          viewTick={0}
          filePath="src/main.py"
          comments={[]}
          loading={false}
          createComment={vi.fn()}
          updateComment={vi.fn()}
          fileContent="line 1\nline 2\nline 3"
        />,
      );
    });
    await flushAsync();
    const host = container.querySelector(".av-comment-gutter") as HTMLElement;
    expect(host).not.toBeNull();
    // Simulate hover over line 1 (y=10 → index 0 → line 1).
    await act(async () => {
      host.dispatchEvent(
        new MouseEvent("mousemove", { bubbles: true, clientY: 10 }),
      );
    });
    const ghost = container.querySelector(
      ".av-comment-chip--ghost",
    ) as HTMLElement;
    expect(ghost).not.toBeNull();
    await act(async () => {
      ghost.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const popover = container.querySelector(".av-comment-popover");
    expect(popover).not.toBeNull();
    const checked = popover?.querySelector(
      "input[type='radio'][value='question']",
    ) as HTMLInputElement | null;
    expect(checked?.checked).toBe(true);
  });

  it("clicking saved chip expands thread with body + reply input", async () => {
    const view = makeFakeView(["line 1", "line 2"]);
    const c = makeComment({
      comment_id: "comment-thread",
      line_number: 1,
      body: "anchor body",
    });
    await act(async () => {
      root.render(
        <CommentChipGutter
          view={view}
          viewTick={0}
          filePath="src/main.py"
          comments={[c]}
          loading={false}
          createComment={vi.fn()}
          updateComment={vi.fn()}
          fileContent="line 1\nline 2"
        />,
      );
    });
    await flushAsync();
    const chipBtn = container.querySelector(
      ".av-comment-chip__btn",
    ) as HTMLButtonElement;
    expect(chipBtn).not.toBeNull();
    await act(async () => {
      chipBtn.click();
    });
    const thread = container.querySelector(".av-comment-thread");
    expect(thread).not.toBeNull();
    expect(thread?.textContent ?? "").toContain("anchor body");
    const replyInput = container.querySelector(
      ".av-comment-thread__reply-input",
    );
    expect(replyInput).not.toBeNull();
  });

  it("resolved comments render hollow chip + 'Resolved' badge when expanded", async () => {
    const view = makeFakeView(["line 1"]);
    const resolved = makeComment({
      comment_id: "comment-resolved",
      line_number: 1,
      body: "fixed",
      resolved_at: "2026-05-13T22:30:00Z",
    });
    await act(async () => {
      root.render(
        <CommentChipGutter
          view={view}
          viewTick={0}
          filePath="src/main.py"
          comments={[resolved]}
          loading={false}
          createComment={vi.fn()}
          updateComment={vi.fn()}
          fileContent="line 1"
        />,
      );
    });
    await flushAsync();
    const chip = container.querySelector(".av-comment-chip--resolved");
    expect(chip).not.toBeNull();
    const chipBtn = chip?.querySelector(
      ".av-comment-chip__btn",
    ) as HTMLButtonElement;
    await act(async () => {
      chipBtn.click();
    });
    const badge = container.querySelector(".av-comment-thread__resolved-badge");
    expect(badge).not.toBeNull();
    expect(badge?.textContent ?? "").toContain("Resolved");
  });
});
