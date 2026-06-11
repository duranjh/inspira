// Lightweight create-workspace dialog (W2 C5).
//
// Per the C5 watch points:
//
// - This is the modal opened from WorkspaceSwitcher's
//   "+ Create new workspace" footer. It is INTENTIONALLY small —
//   name + slug + create. The full B1.4 4-step onboarding wizard
//   (name → connect repo → connect feedback → first prioritization
//   run) is W7/F14 territory.
//
// - Slug strategy: live auto-generated from the name (lowercased,
//   non-alphanumerics → hyphens, collapsed-and-trimmed). User can
//   override via the slug input. Backend is the source of truth
//   for uniqueness — we submit optimistically and surface 409 on
//   collision (no `/check-slug` endpoint exists yet, and
//   debounced pre-check is W7+ polish).
//
// - Auto-switch sequence on success: backend 200 → context.refresh()
//   → context.setActiveWorkspace(new_id) → onCreated(workspace).
//   Strict ordering — never set context-active before the backend
//   confirms persistence.
//
// - Validation surface: client-side enforces 3-40 chars, lowercase
//   alphanumerics + hyphens, no leading/trailing dash, NOT
//   personal-* (reserved for backfilled accounts). Backend
//   re-enforces; client is just for fast feedback.

import { ReactElement, useCallback, useEffect, useId, useState } from "react";

import { HttpError } from "../../lib/httpClient";
import {
  createWorkspace as apiCreateWorkspace,
  Workspace,
} from "./api";
import { useWorkspaceContext } from "./WorkspaceContext";

const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])?$/;
const SLUG_INVALID = /[^a-z0-9-]+/g;
const MULTI_DASH = /-+/g;

export function deriveSlugFromName(name: string): string {
  const lowered = name.toLowerCase().trim();
  const cleaned = lowered.replace(SLUG_INVALID, "-");
  const collapsed = cleaned.replace(MULTI_DASH, "-").replace(/^-+|-+$/g, "");
  return collapsed.slice(0, 40);
}

export function validateSlug(slug: string): string | null {
  if (!slug) return "Slug is required.";
  if (slug.length < 3) return "Slug must be at least 3 characters.";
  if (slug.length > 40) return "Slug must be 40 characters or fewer.";
  if (!SLUG_PATTERN.test(slug)) {
    return "Slug must use lowercase letters, digits, or hyphens; no leading/trailing dash.";
  }
  if (slug.startsWith("personal-")) {
    return "Slug 'personal-*' is reserved.";
  }
  return null;
}

export function validateName(name: string): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "Workspace name is required.";
  if (trimmed.length > 120) {
    return "Workspace name must be 120 characters or fewer.";
  }
  return null;
}

export interface CreateWorkspaceDialogProps {
  open: boolean;
  onClose: () => void;
  /**
   * Called after a successful create — receives the created
   * workspace + role. The caller can use this to navigate
   * post-create (the dialog itself only handles the data flow,
   * not routing). When omitted the dialog closes silently after
   * setting the new workspace as active.
   */
  onCreated?: (workspace: Workspace & { role: string }) => void;
  /**
   * First-run mode: hide the close button + "Cancel" link,
   * making the dialog dismissable only via successful create.
   * Used when the user has 0 workspaces and lands on a page
   * that requires one.
   */
  firstRun?: boolean;
}

