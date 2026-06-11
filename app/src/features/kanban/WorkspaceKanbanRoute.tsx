// Inspira — /workspaces route entry-point.
//
// Thin wrapper that resolves the user's `default_workspace_id` from
// /api/auth/me and dispatches:
//   - is_system        →  /  (anon → sign-in surface)
//   - no workspace     →  /onboarding  (first-time → wizard)
//   - has workspace    →  <AuthedShell><WorkspaceKanban .../></AuthedShell>
//
// Replaces the legacy `WorkspacesPage` flat picker grid mount per the
// v4 partner journey: post-wizard the user lands on the Kanban home,
// not a workspace-picker. The Kanban now renders inside AuthedShell's
// left-rail chrome — workspace switching, /connectors and /inbox
// nav, and the profile menu live in the rail.

import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { api } from "../inspira/api";
import { AuthedShell } from "../shared/AuthedShell";
import { useWorkspaceContext } from "../workspaces/WorkspaceContext";
import { WorkspaceKanban } from "./WorkspaceKanban";

type GateState =
  | { kind: "loading" }
  | { kind: "redirect"; to: string }
  | { kind: "ready" };

export function WorkspaceKanbanRoute() {
  const [state, setState] = useState<GateState>({ kind: "loading" });
  const ctx = useWorkspaceContext();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        if (me.is_system) {
          setState({ kind: "redirect", to: "/" });
          return;
        }
        if (!me.default_workspace_id && ctx.workspaces.length === 0) {
          setState({ kind: "redirect", to: "/onboarding" });
          return;
        }
        setState({ kind: "ready" });
      } catch {
        if (cancelled) return;
        // Backend unreachable — degrade to anon redirect; the sign-in
        // surface itself surfaces backend errors.
        setState({ kind: "redirect", to: "/" });
      }
    })();
    return () => {
      cancelled = true;
    };
    // ctx.workspaces.length in deps so a freshly-loaded context that
    // arrives after the gate runs still settles us into "ready" if the
    // user actually has a workspace.
  }, [ctx.workspaces.length]);

  if (state.kind === "loading" || ctx.loading) {
    return (
      <div
        aria-busy="true"
        aria-live="polite"
        style={{
          minHeight: "100dvh",
          background: "var(--paper, #F5F0E6)",
        }}
      />
    );
  }
  if (state.kind === "redirect") {
    return <Navigate replace to={state.to} />;
  }
  // Render the Kanban for the *active* workspace (set by the rail's
  // WorkspaceSwitcher), not just default_workspace_id. Falls back to
  // the first workspace in the list.
  //
  // Important: only redirect to /onboarding when ctx has *finished*
  // loading and is still empty — otherwise a brief loading window
  // throws every authed visitor through the wizard (regression I
  // shipped in 762a87a; ctx.loading guard above closes the gap).
  const activeId =
    ctx.activeWorkspace?.workspace_id ??
    ctx.workspaces[0]?.workspace_id ??
    null;
  if (!activeId) {
    return <Navigate replace to="/onboarding" />;
  }
  return (
    <AuthedShell>
      <WorkspaceKanban
        // Keying on the workspace id forces a clean unmount/remount
        // when the user switches workspaces — the kanban hook keeps
        // its own state machine and remounting is the simplest way
        // to avoid stale-data flicker.
        key={activeId}
        workspaceId={activeId}
      />
    </AuthedShell>
  );
}
