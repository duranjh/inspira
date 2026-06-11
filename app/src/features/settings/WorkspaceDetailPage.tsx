// /settings/workspaces/:workspaceId — per-workspace settings detail.
//
// Single-column sectioned layout (product decision): the
// design HTML has five tabs (General / Connectors / AI / Members /
// Billing), but the prompt scope is narrower (General + Members +
// Billing-link + Danger). Tabs become useful once Connectors and AI
// prefs have real interactive content — those land in a later pass.
//
// Sections, top → bottom:
//   • Header — name + meta + back-to-list link
//   • General — editable name + slug
//   • Members — list + invite form (stub email flow lands W5)
//   • Billing — plan-tier badge + link to /billing
//   • Danger zone — delete (owner-only). Type-the-name confirmation;
//     a new ~20 LOC inline dialog (intentionally diverged from the
//     list-page dialog's "type 'delete'" affordance).
//
// Authorization on the BE:
//   • PATCH name/slug          → Role.admin
//   • POST invite              → Role.admin
//   • DELETE                   → Role.owner
//   • GET (this page's mount)  → Role.viewer
//
// We don't pre-gate any of these on the FE; the BE 403s and the UI
// surfaces the failure via toast. The Danger-zone button is the
// only exception — disabled when the user isn't an owner so we don't
// dangle a button that can't fire.

import {
  ReactElement,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { toast } from "../../components/ToastProvider";
import { HttpError } from "../../lib/httpClient";
import { AuthedShell } from "../shared/AuthedShell";
import {
  deriveSlugFromName,
  validateName,
  validateSlug,
} from "../workspaces/CreateWorkspaceDialog";
import {
  Workspace,
  WorkspaceMember,
  deleteWorkspace,
  getWorkspace,
  inviteMember,
  updateWorkspace,
} from "../workspaces/api";
import { useWorkspaceContext } from "../workspaces/WorkspaceContext";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

const INVITE_ROLES = ["member", "admin", "viewer"] as const;

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; workspace: Workspace; members: WorkspaceMember[]; yourRole: string }
  | { kind: "denied"; status: number }
  | { kind: "error"; message: string };

export function WorkspaceDetailPage(): ReactElement {
  return (
    <AuthedShell>
      <WorkspaceDetailBody />
    </AuthedShell>
  );
}

function WorkspaceDetailBody(): ReactElement {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();
  const ctx = useWorkspaceContext();
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setState({ kind: "loading" });
    try {
      const resp = await getWorkspace(workspaceId);
      setState({
        kind: "ready",
        workspace: resp.workspace,
        members: resp.members,
        yourRole: resp.your_role,
      });
    } catch (exc) {
      if (exc instanceof HttpError) {
        if (exc.status === 403 || exc.status === 404) {
          setState({ kind: "denied", status: exc.status });
          return;
        }
      }
      setState({
        kind: "error",
        message: exc instanceof Error ? exc.message : String(exc),
      });
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!workspaceId) {
    return (
      <div className="ws-detail__shell">
        <p className="ws-detail__notice">No workspace id in URL.</p>
      </div>
    );
  }

  if (state.kind === "loading") {
    return (
      <div className="ws-detail__shell" aria-busy="true">
        <p className="ws-detail__notice">Loading workspace…</p>
      </div>
    );
  }

  if (state.kind === "denied") {
    return (
      <div className="ws-detail__shell">
        <header className="ws-detail__header">
          <Link to="/settings/workspaces" className="ws-detail__back">
            ← All workspaces
          </Link>
          <h1 className="display ws-detail__title">
            <em>Not found</em>
          </h1>
          <p className="meta ws-detail__lede">
            {state.status === 403
              ? "You are not a member of this workspace."
              : "We couldn't find this workspace."}
          </p>
        </header>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="ws-detail__shell">
        <header className="ws-detail__header">
          <Link to="/settings/workspaces" className="ws-detail__back">
            ← All workspaces
          </Link>
          <h1 className="display ws-detail__title">
            <em>Couldn&rsquo;t load this workspace</em>
          </h1>
          <p className="meta ws-detail__lede">{state.message}</p>
          <button
            type="button"
            className="ws-settings__btn ws-settings__btn--sage"
            onClick={() => void load()}
          >
            Try again
          </button>
        </header>
      </div>
    );
  }

  const { workspace, members, yourRole } = state;
  const canEdit = yourRole === "owner" || yourRole === "admin";
  const isOwner = yourRole === "owner";

  return (
    <div className="ws-detail__shell">
      <header className="ws-detail__header">
        <Link to="/settings/workspaces" className="ws-detail__back">
          ← All workspaces
        </Link>
        <p className="eyebrow">Workspace settings</p>
        <h1 className="display ws-detail__title">
          {workspace.name}
        </h1>
        <p className="meta ws-detail__lede">
          {ROLE_LABEL[yourRole] ?? yourRole} · /{workspace.slug} ·{" "}
          {workspace.plan_tier.charAt(0).toUpperCase() +
            workspace.plan_tier.slice(1)}
        </p>
      </header>

      <GeneralSection
        workspace={workspace}
        canEdit={canEdit}
        onUpdated={(updated) => {
          setState({
            kind: "ready",
            workspace: updated,
            members,
            yourRole,
          });
          void ctx.refresh();
        }}
      />

      <MembersSection
        workspaceId={workspace.workspace_id}
        members={members}
        canInvite={canEdit}
        onMembersChanged={() => void load()}
      />

      <BillingSection plan={workspace.plan_tier} />

      {/* TODO(#190): transfer-ownership flow lands once we add the
          POST /workspaces/{id}/transfer-ownership endpoint. */}
      <DangerSection
        workspace={workspace}
        isOwner={isOwner}
        isLastWorkspace={ctx.workspaces.length <= 1}
        onDeleted={() => {
          void ctx.refresh().then(() => {
            navigate("/settings/workspaces", { replace: true });
          });
        }}
      />
    </div>
  );
}

