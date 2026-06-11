/**
 * Wave F.4 — useArtifactComments hook tests.
 *
 * Coverage:
 *   - fetches comments on mount
 *   - createComment optimistically adds then reconciles on success
 *   - createComment rolls back on 4xx
 *   - hashArtifactCommentLine matches BE format (SHA-256[:16] UTF-8)
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useArtifactComments,
  type UseArtifactCommentsReturn,
  hashArtifactCommentLine,
} from "./useArtifactComments";
import type { ArtifactComment } from "../api";

vi.mock("../api", async () => {
  const actual: object = await vi.importActual("../api");
  return {
    ...actual,
    api: {
      listArtifactComments: vi.fn(),
      createArtifactComment: vi.fn(),
      updateArtifactComment: vi.fn(),
    },
  };
});

import { api } from "../api";

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
  vi.clearAllMocks();
});

function makeComment(overrides: Partial<ArtifactComment>): ArtifactComment {
  return {
    comment_id: overrides.comment_id ?? "comment-aaa",
    project_id: overrides.project_id ?? "proj-1",
    file_path: overrides.file_path ?? "src/main.py",
    line_number: overrides.line_number ?? 7,
    line_content_hash: overrides.line_content_hash ?? "0000000000000000",
    category: overrides.category ?? "question",
    body: overrides.body ?? "Why 42?",
    author_user_id: overrides.author_user_id ?? "user-1",
    parent_comment_id: overrides.parent_comment_id ?? null,
    resolved_at: overrides.resolved_at ?? null,
    created_at: overrides.created_at ?? "2026-05-13T22:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-13T22:00:00Z",
  };
}

function mountHook(projectId: string): {
  hook: { current: UseArtifactCommentsReturn | null };
  renderAndWait: () => Promise<void>;
} {
  const hookRef = { current: null as UseArtifactCommentsReturn | null };

  function Probe(): null {
    const value = useArtifactComments(projectId);
    useEffect(() => {
      hookRef.current = value;
    });
    return null;
  }

  return {
    hook: hookRef,
    renderAndWait: async () => {
      await act(async () => {
        root.render(<Probe />);
      });
      // Flush microtasks so the mount effect's fetch resolves.
      await act(async () => {
        await Promise.resolve();
      });
    },
  };
}

describe("useArtifactComments", () => {
  it("fetches comments on mount", async () => {
    const c = makeComment({ comment_id: "comment-1" });
    (api.listArtifactComments as ReturnType<typeof vi.fn>).mockResolvedValue({
      comments: [c],
    });

    const { hook, renderAndWait } = mountHook("proj-1");
    await renderAndWait();

    expect(api.listArtifactComments).toHaveBeenCalledWith("proj-1");
    expect(hook.current?.comments.length).toBe(1);
    expect(hook.current?.comments[0].comment_id).toBe("comment-1");
    expect(hook.current?.loading).toBe(false);
  });

  it("createComment optimistically adds then reconciles on success", async () => {
    (api.listArtifactComments as ReturnType<typeof vi.fn>).mockResolvedValue({
      comments: [],
    });
    let resolveCreate: (
      value: { comment: ArtifactComment },
    ) => void = () => {};
    (api.createArtifactComment as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );

    const { hook, renderAndWait } = mountHook("proj-1");
    await renderAndWait();
    expect(hook.current?.comments.length).toBe(0);

    let creating: Promise<ArtifactComment> | null = null;
    await act(async () => {
      creating = hook.current!.createComment({
        filePath: "src/main.py",
        lineNumber: 7,
        lineContent: "    return 42",
        category: "question",
        body: "why?",
      });
    });

    // Optimistic placeholder visible immediately.
    expect(hook.current?.comments.length).toBe(1);
    expect(hook.current?.comments[0].comment_id).toMatch(/^pending-/);

    // Resolve with the server's row.
    const server = makeComment({
      comment_id: "comment-srv",
      body: "why?",
    });
    await act(async () => {
      resolveCreate({ comment: server });
      await creating;
    });
    expect(hook.current?.comments.length).toBe(1);
    expect(hook.current?.comments[0].comment_id).toBe("comment-srv");
  });

  it("createComment rolls back the optimistic insert on failure", async () => {
    (api.listArtifactComments as ReturnType<typeof vi.fn>).mockResolvedValue({
      comments: [],
    });
    (api.createArtifactComment as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("HTTP 422"),
    );

    const { hook, renderAndWait } = mountHook("proj-1");
    await renderAndWait();

    let caught: unknown = null;
    await act(async () => {
      try {
        await hook.current!.createComment({
          filePath: "src/main.py",
          lineNumber: 7,
          lineContent: "    return 42",
          category: "question",
          body: "why?",
        });
      } catch (err) {
        caught = err;
      }
    });

    expect(caught).toBeInstanceOf(Error);
    expect(hook.current?.comments.length).toBe(0);
  });

  it("hashArtifactCommentLine matches Python's hashlib.sha256(...).hexdigest()[:16]", async () => {
    // Golden values produced offline by:
    //   import hashlib
    //   hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    const cases: Array<[string, string]> = [
      ["", "e3b0c44298fc1c14"],
      ["    return 42", "ee31a143244968c7"],
      ["console.log('hi')", "d68859168dc1f70d"],
    ];
    for (const [input, expected] of cases) {
      const actual = await hashArtifactCommentLine(input);
      expect(actual).toBe(expected);
    }
  });
});
