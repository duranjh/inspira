// WorkspaceContext lifecycle tests (W2 C4).
//
// Covers:
//  - Provider mounts → calls listWorkspaces → hydrates context.
//  - workspaceReady() promise resolves after first hydration.
//  - getActiveWorkspaceId() returns the persisted-or-first id.
//  - localStorage round-trip: persisted id is preferred when in
//    the response set; falls through to first when stale.
//  - setActiveWorkspace ignores an id that's not in the list
//    (spoofing-prevention invariant).
//  - 0-workspace state: activeWorkspace null + getActiveWorkspaceId
//    null + ready resolves anyway.
//  - skipInitialFetch: ready resolves immediately, no fetch fires.
//
// Pattern matches CanvasErrorBoundary.test.tsx — react-dom/client +
// React 19's `act` (no testing-library dep on this codebase).

import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetForTesting,
  WorkspaceProvider,
  WorkspaceSummary,
  getActiveWorkspaceId,
  useWorkspaceContext,
  workspaceReady,
} from "./WorkspaceContext";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const STORAGE_KEY = "inspira_active_workspace_id";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  __resetForTesting();
  window.localStorage.clear();
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.restoreAllMocks();
});

function fakeFetch(workspaces: WorkspaceSummary[]): typeof fetch {
  return vi.fn(async (url: RequestInfo | URL) => {
    const u = typeof url === "string" ? url : url.toString();
    if (u.endsWith("/api/v2/workspaces")) {
      return new Response(
        JSON.stringify({ workspaces }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    return new Response("not found", { status: 404 });
  }) as unknown as typeof fetch;
}

let captured: ReturnType<typeof useWorkspaceContext> | null = null;

function ConsumeContext(): React.ReactElement {
  captured = useWorkspaceContext();
  return <div data-testid="consumer" />;
}

async function flushAsync(): Promise<void> {
  // Yield to the microtask queue + give async effects (fetch +
  // setState chain) a chance to land.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("WorkspaceProvider hydration", () => {
  it("loads workspaces on mount and resolves workspaceReady", async () => {
    const rows: WorkspaceSummary[] = [
      {
        workspace_id: "ws-1",
        slug: "acme",
        name: "Acme",
        plan_tier: "free",
        role: "owner",
      },
      {
        workspace_id: "ws-2",
        slug: "beta",
        name: "Beta",
        plan_tier: "pro",
        role: "member",
      },
    ];
    vi.stubGlobal("fetch", fakeFetch(rows));

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    expect(captured?.workspaces.length).toBe(2);
    expect(captured?.activeWorkspace?.workspace_id).toBe("ws-1");
    expect(getActiveWorkspaceId()).toBe("ws-1");
  });

  it("respects localStorage-persisted id when still in the list", async () => {
    window.localStorage.setItem(STORAGE_KEY, "ws-2");
    const rows: WorkspaceSummary[] = [
      {
        workspace_id: "ws-1",
        slug: "acme",
        name: "Acme",
        plan_tier: "free",
        role: "owner",
      },
      {
        workspace_id: "ws-2",
        slug: "beta",
        name: "Beta",
        plan_tier: "pro",
        role: "member",
      },
    ];
    vi.stubGlobal("fetch", fakeFetch(rows));

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    expect(captured?.activeWorkspace?.workspace_id).toBe("ws-2");
    expect(getActiveWorkspaceId()).toBe("ws-2");
  });

  it("falls through to first workspace when persisted id is stale", async () => {
    window.localStorage.setItem(STORAGE_KEY, "ws-deleted");
    const rows: WorkspaceSummary[] = [
      {
        workspace_id: "ws-1",
        slug: "acme",
        name: "Acme",
        plan_tier: "free",
        role: "owner",
      },
    ];
    vi.stubGlobal("fetch", fakeFetch(rows));

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    expect(captured?.activeWorkspace?.workspace_id).toBe("ws-1");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("ws-1");
  });

  it("0 workspaces leaves activeWorkspace null and resolves ready", async () => {
    vi.stubGlobal("fetch", fakeFetch([]));

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    expect(captured?.loading).toBe(false);
    expect(captured?.activeWorkspace).toBeNull();
    expect(getActiveWorkspaceId()).toBeNull();
  });

  it("skipInitialFetch resolves ready without firing fetch", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider skipInitialFetch>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("setActiveWorkspace", () => {
  it("ignores an id outside the user's listWorkspaces (spoofing guard)", async () => {
    const rows: WorkspaceSummary[] = [
      {
        workspace_id: "ws-1",
        slug: "acme",
        name: "Acme",
        plan_tier: "free",
        role: "owner",
      },
    ];
    vi.stubGlobal("fetch", fakeFetch(rows));

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    await act(async () => {
      captured!.setActiveWorkspace("ws-spoofed");
    });
    await flushAsync();

    expect(captured?.activeWorkspace?.workspace_id).toBe("ws-1");
    expect(getActiveWorkspaceId()).toBe("ws-1");
  });

  it("switches to a valid id from the list and persists it", async () => {
    const rows: WorkspaceSummary[] = [
      {
        workspace_id: "ws-1",
        slug: "acme",
        name: "Acme",
        plan_tier: "free",
        role: "owner",
      },
      {
        workspace_id: "ws-2",
        slug: "beta",
        name: "Beta",
        plan_tier: "free",
        role: "admin",
      },
    ];
    vi.stubGlobal("fetch", fakeFetch(rows));

    captured = null;
    await act(async () => {
      root.render(
        <WorkspaceProvider>
          <ConsumeContext />
        </WorkspaceProvider>,
      );
    });
    await workspaceReady();
    await flushAsync();

    await act(async () => {
      captured!.setActiveWorkspace("ws-2");
    });
    await flushAsync();

    expect(captured?.activeWorkspace?.workspace_id).toBe("ws-2");
    expect(getActiveWorkspaceId()).toBe("ws-2");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("ws-2");
  });
});