// -----------------------------------------------------------------
// General — name + slug
// -----------------------------------------------------------------

interface GeneralSectionProps {
  workspace: Workspace;
  canEdit: boolean;
  onUpdated: (workspace: Workspace) => void;
}

function GeneralSection({
  workspace,
  canEdit,
  onUpdated,
}: GeneralSectionProps): ReactElement {
  const nameId = useId();
  const slugId = useId();

  const [name, setName] = useState(workspace.name);
  const [slug, setSlug] = useState(workspace.slug);
  const [savingField, setSavingField] = useState<"name" | "slug" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Reset when the workspace prop changes (post-save echo).
  useEffect(() => {
    setName(workspace.name);
    setSlug(workspace.slug);
  }, [workspace.name, workspace.slug]);

  const nameDirty = name.trim() !== workspace.name;
  const slugDirty = slug.trim() !== workspace.slug;

  const saveField = useCallback(
    async (field: "name" | "slug") => {
      setError(null);
      const validation =
        field === "name" ? validateName(name) : validateSlug(slug);
      if (validation) {
        setError(validation);
        return;
      }
      setSavingField(field);
      try {
        const resp = await updateWorkspace(workspace.workspace_id, {
          [field]: field === "name" ? name.trim() : slug.trim(),
        });
        toast.success(
          field === "name" ? "Workspace name updated." : "Slug updated.",
        );
        onUpdated(resp.workspace);
      } catch (exc) {
        if (exc instanceof HttpError && exc.status === 409) {
          setError("That slug is already taken. Try a different one.");
        } else if (exc instanceof HttpError && exc.status === 403) {
          setError("You don't have permission to update this workspace.");
        } else if (exc instanceof HttpError && exc.status === 422) {
          setError("Validation failed. Check the field and try again.");
        } else {
          setError(
            exc instanceof Error
              ? exc.message
              : "Couldn't save changes. Try again.",
          );
        }
      } finally {
        setSavingField(null);
      }
    },
    [name, slug, workspace.workspace_id, onUpdated],
  );

  return (
    <section className="ws-detail__section">
      <h2 className="section-title">General</h2>
      <div className="ws-detail__field">
        <label htmlFor={nameId} className="ws-detail__label">
          Workspace name
        </label>
        <div className="ws-detail__row">
          <input
            id={nameId}
            type="text"
            className="ws-detail__input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            disabled={!canEdit || savingField !== null}
          />
          <button
            type="button"
            className="ws-settings__btn ws-settings__btn--sage"
            disabled={!canEdit || !nameDirty || savingField !== null}
            onClick={() => void saveField("name")}
          >
            {savingField === "name" ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div className="ws-detail__field">
        <label htmlFor={slugId} className="ws-detail__label">
          URL slug
        </label>
        <div className="ws-detail__row">
          <span className="ws-detail__slug-prefix">/workspaces/</span>
          <input
            id={slugId}
            type="text"
            className="ws-detail__input ws-detail__input--slug"
            value={slug}
            onChange={(e) =>
              setSlug(e.target.value.toLowerCase().replace(/\s+/g, "-"))
            }
            maxLength={40}
            spellCheck={false}
            autoCapitalize="none"
            disabled={!canEdit || savingField !== null}
          />
          <button
            type="button"
            className="ws-settings__btn ws-settings__btn--sage"
            disabled={!canEdit || !slugDirty || savingField !== null}
            onClick={() => void saveField("slug")}
          >
            {savingField === "slug" ? "Saving…" : "Save"}
          </button>
        </div>
        <p className="ws-detail__hint">
          Lowercase letters, digits, hyphens — 3 to 40 characters.
          Used in workspace URLs.
        </p>
      </div>

      {!canEdit ? (
        <p className="ws-detail__hint">
          Only owners and admins can edit workspace details.
        </p>
      ) : null}
      {error ? (
        <div className="ws-detail__error" role="alert">
          {error}
        </div>
      ) : null}
      {/* Suppress unused-import warning when deriveSlugFromName isn't
          used yet — the helper is exported for future slug-suggest UX. */}
      <span hidden>{deriveSlugFromName("")}</span>
    </section>
  );
}

// -----------------------------------------------------------------
// Members — list + invite
// -----------------------------------------------------------------

interface MembersSectionProps {
  workspaceId: string;
  members: WorkspaceMember[];
  canInvite: boolean;
  onMembersChanged: () => void;
}

function MembersSection({
  workspaceId,
  members,
  canInvite,
  onMembersChanged,
}: MembersSectionProps): ReactElement {
  const emailId = useId();
  const roleId = useId();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<(typeof INVITE_ROLES)[number]>("member");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleInvite = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      const trimmed = email.trim();
      if (!trimmed) {
        setError("Enter an email address.");
        return;
      }
      setSubmitting(true);
      try {
        const resp = await inviteMember(workspaceId, {
          email: trimmed,
          role,
        });
        if (resp.invitation.status === "added") {
          toast.success(`Added ${trimmed} as ${ROLE_LABEL[role]}.`);
        } else if (resp.invitation.status === "queued") {
          toast.success(`Invite queued for ${trimmed}.`);
        } else if (resp.invitation.status === "already_member") {
          toast.info(`${trimmed} is already a member.`);
        }
        setEmail("");
        onMembersChanged();
      } catch (exc) {
        if (exc instanceof HttpError && exc.status === 422) {
          setError("That email address isn't valid.");
        } else if (exc instanceof HttpError && exc.status === 403) {
          setError("You don't have permission to invite members.");
        } else {
          setError(
            exc instanceof Error
              ? exc.message
              : "Couldn't send invite. Try again.",
          );
        }
      } finally {
        setSubmitting(false);
      }
    },
    [email, role, workspaceId, onMembersChanged],
  );

  return (
    <section className="ws-detail__section">
      <header className="ws-detail__section-head">
        <h2 className="section-title">Members</h2>
        <span className="ws-detail__count">{members.length}</span>
      </header>

      <ul className="ws-detail__members">
        {members.map((m) => (
          <li key={m.user_id} className="ws-detail__member">
            <span className="ws-detail__member-id">{m.user_id}</span>
            <span className={`chip chip--${m.role}`}>
              {ROLE_LABEL[m.role] ?? m.role}
            </span>
          </li>
        ))}
      </ul>

      {canInvite ? (
        <form className="ws-detail__invite" onSubmit={handleInvite}>
          <div className="ws-detail__invite-row">
            <label htmlFor={emailId} className="ws-detail__label">
              Invite by email
            </label>
            <div className="ws-detail__row">
              <input
                id={emailId}
                type="email"
                className="ws-detail__input"
                placeholder="name@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
              />
              <label
                htmlFor={roleId}
                className="visually-hidden"
              >
                Role
              </label>
              <select
                id={roleId}
                className="ws-detail__select"
                value={role}
                onChange={(e) =>
                  setRole(e.target.value as (typeof INVITE_ROLES)[number])
                }
                disabled={submitting}
              >
                {INVITE_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
              <button
                type="submit"
                className="ws-settings__btn ws-settings__btn--sage"
                disabled={submitting || !email.trim()}
              >
                {submitting ? "Sending…" : "Send invite"}
              </button>
            </div>
          </div>
          {error ? (
            <div className="ws-detail__error" role="alert">
              {error}
            </div>
          ) : null}
          <p className="ws-detail__hint">
            Unknown emails are queued — they receive an invite once
            email delivery ships. Existing Inspira users are added
            immediately.
          </p>
        </form>
      ) : (
        <p className="ws-detail__hint">
          Only owners and admins can invite members.
        </p>
      )}
    </section>
  );
}

