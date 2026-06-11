import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

vi.mock("../api", async () => {
  const actual = await vi.importActual<object>("../api");
  return {
    ...actual,
    api: {
      listWorkspaceProjects: vi.fn(),
      getPrOverlayTree: vi.fn(),
      getPrOverlayFile: vi.fn(),
      getRepoFile: vi.fn(),
      getPrOverlayStaleness: vi.fn(),
    },
  };
});

import { api, type PrOverlayStalenessResponse } from "../api";
import { PrFolderExplorer } from "./PrFolderExplorer";
import { StalenessBanner } from "./StalenessBanner";
import { useSoftEditBlock } from "./useSoftEditBlock";
import { useStaleness } from "./useStaleness";

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
  (api.listWorkspaceProjects as unknown as MockInstance).mockReset();
  (api.getPrOverlayTree as unknown as MockInstance).mockReset();
  (api.getPrOverlayStaleness as unknown as MockInstance).mockReset();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.useRealTimers();
});

async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function makeStaleness(
  over: Partial<PrOverlayStalenessResponse> = {},
): PrOverlayStalenessResponse {
  return {
    is_stale: true,
    base_main_sha: "old-sha",
    current_main_sha: "new-sha",
    main_moved_at: "2026-05-13T15:30:00Z",
    affected_files_count: 2,
    scaffold_files_count: 5,
    affected_paths_sample: ["src/Pricing.tsx", "src/Cart.tsx"],
    last_partner_edit: null,
    scaffold_drafted_at: "2026-05-10T00:00:00Z",
    legacy: false,
    truncated: false,
    ...over,
  };
}

// ---------------------------------------------------------------------
// 1) useStaleness hook — fetches on mount + refreshes on the 60s interval
// ---------------------------------------------------------------------

describe("useStaleness", () => {
  it("fetches on mount and re-fetches every 60s", async () => {
    vi.useFakeTimers();
    (api.getPrOverlayStaleness as unknown as MockInstance)
      .mockResolvedValueOnce(makeStaleness({ affected_files_count: 1 }))
      .mockResolvedValueOnce(makeStaleness({ affected_files_count: 7 }));

    function Probe(): JSX.Element {
      const { staleness, loading } = useStaleness("project-aaa");
      return (
        <div data-testid="probe" data-loading={loading ? "true" : "false"}>
          {staleness ? String(staleness.affected_files_count) : "(none)"}
        </div>
      );
    }
    act(() => {
      root.render(<Probe />);
    });
    // Initial fetch in flight.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.querySelector("[data-testid=probe]")?.textContent).toBe(
      "1",
    );
    expect(
      (api.getPrOverlayStaleness as unknown as MockInstance).mock.calls.length,
    ).toBe(1);

    // Advance 60s — the interval refresh fires.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      (api.getPrOverlayStaleness as unknown as MockInstance).mock.calls.length,
    ).toBe(2);
    expect(container.querySelector("[data-testid=probe]")?.textContent).toBe(
      "7",
    );
  });
});

// ---------------------------------------------------------------------
// 2) StalenessBanner — hidden when not stale; visible with the right copy
// ---------------------------------------------------------------------

describe("StalenessBanner", () => {
  it("renders nothing when staleness is null, legacy, or not stale", async () => {
    function Cases(): JSX.Element {
      return (
        <>
          <div data-testid="null">
            <StalenessBanner staleness={null} />
          </div>
          <div data-testid="legacy">
            <StalenessBanner
              staleness={makeStaleness({ legacy: true, is_stale: false })}
            />
          </div>
          <div data-testid="fresh">
            <StalenessBanner
              staleness={makeStaleness({ is_stale: false })}
            />
          </div>
        </>
      );
    }
    act(() => {
      root.render(<Cases />);
    });
    await flushAsync();
    expect(
      container.querySelector("[data-testid=null]")?.textContent ?? "",
    ).toBe("");
    expect(
      container.querySelector("[data-testid=legacy]")?.textContent ?? "",
    ).toBe("");
    expect(
      container.querySelector("[data-testid=fresh]")?.textContent ?? "",
    ).toBe("");
    expect(container.querySelectorAll(".av-staleness-banner").length).toBe(0);
  });

  it("renders with affected_files_count + scaffold_files_count when stale", async () => {
    act(() => {
      root.render(
        <StalenessBanner
          staleness={makeStaleness({
            affected_files_count: 3,
            scaffold_files_count: 8,
          })}
        />,
      );
    });
    await flushAsync();
    const banner = container.querySelector(".av-staleness-banner");
    expect(banner).toBeTruthy();
    const text = banner?.textContent ?? "";
    // Copy mentions both counts.
    expect(text).toContain("3");
    expect(text).toContain("8");
    // Wave F.6 — CTA stays disabled when no onRefreshClick is wired,
    // with the fallback "unavailable in this build" tooltip. When the
    // parent wires onRefreshClick, the CTA becomes enabled (covered
    // by refresh.test.tsx). The legacy F.5 "Coming soon" tooltip is
    // now reserved for the F.6 escape hatch (no handler attached).
    const cta = banner?.querySelector(
      ".av-staleness-banner__cta",
    ) as HTMLButtonElement | null;
    expect(cta).toBeTruthy();
    expect(cta?.disabled).toBe(true);
    expect(cta?.title ?? "").toContain("unavailable");
  });
});

// ---------------------------------------------------------------------
// 3) PrFolderExplorer — rust "behind main" pill on the stale project row
// ---------------------------------------------------------------------

