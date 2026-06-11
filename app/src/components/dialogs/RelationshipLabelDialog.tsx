// Inspira — relationship-label dialog (L5b, #036).
//
// Edits the label on a single relationship edge. Replaces the
// native window.prompt that was wired in `ProjectCanvas.tsx:1019`,
// AND lets the user delete the relationship from the same surface
// (so first-time users don't have to discover the Delete-key path).
//
// Why not extend RenameProjectDialog?
// - RenameProjectDialog disables Save when the input is empty;
//   relationship labels can be cleared (an unlabeled edge is a
//   valid state).
// - We need a third action — Delete — alongside Save and Cancel.
//   The base Dialog shell only takes primary + secondary action
//   slots, so we render the action row inside the dialog body and
//   leave Dialog's `primaryAction`/`secondaryAction` undefined.
//
// Why is Delete confirmed in-dialog (two-click) rather than via a
// nested DeleteConfirmDialog? One dialog open at a time keeps the
// focus-trap behaviour simple, and the two-click pattern matches
// the topic-detail decision-delete UX already in the app. The
// danger button label flips to "Confirm delete?" on first click
// and reverts after 4 seconds if the user steps away.
//
// Empty-string semantics: the dialog passes `null` to its
// `onSubmit` when the input is blank. The backend's PATCH
// handler normalizes both empty strings and null to NULL in the
// DB, but we send null explicitly to make the intent obvious.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Dialog } from "./Dialog";

import { t } from "../../i18n";

export type RelationshipLabelDialogProps = {
  open: boolean;
  /** Current label on the edge ("" if none). */
  currentLabel: string;
  /** Source topic title — for the contextual "Title → Title" subline. */
  fromTopicTitle: string;
  /** Target topic title — for the contextual "Title → Title" subline. */
  toTopicTitle: string;
  /** Called on Save. Receives the new label, or `null` when cleared.
   *  Throws if the persist fails so the dialog can paint inline. */
  onSubmit: (newLabel: string | null) => Promise<void>;
  /** Called when the user confirms Delete in-dialog. */
  onDelete: () => Promise<void> | void;
  /** Called on Cancel / X / Esc / backdrop. */
  onClose: () => void;
};

// Two-click delete confirmation window. After the first click, the
// danger button label flips to "Confirm delete?" for this many ms;
// if the user clicks again within that window, the delete fires.
// After the window expires, the button reverts to the idle label.
const DELETE_CONFIRM_TIMEOUT_MS = 4000;

