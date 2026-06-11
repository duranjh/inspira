// AuthedShell rightSlot passthrough — Item #125.

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../workspaces/WorkspaceContext", () => ({
  __esModule: true,
  useWorkspaceContext: () => ({
    activeWorkspace: { workspace_id: "ws_test", name: "Test" },
    workspaces: [{ workspace_id: "ws_test", name: "Test" }],
    loading: false,
    error: null,
    setActiveWorkspace: () => {},
    refresh: () => Promise.resolve(),
  }),
  getActiveWorkspaceId: () => "ws_test",
  workspaceReady: () => Promise.resolve(),
}));

vi.mock("../workspaces/CreateWorkspaceDialog", () => ({
  __esModule: true,
  CreateWorkspaceDialog: () => null,
}));

vi.mock("../workspaces/FirstRunCard", () => ({
  __esModule: true,
  FirstRunCard: () => null,
}));

// Stub AppRail to a thin spy so we can assert the rightSlot prop
// actually reaches it from AuthedShell. We don't care about the real
// rail's nav/UserMenu rendering here — that's covered by AppRail.test.
vi.mock("./AppRail", () => ({
  __esModule: true,
  AppRail: ({ rightSlot }: { rightSlot?: React.ReactNode }) => (
    <aside data-testid="app-rail">{rightSlot ?? null}</aside>
  ),
}));

import { AuthedShell } from "./AuthedShell";

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

function mount(node: React.ReactNode) {
  act(() => {
    root.render(<MemoryRouter>{node}</MemoryRouter>);
  });
}

describe("AuthedShell — rightSlot passthrough", () => {
  it("passes rightSlot through to AppRail", () => {
    mount(
      <AuthedShell rightSlot={<span data-testid="rs">x</span>}>
        <main>page</main>
      </AuthedShell>,
    );
    const rail = container.querySelector('[data-testid="app-rail"]');
    expect(rail).not.toBeNull();
    expect(rail?.querySelector('[data-testid="rs"]')).not.toBeNull();
  });

  it("renders no rightSlot content when prop is omitted", () => {
    mount(
      <AuthedShell>
        <main>page</main>
      </AuthedShell>,
    );
    const rail = container.querySelector('[data-testid="app-rail"]');
    expect(rail).not.toBeNull();
    expect(rail?.querySelector('[data-testid="rs"]')).toBeNull();
  });
});
