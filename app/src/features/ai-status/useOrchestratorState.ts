// useOrchestratorState — Wave-2 polling hook for the AI Status chip.
//
// Replaces the earlier mock useState. Polls the workspace's orchestrator runs
// every 3s and derives the chip's OrchestratorState. SSE per-event
// updates are deferred to Wave 3 — the canvas-side per-agent live
// animation already covers the visual fidelity case (useSSE wires
// MultiAgentDots + ConflictBanner). The chip's job is the workspace-
// level "is anything running right now?" signal, which polling answers
// with a 3s lag.
//
// Workspace sourcing: caller passes workspaceId (typically from
// useWorkspaceContext().activeWorkspace). Pass null to skip polling
// entirely (test path or pre-context-resolve render).
//
// Re-run: POST /api/v2/orchestrator/run with the prioritization_run_id
// of the most recent run (running OR completed). Cold start (no prior
// run) → button disabled with tooltip directing the partner to the
// actual cold-start path (Promote-from-cluster on /inbox). The
// optimistic update flips state to running on POST success so the chip
// reacts in <100ms instead of waiting for the next 3s tick.

import { useCallback, useEffect, useRef, useState } from "react";

import { HttpError } from "../../lib/httpClient";
import {
  type ListOrchestratorRunsOptions,
  type OrchestratorRunDTO,
  type SubAgentDTO,
  listOrchestratorRuns,
  startOrchestratorRun,
  startPrioritization,
} from "./api";
import { makeIdleState } from "./mockOrchestratorState";
import type {
  OrchestratorConflict,
  OrchestratorState,
  SubAgent,
  SubAgentStatus,
} from "./types";

export const POLL_INTERVAL_MS = 3000;

export interface UseOrchestratorStateResult {
  state: OrchestratorState;
  rerun: () => Promise<void>;
  rerunDisabled: boolean;
  rerunTooltip: string | null;
}

interface FetchedRuns {
  running: OrchestratorRunDTO[];
  lastCompleted: OrchestratorRunDTO | null;
}

function mapSubAgentStatus(s: SubAgentDTO["status"]): SubAgentStatus {
  switch (s) {
    case "running":
      return "working";
    case "completed":
      return "done";
    case "error":
      return "failed";
  }
}

function mapSubAgent(dto: SubAgentDTO): SubAgent {
  // theme_label is the joined human label; theme_id is the cluster_id
  // fallback. Activity copy is intentionally minimal — the orchestrator's SSE event
  // stream owns rich activity strings, which the canvas surfaces.
  return {
    id: dto.sub_agent_run_id,
    name: dto.theme_label ?? dto.theme_id,
    status: mapSubAgentStatus(dto.status),
    activity:
      dto.status === "running"
        ? "Working…"
        : dto.status === "completed"
          ? `${dto.decisions_count} decisions drafted`
          : (dto.error ?? "Sub-agent error"),
  };
}

function deriveConflict(
  run: OrchestratorRunDTO,
): OrchestratorConflict | null {
  // Best-effort conflict surfacing without SSE. If any still-running
  // sub-agent has conflicts_count > 0, the orchestrator detected a
  // pending conflict but hasn't resolved it yet. The chip's conflict
  // state renders a generic banner; rich conflict.detected payloads
  // (decision IDs, descriptions) require SSE — deferred.
  const hasPendingConflict = run.sub_agents.some(
    (sa) => sa.status === "running" && sa.conflicts_count > 0,
  );
  if (!hasPendingConflict) return null;
  return { description: "Resolving sub-agent conflict" };
}

function emptyIdleState(): OrchestratorState {
  // Distinct from the earlier mock makeIdleState (which seeds a 4-hour-ago demo
  // timestamp). The polled chip surfaces an honest "no last run" so
  // partners aren't misled about workspace activity.
  return {
    state: "idle",
    runId: null,
    startedAt: null,
    lastFinishedAt: null,
    agents: [],
    conflict: null,
  };
}

function deriveStateFromRuns(fetched: FetchedRuns): OrchestratorState {
  const { running, lastCompleted } = fetched;
  if (running.length > 0) {
    const run = running[0];
    const conflict = deriveConflict(run);
    return {
      state: conflict ? "conflict" : "running",
      runId: run.run_id,
      startedAt: run.started_at,
      lastFinishedAt: null,
      agents: run.sub_agents.map(mapSubAgent),
      conflict,
    };
  }
  if (lastCompleted && lastCompleted.status === "error") {
    return {
      state: "failed",
      runId: lastCompleted.run_id,
      startedAt: null,
      lastFinishedAt: lastCompleted.completed_at,
      agents: [],
      conflict: null,
    };
  }
  return {
    ...emptyIdleState(),
    lastFinishedAt: lastCompleted?.completed_at ?? null,
  };
}

function pickPrioritizationRunId(fetched: FetchedRuns): string | null {
  // Re-run reuses the most recent run's prioritization_run_id (idempotent
  // backend → repeat POST returns existing run_id with idempotent_hit=true
  // when the prior run is still around). Prefer running > last-completed
  // so an in-flight re-run resolves to itself.
  if (fetched.running.length > 0) return fetched.running[0].prioritization_run_id;
  if (fetched.lastCompleted) return fetched.lastCompleted.prioritization_run_id;
  return null;
}

