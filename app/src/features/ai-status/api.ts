// AI Status API client (λ Wave-2 wiring).
//
// Reads via the shared httpClient — X-Workspace-Id is auto-injected
// and all reads/writes are workspace-scoped server-side. Mirrors the
// orchestrator router's response shape from
// services/planning_studio_service/orchestrator_router.py
// (the single GET /runs/{id} response, list-wrapped under {runs: [...]}).

import { httpClient } from "../../lib/httpClient";

export type RunStatus = "running" | "completed" | "error";

export type SubAgentStatus = "running" | "completed" | "error";

export interface SubAgentDTO {
  sub_agent_run_id: string;
  theme_id: string;
  /** Human-readable label joined from feedback_clusters.theme on the
   *  backend. ``null`` for legacy rows whose cluster has been deleted
   *  (the chip falls back to ``theme_id`` for display). */
  theme_label: string | null;
  project_id: string | null;
  status: SubAgentStatus;
  started_at: string;
  completed_at: string | null;
  decisions_count: number;
  conflicts_count: number;
  error: string | null;
}

export interface OrchestratorRunDTO {
  run_id: string;
  workspace_id: string;
  prioritization_run_id: string;
  triggered_by: string;
  top_n: number;
  status: RunStatus;
  started_at: string;
  completed_at: string | null;
  summary: Record<string, unknown> | null;
  error: string | null;
  sub_agents: SubAgentDTO[];
}

export interface ListOrchestratorRunsResponse {
  runs: OrchestratorRunDTO[];
}

export interface ListOrchestratorRunsOptions {
  status?: RunStatus;
  limit?: number;
  signal?: AbortSignal;
}

export async function listOrchestratorRuns(
  opts: ListOrchestratorRunsOptions = {},
): Promise<ListOrchestratorRunsResponse> {
  const qs = new URLSearchParams();
  if (opts.status) qs.set("status", opts.status);
  if (typeof opts.limit === "number") qs.set("limit", String(opts.limit));
  const qsStr = qs.toString();
  return httpClient.get<ListOrchestratorRunsResponse>(
    `/api/v2/orchestrator/runs${qsStr ? `?${qsStr}` : ""}`,
    opts.signal ? { signal: opts.signal } : undefined,
  );
}

export interface StartOrchestratorRunResponse {
  run_id: string;
  status: "running";
  idempotent_hit: boolean;
}

export async function startOrchestratorRun(
  prioritization_run_id: string,
  top_n?: number,
): Promise<StartOrchestratorRunResponse> {
  return httpClient.post<StartOrchestratorRunResponse>(
    "/api/v2/orchestrator/run",
    { prioritization_run_id, ...(top_n !== undefined ? { top_n } : {}) },
  );
}

export interface StartPrioritizationResponse {
  run_id: string;
  status: "running";
}

/**
 * Kick off a fresh F6 prioritization run for the workspace. Used by the
 * AI Status "Trigger a new run now" CTA when the workspace has no prior
 * orchestrator runs to re-trigger.
 */
export async function startPrioritization(): Promise<StartPrioritizationResponse> {
  return httpClient.post<StartPrioritizationResponse>(
    "/api/v2/orchestrator/prioritize",
    {},
  );
}
