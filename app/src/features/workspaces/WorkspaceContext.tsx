// React context + module-level emitter for the active workspace.
//
// W2 C4 design:
//
//  - Source of truth for the active workspace_id is this context.
//    The httpClient interceptor reads via the module-level
//    ``getActiveWorkspaceId()`` getter (no React hook required —
//    fetch wrappers run outside React's render tree).
//
//  - Initialization: the provider calls ``listWorkspaces()`` once
//    on mount. Once the response lands, ``workspaceReady()``
//    resolves so the httpClient can stop blocking.
//
//  - Workspace selection: prefer the localStorage-persisted ID if
//    it still appears in the user's listWorkspaces; else default
//    to the first; else null (anon or zero workspaces).
//
//  - Persistence: localStorage key ``inspira_active_workspace_id``.
//    Survives reloads on the same device. Cross-device drift is
//    accepted — a user logging in on a new browser falls through
//    to "first workspace" until they switch.
//
//  - URL params NEVER drive context updates here. A future deep
//    link like ``/connectors?workspace=ws-xyz`` should be parsed
//    by the page component, validated against the user's
//    listWorkspaces, then call ``setActiveWorkspace(id)`` if
//    valid. This isolates the "spoofed workspace_id from URL"
//    attack surface to the entry-page validators rather than the
//    interceptor.
//
// Mirrors the state-emitter pattern at ``app/src/theme/index.ts``
// — module-level subscriber list lets non-React code (httpClient)
// read live state without a context hook.

import {
  createContext,
  ReactElement,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { httpClient } from "../../lib/httpClient";

// -----------------------------------------------------------
// Types
// -----------------------------------------------------------

export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export interface WorkspaceSummary {
  workspace_id: string;
  slug: string;
  name: string;
  plan_tier: string;
  role: WorkspaceRole;
}

export interface WorkspaceContextValue {
  /** All active workspaces the current user is a member of. */
  workspaces: WorkspaceSummary[];
  /** Currently-selected workspace, or null when none. */
  activeWorkspace: WorkspaceSummary | null;
  /** True while the first listWorkspaces() call is in flight. */
  loading: boolean;
  /** Last fetch error, or null. */
  error: string | null;
  /** Switch to another workspace. Only accepts IDs from `workspaces`. */
  setActiveWorkspace: (workspaceId: string) => void;
  /** Re-fetch the workspace list (e.g. after a CreateWorkspaceDialog). */
  refresh: () => Promise<void>;
}

// -----------------------------------------------------------
// Module-level state for non-React callers (httpClient interceptor)
// -----------------------------------------------------------

const STORAGE_KEY = "inspira_active_workspace_id";

let _activeWorkspaceId: string | null = null;

// Promise resolved once the first hydration completes (success OR
// empty list). The httpClient interceptor awaits this before
// emitting a non-skipped request — that's the init-race fix.
let _resolveReady: () => void = () => {};
let _readyPromise: Promise<void> = new Promise<void>((resolve) => {
  _resolveReady = resolve;
});

/**
 * Returns the currently-active workspace_id, or null when none.
 * Safe to call before the provider mounts (returns null) or
 * after; safe to call from non-React code.
 */
export function getActiveWorkspaceId(): string | null {
  return _activeWorkspaceId;
}

/**
 * Promise that resolves once the workspace context has finished
 * its initial hydration. Resolves regardless of outcome (loaded
 * with N workspaces, loaded with 0, or fetch failed).
 */
export function workspaceReady(): Promise<void> {
  return _readyPromise;
}

// Test-only: reset module state between tests. Production callers
// don't touch this.
export function __resetForTesting(): void {
  _activeWorkspaceId = null;
  _readyPromise = new Promise<void>((resolve) => {
    _resolveReady = resolve;
  });
}

// Test-only: resolve the ready promise without going through the
// provider mount. Used by tests that exercise modules which await
// ``workspaceReady()`` (e.g. ``inspira/api.ts`` postJson/getJson)
// but don't actually mount a ``WorkspaceProvider``.
export function __resolveReadyForTesting(workspaceId: string | null): void {
  _activeWorkspaceId = workspaceId;
  _resolveReady();
}

// -----------------------------------------------------------
// Context
// -----------------------------------------------------------

const WorkspaceCtx = createContext<WorkspaceContextValue | null>(null);

function readPersistedWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw && raw.trim() ? raw.trim() : null;
  } catch {
    return null;
  }
}

