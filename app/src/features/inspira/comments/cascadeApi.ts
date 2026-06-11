// W2-θ cascade API — small fetch wrapper for the 3 BE endpoints.
//
// Standalone (rather than extending the giant inspira/api.ts) so the
// comments folder is self-contained and easy to fork for ι later.
// Reuses ``DEFAULT_BASE_URL`` from inspira/api so dev / prod base URL
// resolution stays consistent.

import {
  DEFAULT_BASE_URL,
  ProjectNotFoundError,
  dispatchProjectNotFound,
  extractProjectIdFromPath,
  isProjectNotFoundBody,
  maybeDispatchUnauthorized,
} from "../api";

import type {
  CascadePreview,
  CascadeRun,
  ScopeMode,
} from "./types";

export type CommentedDecisionPayload = {
  decision_id: string;
  comment_text: string;
};

async function _fetchJson<T>(
  path: string,
  init: RequestInit,
): Promise<T> {
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    // Route 401 + project-not-found 404 through the same global
    // dispatchers the rest of inspira/api.ts uses, so the
    // SessionExpiredModal + project-not-found toast both fire when
    // a cascade call hits these states (e.g., session expired
    // mid-poll, or the project was deleted in another tab).
    maybeDispatchUnauthorized(path, res.status);
    const detail = await res.text();
    if (res.status === 404 && isProjectNotFoundBody(detail)) {
      const err = new ProjectNotFoundError(extractProjectIdFromPath(path), path);
      dispatchProjectNotFound(err.projectId);
      throw err;
    }
    throw new Error(
      `${init.method ?? "GET"} ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  return res.json() as Promise<T>;
}

export const cascadeApi = {
  preview: (
    projectId: string,
    body: {
      commented_decisions: CommentedDecisionPayload[];
      scope_mode: ScopeMode;
    },
  ): Promise<CascadePreview> =>
    _fetchJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/regenerate-cascade/preview`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  commit: (
    projectId: string,
    body: {
      commented_decisions: CommentedDecisionPayload[];
      scope_mode: ScopeMode;
      confirm_scope?: "none" | "narrow" | "wide";
    },
  ): Promise<{ cascade_id: string; status: "pending" }> =>
    _fetchJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/regenerate-cascade`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  status: (
    projectId: string,
    cascadeId: string,
  ): Promise<CascadeRun> =>
    _fetchJson(
      `/api/v2/projects/${encodeURIComponent(projectId)}/regenerate-cascade/${encodeURIComponent(cascadeId)}`,
      { method: "GET" },
    ),
};

// Poll helper — mirrors getNextStepsArtifact pattern in inspira/api.ts.
// Resolves with the terminal status; rejects if it stays pending past
// ``maxMs``. Caller controls the AbortController via ``signal`` to cancel
// (e.g., on component unmount).
export async function pollCascadeUntilDone(
  projectId: string,
  cascadeId: string,
  options: {
    intervalMs?: number;
    maxMs?: number;
    signal?: AbortSignal;
  } = {},
): Promise<CascadeRun> {
  const intervalMs = options.intervalMs ?? 2000;
  const maxMs = options.maxMs ?? 120_000;
  const signal = options.signal;
  const started = Date.now();
  // Loop with explicit setTimeout so AbortSignal can short-circuit.
  // eslint-disable-next-line no-constant-condition
  while (true) {
    if (signal?.aborted) {
      throw new DOMException("aborted", "AbortError");
    }
    const run = await cascadeApi.status(projectId, cascadeId);
    if (run.status === "complete" || run.status === "failed") {
      return run;
    }
    if (Date.now() - started > maxMs) {
      throw new Error(`cascade poll timed out after ${maxMs}ms`);
    }
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, intervalMs);
      signal?.addEventListener(
        "abort",
        () => {
          clearTimeout(t);
          resolve();
        },
        { once: true },
      );
    });
  }
}