// -----------------------------------------------------------------
// Billing
// -----------------------------------------------------------------

interface BillingSectionProps {
  plan: string;
}

function BillingSection({ plan }: BillingSectionProps): ReactElement {
  return (
    <section className="ws-detail__section">
      <h2 className="section-title">Billing</h2>
      <div className="ws-detail__billing-row">
        <span className="ws-detail__plan">
          {plan.charAt(0).toUpperCase() + plan.slice(1)} plan
        </span>
        <Link
          to="/billing"
          className="ws-settings__btn ws-settings__btn--ghost"
        >
          Open billing →
        </Link>
      </div>
      <p className="ws-detail__hint">
        Subscription and invoice management lives in the billing
        overview.
      </p>
    </section>
  );
}

// -----------------------------------------------------------------
// Danger zone — delete with type-the-name confirmation
// -----------------------------------------------------------------

interface DangerSectionProps {
  workspace: Workspace;
  isOwner: boolean;
  isLastWorkspace: boolean;
  onDeleted: () => void;
}

function DangerSection({
  workspace,
  isOwner,
  isLastWorkspace,
  onDeleted,
}: DangerSectionProps): ReactElement {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const disabledReason = useMemo(() => {
    if (!isOwner) return "Only the owner can delete this workspace.";
    if (isLastWorkspace)
      return "Create another workspace before deleting your last one.";
    return null;
  }, [isOwner, isLastWorkspace]);

  return (
    <section className="ws-detail__section ws-detail__danger">
      <h2 className="section-title">Danger zone</h2>
      <p className="ws-detail__hint">
        Deleting a workspace hides it from your list. Support can
        recover the underlying projects and feedback if needed, but
        the workspace itself stops appearing in the switcher.
      </p>
      <button
        type="button"
        className="ws-settings__btn ws-settings__btn--danger"
        onClick={() => setConfirmOpen(true)}
        disabled={!!disabledReason}
        title={disabledReason ?? undefined}
      >
        Delete workspace
      </button>
      {confirmOpen ? (
        <DeleteByNameDialog
          workspace={workspace}
          onClose={() => setConfirmOpen(false)}
          onDeleted={onDeleted}
        />
      ) : null}
    </section>
  );
}

