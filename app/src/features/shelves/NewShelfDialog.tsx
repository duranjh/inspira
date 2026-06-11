// NewShelfDialog — minimal name-entry dialog for creating a shelf.
//
// Thin wrapper over the shared `Dialog` shell. Shape: single text input,
// Save + Cancel. Enter submits; empty / whitespace names disable the
// primary action. The 80-char cap matches the backend's
// `shelves.MAX_SHELF_NAME_CHARS`.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Dialog } from "../../components/dialogs/Dialog";
import { t } from "../../i18n";

export type NewShelfDialogProps = {
  open: boolean;
  onSubmit: (name: string) => Promise<void>;
  onClose: () => void;
};

const MAX_SHELF_NAME_CHARS = 80;

export function NewShelfDialog({ open, onSubmit, onClose }: NewShelfDialogProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [draft, setDraft] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Reset state whenever the dialog is (re-)opened.
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
      // Parent typically closes on success; we don't force it here so
      // the caller can keep the dialog open on a retriable error.
    } catch (err) {
      console.error("[Inspira] new shelf failed", err);
      setError(t("shelves.new_dialog.error_fallback"));
      setBusy(false);
    }
  }, [disabled, onSubmit, trimmed]);

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

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("shelves.new_dialog.title")}
      primaryAction={{
        label: t("shelves.new_dialog.save"),
        onClick: submit,
        disabled,
        busy,
      }}
      secondaryAction={{
        label: t("shelves.new_dialog.cancel"),
        onClick: onClose,
      }}
    >
      <form onSubmit={handleFormSubmit} noValidate>
        <div className="dlg__field">
          <label className="dlg__label" htmlFor="dlg-new-shelf-input">
            {t("shelves.new_dialog.name_label")}
          </label>
          <input
            id="dlg-new-shelf-input"
            ref={inputRef}
            className="dlg__input"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleInputKeyDown}
            placeholder={t("shelves.new_dialog.placeholder")}
            autoComplete="off"
            spellCheck={true}
            disabled={busy}
            maxLength={MAX_SHELF_NAME_CHARS}
          />
        </div>
        <p className="dlg__rename-hint">
          {t("shelves.new_dialog.hint")}
        </p>
        {error && <div className="dlg__share-error">{error}</div>}
      </form>
    </Dialog>
  );
}
