// Stub-mode fixtures. Default export is Idle so the chip ships visually
// static — no fabricated "started 2 min ago" claim on first render.
// Wave 2 (Session α) replaces useState(mockOrchestratorState) with a hook
// that subscribes to the orchestrator SSE stream.

import type { OrchestratorConflict, OrchestratorState } from "./types";

export function makeIdleState(): OrchestratorState {
  return {
    state: "idle",
    runId: null,
    startedAt: null,
    lastFinishedAt: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString(),
    agents: [],
    conflict: null,
  };
}

export function makeRunningState(opts?: {
  conflict?: OrchestratorConflict | null;
}): OrchestratorState {
  return {
    state: "running",
    runId: "mock-run-1",
    startedAt: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
    lastFinishedAt: null,
    agents: [
      {
        id: "a1",
        name: "Reproduce the bug",
        status: "working",
        activity: "Reading 12 feedback items…",
      },
      {
        id: "a2",
        name: "Identify root cause",
        status: "working",
        activity: "Drafting decision 3 of 5…",
      },
      {
        id: "a3",
        name: "Fix login flow",
        status: "conflict",
        activity: "Waiting on orchestrator resolution…",
      },
      {
        id: "a4",
        name: "Test across browsers",
        status: "working",
        activity: "Generating browser test matrix…",
      },
    ],
    conflict:
      opts?.conflict !== undefined
        ? opts.conflict
        : {
            description: "Fix login flow vs Test across browsers",
            decisionAId: "decision-a3-1",
            decisionBId: "decision-a4-1",
          },
  };
}

export function makeFailedState(): OrchestratorState {
  return {
    state: "failed",
    runId: "mock-run-1",
    startedAt: null,
    lastFinishedAt: new Date(Date.now() - 30 * 1000).toISOString(),
    agents: [],
    conflict: null,
  };
}

export function makeConflictState(): OrchestratorState {
  const running = makeRunningState();
  return { ...running, state: "conflict" };
}

export const mockOrchestratorState: OrchestratorState = makeIdleState();