describe("PrFolderExplorer × activeStaleness", () => {
  it("renders the behind-main pill only on the autoExpandProjectId folder", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        {
          project_id: "project-zzz",
          user_id: "user-1",
          title: "Stale one",
          metadata: { dominant_category: "feature" },
          created_at: "2026-05-14T00:00:00Z",
          updated_at: "2026-05-14T00:00:00Z",
          archived_at: null,
        },
        {
          project_id: "project-aaa",
          user_id: "user-1",
          title: "Other one",
          metadata: { dominant_category: "feature" },
          created_at: "2026-05-14T00:00:00Z",
          updated_at: "2026-05-14T00:00:00Z",
          archived_at: null,
        },
      ],
    });
    (api.getPrOverlayTree as unknown as MockInstance).mockResolvedValueOnce({
      project_id: "project-zzz",
      project_title: "Stale one",
      dominant_category: "feature",
      repo_full_name: "acme/demo",
      base_ref: "main",
      base_sha: "treesha",
      tree: [
        { path: "src/App.tsx", type: "blob", size: 100, source: "base" },
      ],
      truncated: false,
      warnings: [],
    });
    act(() => {
      root.render(
        <MemoryRouter>
          <PrFolderExplorer
            workspaceId="ws-1"
            autoExpandProjectId="project-zzz"
            selectedProjectId={null}
            selectedPath={null}
            onSelectFile={() => {}}
            activeStaleness={makeStaleness()}
          />
        </MemoryRouter>,
      );
    });
    await flushAsync();

    const pills = container.querySelectorAll(
      ".av-repo-tree__badge--behind-main",
    );
    expect(pills.length).toBe(1);
    // The pill is on the active project's folder, not the other one.
    const staleRow = container.querySelector(
      'details[data-project-id="project-zzz"]',
    );
    expect(
      staleRow?.querySelector(".av-repo-tree__badge--behind-main"),
    ).toBeTruthy();
    const otherRow = container.querySelector(
      'details[data-project-id="project-aaa"]',
    );
    expect(
      otherRow?.querySelector(".av-repo-tree__badge--behind-main"),
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------
// 4 + 5) useSoftEditBlock — request blocks when stale; confirm dismisses
// ---------------------------------------------------------------------

describe("useSoftEditBlock", () => {
  function HookHarness({
    onResult,
  }: {
    onResult: (api: ReturnType<typeof useSoftEditBlock>) => void;
  }): JSX.Element {
    const hook = useSoftEditBlock();
    onResult(hook);
    return (
      <div data-testid="pending">
        {hook.pendingEditTarget?.filePath ?? "(none)"}
      </div>
    );
  }

  it("blocks edits when stale and surfaces a pending target", async () => {
    let captured: ReturnType<typeof useSoftEditBlock> | null = null;
    act(() => {
      root.render(
        <HookHarness
          onResult={(api) => {
            captured = api;
          }}
        />,
      );
    });
    await flushAsync();
    if (captured === null) throw new Error("hook didn't render");

    let decision: ReturnType<
      NonNullable<typeof captured>["requestEdit"]
    > | undefined;
    act(() => {
      decision = captured!.requestEdit(
        { projectId: "p", filePath: "src/Pricing.tsx" },
        makeStaleness(),
      );
    });
    expect(decision!.proceed).toBe(false);
    expect(decision!.reason).toBe("modal_opened");
    expect(
      container.querySelector("[data-testid=pending]")?.textContent,
    ).toBe("src/Pricing.tsx");
  });

  it("confirmEdit dismisses for session — next requestEdit on same file passes", async () => {
    let captured: ReturnType<typeof useSoftEditBlock> | null = null;
    act(() => {
      root.render(
        <HookHarness
          onResult={(api) => {
            captured = api;
          }}
        />,
      );
    });
    await flushAsync();
    if (captured === null) throw new Error("hook didn't render");

    // Read ``captured`` AFTER each act() so we use the freshest hook
    // reference — useCallback rebuilds confirmEdit when pendingEditTarget
    // changes, and capturing once at render time would return the
    // stale closure with pendingEditTarget=null.
    act(() => {
      captured!.requestEdit(
        { projectId: "p", filePath: "src/Pricing.tsx" },
        makeStaleness(),
      );
    });
    let confirmedTarget: ReturnType<
      NonNullable<typeof captured>["confirmEdit"]
    > = null;
    act(() => {
      confirmedTarget = captured!.confirmEdit();
    });
    expect(confirmedTarget!).toEqual({
      projectId: "p",
      filePath: "src/Pricing.tsx",
    });

    // Pending target cleared; second requestEdit on same file now
    // proceeds because of the recorded dismissal.
    expect(
      container.querySelector("[data-testid=pending]")?.textContent,
    ).toBe("(none)");
    let secondDecision: ReturnType<
      NonNullable<typeof captured>["requestEdit"]
    > | undefined;
    act(() => {
      secondDecision = captured!.requestEdit(
        { projectId: "p", filePath: "src/Pricing.tsx" },
        makeStaleness(),
      );
    });
    expect(secondDecision!.proceed).toBe(true);
    expect(secondDecision!.reason).toBe("dismissed");

    // But a DIFFERENT file in the same project is still blocked —
    // dismissal is per-file, not per-project.
    let thirdDecision: ReturnType<
      NonNullable<typeof captured>["requestEdit"]
    > | undefined;
    act(() => {
      thirdDecision = captured!.requestEdit(
        { projectId: "p", filePath: "src/Other.tsx" },
        makeStaleness(),
      );
    });
    expect(thirdDecision!.proceed).toBe(false);
  });
});
