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
    },
  };
});

import { api } from "../api";
import {
  PrFolderExplorer,
  type PrFolderSelection,
} from "./PrFolderExplorer";

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
  (api.getPrOverlayFile as unknown as MockInstance).mockReset();
  (api.getRepoFile as unknown as MockInstance).mockReset();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

function renderExplorer(opts: {
  workspaceId?: string;
  autoExpandProjectId?: string | null;
  onSelectFile?: (sel: PrFolderSelection) => void;
} = {}): void {
  act(() => {
    root.render(
      <MemoryRouter>
        <PrFolderExplorer
          workspaceId={opts.workspaceId ?? "ws-1"}
          autoExpandProjectId={opts.autoExpandProjectId ?? null}
          selectedProjectId={null}
          selectedPath={null}
          onSelectFile={opts.onSelectFile ?? (() => {})}
        />
      </MemoryRouter>,
    );
  });
}

async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function makeProject(over: Partial<{
  project_id: string;
  title: string;
  metadata: Record<string, unknown>;
  archived_at: string | null;
}> = {}) {
  return {
    project_id: over.project_id ?? "project-aaa",
    user_id: "user-1",
    title: over.title ?? "Speed up checkout",
    metadata: over.metadata ?? { dominant_category: "feature" },
    created_at: "2026-05-14T00:00:00Z",
    updated_at: "2026-05-14T00:00:00Z",
    archived_at: over.archived_at ?? null,
  };
}

