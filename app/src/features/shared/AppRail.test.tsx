// AppRail rightSlot prop — Item #125.

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

vi.mock("../workspaces/WorkspaceSwitcher", () => ({
  __esModule: true,
  WorkspaceSwitcher: () => <div data-testid="ws-switcher" />,
}));

vi.mock("../workspaces/CreateWorkspaceDialog", () => ({
  __esModule: true,
  CreateWorkspaceDialog: () => null,
}));

vi.mock("../auth/UserMenu", () => ({
  __esModule: true,
  UserMenu: () => <div data-testid="user-menu" />,
}));

import { AppRail } from "./AppRail";

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

describe("AppRail — rightSlot", () => {
  it("renders the right-slot wrapper and its children when rightSlot is passed", () => {
    mount(
      <AppRail rightSlot={<span data-testid="rs">slot-content</span>} />,
    );
    const slot = container.querySelector(".app-rail__right-slot");
    expect(slot).not.toBeNull();
    const child = container.querySelector('[data-testid="rs"]');
    expect(child).not.toBeNull();
    expect(child?.textContent).toBe("slot-content");
  });

  it("renders no right-slot wrapper when rightSlot is omitted", () => {
    mount(<AppRail />);
    const slot = container.querySelector(".app-rail__right-slot");
    expect(slot).toBeNull();
  });

  it("renders right-slot above the footer in DOM order", () => {
    mount(
      <AppRail rightSlot={<span data-testid="rs">x</span>} />,
    );
    const slot = container.querySelector(".app-rail__right-slot");
    const footer = container.querySelector(".app-rail__footer");
    expect(slot).not.toBeNull();
    expect(footer).not.toBeNull();
    expect(
      slot!.compareDocumentPosition(footer!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