export function RelationshipLabelDialog({
  open,
  currentLabel,
  fromTopicTitle,
  toTopicTitle,
  onSubmit,
  onDelete,
  onClose,
}: RelationshipLabelDialogProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [draft, setDraft] = useState<string>(currentLabel);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmResetTimerRef = useRef<number | null>(null);

  // Reset local state every time the dialog (re-)opens so the
  // input reflects the latest label and we don't carry over a
  // stale Confirm-delete state.
  useEffect(() => {
    if (open) {
      setDraft(currentLabel);
      setBusy(false);
      setError(null);
      setConfirmingDelete(false);
      if (confirmResetTimerRef.current !== null) {
        window.clearTimeout(confirmResetTimerRef.current);
        confirmResetTimerRef.current = null;
      }
    }
  }, [open, currentLabel]);

  // Cleanup the confirm-revert timer on unmount.
  useEffect(() => {
    return () => {
      if (confirmResetTimerRef.current !== null) {
        window.clearTimeout(confirmResetTimerRef.current);
      }
    };
  }, []);

  // Select-all on focus so the user can replace immediately.
  const handleFocus = useCallback(() => {
    inputRef.current?.select();
  }, []);

  const trimmed = draft.trim();
  // Save is disabled when the value didn't change. Empty-vs-empty
  // and same-string both count as no-op. We allow submitting
  // empty though — that's how the user clears a label.
  const unchanged = trimmed === currentLabel.trim();
  const saveDisabled = busy || unchanged;

  const submit = useCallback(async () => {
    if (saveDisabled) return;
    setBusy(true);
    setError(null);
    try {
      // Send `null` when cleared so the backend's empty-vs-null
      // intent is obvious in network logs.
      await onSubmit(trimmed.length === 0 ? null : trimmed);
    } catch (err) {
      console.error("[Inspira] update relationship label failed", err);
      setError(t("relationship_dialog.update_failed"));
      setBusy(false);
    }
  }, [saveDisabled, onSubmit, trimmed]);

  const handleFormSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      void submit();
    },
    [submit],
  );

  const handleInputKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  const handleDeleteClick = useCallback(() => {
    if (busy) return;
    if (!confirmingDelete) {
      // First click — arm the confirmation window.
      setConfirmingDelete(true);
      if (confirmResetTimerRef.current !== null) {
        window.clearTimeout(confirmResetTimerRef.current);
      }
      confirmResetTimerRef.current = window.setTimeout(() => {
        setConfirmingDelete(false);
        confirmResetTimerRef.current = null;
      }, DELETE_CONFIRM_TIMEOUT_MS);
      return;
    }
    // Second click within the window — fire the delete. The parent
    // is expected to close the dialog; we don't unilaterally close
    // in case the parent wants to keep it open on failure.
    if (confirmResetTimerRef.current !== null) {
      window.clearTimeout(confirmResetTimerRef.current);
      confirmResetTimerRef.current = null;
    }
    setBusy(true);
    void Promise.resolve(onDelete()).catch((err) => {
      console.error("[Inspira] delete relationship from dialog failed", err);
      setError(t("relationship_dialog.delete_failed"));
      setBusy(false);
      setConfirmingDelete(false);
    });
  }, [busy, confirmingDelete, onDelete]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("relationship_dialog.title")}
      width={480}
      // No primaryAction or secondaryAction — we render our own
      // action row in the body so the danger Delete button can
      // sit alongside Save + Cancel.
    >
      <form onSubmit={handleFormSubmit} noValidate>
        <p className="dlg__lede" style={ledeStyle}>
          {t("relationship_dialog.subtitle", {
            from: fromTopicTitle || t("relationship_dialog.unknown_topic"),
            to: toTopicTitle || t("relationship_dialog.unknown_topic"),
          })}
        </p>
        <div className="dlg__field">
          <label className="dlg__label" htmlFor="dlg-relationship-label-input">
            {t("relationship_dialog.label")}
          </label>
          <input
            id="dlg-relationship-label-input"
            ref={inputRef}
            className="dlg__input"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={handleFocus}
            onKeyDown={handleInputKeyDown}
            placeholder={t("relationship_dialog.placeholder")}
            autoComplete="off"
            spellCheck={true}
            disabled={busy}
            maxLength={120}
          />
        </div>
        <p className="dlg__rename-hint">{t("relationship_dialog.hint")}</p>
        {error && <div className="dlg__share-error">{error}</div>}

        <div className="dlg__actions" style={actionsRowStyle}>
          <button
            type="button"
            className="dlg__btn dlg__btn--danger"
            onClick={handleDeleteClick}
            disabled={busy}
          >
            {confirmingDelete
              ? t("relationship_dialog.action_delete_confirm")
              : t("relationship_dialog.action_delete")}
          </button>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="dlg__btn dlg__btn--secondary"
            onClick={onClose}
            disabled={busy}
          >
            {t("relationship_dialog.action_cancel")}
          </button>
          <button
            type="submit"
            className={
              "dlg__btn dlg__btn--primary" + (busy ? " dlg__btn--busy" : "")
            }
            disabled={saveDisabled}
            aria-busy={busy || undefined}
          >
            {busy ? (
              <span className="dlg__btn-spinner" aria-hidden="true" />
            ) : null}
            {t("relationship_dialog.action_save")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// Inline styles to keep this component self-contained — the action
// row needs a slightly different shape than Dialog's default
// (Delete pinned far-left so it's visually separated from
// Save/Cancel). Existing dialogs.css covers everything else.
const ledeStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif)",
  fontStyle: "italic",
  fontSize: 13,
  color: "var(--ink-3)",
  margin: "0 0 14px 0",
};

const actionsRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 16,
  paddingTop: 16,
  borderTop: "1px solid var(--paper-edge)",
};
