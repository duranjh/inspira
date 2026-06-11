import { useEffect, useId, useRef, useState } from "react";

import { useDismissOn } from "../../hooks/useDismissOn";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import type { KanbanColumn } from "../inspira/api";

export type ManualOverrideDialogProps = {
  /** Title of the anchor card the user actually grabbed — surfaced
   *  as the dialog heading on single-card drags. On bulk drags
   *  (batchSize > 1) the heading flips to "Move N issues...". */
  projectTitle: string;
  fromColumn: KanbanColumn;
  toColumn: KanbanColumn;
  /** Total number of cards in the drag — defaults to 1 when omitted
   *  for backwards-compat. > 1 switches the dialog into bulk mode. */
  batchSize?: number;
  /** When true, render the "Have Inspira rerun on this issue" toggle.
   *  The dialog only fires for cross-column moves *into* In Progress
   *  (other destinations are now silent), so this defaults to true
   *  whenever it's set. The default toggle position is up to the
   *  caller via ``rerunDefault``. */
  showRerunToggle?: boolean;
  /** Initial state of the rerun toggle. Caller picks based on whether
   *  the project already has a canvas: ON for fresh-from-Queue
   *  (no canvas yet → AI clearly wanted), OFF when re-opening a
   *  project that already has topics drafted (partner is moving for
   *  organization, doesn't want overwrite). */
  rerunDefault?: boolean;
  /** User confirmed.  ``rerun`` is the toggle position at confirm
   *  time (false when the toggle isn't shown). When ``rerun`` is
   *  true, ``note`` is required (button stays disabled until the
   *  partner types a non-empty reason). */
  onConfirm: (note: string, rerun: boolean) => void;
  /** User cancelled; restore the optimistic board snapshot. */
  onCancel: () => void;
};

const COLUMN_LABEL: Record<KanbanColumn, string> = {
  queue: "Queue",
  in_progress: "In Progress",
  in_review: "In Review",
  approved: "Approved",
  shipped: "Shipped",
};

/**
 * Cross-column drag confirmation, fired only for drops INTO the
 * In Progress column (product decision: other column
 * destinations are silent state changes — no friction). The dialog
 * carries an optional "Have Inspira rerun on this issue" toggle that
 * controls whether confirming the move also kicks the orchestrator.
 *
 * Note semantics:
 *   - rerun toggle OFF: note is OPTIONAL (audit trail captures
 *     actor_user_id from the auth context regardless).
 *   - rerun toggle ON: note is REQUIRED — partners need to tell
 *     Inspira what was wrong / what to improve, otherwise the AI
 *     re-run is rudderless. Confirm button stays disabled until a
 *     non-empty note is typed.
 *
 * Pressing Escape cancels; ⌘/Ctrl+Enter confirms (when allowed).
 */
export function ManualOverrideDialog(props: ManualOverrideDialogProps) {
  const {
    projectTitle,
    fromColumn,
    toColumn,
    batchSize = 1,
    showRerunToggle = false,
    rerunDefault = false,
    onConfirm,
    onCancel,
  } = props;
  const isBulk = batchSize > 1;
  const [note, setNote] = useState("");
  const [rerun, setRerun] = useState(rerunDefault);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);
  const headingId = useId();

  const trimmed = note.trim();
  const noteRequired = showRerunToggle && rerun;
  const canConfirm = noteRequired ? trimmed.length > 0 : true;

  useDismissOn({ enabled: true, onDismiss: onCancel, esc: true });
  const { onKeyDown } = useFocusTrap(modalRef, {
    enabled: true,
    initialFocusRef: textareaRef,
  });

  // Cmd/Ctrl+Enter confirms when allowed. Standalone so the bespoke
  // confirm shortcut stays decoupled from the shared dismiss hook.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (canConfirm) onConfirm(trimmed, rerun);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [trimmed, rerun, canConfirm, onConfirm]);

  return (
    <div
      className="kb-override-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby={headingId}
      onClick={(e) => {
        // Backdrop click cancels — but only if the click started on
        // the backdrop itself, not bubbled from the modal panel.
        if (e.target === e.currentTarget) {
          onCancel();
        }
      }}
    >
      <div className="kb-override-modal" ref={modalRef} onKeyDown={onKeyDown}>
        <h3 id={headingId} className="kb-override-modal__title">
          {isBulk
            ? `Move ${batchSize} issues to ${COLUMN_LABEL[toColumn]}?`
            : `Move ‘${projectTitle}’ to ${COLUMN_LABEL[toColumn]}?`}
        </h3>
        <p className="kb-override-modal__sub">
          {isBulk
            ? `You're overriding Inspira's decision on ${batchSize} issues. Inspira logs who moved them.`
            : `You're overriding Inspira's decision to keep this in ${COLUMN_LABEL[fromColumn]}. Inspira logs who moved it.`}
        </p>
        {showRerunToggle ? (
          <label className="kb-override-modal__toggle">
            <input
              type="checkbox"
              checked={rerun}
              onChange={(e) => setRerun(e.target.checked)}
            />
            <span className="kb-override-modal__toggle-text">
              <strong>Have Inspira rerun on this issue.</strong>
              <em>
                {" "}
                When on, the orchestrator re-drafts topics + decisions
                from your note. Leave off to just move the card without
                touching the existing canvas.
              </em>
            </span>
          </label>
        ) : null}
        <textarea
          ref={textareaRef}
          className="kb-override-modal__textarea"
          placeholder={
            noteRequired
              ? "Required when Inspira is rerunning — what was wrong? What should it improve?"
              : "Optional — e.g., 'Customer escalation, deprioritising for now'"
          }
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={4}
        />
        <div className="kb-override-modal__footer">
          <button
            type="button"
            className="kb-override-modal__cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
          <div className="kb-override-modal__spacer" />
          <button
            type="button"
            className="kb-override-modal__primary"
            onClick={() => {
              if (canConfirm) onConfirm(trimmed, rerun);
            }}
            disabled={!canConfirm}
          >
            {showRerunToggle && rerun
              ? "Move and rerun"
              : "Override and move"}
          </button>
        </div>
      </div>
    </div>
  );
}
