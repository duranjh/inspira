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
    },
  };
});

import { api } from "../api";
import {
  RepoFileExplorer,
  type RepoFileExplorerSelection,
} from "./RepoFileExplorer";

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
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

function renderExplorer(
  onSelectFile: (sel: RepoFileExplorerSelection) => void = () => {},
  selectedPath: string | null = null,
): void {
  act(() => {
    root.render(
      <MemoryRouter>
        <RepoFileExplorer
          selectedPath={selectedPath}
          onSelectFile={onSelectFile}
        />
      </MemoryRouter>,
    );
  });
}

// Helper: flush microtasks so the useEffect-fired promise resolves
// AND the resulting setState lands inside an act boundary.
async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

const SAMPLE_TREE_RESPONSE = {
  repo_full_name: "acme/demo",
  ref: "main",
  sha: "treesha",
  truncated: false,
  tree: [
    { path: "README.md", type: "blob" as const, size: 42 },
    { path: "src", type: "tree" as const },
    { path: "src/app.tsx", type: "blob" as const, size: 1024 },
  ],
};

describe("RepoFileExplorer", () => {
  it("renders the tree from mock data", async () => {
    (api.getRepoTree as unknown as MockInstance).mockResolvedValueOnce(
      SAMPLE_TREE_RESPONSE,
    );
    renderExplorer();
    await flushAsync();

    const files = container.querySelectorAll(".av-repo-tree__file-name");
    const fileNames = Array.from(files).map((n) => n.textContent);
    expect(fileNames).toContain("README.md");
    expect(fileNames).toContain("app.tsx");
    // The "src" directory row renders as a folder, not a file row.
    const dirs = container.querySelectorAll(".av-repo-tree__dir-summary");
    const dirNames = Array.from(dirs).map((n) =>
      n.textContent?.replace("▸", "").trim(),
    );
    expect(dirNames).toContain("src");
  });

  it("renders the empty state when getRepoTree rejects with 409 github_not_connected", async () => {
    const err = new Error(
      "GET /api/v2/connectors/github/repo/tree?ref=main failed: 409 Conflict — " +
        JSON.stringify({
          detail: {
            error: "github_not_connected",
            message: "Connect a GitHub repo to browse files.",
          },
        }),
    );
    (api.getRepoTree as unknown as MockInstance).mockRejectedValueOnce(err);
    renderExplorer();
    await flushAsync();

    const empty = container.querySelector(".av-repo-empty");
    expect(empty).not.toBeNull();
    // The CTA points partners at the Connectors page.
    const cta = empty?.querySelector(".av-repo-empty__cta") as
      | HTMLAnchorElement
      | null;
    expect(cta?.getAttribute("href")).toBe("/connectors");
    // Server-supplied message wins over the static fallback.
    const line = empty?.querySelector(".av-repo-empty__line");
    expect(line?.textContent).toContain("Connect a GitHub repo");
  });

  it("renders the loading skeleton while the tree fetch is pending", async () => {
    (api.getRepoTree as unknown as MockInstance).mockReturnValueOnce(
      new Promise(() => {
        /* never resolves */
      }),
    );
    renderExplorer();
    // No flushAsync — we want the in-flight state.
    const skeleton = container.querySelectorAll(
      ".av-repo-tree__skeleton-row",
    );
    expect(skeleton.length).toBe(5);
  });

  it("fires onSelectFile with the loaded content when a file row is clicked", async () => {
    (api.getRepoTree as unknown as MockInstance).mockResolvedValueOnce(
      SAMPLE_TREE_RESPONSE,
    );
    (api.getRepoFile as unknown as MockInstance).mockResolvedValueOnce({
      path: "README.md",
      content: "# hello\n",
      binary: false,
      sha: "blobsha",
      encoding: "utf-8",
    });
    const onSelect = vi.fn();
    renderExplorer(onSelect);
    await flushAsync();

    const buttons = container.querySelectorAll<HTMLButtonElement>(
      ".av-repo-tree__file",
    );
    const readmeBtn = Array.from(buttons).find((b) =>
      b.textContent?.includes("README.md"),
    );
    expect(readmeBtn).toBeTruthy();
    act(() => {
      readmeBtn!.click();
    });
    await flushAsync();
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith({
      repoFullName: "acme/demo",
      path: "README.md",
      content: "# hello\n",
      binary: false,
      sha: "blobsha",
    });
  });

  it("renders the truncated banner when the tree response has truncated: true", async () => {
    (api.getRepoTree as unknown as MockInstance).mockResolvedValueOnce({
      ...SAMPLE_TREE_RESPONSE,
      truncated: true,
    });
    renderExplorer();
    await flushAsync();

    const banner = container.querySelector(".av-repo-truncated");
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain("truncated");
  });

  it("renders an error state when getRepoTree throws an unmatched error", async () => {
    (api.getRepoTree as unknown as MockInstance).mockRejectedValueOnce(
      new Error("network kaboom"),
    );
    renderExplorer();
    await flushAsync();

    const err = container.querySelector(".av-repo-error");
    expect(err).not.toBeNull();
    // Retry button is rendered.
    const retry = err?.querySelector(".av-repo-error__retry");
    expect(retry).not.toBeNull();
  });
});