describe("PrFolderExplorer", () => {
  it("groups projects by category using plural folder labels", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-aaa",
          title: "Checkout latency",
          metadata: { dominant_category: "feature" },
        }),
        makeProject({
          project_id: "project-bbb",
          title: "Crash on signup",
          metadata: { dominant_category: "bug" },
        }),
      ],
    });
    renderExplorer();
    await flushAsync();

    const dirSummaries = container.querySelectorAll(
      ".av-repo-tree__dir-summary",
    );
    const labels = Array.from(dirSummaries).map((n) =>
      n.textContent?.replace("▸", "").trim(),
    );
    expect(labels).toContain("features/");
    expect(labels).toContain("bugs/");
  });

  it("falls back to general for unknown or missing dominant_category", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-unknown",
          title: "Mystery work",
          metadata: { dominant_category: "weird-not-real" },
        }),
        makeProject({
          project_id: "project-missing",
          title: "No metadata",
          metadata: {},
        }),
      ],
    });
    renderExplorer();
    await flushAsync();

    const labels = Array.from(
      container.querySelectorAll(".av-repo-tree__dir-summary"),
    ).map((n) => n.textContent?.replace("▸", "").trim());
    expect(labels).toContain("general/");
    // No singular leakage from unknown values.
    expect(labels).not.toContain("weird-not-real/");
  });

  it("hides archived projects from the tree", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-live",
          title: "Live one",
          metadata: { dominant_category: "feature" },
          archived_at: null,
        }),
        makeProject({
          project_id: "project-dead",
          title: "Archived one",
          metadata: { dominant_category: "feature" },
          archived_at: "2026-05-10T00:00:00Z",
        }),
      ],
    });
    renderExplorer();
    await flushAsync();

    const summaries = Array.from(
      container.querySelectorAll(".av-repo-tree__dir-summary"),
    ).map((n) => n.textContent ?? "");
    expect(summaries.some((s) => s.includes("live-one"))).toBe(true);
    expect(summaries.some((s) => s.includes("archived-one"))).toBe(false);
  });

  it("auto-expands the matching project's PR folder when autoExpandProjectId is set", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-zzz",
          title: "Current one",
          metadata: { dominant_category: "feature" },
        }),
        makeProject({
          project_id: "project-aaa",
          title: "Another one",
          metadata: { dominant_category: "feature" },
        }),
      ],
    });
    (api.getPrOverlayTree as unknown as MockInstance).mockResolvedValueOnce({
      project_id: "project-zzz",
      project_title: "Current one",
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
    renderExplorer({ autoExpandProjectId: "project-zzz" });
    await flushAsync();

    // The auto-expanded project triggered an overlay-tree fetch on
    // first render — the other one didn't.
    expect(
      (api.getPrOverlayTree as unknown as MockInstance).mock.calls,
    ).toEqual([["project-zzz"]]);
  });

  it("renders a modified badge on scaffold-source rows after the overlay loads", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-aaa",
          title: "Has scaffold",
          metadata: { dominant_category: "feature" },
        }),
      ],
    });
    (api.getPrOverlayTree as unknown as MockInstance).mockResolvedValueOnce({
      project_id: "project-aaa",
      project_title: "Has scaffold",
      dominant_category: "feature",
      repo_full_name: "acme/demo",
      base_ref: "main",
      base_sha: "treesha",
      tree: [
        { path: "README.md", type: "blob", size: 42, source: "base" },
        {
          path: "src/Checkout.tsx",
          type: "blob",
          size: 200,
          source: "scaffold",
        },
        { path: "src/app.tsx", type: "blob", size: 100, source: "modified" },
      ],
      truncated: false,
      warnings: [],
    });
    renderExplorer({ autoExpandProjectId: "project-aaa" });
    await flushAsync();

    const badges = container.querySelectorAll(
      ".av-repo-tree__badge--modified",
    );
    // scaffold + modified rows show the badge; README (base) does not.
    expect(badges.length).toBe(2);
  });

  it("calls getPrOverlayFile and bubbles scaffold content for scaffold-source rows", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-aaa",
          title: "X",
          metadata: { dominant_category: "feature" },
        }),
      ],
    });
    (api.getPrOverlayTree as unknown as MockInstance).mockResolvedValueOnce({
      project_id: "project-aaa",
      project_title: "X",
      dominant_category: "feature",
      repo_full_name: "acme/demo",
      base_ref: "main",
      base_sha: "treesha",
      tree: [
        {
          path: "src/Checkout.tsx",
          type: "blob",
          size: 200,
          source: "scaffold",
        },
      ],
      truncated: false,
      warnings: [],
    });
    (api.getPrOverlayFile as unknown as MockInstance).mockResolvedValueOnce({
      path: "src/Checkout.tsx",
      content: "// scaffold content\n",
      binary: false,
      source: "scaffold",
      encoding: "utf-8",
    });
    const selections: PrFolderSelection[] = [];
    renderExplorer({
      autoExpandProjectId: "project-aaa",
      onSelectFile: (sel) => selections.push(sel),
    });
    await flushAsync();

    const fileBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".av-repo-tree__file"),
    ).find((b) => b.textContent?.includes("Checkout.tsx"));
    expect(fileBtn).toBeTruthy();
    await act(async () => {
      fileBtn!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushAsync();

    expect(
      (api.getPrOverlayFile as unknown as MockInstance).mock.calls,
    ).toEqual([["project-aaa", "src/Checkout.tsx"]]);
    expect(
      (api.getRepoFile as unknown as MockInstance).mock.calls.length,
    ).toBe(0);
    expect(selections.length).toBe(1);
    expect(selections[0].source).toBe("scaffold");
    expect(selections[0].content).toBe("// scaffold content\n");
  });

  it("falls through to getRepoFile when a base-source row is clicked", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [
        makeProject({
          project_id: "project-aaa",
          title: "X",
          metadata: { dominant_category: "feature" },
        }),
      ],
    });
    (api.getPrOverlayTree as unknown as MockInstance).mockResolvedValueOnce({
      project_id: "project-aaa",
      project_title: "X",
      dominant_category: "feature",
      repo_full_name: "acme/demo",
      base_ref: "main",
      base_sha: "treesha",
      tree: [
        { path: "README.md", type: "blob", size: 42, source: "base" },
      ],
      truncated: false,
      warnings: [],
    });
    // Overlay-file says "this path is base-only" → FE falls through.
    (api.getPrOverlayFile as unknown as MockInstance).mockResolvedValueOnce({
      path: "README.md",
      content: null,
      binary: false,
      source: "base",
      encoding: "utf-8",
    });
    (api.getRepoFile as unknown as MockInstance).mockResolvedValueOnce({
      path: "README.md",
      content: "# Acme",
      binary: false,
      sha: "abc",
      encoding: "utf-8",
    });
    const selections: PrFolderSelection[] = [];
    renderExplorer({
      autoExpandProjectId: "project-aaa",
      onSelectFile: (sel) => selections.push(sel),
    });
    await flushAsync();

    const fileBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".av-repo-tree__file"),
    ).find((b) => b.textContent?.includes("README.md"));
    expect(fileBtn).toBeTruthy();
    await act(async () => {
      fileBtn!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushAsync();

    expect(
      (api.getPrOverlayFile as unknown as MockInstance).mock.calls.length,
    ).toBe(1);
    expect(
      (api.getRepoFile as unknown as MockInstance).mock.calls,
    ).toEqual([["README.md"]]);
    expect(selections[0].source).toBe("base");
    expect(selections[0].content).toBe("# Acme");
  });

  it("renders an empty-state message when no projects are eligible", async () => {
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [],
    });
    renderExplorer();
    await flushAsync();

    expect(container.textContent ?? "").toContain("No in-flight PRs yet");
    // No category folders rendered.
    expect(
      container.querySelectorAll(".av-repo-tree__dir-summary").length,
    ).toBe(0);
  });
});
