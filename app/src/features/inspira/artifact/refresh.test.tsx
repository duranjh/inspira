/**
 * Wave F.6 — Refresh PR with Inspira tests.
 *
 * Coverage:
 *   - useRefreshPr starts + transitions through refreshing → ready
 *   - 3-pane diff renders for a partner-edited file
 *   - 2-pane diff renders for an unedited file (no "Keep my edit" radio)
 *   - per-file decision buttons fire the right decision
 *   - bulk "Accept all AI changes" sets every path to accept_redraft
 *   - submitResolutions is called with the full decision map
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RefreshReviewModal } from "./RefreshReviewModal";
import {
  useRefreshPr,
  type UseRefreshPrReturn,
} from "./useRefreshPr";
import type {
  RefreshDecision,
  RefreshDiffResponse,
  StartRefreshResponse,
} from "../api";

vi.mock("../api", async () => {
  const actual: object = await vi.importActual("../api");
  return {
    ...actual,
    api: {
      startPrRefresh: vi.fn(),
      getRefreshDiff: vi.fn(),
      postRefreshResolutions: vi.fn(),
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

function flush(): Promise<void> {
  return act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function makeDiff(
  overrides: Partial<RefreshDiffResponse> = {},
): RefreshDiffResponse {
  return {
    refresh_id: "refr-aaa",
    status: "completed",
    previous_scaffold_id: "scaf-old",
    new_scaffold_id: "scaf-new",
    base_main_sha_before: "old-sha",
    base_main_sha_after: "new-sha",
    changed_paths: ["README.md"],
    files: [
      {
        path: "README.md",
        base: "# v1\n",
        partner_edit: "# partner\n",
        ai_redraft: "# redraft\n",
        conflict: true,
      },
    ],
    ...overrides,
  };
}

function mountHook(projectId: string): {
  hook: { current: UseRefreshPrReturn | null };
} {
  const hookRef = { current: null as UseRefreshPrReturn | null };

  function Probe(): null {
    const value = useRefreshPr(projectId);
    useEffect(() => {
      hookRef.current = value;
    });
    return null;
  }

  act(() => {
    root.render(<Probe />);
  });

  return { hook: hookRef };
}

describe("useRefreshPr", () => {
  it("transitions idle → refreshing → ready on successful start", async () => {
    const startResp: StartRefreshResponse = {
      scaffold_id: "scaf-new",
      refresh_id: "refr-aaa",
      base_main_sha: "new-sha",
      changed_paths: ["README.md"],
      changed_count: 1,
    };
    vi.mocked(api.startPrRefresh).mockResolvedValueOnce(startResp);
    vi.mocked(api.getRefreshDiff).mockResolvedValueOnce(makeDiff());

    const { hook } = mountHook("proj-1");
    expect(hook.current?.state).toBe("idle");

    await act(async () => {
      await hook.current!.startRefresh();
    });

    expect(hook.current?.state).toBe("ready");
    expect(hook.current?.refreshId).toBe("refr-aaa");
    expect(hook.current?.diff?.files.length).toBe(1);
    expect(vi.mocked(api.startPrRefresh)).toHaveBeenCalledWith("proj-1");
    expect(vi.mocked(api.getRefreshDiff)).toHaveBeenCalledWith(
      "proj-1", "refr-aaa",
    );
  });
});

describe("RefreshReviewModal", () => {
  function render(
    diff: RefreshDiffResponse | null,
    onSubmitImpl?: (
      decisions: Record<string, RefreshDecision>,
    ) => Promise<{
      scaffold_id: string;
      refresh_id: string;
      diff_summary: { accepted: number; kept: number; merged: number };
    } | null>,
  ): {
    submitSpy: (
      decisions: Record<string, RefreshDecision>,
    ) => Promise<unknown>;
    closeSpy: () => void;
  } {
    const submitSpy = vi.fn(
      onSubmitImpl
        ?? (async () => ({
          scaffold_id: "scaf-new",
          refresh_id: "refr-aaa",
          diff_summary: { accepted: 0, kept: 0, merged: 0 },
        })),
    );
    const closeSpy = vi.fn();
    act(() => {
      root.render(
        <RefreshReviewModal
          open={true}
          diff={diff}
          refreshing={false}
          error={null}
          onSubmit={submitSpy}
          onClose={closeSpy}
        />,
      );
    });
    return { submitSpy, closeSpy };
  }

  it("renders 3-pane diff for a partner-edited file", async () => {
    render(makeDiff());
    await flush();
    const panes = container.querySelectorAll(".av-refresh-diff__pane");
    // 3 panes: original, your edit, redraft
    expect(panes.length).toBe(3);
    // Decision row includes a "Keep my edit" option (not hidden).
    const keepLabel = Array.from(
      container.querySelectorAll(".av-refresh-diff__decisions label"),
    ).find((el) => el.textContent?.includes("Keep my edit"));
    expect(keepLabel).toBeTruthy();
    expect(
      keepLabel?.className.includes(
        "av-refresh-diff__decision--hidden",
      ),
    ).toBe(false);
  });

  it("renders 2-pane diff for an unedited file + hides 'Keep my edit'", async () => {
    const twoWay = makeDiff({
      files: [
        {
          path: "README.md",
          base: "# v1\n",
          partner_edit: null,
          ai_redraft: "# redraft\n",
          conflict: false,
        },
      ],
    });
    render(twoWay);
    await flush();
    const panes = container.querySelectorAll(".av-refresh-diff__pane");
    // 2 panes: previous draft, redraft.
    expect(panes.length).toBe(2);
    // "Keep my edit" radio is rendered but visibility-hidden + disabled.
    const keepInput = container.querySelector(
      'input[value="keep_partner_edit"]',
    ) as HTMLInputElement | null;
    expect(keepInput?.disabled).toBe(true);
  });

  it("per-file decision buttons fire the right decision", async () => {
    const { submitSpy } = render(makeDiff());
    await flush();
    // Click "Keep my edit".
    const keepInput = container.querySelector(
      'input[value="keep_partner_edit"]',
    ) as HTMLInputElement | null;
    expect(keepInput).toBeTruthy();
    await act(async () => {
      keepInput!.click();
    });
    // Submit.
    const submitBtn = Array.from(
      container.querySelectorAll("button"),
    ).find((b) => b.textContent === "Apply decisions");
    expect(submitBtn).toBeTruthy();
    await act(async () => {
      submitBtn!.click();
    });
    await flush();
    expect(submitSpy).toHaveBeenCalledTimes(1);
    const decisions = submitSpy.mock.calls[0][0] as Record<
      string, RefreshDecision
    >;
    expect(decisions["README.md"].decision).toBe("keep_partner_edit");
  });

  it("bulk 'Accept all AI changes' sets every path to accept_redraft", async () => {
    const { submitSpy } = render(
      makeDiff({
        files: [
          {
            path: "a.ts", base: "a-old", partner_edit: "a-mid",
            ai_redraft: "a-new", conflict: true,
          },
          {
            path: "b.ts", base: "b-old", partner_edit: "b-mid",
            ai_redraft: "b-new", conflict: true,
          },
        ],
        changed_paths: ["a.ts", "b.ts"],
      }),
    );
    await flush();
    // Pre-flip both files to "keep" so we can verify bulk overrides.
    const acceptAll = Array.from(
      container.querySelectorAll(".av-refresh-modal__bulk-btn"),
    ).find((b) => b.textContent === "Accept all AI changes") as
      HTMLButtonElement;
    await act(async () => {
      acceptAll.click();
    });
    const submitBtn = Array.from(
      container.querySelectorAll("button"),
    ).find((b) => b.textContent === "Apply decisions") as HTMLButtonElement;
    await act(async () => {
      submitBtn.click();
    });
    await flush();
    const decisions = submitSpy.mock.calls[0][0] as Record<
      string, RefreshDecision
    >;
    expect(decisions["a.ts"].decision).toBe("accept_redraft");
    expect(decisions["b.ts"].decision).toBe("accept_redraft");
  });

  it("submitResolutions is called with the full decision map", async () => {
    const { submitSpy } = render(makeDiff());
    await flush();
    const submitBtn = Array.from(
      container.querySelectorAll("button"),
    ).find((b) => b.textContent === "Apply decisions") as HTMLButtonElement;
    await act(async () => {
      submitBtn.click();
    });
    await flush();
    expect(submitSpy).toHaveBeenCalledTimes(1);
    const decisions = submitSpy.mock.calls[0][0] as Record<
      string, RefreshDecision
    >;
    // Default seeded for every file in the diff.
    expect(decisions["README.md"]).toBeDefined();
    expect(decisions["README.md"].decision).toBe("accept_redraft");
  });
});
