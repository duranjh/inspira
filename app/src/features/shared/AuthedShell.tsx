// AuthedShell — left-rail + content wrapper for v4 routes.
//
// Used by /workspaces (Kanban home), /connectors, /inbox, and the
// future member-mgmt + settings surfaces. NOT used by the canvas /
// topic-detail flows — those keep InspiraApp's own chrome (avoids
// regression risk on the canvas codebase).
//
// Layout:
//   ┌──────┬───────────────────────────────────────────────┐
//   │      │                                                │
//   │ rail │              page content                      │
//   │      │                                                │
//   └──────┴───────────────────────────────────────────────┘
//
// Rail (60px collapsed / 224px expanded) hosts:
//   • WorkspaceSwitcher (brand variant when expanded; ws-mini icon
//     when collapsed)
//   • Workspaces / Connectors / Inbox nav links
//   • AIStatus chip
//   • UserMenu (avatar dropdown + sign out)
//
// First-run interaction: when the workspace context has 0
// workspaces, AuthedShell renders the FirstRunCard + CreateWorkspaceDialog.
// The page content is hidden until at least one workspace exists,
// and the rail's nav links are hidden too (no point teasing
// /connectors when there's no workspace yet).

import { ReactElement, ReactNode, useState } from "react";

import { CreateWorkspaceDialog } from "../workspaces/CreateWorkspaceDialog";
import { FirstRunCard } from "../workspaces/FirstRunCard";
import { useWorkspaceContext } from "../workspaces/WorkspaceContext";
import { AppRail } from "./AppRail";

export interface AuthedShellProps {
  children: ReactNode;
  /**
   * When true, the shell renders the page content even if the
   * user has 0 workspaces (used by /workspaces itself, since
   * that's where they create their first one). Default false:
   * the FirstRunCard takes over the content area.
   */
  allowZeroWorkspaces?: boolean;
  /**
   * Optional surface-specific widget rendered in the AppRail's
   * right-slot. Pure passthrough — the route decides what (if
   * anything) belongs there. CodeRoute passes `<OrchestratorChip />`
   * on /code/:projectId so agent activity surfaces in the rail.
   */
  rightSlot?: ReactNode;
}

export function AuthedShell({
  children,
  allowZeroWorkspaces,
  rightSlot,
}: AuthedShellProps): ReactElement {
  const ctx = useWorkspaceContext();
  const [createOpen, setCreateOpen] = useState(false);

  const showFirstRun =
    !ctx.loading &&
    ctx.workspaces.length === 0 &&
    !allowZeroWorkspaces;

  return (
    <div className="authed-shell authed-shell--rail">
      <AppRail hideNav={showFirstRun} rightSlot={rightSlot} />

      <main className="authed-shell__main" id="main-content" tabIndex={-1}>
        {showFirstRun ? (
          <FirstRunCard onCreate={() => setCreateOpen(true)} />
        ) : (
          children
        )}
      </main>

      <CreateWorkspaceDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        firstRun={showFirstRun}
      />
    </div>
  );
}