function writePersistedWorkspaceId(workspaceId: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (workspaceId) {
      window.localStorage.setItem(STORAGE_KEY, workspaceId);
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // localStorage can throw in private mode + SSR. Swallow —
    // the in-memory state is still authoritative for the session.
  }
}

interface ListWorkspacesResponse {
  workspaces: WorkspaceSummary[];
}

async function fetchWorkspaces(): Promise<WorkspaceSummary[]> {
  const body = await httpClient.get<ListWorkspacesResponse>(
    "/api/v2/workspaces",
  );
  return body.workspaces ?? [];
}

export interface WorkspaceProviderProps {
  children: ReactNode;
  /**
   * When true, skip the initial fetch entirely and resolve ready
   * with an empty list. Used by tests + the SSR pass.
   */
  skipInitialFetch?: boolean;
}

export function WorkspaceProvider({
  children,
  skipInitialFetch,
}: WorkspaceProviderProps): ReactElement {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(!skipInitialFetch);
  const [error, setError] = useState<string | null>(null);

  // Apply a workspace id locally + module-level + localStorage in
  // one place so no codepath drifts.
  const applyActive = useCallback((id: string | null) => {
    setActiveId(id);
    _activeWorkspaceId = id;
    writePersistedWorkspaceId(id);
  }, []);

  const reconcileSelection = useCallback(
    (rows: WorkspaceSummary[]) => {
      // Selection priority:
      //   1. localStorage-persisted ID, if still in the list.
      //   2. First workspace in the list.
      //   3. null (anon or zero workspaces).
      const persisted = readPersistedWorkspaceId();
      if (persisted && rows.some((w) => w.workspace_id === persisted)) {
        applyActive(persisted);
        return;
      }
      if (rows.length > 0) {
        applyActive(rows[0].workspace_id);
        return;
      }
      applyActive(null);
    },
    [applyActive],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchWorkspaces();
      setWorkspaces(rows);
      reconcileSelection(rows);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
      _resolveReady();
    }
  }, [reconcileSelection]);

  // React 19 Strict Mode in dev double-fires effects to surface
  // cleanup bugs. Without a guard, the auto-mount path would
  // call listWorkspaces twice on every dev mount — masking
  // production behaviour and inflating the dev network panel.
  // The ref is local to this provider instance; explicit
  // refresh() calls (e.g. from CreateWorkspaceDialog success)
  // still work because they go through the public callback,
  // not this guarded effect.
  const initialFetchedRef = useRef(false);

  useEffect(() => {
    if (skipInitialFetch) {
      _resolveReady();
      setLoading(false);
      return;
    }
    if (initialFetchedRef.current) return;
    initialFetchedRef.current = true;
    void refresh();
    // refresh is stable via useCallback, but lint wants it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skipInitialFetch]);

  const setActiveWorkspace = useCallback(
    (workspaceId: string) => {
      // C4 watch point #1: only accept IDs the user already
      // belongs to. The interceptor must never see a workspace_id
      // outside the membership list. If a caller (URL parser,
      // future deep-link handler) passes a stranger id, ignore
      // silently — components should validate before calling.
      if (!workspaces.some((w) => w.workspace_id === workspaceId)) {
        return;
      }
      applyActive(workspaceId);
    },
    [workspaces, applyActive],
  );

  const value: WorkspaceContextValue = {
    workspaces,
    activeWorkspace:
      workspaces.find((w) => w.workspace_id === activeId) ?? null,
    loading,
    error,
    setActiveWorkspace,
    refresh,
  };

  return (
    <WorkspaceCtx.Provider value={value}>{children}</WorkspaceCtx.Provider>
  );
}

export function useWorkspaceContext(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceCtx);
  if (ctx === null) {
    throw new Error(
      "useWorkspaceContext must be used inside a <WorkspaceProvider>",
    );
  }
  return ctx;
}