interface DeleteByNameDialogProps {
  workspace: Workspace;
  onClose: () => void;
  onDeleted: () => void;
}

/**
 * Type-the-workspace-name confirmation. Intentionally diverges from
 * the list-page DeleteWorkspaceDialog (which uses "type 'delete'")
 * — per-workspace context calls for per-workspace confirmation.
 */
function DeleteByNameDialog({
  workspace,
  onClose,
  onDeleted,
}: DeleteByNameDialogProps): ReactElement {
  const [confirmText, setConfirmText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const canDelete =
    confirmText.trim() === workspace.name.trim() && !submitting;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleDelete = useCallback(async () => {
    if (!canDelete) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteWorkspace(workspace.workspace_id);
      toast.success(`Deleted '${workspace.name}'.`);
      onDeleted();
    } catch (exc) {
      const detail = (
        exc as { detail?: { error?: string; message?: string } }
      ).detail;
      if (detail?.error === "last_active_workspace") {
        setError(
          detail.message ??
            "This is your only workspace. Create another one first.",
        );
      } else if (detail?.message) {
        setError(detail.message);
      } else if (exc instanceof Error) {
        setError(exc.message);
      } else {
        setError("Couldn't delete the workspace. Try again.");
      }
      setSubmitting(false);
    }
  }, [canDelete, workspace.workspace_id, workspace.name, onDeleted]);

  return (
    <div
      className="ws-delete-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ws-detail-delete-title"
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
        <h2 id="ws-detail-delete-title" className="ws-delete-modal__title">
          Delete &lsquo;{workspace.name}&rsquo;?
        </h2>
        <p className="ws-delete-modal__sub">
          This hides the workspace from your list and the switcher.
          To confirm, type the workspace name exactly as shown.
        </p>
        <p className="ws-delete-modal__sub">
          Type{" "}
          <code className="ws-delete-modal__code">{workspace.name}</code>{" "}
          below to confirm.
        </p>
        <input
          ref={inputRef}
          type="text"
          className="ws-delete-modal__input"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder={workspace.name}
          disabled={submitting}
          aria-label={`Type "${workspace.name}" to confirm`}
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
