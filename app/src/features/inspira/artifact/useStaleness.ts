import { useCallback, useEffect, useRef, useState } from "react";

import { api, type PrOverlayStalenessResponse } from "../api";

const STALENESS_REFRESH_MS = 60_000;

export type UseStalenessReturn = {
  staleness: PrOverlayStalenessResponse | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
};

/**
 * Wave F.5 — fetch + auto-refresh staleness for one project's PR overlay.
 *
 * Fetches once on mount, then re-fetches every 60s. The BE caches the
 * compute path for 60s so a 60s client interval stays cache-friendly
 * without losing freshness when main moves. Aborts in flight on
 * unmount + on projectId change (prevents the stale-response race
 * when the partner clicks between two projects quickly).
 *
 * Errors do NOT block the surrounding UI — staleness is purely
 * advisory. The caller renders banners/badges only when
 * ``staleness?.is_stale`` is true, so a fetch failure simply means
 * no badges appear until the next interval succeeds.
 */
export function useStaleness(
  projectId: string | null,
): UseStalenessReturn {
  const [staleness, setStaleness] =
    useState<PrOverlayStalenessResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);

  // Track the latest projectId in a ref so the interval callback (which
  // closes over its first-render value) always refreshes the current
  // project, not a stale one.
  const projectIdRef = useRef<string | null>(projectId);
  projectIdRef.current = projectId;

  const refresh = useCallback(async (): Promise<void> => {
    const pid = projectIdRef.current;
    if (!pid) {
      setStaleness(null);
      setLoading(false);
      return;
    }
    try {
      const res = await api.getPrOverlayStaleness(pid);
      // Guard against a project switch landing between request + reply.
      if (projectIdRef.current !== pid) return;
      setStaleness(res);
      setError(null);
    } catch (err) {
      if (projectIdRef.current !== pid) return;
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (projectIdRef.current === pid) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setStaleness(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setStaleness(null);
    setError(null);
    api
      .getPrOverlayStaleness(projectId)
      .then((res) => {
        if (cancelled) return;
        setStaleness(res);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    const interval = window.setInterval(() => {
      void refresh();
    }, STALENESS_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [projectId, refresh]);

  return { staleness, loading, error, refresh };
}
