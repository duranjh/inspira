// B1.2 / W3 δ — Reject-with-reason dialog.
//
// Composes the base Dialog. Modeled on RenameProjectDialog's input-driven
// pattern, but with a textarea so the user can articulate why they're
// rejecting the AI's draft. Submit calls back with the trimmed reason
// (or empty string if they didn't type anything — caller decides whether
// empty rejection is allowed). Reason is then passed to
// `api.updateProjectState(projectId, "rejected", reason)`.

import { useCallback, useEffect, useRef, useState } from "react";

import { Dialog } from "../../../components/dialogs/Dialog";

export interface RejectWithReasonDialogProps {
  open: boolean;
  onSubmit: (reason: string) => Promise<void>;
  onClose: () => void;
}

export function RejectWithReasonDialog({
  open,
  onSubmit,
  onClose,
}: RejectWithReasonDialogProps) {
  const textRef = useRef<HTMLTextAreaElement | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft("");
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const trimmed = draft.trim();
  const disabled = trimmed.length === 0 || busy;

  const submit = useCallback(async () => {
    if (disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(trimmed);
    } catch (err) {
      console.error("[Inspira] reject with reason failed", err);
      setError("Couldn't reject — try again.");
      setBusy(false);
    }
  }, [disabled, onSubmit, trimmed]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Reject with reason"
      width={480}
      primaryAction={{
        label: "Reject",
        onClick: submit,
        disabled,
        busy,
        variant: "danger",
      }}
      secondaryAction={{
        label: "Cancel",
        onClick: onClose,
      }}
    >
      <div className="dlg__field">
        <label className="dlg__label" htmlFor="dlg-reject-input">
          Why are you rejecting this draft?
        </label>
        <textarea
          id="dlg-reject-input"
          ref={textRef}
          className="dlg__input reject-dialog__textarea"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g., Cookie migration is the wrong layer; we need a service-worker rollback first."
          disabled={busy}
          maxLength={1000}
          rows={4}
        />
      </div>
      <p className="dlg__rename-hint">
        Inspira will note your reason on the project before transitioning to
        Rejected.
      </p>
      {error && <div className="dlg__share-error">{error}</div>}
    </Dialog>
  );
}
