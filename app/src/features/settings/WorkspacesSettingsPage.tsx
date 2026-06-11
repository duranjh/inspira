// /settings/workspaces — workspace management surface.
//
// Lists every workspace the user is a member of with role, plan tier,
// active state, slug, and a Switch action. Surfaces "+ Create new"
// inline. Each row also gets a Delete affordance gated behind a
// type-"delete"-to-confirm modal (a deliberately careful, tedious
// confirmation); BE soft-deletes by setting
// archived_at. The user's last active workspace can't be deleted —
// both the FE button and the BE both enforce.

import { ReactElement, useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { toast } from "../../components/ToastProvider";
import { AuthedShell } from "../shared/AuthedShell";
import { CreateWorkspaceDialog } from "../workspaces/CreateWorkspaceDialog";
import { deleteWorkspace } from "../workspaces/api";
import {
  WorkspaceRole,
  useWorkspaceContext,
} from "../workspaces/WorkspaceContext";

const ROLE_LABEL: Record<WorkspaceRole, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

type DeleteTarget = {
  workspaceId: string;
  workspaceName: string;
};

export function WorkspacesSettingsPage(): ReactElement {
  return (
    <AuthedShell>
      <WorkspacesSettingsBody />
    </AuthedShell>
  );
}

function WorkspacesSettingsBody(): ReactElement {
  const ctx = useWorkspaceContext();
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const isLastWorkspace = ctx.workspaces.length <= 1;

  return (
    <div className="ws-settings">
      <header className="ws-settings__header">
        <p className="eyebrow">Settings</p>
        <h1 className="display ws-settings__title">
          Manage <em>workspaces</em>.
        </h1>
        <p className="meta ws-settings__lede">
          Each workspace has its own feedback inbox, repo connections,
          and project board. Switch between them anytime from the
          left rail.
        </p>
      </header>

      <section className="ws-settings__section">
        <header className="ws-settings__section-head">
          <h2 className="section-title">Your workspaces</h2>
          <button
            type="button"
            className="ws-settings__btn ws-settings__btn--sage"
            onClick={() => setCreateOpen(true)}
          >
            + Create workspace
          </button>
        </header>

        {ctx.loading ? (
          <div className="ws-settings__loading">Loading…</div>
        ) : ctx.workspaces.length === 0 ? (
          <div className="ws-settings__empty">
            You don&rsquo;t have any workspaces yet. Create one to start
            wiring connectors.
          </div>
        ) : (
          <ul className="ws-settings__list">
            {ctx.workspaces.map((w) => {
              const isActive =
                w.workspace_id === ctx.activeWorkspace?.workspace_id;
              const isOwner = w.role === "owner";
              return (
                <li
                  key={w.workspace_id}
                  className={
                    "ws-settings__row" +
                    (isActive ? " ws-settings__row--active" : "")
                  }
                >
                  <div className="ws-settings__row-main">
                    <div className="ws-settings__row-name">
                      <span className="ws-settings__row-name-text">
                        {w.name}
                      </span>
                      {isActive ? (
                        <span className="chip chip--sage ws-settings__active-chip">
                          Active
                        </span>
                      ) : null}
                    </div>
                    <div className="ws-settings__row-meta">
                      <span
                        className="ws-settings__role"
                        title={`Your role in ${w.name}`}
                      >
                        {ROLE_LABEL[w.role]}
                      </span>
                      <span className="ws-settings__dot" aria-hidden="true">
                        ·
                      </span>
                      <span className="ws-settings__plan">
                        {w.plan_tier.charAt(0).toUpperCase() +
                          w.plan_tier.slice(1)}
                      </span>
                      <span className="ws-settings__dot" aria-hidden="true">
                        ·
                      </span>
                      <span className="ws-settings__slug">/{w.slug}</span>
                    </div>
                  </div>
                  <div className="ws-settings__row-actions">
                    <Link
                      to={`/settings/workspaces/${w.workspace_id}`}
                      className="ws-settings__btn ws-settings__btn--ghost"
                      title={`Open ${w.name} settings`}
                    >
                      Settings →
                    </Link>
                    {!isActive ? (
                      <button
                        type="button"
                        className="ws-settings__btn ws-settings__btn--ghost"
                        onClick={() =>
                          ctx.setActiveWorkspace(w.workspace_id)
                        }
                      >
                        Switch to this
                      </button>
                    ) : null}
                    {isOwner ? (
                      <button
                        type="button"
                        className="ws-settings__btn ws-settings__btn--danger"
                        disabled={isLastWorkspace}
                        title={
                          isLastWorkspace
                            ? "Create another workspace before deleting this one"
                            : `Delete ${w.name}`
                        }
                        onClick={() =>
                          setDeleteTarget({
                            workspaceId: w.workspace_id,
                            workspaceName: w.name,
                          })
                        }
                      >
                        Delete
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <CreateWorkspaceDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />

      {deleteTarget ? (
        <DeleteWorkspaceDialog
          target={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={() => {
            setDeleteTarget(null);
            void ctx.refresh();
          }}
        />
      ) : null}
    </div>
  );
}

interface DeleteWorkspaceDialogProps {
  target: DeleteTarget;
  onClose: () => void;
  onDeleted: () => void;
}

/**
 * Type-"delete"-to-confirm modal. Product decision: a deliberately
 * careful, tedious confirmation — type the word delete to confirm,
 * then press continue.
 *
 * The textarea must contain exactly "delete" (case-insensitive,
 * trimmed) before the destructive button enables. Backdrop click
 * + Escape both cancel; Cmd/Ctrl+Enter confirms when allowed.
 */
function DeleteWorkspaceDialog({
  target,
  onClose,
  onDeleted,
}: DeleteWorkspaceDialogProps): ReactElement {
  const [confirmText, setConfirmText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canDelete =
    confirmText.trim().toLowerCase() === "delete" && !submitting;

  const handleDelete = useCallback(async () => {
    if (!canDelete) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteWorkspace(target.workspaceId);
      toast.success(`Deleted '${target.workspaceName}'.`);
      onDeleted();
    } catch (err) {
      const detail = (err as { detail?: { error?: string; message?: string } })
        .detail;
      if (detail?.error === "last_active_workspace") {
        setError(
          detail.message ??
            "This is your only workspace. Create another one before deleting it.",
        );
      } else if (detail?.message) {
        setError(detail.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Couldn't delete the workspace. Try again.");
      }
      setSubmitting(false);
    }
  }, [canDelete, target.workspaceId, target.workspaceName, onDeleted]);

  return (
    <div
      className="ws-delete-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ws-delete-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !submitting) {
          e.preventDefault();
          onClose();
          return;
        }
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canDelete) {
          e.preventDefault();
          void handleDelete();
        }
      }}
    >
      <div className="ws-delete-modal">
        <h2 id="ws-delete-title" className="ws-delete-modal__title">
          Delete &lsquo;{target.workspaceName}&rsquo;?
        </h2>
        <p className="ws-delete-modal__sub">
          This hides the workspace from your list. Inspira keeps your
          feedback, projects, and connections recoverable through
          support &mdash; but the workspace stops appearing in the
          rail and you won&rsquo;t be able to switch to it.
        </p>
        <p className="ws-delete-modal__sub">
          Type{" "}
          <code className="ws-delete-modal__code">delete</code> below
          to confirm.
        </p>
        <input
          autoFocus
          type="text"
          className="ws-delete-modal__input"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder="delete"
          disabled={submitting}
          aria-label="Type 'delete' to confirm"
        />
        {error ? (
          <div className="ws-delete-modal__error" role="alert">
            {error}
          </div>
        ) : null}
        <div className="ws-delete-modal__footer">
          <button
            type="button"
            className="ws-settings__btn ws-settings__btn--ghost"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <span className="ws-delete-modal__spacer" />
          <button
            type="button"
            className="ws-settings__btn ws-settings__btn--danger"
            onClick={() => void handleDelete()}
            disabled={!canDelete}
          >
            {submitting ? "Deleting…" : "Delete forever"}
          </button>
        </div>
      </div>
    </div>
  );
}