export function CreateWorkspaceDialog({
  open,
  onClose,
  onCreated,
  firstRun,
}: CreateWorkspaceDialogProps): ReactElement | null {
  const ctx = useWorkspaceContext();
  const nameId = useId();
  const slugId = useId();

  const [name, setName] = useState("");
  // True until the user manually edits the slug — locks the
  // auto-derive behaviour off so renaming the workspace doesn't
  // clobber a slug they cared about.
  const [slugAuto, setSlugAuto] = useState(true);
  const [slug, setSlug] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  // Auto-derive slug from name unless the user has manually
  // edited it.
  useEffect(() => {
    if (slugAuto) {
      setSlug(deriveSlugFromName(name));
    }
  }, [name, slugAuto]);

  // Reset state when the dialog opens / closes.
  useEffect(() => {
    if (!open) return;
    setName("");
    setSlug("");
    setSlugAuto(true);
    setSubmitting(false);
    setErrorBanner(null);
  }, [open]);

  // ESC closes (not in firstRun mode where there's no exit).
  useEffect(() => {
    if (!open || firstRun) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, firstRun, onClose]);

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setErrorBanner(null);

      const nameError = validateName(name);
      if (nameError) {
        setErrorBanner(nameError);
        return;
      }
      const slugError = validateSlug(slug);
      if (slugError) {
        setErrorBanner(slugError);
        return;
      }

      setSubmitting(true);
      try {
        const result = await apiCreateWorkspace({
          slug: slug.trim(),
          name: name.trim(),
        });
        // Auto-switch sequence: refresh context first so the
        // new workspace lands in `workspaces`, THEN set it
        // active. Doing setActive before refresh would noop
        // (the spoofing guard rejects ids not in the list).
        await ctx.refresh();
        ctx.setActiveWorkspace(result.workspace.workspace_id);
        onCreated?.(result.workspace);
        onClose();
      } catch (exc) {
        if (exc instanceof HttpError && exc.status === 409) {
          setErrorBanner(
            "That slug is already taken. Try a different one.",
          );
        } else if (exc instanceof HttpError && exc.status === 422) {
          setErrorBanner(
            "Slug or name didn't pass validation. " +
              "Use 3–40 lowercase letters/digits/hyphens.",
          );
        } else {
          setErrorBanner(
            exc instanceof Error
              ? exc.message
              : "Couldn't create the workspace. Try again.",
          );
        }
      } finally {
        setSubmitting(false);
      }
    },
    [name, slug, ctx, onCreated, onClose],
  );

  if (!open) return null;

  return (
    <div
      className="cw-dialog__backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cw-dialog__title"
      onClick={(e) => {
        // Backdrop click closes — but not in firstRun mode.
        if (firstRun) return;
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="card cw-dialog">
        <header className="cw-dialog__header">
          <h2 id="cw-dialog__title" className="section-title">
            {firstRun
              ? "Welcome — create your workspace"
              : "Create a new workspace"}
          </h2>
          {!firstRun ? (
            <button
              type="button"
              className="btn btn--icon btn--ghost"
              onClick={onClose}
              aria-label="Close dialog"
            >
              ×
            </button>
          ) : null}
        </header>

        {firstRun ? (
          <p className="meta cw-dialog__intro">
            A workspace is where your repo, feedback, and decisions
            live. You can invite teammates later.
          </p>
        ) : null}

        <form className="cw-dialog__form" onSubmit={onSubmit} noValidate>
          <label className="cw-dialog__field">
            <span className="cw-dialog__label">Workspace name</span>
            <input
              id={nameId}
              type="text"
              className="cw-dialog__input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme Corp"
              autoFocus
              maxLength={120}
              disabled={submitting}
            />
          </label>

          <label className="cw-dialog__field">
            <span className="cw-dialog__label">Slug</span>
            <span className="cw-dialog__hint">
              Lowercase letters, digits, hyphens. 3–40 chars.
            </span>
            <input
              id={slugId}
              type="text"
              className="cw-dialog__input cw-dialog__input--slug"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugAuto(false);
              }}
              placeholder="acme-corp"
              maxLength={40}
              disabled={submitting}
              spellCheck={false}
              autoCapitalize="none"
            />
          </label>

          {errorBanner ? (
            <div className="cw-dialog__error" role="alert">
              {errorBanner}
            </div>
          ) : null}

          <div className="cw-dialog__actions">
            {!firstRun ? (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={onClose}
                disabled={submitting}
              >
                Cancel
              </button>
            ) : null}
            <button
              type="submit"
              className="btn btn--primary"
              disabled={submitting || !name.trim() || !slug}
            >
              {submitting ? "Creating…" : "Create workspace"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
