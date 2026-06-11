// Inspira — rename-project dialog.
//
// Thin wrapper over `Dialog`: single text input pre-filled with the current
// title, select-all on focus, Enter to submit. Primary action is disabled
// when the draft is empty or unchanged from the current title.

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

export type RenameProjectDialogProps = {
  open: boolean;
  currentTitle: string;
  onSubmit: (newTitle: string) => Promise<void>;
  onClose: () => void;
  // Optional copy overrides — lets the same dialog do double duty for
  // renaming shelves (and any future nameable entity) without forking the
  // component. When omitted, all copy falls back to the project-rename
  // i18n defaults.
  titleOverride?: string;
  labelOverride?: string;
  placeholderOverride?: string;
  hintOverride?: string;
};

export function RenameProjectDialog({
  open,
  currentTitle,
  onSubmit,
  onClose,
  titleOverride,
  labelOverride,
  placeholderOverride,
  hintOverride,
}: RenameProjectDialogProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [draft, setDraft] = useState<string>(currentTitle);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Reset local state whenever the dialog is (re-)opened so the input
  // reflects the latest title.
  useEffect(() => {
    if (open) {
      setDraft(currentTitle);
      setError(null);
      setBusy(false);
    }
  }, [open, currentTitle]);

  // Select-all on focus so the user can start typing immediately. We
  // trigger it on mount — the base Dialog will focus us via its initial
  // focus pass.
  const handleFocus = useCallback(() => {
    inputRef.current?.select();
  }, []);

  const trimmed = draft.trim();
  const unchanged = trimmed === currentTitle.trim();
  const disabled = trimmed.length === 0 || unchanged || busy;

  const submit = useCallback(async () => {
    if (disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(trimmed);
      // Parent typically closes on success; we don't force it.
    } catch (err) {
      console.error("[Inspira] rename project failed", err);
      setError(t("rename_dialog.error_fallback"));
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
      title={titleOverride ?? t("rename_dialog.title")}
      primaryAction={{
        label: t("rename_dialog.action_rename"),
        onClick: submit,
        disabled,
        busy,
      }}
      secondaryAction={{
        label: t("rename_dialog.action_cancel"),
        onClick: onClose,
      }}
    >
      <form onSubmit={handleFormSubmit} noValidate>
        <div className="dlg__field">
          <label className="dlg__label" htmlFor="dlg-rename-input">
            {labelOverride ?? t("rename_dialog.label")}
          </label>
          <input
            id="dlg-rename-input"
            ref={inputRef}
            className="dlg__input"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={handleFocus}
            onKeyDown={handleInputKeyDown}
            placeholder={placeholderOverride ?? t("rename_dialog.placeholder")}
            autoComplete="off"
            spellCheck={true}
            disabled={busy}
            maxLength={200}
          />
        </div>
        <p className="dlg__rename-hint">
          {hintOverride ?? t("rename_dialog.hint")}
        </p>
        {error && <div className="dlg__share-error">{error}</div>}
      </form>
    </Dialog>
  );
}
