// useOrchestratorState — λ Wave-2 polling hook tests.
//
// Covers: skip-when-null-wsId, 3s polling cadence, state derivation
// for the four real-data shapes (no-runs, running, last-completed,
// last-errored), cleanup on unmount, optimistic re-run + revert,
// theme_label fallback, and 503 orchestrator_disabled handling.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { HttpError } from "../../lib/httpClient";
import type { OrchestratorRunDTO } from "./api";

// API module mocked at module load — every call goes through these
// vi.fn() spies. Tests reset call history in beforeEach.
const listMock = vi.fn();
const startMock = vi.fn();
vi.mock("./api", () => ({
  listOrchestratorRuns: (...args: unknown[]) => listMock(...args),
  startOrchestratorRun: (...args: unknown[]) => startMock(...args),
}));

import { useOrchestratorState } from "./useOrchestratorState";
import type { UseOrchestratorStateResult } from "./useOrchestratorState";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let captured: UseOrchestratorStateResult | null = null;

function HookHost({ wsId }: { wsId: string | null }) {
  captured = useOrchestratorState(wsId);
  return null;
}

function mountHook(wsId: string | null): void {
  act(() => {
    root.render(<HookHost wsId={wsId} />);
  });
}

function makeRunDTO(overrides: Partial<OrchestratorRunDTO> = {}): OrchestratorRunDTO {
  return {
    run_id: "or-1",
    workspace_id: "ws-test",
    prioritization_run_id: "pr-1",
    triggered_by: "user-1",
    top_n: 5,
    status: "running",
    started_at: "2026-05-03T11:58:00Z",
    completed_at: null,
    summary: null,
    error: null,
    sub_agents: [],
    ...overrides,
  };
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-05-03T12:00:00Z"));
  listMock.mockReset();
  startMock.mockReset();
  captured = null;
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.useRealTimers();
});

// Drains pending Promise microtasks created by the hook's async
// fetchBoth. Vitest's fake timers don't auto-flush microtasks, so
// we explicitly await a real-time tick wrapped in act.
async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useOrchestratorState — workspace gating", () => {
  it("returns idle and never polls when workspaceId is null", async () => {
    mountHook(null);
    await flushAsync();
    expect(captured!.state.state).toBe("idle");
    expect(listMock).not.toHaveBeenCalled();
    // Advance fake timer past the polling interval; still no calls.
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(listMock).not.toHaveBeenCalled();
  });
});

describe("useOrchestratorState — derivation from polled data", () => {
  it("idle with lastFinishedAt when 0 running + 1 completed", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "completed"
            ? [
                makeRunDTO({
                  status: "completed",
                  completed_at: "2026-05-03T11:55:00Z",
                }),
              ]
            : [],
      }),
    );
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("idle");
    expect(captured!.state.lastFinishedAt).toBe("2026-05-03T11:55:00Z");
    expect(captured!.state.runId).toBeNull();
  });

  it("idle with null lastFinishedAt when no runs at all", async () => {
    listMock.mockResolvedValue({ runs: [] });
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("idle");
    expect(captured!.state.lastFinishedAt).toBeNull();
  });

  it("running with mapped agents when 1+ running run", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "running"
            ? [
                makeRunDTO({
                  run_id: "or-active",
                  status: "running",
                  started_at: "2026-05-03T11:59:00Z",
                  sub_agents: [
                    {
                      sub_agent_run_id: "sa-1",
                      theme_id: "cluster-a",
                      theme_label: "Login crashes",
                      project_id: null,
                      status: "running",
                      started_at: "2026-05-03T11:59:01Z",
                      completed_at: null,
                      decisions_count: 0,
                      conflicts_count: 0,
                      error: null,
                    },
                    {
                      sub_agent_run_id: "sa-2",
                      theme_id: "cluster-b",
                      theme_label: null,
                      project_id: null,
                      status: "completed",
                      started_at: "2026-05-03T11:59:01Z",
                      completed_at: "2026-05-03T11:59:30Z",
                      decisions_count: 3,
                      conflicts_count: 0,
                      error: null,
                    },
                  ],
                }),
              ]
            : [],
      }),
    );
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("running");
    expect(captured!.state.runId).toBe("or-active");
    expect(captured!.state.startedAt).toBe("2026-05-03T11:59:00Z");
    expect(captured!.state.agents).toHaveLength(2);
    // theme_label preferred over theme_id when set.
    expect(captured!.state.agents[0].name).toBe("Login crashes");
    expect(captured!.state.agents[0].status).toBe("working");
    // theme_label null → fall back to theme_id.
    expect(captured!.state.agents[1].name).toBe("cluster-b");
    expect(captured!.state.agents[1].status).toBe("done");
  });

  it("failed when 0 running + last completed is error", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "completed"
            ? [
                makeRunDTO({
                  run_id: "or-errored",
                  status: "error",
                  completed_at: "2026-05-03T11:30:00Z",
                  error: "stub failure",
                }),
              ]
            : [],
      }),
    );
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("failed");
    expect(captured!.state.lastFinishedAt).toBe("2026-05-03T11:30:00Z");
  });

  it("conflict when running run has a sub-agent with conflicts_count > 0", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "running"
            ? [
                makeRunDTO({
                  status: "running",
                  sub_agents: [
                    {
                      sub_agent_run_id: "sa-conflict",
                      theme_id: "cluster-x",
                      theme_label: "Conflicting work",
                      project_id: null,
                      status: "running",
                      started_at: "2026-05-03T11:59:00Z",
                      completed_at: null,
                      decisions_count: 1,
                      conflicts_count: 1,
                      error: null,
                    },
                  ],
                }),
              ]
            : [],
      }),
    );
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("conflict");
    expect(captured!.state.conflict).not.toBeNull();
    expect(captured!.state.conflict!.description).toContain("conflict");
  });
});

