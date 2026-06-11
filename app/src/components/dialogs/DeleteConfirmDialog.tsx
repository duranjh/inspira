// Inspira — delete-confirmation dialog.
//
// Destructive-variant `Dialog`: rust-tinted primary pill, a serif paragraph
// explaining the consequences, and an optional "type the name to confirm"
// input gated behind `requireTypedConfirmation`. That gate is meant for
// heavy objects (projects) where accidental deletion is expensive; small
// items (one decision, one topic) should leave it off.

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Dialog } from "./Dialog";

import { t } from "../../i18n";

export type DeleteConfirmDialogProps = {
  open: boolean;
  itemType: string;
  itemName: string;
  consequences: string;
  requireTypedConfirmation?: boolean;
  onConfirm: () => Promise<void>;
  onClose: () => void;
};

export function DeleteConfirmDialog({
  open,
  itemType,
  itemName,
  consequences,
  requireTypedConfirmation = false,
  onConfirm,
  onClose,
}: DeleteConfirmDialogProps) {
  const [typed, setTyped] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTyped("");
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const confirmationOk =
    !requireTypedConfirmation || typed.trim() === itemName.trim();
  const disabled = !confirmationOk || busy;

  const confirm = useCallback(async () => {
    if (disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      console.error("[Inspira] delete confirm failed", err);
      setError(t("delete_dialog.error_fallback"));
      setBusy(false);
    }
  }, [disabled, onConfirm]);

  const handleFormSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      void confirm();
    },
    [confirm],
  );

  const handleInputKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        void confirm();
      }
    },
    [confirm],
  );

  const itemTypeTranslated = t(`delete_dialog.item_type.${itemType}`, undefined) || itemType;
  const title = t("delete_dialog.title", { item_type: itemTypeTranslated });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      primaryAction={{
        label: t("delete_dialog.action_delete"),
        onClick: confirm,
        disabled,
        busy,
        variant: "danger",
      }}
      secondaryAction={{
        label: t("delete_dialog.action_cancel"),
        onClick: onClose,
      }}
    >
      <p className="dlg__delete-consequences">
        {t("delete_dialog.about_to_delete")}{" "}
        <span className="dlg__delete-name">{itemName}</span>. {consequences}
      </p>
      {requireTypedConfirmation && (
        <form onSubmit={handleFormSubmit} noValidate>
          <div className="dlg__field">
            <label className="dlg__label" htmlFor="dlg-delete-confirm-input">
              {t("delete_dialog.type_to_confirm")}
            </label>
            <input
              id="dlg-delete-confirm-input"
              className="dlg__input dlg__input--mono"
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder={itemName}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </div>
          <p className="dlg__typed-confirm-hint">
            {t("delete_dialog.hint")}
          </p>
        </form>
      )}
      {error && <div className="dlg__share-error">{error}</div>}
    </Dialog>
  );
}