function isOrchestratorDisabled503(err: unknown): boolean {
  if (!(err instanceof HttpError) || err.status !== 503) return false;
  const detail = err.detail as { error?: string } | string | undefined;
  return (
    typeof detail === "object" &&
    detail !== null &&
    detail.error === "orchestrator_disabled"
  );
}

async function fetchBoth(signal: AbortSignal): Promise<FetchedRuns> {
  const opts = (status: ListOrchestratorRunsOptions["status"], limit?: number) => ({
    status,
    limit,
    signal,
  });
  const [runningResp, completedResp] = await Promise.all([
    listOrchestratorRuns(opts("running")),
    listOrchestratorRuns(opts("completed", 1)),
  ]);
  return {
    running: runningResp.runs,
    lastCompleted: completedResp.runs[0] ?? null,
  };
}

export function useOrchestratorState(
  workspaceId: string | null,
): UseOrchestratorStateResult {
  const [state, setState] = useState<OrchestratorState>(() => makeIdleState());
  const fetchedRef = useRef<FetchedRuns>({ running: [], lastCompleted: null });

  useEffect(() => {
    if (workspaceId === null) {
      // Reset to idle when workspace is unset (logout / no active workspace).
      fetchedRef.current = { running: [], lastCompleted: null };
      setState(makeIdleState());
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    let intervalId: number | null = null;

    const tick = async (): Promise<void> => {
      try {
        const fetched = await fetchBoth(controller.signal);
        if (cancelled) return;
        fetchedRef.current = fetched;
        setState(deriveStateFromRuns(fetched));
      } catch (err) {
        if (cancelled) return;
        if (
          err instanceof DOMException &&
          err.name === "AbortError"
        ) {
          return;
        }
        if (isOrchestratorDisabled503(err)) {
          // Env-gate off (INSPIRA_ORCHESTRATOR_ENABLED unset on the
          // server). Stop polling entirely — re-enabling requires a
          // page reload anyway, and continued polling would spam the
          // network panel with 503s. Chip stays in idle state.
          fetchedRef.current = { running: [], lastCompleted: null };
          setState(emptyIdleState());
          if (intervalId !== null) {
            window.clearInterval(intervalId);
            intervalId = null;
          }
          return;
        }
        // Network blip / 5xx — keep last state, retry next tick.
        // Surface to console for ops; no toast because the chip is
        // ambient and shouldn't shout about transient failures.
        // eslint-disable-next-line no-console
        console.warn("useOrchestratorState poll failed:", err);
      }
    };

    void tick();
    intervalId = window.setInterval(() => void tick(), POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      controller.abort();
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, [workspaceId]);

  const rerun = useCallback(async (): Promise<void> => {
    if (workspaceId === null) return;
    const prioritizationRunId = pickPrioritizationRunId(fetchedRef.current);
    if (prioritizationRunId === null) {
      // No prior run to re-fire — start fresh via /prioritize. The
      // backend creates a prioritization_runs row and kicks off ROI
      // scoring in BackgroundTasks; the next 3s poll picks up the
      // running state.
      setState((prev) => ({
        ...prev,
        state: "running",
        runId: "pending",
        startedAt: new Date().toISOString(),
        lastFinishedAt: null,
        agents: [],
        conflict: null,
      }));
      try {
        const resp = await startPrioritization();
        setState((prev) =>
          prev.state === "running"
            ? { ...prev, runId: resp.run_id }
            : prev,
        );
      } catch (err) {
        setState(deriveStateFromRuns(fetchedRef.current));
        // eslint-disable-next-line no-console
        console.warn("useOrchestratorState startPrioritization failed:", err);
      }
      return;
    }
    // Optimistic update: flip to running immediately so the chip reacts
    // in <100ms. The next 3s poll will reconcile with real agent data.
    setState((prev) => ({
      ...prev,
      state: "running",
      runId: prev.runId ?? "pending",
      startedAt: new Date().toISOString(),
      lastFinishedAt: null,
      agents: [],
      conflict: null,
    }));
    try {
      const resp = await startOrchestratorRun(prioritizationRunId);
      // Patch the placeholder runId with the real one. Agents still
      // empty until next poll lands.
      setState((prev) =>
        prev.state === "running"
          ? { ...prev, runId: resp.run_id }
          : prev,
      );
    } catch (err) {
      // POST failed — re-derive from the latest polled snapshot so a
      // poll that landed during the await isn't clobbered by a stale
      // snapshot taken before the optimistic flip.
      setState(deriveStateFromRuns(fetchedRef.current));
      // eslint-disable-next-line no-console
      console.warn("useOrchestratorState rerun failed:", err);
    }
  }, [workspaceId]);

  // The button stays clickable for cold-start workspaces — `rerun()`
  // detects the no-prior-run case and routes to /prioritize instead.
  // Only disable when no workspace OR a run is already in flight.
  const rerunDisabled =
    workspaceId === null || state.state === "running";
  const rerunTooltip = null;

  return { state, rerun, rerunDisabled, rerunTooltip };
}
