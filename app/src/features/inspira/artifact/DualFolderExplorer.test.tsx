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
      getRepoTree: vi.fn(),
      getRepoFile: vi.fn(),
      listWorkspaceProjects: vi.fn(),
      getPrOverlayTree: vi.fn(),
      getPrOverlayFile: vi.fn(),
    },
  };
});

import { api } from "../api";
import { DualFolderExplorer } from "./DualFolderExplorer";

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
  (api.getRepoTree as unknown as MockInstance).mockReset();
  (api.getRepoFile as unknown as MockInstance).mockReset();
  (api.listWorkspaceProjects as unknown as MockInstance).mockReset();
  (api.getPrOverlayTree as unknown as MockInstance).mockReset();
  (api.getPrOverlayFile as unknown as MockInstance).mockReset();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function renderDual(currentProjectId: string | null = null): void {
  act(() => {
    root.render(
      <MemoryRouter>
        <DualFolderExplorer
          workspaceId="ws-1"
          currentProjectId={currentProjectId}
          selectedRepoPath={null}
          selectedPrProjectId={null}
          selectedPrPath={null}
          onSelectRepo={() => {}}
          onSelectPr={() => {}}
        />
      </MemoryRouter>,
    );
  });
}

describe("DualFolderExplorer", () => {
  it("renders both main/ and PRs/ root folders", async () => {
    (api.getRepoTree as unknown as MockInstance).mockResolvedValueOnce({
      repo_full_name: "acme/demo",
      ref: "main",
      sha: "treesha",
      truncated: false,
      tree: [],
    });
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [],
    });
    renderDual();
    await flushAsync();

    const rootSummaries = Array.from(
      container.querySelectorAll(".av-repo-tree__dir-summary"),
    )
      .map((n) => n.textContent?.replace("▸", "").trim() ?? "");
    expect(rootSummaries).toContain("main/");
    expect(rootSummaries).toContain("PRs/");
  });

  it("collapses PRs/ by default when no currentProjectId is provided", async () => {
    (api.getRepoTree as unknown as MockInstance).mockResolvedValueOnce({
      repo_full_name: "acme/demo",
      ref: "main",
      sha: "treesha",
      truncated: false,
      tree: [],
    });
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [],
    });
    renderDual(null);
    await flushAsync();

    const rootDetails = container.querySelectorAll<HTMLDetailsElement>(
      "div[data-variant='dual'] > details.av-repo-tree__dir",
    );
    // main/ is at index 0, PRs/ at index 1 (DOM order matches JSX).
    expect(rootDetails.length).toBe(2);
    expect(rootDetails[0].open).toBe(true);
    expect(rootDetails[1].open).toBe(false);
  });

  it("expands PRs/ when currentProjectId is provided", async () => {
    (api.getRepoTree as unknown as MockInstance).mockResolvedValueOnce({
      repo_full_name: "acme/demo",
      ref: "main",
      sha: "treesha",
      truncated: false,
      tree: [],
    });
    (api.listWorkspaceProjects as unknown as MockInstance).mockResolvedValueOnce({
      projects: [],
    });
    renderDual("project-abc");
    await flushAsync();

    const rootDetails = container.querySelectorAll<HTMLDetailsElement>(
      "div[data-variant='dual'] > details.av-repo-tree__dir",
    );
    expect(rootDetails[1].open).toBe(true);
  });
});
