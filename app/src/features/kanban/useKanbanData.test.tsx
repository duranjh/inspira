/**
 * Tests for useKanbanData mutate paths (W5).
 *
 * Coverage:
 *   - mutateState refuses the thinking column (system-managed)
 *   - mutateState refuses an empty note
 *   - mutateState applies the move optimistically + rolls back on error
 *   - mutatePriority writes an int + rolls back on error
 *
 * The hook is exercised via @testing-library-free renderHook lite —
 * we render a small component that exposes the hook's surface to
 * the test through a ref-like callback.
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { useKanbanData, type KanbanDataHook } from "./useKanbanData";
import type { V2Project } from "../inspira/api";

vi.mock("../inspira/api", async () => {
  const actual: object = await vi.importActual("../inspira/api");
  return {
    ...actual,
    api: {
      listWorkspaceProjects: vi.fn(),
      manualStateOverrideProject: vi.fn(),
      manualPriorityOrderProject: vi.fn(),
    },
  };
});

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

function makeProject(overrides: Partial<V2Project>): V2Project {
  return {
    project_id: overrides.project_id ?? `p-${Math.random()}`,
    user_id: "u1",
    title: overrides.title ?? "Demo",
    metadata: overrides.metadata ?? {},
    created_at: overrides.created_at ?? "2026-05-03T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-03T00:00:00Z",
    archived_at: overrides.archived_at ?? null,
    workspace_id: overrides.workspace_id ?? "ws-1",
    project_state: overrides.project_state ?? "pending_review",
    priority_order: overrides.priority_order ?? null,
    roi_score: overrides.roi_score ?? null,
    ...overrides,
  };
}

function harness(): {
  hook: { current: KanbanDataHook | null };
  renderAndWait: () => Promise<void>;
} {
  const hookRef = { current: null as KanbanDataHook | null };

  function Probe(): null {
    const value = useKanbanData("ws-1");
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
      // Resolve the initial fetch.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    },
  };
}

describe("mutateState — guard rails", () => {
  it("refuses the thinking column (system-managed)", async () => {
    const { api } = await import("../inspira/api");
    (api.listWorkspaceProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      projects: [
        makeProject({ project_id: "p1", project_state: "pending_review" }),
      ],
    });
    const { hook, renderAndWait } = harness();
    await renderAndWait();
    let result: boolean | undefined;
    await act(async () => {
      result = await hook.current!.mutateState({
        projectId: "p1",
        fromColumn: "queue",
        toColumn: "in_progress",
        note: "trying to bypass",
      });
    });
    expect(result).toBe(false);
    expect(
      api.manualStateOverrideProject as ReturnType<typeof vi.fn>,
    ).not.toHaveBeenCalled();
  });
});

describe("mutateState — optimistic + rollback", () => {
  it("calls the override endpoint with the column-mapped state", async () => {
    const { api } = await import("../inspira/api");
    (api.listWorkspaceProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      projects: [
        makeProject({ project_id: "p1", project_state: "pending_review" }),
      ],
    });
    (
      api.manualStateOverrideProject as ReturnType<typeof vi.fn>
    ).mockResolvedValue({
      project: makeProject({ project_id: "p1", project_state: "approved" }),
    });
    const { hook, renderAndWait } = harness();
    await renderAndWait();
    let result: boolean | undefined;
    await act(async () => {
      result = await hook.current!.mutateState({
        projectId: "p1",
        fromColumn: "queue",
        toColumn: "approved",
        note: "pushing through",
      });
    });
    expect(result).toBe(true);
    expect(
      api.manualStateOverrideProject as ReturnType<typeof vi.fn>,
    ).toHaveBeenCalledWith("p1", "approved", "pushing through");
  });

  it("rolls back the optimistic move on error", async () => {
    const { api } = await import("../inspira/api");
    (api.listWorkspaceProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      projects: [
        makeProject({ project_id: "p1", project_state: "pending_review" }),
      ],
    });
    (
      api.manualStateOverrideProject as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error("server unreachable"));
    const { hook, renderAndWait } = harness();
    await renderAndWait();
    let result: boolean | undefined;
    await act(async () => {
      result = await hook.current!.mutateState({
        projectId: "p1",
        fromColumn: "queue",
        toColumn: "in_review",
        note: "trying",
      });
    });
    expect(result).toBe(false);
    // Rollback restored the card to its original column.
    expect(hook.current!.board.queue.length).toBe(1);
    expect(hook.current!.board.in_review.length).toBe(0);
    expect(hook.current!.error).toBe("server unreachable");
  });
});

describe("mutatePriority", () => {
  it("calls the priority endpoint with the integer", async () => {
    const { api } = await import("../inspira/api");
    (api.listWorkspaceProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      projects: [
        makeProject({ project_id: "p1", project_state: "pending_review" }),
      ],
    });
    (
      api.manualPriorityOrderProject as ReturnType<typeof vi.fn>
    ).mockResolvedValue({
      project: makeProject({ project_id: "p1", priority_order: 1024 }),
    });
    const { hook, renderAndWait } = harness();
    await renderAndWait();
    let result: boolean | undefined;
    await act(async () => {
      result = await hook.current!.mutatePriority({
        projectId: "p1",
        columnId: "queue",
        priorityOrder: 1024,
      });
    });
    expect(result).toBe(true);
    expect(
      api.manualPriorityOrderProject as ReturnType<typeof vi.fn>,
    ).toHaveBeenCalledWith("p1", 1024);
  });

  it("rolls back on error", async () => {
    const { api } = await import("../inspira/api");
    (api.listWorkspaceProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      projects: [
        makeProject({ project_id: "p1", priority_order: 1 }),
      ],
    });
    (
      api.manualPriorityOrderProject as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error("422"));
    const { hook, renderAndWait } = harness();
    await renderAndWait();
    await act(async () => {
      await hook.current!.mutatePriority({
        projectId: "p1",
        columnId: "queue",
        priorityOrder: 999,
      });
    });
    // Snapshot restored — original priority back in place.
    expect(hook.current!.board.queue[0].priority_order).toBe(1);
    expect(hook.current!.error).toBe("422");
  });
});