describe("useOrchestratorState — polling cadence + cleanup", () => {
  it("calls listOrchestratorRuns twice per tick (running + completed)", async () => {
    listMock.mockResolvedValue({ runs: [] });
    mountHook("ws-test");
    await flushAsync();
    // Initial tick = 2 calls.
    expect(listMock).toHaveBeenCalledTimes(2);
    // Advance 3s → second tick = 2 more calls.
    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(listMock).toHaveBeenCalledTimes(4);
  });

  it("stops polling on unmount", async () => {
    listMock.mockResolvedValue({ runs: [] });
    mountHook("ws-test");
    await flushAsync();
    expect(listMock).toHaveBeenCalledTimes(2);
    act(() => {
      root.unmount();
    });
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(listMock).toHaveBeenCalledTimes(2);
  });
});

describe("useOrchestratorState — re-run", () => {
  it("rerunDisabled false when no prior run (cold-start hits /prioritize)", async () => {
    // Founder direction (2026-05-04): the AI Status "Trigger a new run
    // now" CTA must work for fresh workspaces with no prior orchestrator
    // runs — rerun() routes to startPrioritization() in that case
    // instead of returning early. Button stays clickable.
    listMock.mockResolvedValue({ runs: [] });
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.rerunDisabled).toBe(false);
    expect(captured!.rerunTooltip).toBe(null);
  });

  it("rerunDisabled while a run is already running", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "running"
            ? [makeRunDTO({ status: "running" })]
            : [],
      }),
    );
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("running");
    expect(captured!.rerunDisabled).toBe(true);
    expect(captured!.rerunTooltip).toBeNull();
  });

  it("rerun() POSTs the most-recent prioritization_run_id and flips state optimistically", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "completed"
            ? [
                makeRunDTO({
                  run_id: "or-old",
                  prioritization_run_id: "pr-the-one",
                  status: "completed",
                  completed_at: "2026-05-03T11:00:00Z",
                }),
              ]
            : [],
      }),
    );
    startMock.mockResolvedValue({
      run_id: "or-new",
      status: "running",
      idempotent_hit: false,
    });
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("idle");
    expect(captured!.rerunDisabled).toBe(false);

    await act(async () => {
      await captured!.rerun();
    });

    expect(startMock).toHaveBeenCalledWith("pr-the-one");
    // Optimistic update + reconciled runId.
    expect(captured!.state.state).toBe("running");
    expect(captured!.state.runId).toBe("or-new");
    expect(captured!.state.startedAt).toBe(
      new Date("2026-05-03T12:00:00Z").toISOString(),
    );
  });

  it("rerun() reverts state when POST rejects", async () => {
    listMock.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve({
        runs:
          status === "completed"
            ? [
                makeRunDTO({
                  status: "completed",
                  completed_at: "2026-05-03T11:00:00Z",
                }),
              ]
            : [],
      }),
    );
    startMock.mockRejectedValue(new HttpError(500, "/run", "boom"));
    // Suppress expected console.warn from the catch branch.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mountHook("ws-test");
    await flushAsync();
    const before = captured!.state;
    await act(async () => {
      await captured!.rerun();
    });
    expect(captured!.state.state).toBe(before.state);
    expect(captured!.state.lastFinishedAt).toBe(before.lastFinishedAt);
    warn.mockRestore();
  });
});

describe("useOrchestratorState — 503 orchestrator_disabled", () => {
  it("renders idle silently when env-gate is off", async () => {
    listMock.mockRejectedValue(
      new HttpError(503, "/runs", { error: "orchestrator_disabled" }),
    );
    mountHook("ws-test");
    await flushAsync();
    expect(captured!.state.state).toBe("idle");
    expect(captured!.state.lastFinishedAt).toBeNull();
  });
});
