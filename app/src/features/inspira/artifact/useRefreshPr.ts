import { useCallback, useRef, useState } from "react";

import {
  api,
  type RefreshDecision,
  type RefreshDiffResponse,
  type RefreshResolveResponse,
  type StartRefreshResponse,
} from "../api";

export type RefreshPrState =
  | "idle"
  | "refreshing"
  | "ready"
  | "resolving"
  | "resolved"
  | "error";

export type UseRefreshPrReturn = {
  state: RefreshPrState;
  refreshId: string | null;
  diff: RefreshDiffResponse | null;
  error: Error | null;
  startRefresh: () => Promise<void>;
  submitResolutions: (
    decisions: Record<string, RefreshDecision>,
  ) => Promise<RefreshResolveResponse | null>;
  reset: () => void;
};

/**
 * Wave F.6 — kicks off "Refresh PR with Inspira", loads the diff
 * payload, and submits per-file resolutions.
 *
 * Server-side the POST /refresh-overlay route runs synchronously: the
 * adapter call happens inside ``run_in_executor`` and the response
 * doesn't come back until the new scaffold is persisted. That means
 * we don't actually need to poll — by the time ``startRefresh``
 * resolves we can fetch the diff immediately.
 *
 * (The plan originally specced exponential-backoff polling for a 30-90
 * second window; the implementation collapses that into a single
 * await because the BE doesn't expose a separate "in progress" path
 * to poll. If a future variant moves refresh to a BackgroundTask, add
 * polling back here.)
 *
 * Cancellation: an in-flight refresh is tracked by an incrementing
 * generation counter. Calling ``reset`` (or unmounting) increments
 * the counter so any pending responses from a previous attempt
 * resolve into a no-op.
 */
export function useRefreshPr(
  projectId: string | null,
): UseRefreshPrReturn {
  const [state, setState] = useState<RefreshPrState>("idle");
  const [refreshId, setRefreshId] = useState<string | null>(null);
  const [diff, setDiff] = useState<RefreshDiffResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const generationRef = useRef<number>(0);

  const startRefresh = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    generationRef.current += 1;
    const myGen = generationRef.current;
    setState("refreshing");
    setError(null);
    setDiff(null);
    try {
      const start: StartRefreshResponse = await api.startPrRefresh(
        projectId,
      );
      if (generationRef.current !== myGen) return;
      setRefreshId(start.refresh_id);
      const diffPayload = await api.getRefreshDiff(
        projectId, start.refresh_id,
      );
      if (generationRef.current !== myGen) return;
      setDiff(diffPayload);
      setState("ready");
    } catch (err: unknown) {
      if (generationRef.current !== myGen) return;
      setError(err instanceof Error ? err : new Error(String(err)));
      setState("error");
    }
  }, [projectId]);

  const submitResolutions = useCallback(
    async (
      decisions: Record<string, RefreshDecision>,
    ): Promise<RefreshResolveResponse | null> => {
      if (!projectId || !refreshId) return null;
      generationRef.current += 1;
      const myGen = generationRef.current;
      setState("resolving");
      setError(null);
      try {
        const result = await api.postRefreshResolutions(projectId, {
          refresh_id: refreshId,
          decisions,
        });
        if (generationRef.current !== myGen) return null;
        setState("resolved");
        return result;
      } catch (err: unknown) {
        if (generationRef.current !== myGen) return null;
        setError(err instanceof Error ? err : new Error(String(err)));
        setState("error");
        return null;
      }
    },
    [projectId, refreshId],
  );

  const reset = useCallback((): void => {
    generationRef.current += 1;
    setState("idle");
    setRefreshId(null);
    setDiff(null);
    setError(null);
  }, []);

  return {
    state,
    refreshId,
    diff,
    error,
    startRefresh,
    submitResolutions,
    reset,
  };
}
