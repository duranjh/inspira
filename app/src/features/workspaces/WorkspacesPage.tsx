// /workspaces — list-and-create surface. Wrapped by AuthedShell.
//
// Renders the user's workspace list as cards. The "New workspace"
// affordance opens CreateWorkspaceDialog (the same dialog the
// switcher's "+ Create" footer opens). Clicking a workspace card
// switches the active workspace via context.setActiveWorkspace.
//
// C5 scope: viewing + creating + active-switching. Editing
// (rename, archive, transfer ownership) is W5/F11 territory.
//
// `allowZeroWorkspaces=true` on the AuthedShell wrapper means
// this page is reachable even when the user has none — they need
// somewhere to land that has the create button.

import { ReactElement, useState } from "react";

import { AuthedShell } from "../shared/AuthedShell";
import { CreateWorkspaceDialog } from "./CreateWorkspaceDialog";
import { useWorkspaceContext } from "./WorkspaceContext";

export function WorkspacesPage(): ReactElement {
  const ctx = useWorkspaceContext();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <AuthedShell allowZeroWorkspaces>
      <div className="workspaces-page">
        <header className="workspaces-page__header">
          <p className="eyebrow">Workspaces</p>
          <h1 className="display workspaces-page__title">
            Your <em>workspaces</em>
          </h1>
          <p className="meta workspaces-page__lede">
            {ctx.workspaces.length === 0
              ? "You haven't created a workspace yet."
              : ctx.workspaces.length === 1
                ? "You're a member of one workspace."
                : `You're a member of ${ctx.workspaces.length} workspaces.`}
          </p>
        </header>

        <div className="workspaces-page__grid">
          {ctx.workspaces.map((w) => {
            const active =
              ctx.activeWorkspace?.workspace_id === w.workspace_id;
            return (
              <button
                key={w.workspace_id}
                type="button"
                className={
                  "card workspaces-page__card" +
                  (active ? " workspaces-page__card--active" : "")
                }
                onClick={() => {
                  if (!active) ctx.setActiveWorkspace(w.workspace_id);
                }}
              >
                <div className="workspaces-page__card-row">
                  <h2 className="section-title workspaces-page__card-title">
                    {w.name}
                  </h2>
                  <span className="chip chip--ghost">
                    {w.role.charAt(0).toUpperCase() + w.role.slice(1)}
                  </span>
                </div>
                <p className="meta workspaces-page__card-slug">
                  {w.slug} · {w.plan_tier}
                </p>
                {active ? (
                  <span className="chip chip--sage workspaces-page__card-active">
                    Active
                  </span>
                ) : null}
              </button>
            );
          })}

          <button
            type="button"
            className="card workspaces-page__create"
            onClick={() => setCreateOpen(true)}
          >
            <span className="workspaces-page__create-plus">+</span>
            <span className="workspaces-page__create-label">
              Create new workspace
            </span>
          </button>
        </div>

        <CreateWorkspaceDialog
          open={createOpen}
          onClose={() => setCreateOpen(false)}
        />
      </div>
    </AuthedShell>
  );
}
