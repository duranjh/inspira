// AI Status types — shape mirrors the SSE events Wave 2 will fold in
// from /api/v2/orchestrator/runs/{run_id}/events. Keeping runId,
// startedAt, and the per-agent status enum aligned with the backend
// contract so the Wave-2 swap is a one-file change in AIStatus.tsx.

export type AIStatusState = "idle" | "running" | "failed" | "conflict";

export type SubAgentStatus = "working" | "done" | "conflict" | "failed";

export interface SubAgent {
  id: string;
  name: string;
  status: SubAgentStatus;
  activity: string;
}

export interface OrchestratorConflict {
  description: string;
  decisionAId?: string;
  decisionBId?: string;
}

export interface OrchestratorState {
  state: AIStatusState;
  runId: string | null;
  startedAt: string | null;
  lastFinishedAt: string | null;
  agents: SubAgent[];
  conflict: OrchestratorConflict | null;
}
