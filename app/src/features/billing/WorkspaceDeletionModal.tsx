// Inspira — Workspace deletion modal (Tier 2, B10).
//
// Typed-confirmation friction modeled on DangerZoneSection. Submit is
// disabled until the trimmed confirm input exactly matches the word
// declared by `billing.delete.confirm_word` ("DELETE"). On success we fire
// `onScheduled` and close; the grace-period banner lives on the overview
// page and picks up the scheduled status on next load.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { billingApi } from "./api";
import { t } from "../../i18n";

export type WorkspaceDeletionModalProps = {
  open: boolean;
  graceDays: number;
  onClose: () => void;
  onScheduled?: () => void;
};

export function WorkspaceDeletionModal({
  open,
  graceDays,
  onClose,
  onScheduled,
}: WorkspaceDeletionModalProps) {
  const [confirmText, setConfirmText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset state on open so every re-entry starts clean.
  useEffect(() => {
    if (!open) {
      setConfirmText("");
      setSubmitting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const focusId = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(focusId);
    };
  }, [open, onClose]);

  const requiredWord = t("billing.delete.confirm_word");
  const canSubmit = confirmText.trim() === requiredWord && !submitting;

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!canSubmit) return;
      setSubmitting(true);
      try {
        await billingApi.scheduleWorkspaceDeletion({
          confirmation: requiredWord,
        });
        onScheduled?.();
        onClose();
      } finally {
        setSubmitting(false);
      }
    },
    [canSubmit, onClose, onScheduled, requiredWord],
  );

  if (!open) return null;

  return (
    <div
      className="billing-modal-scrim"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="billing-modal billing-modal--sm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-delete-title"
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
        >
          ×
        </button>

        <p
          className="billing-eyebrow"
          style={{ color: "var(--rust)" }}
        >
          {t("billing.delete.title")}
        </p>
        <h3
          id="billing-delete-title"
          className="billing-display billing-display--lg"
        >
          {t("billing.delete.title")}
        </h3>

        <p className="billing-serif" style={{ marginTop: "10px" }}>
          {t("billing.delete.body", { days: graceDays })}
        </p>

        <form
          onSubmit={handleSubmit}
          style={{ marginTop: "18px" }}
          noValidate
        >
          <label className="billing-field">
            <span className="billing-field__label">
              {t("billing.delete.confirm_label", { word: requiredWord })}
            </span>
            <input
              ref={inputRef}
              type="text"
              className="billing-field__input"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              disabled={submitting}
              autoComplete="off"
              spellCheck={false}
            />
          </label>

          <div
            className="billing-pm__actions"
            style={{
              marginTop: "18px",
              justifyContent: "flex-end",
            }}
          >
            <button
              type="button"
              className="billing-btn billing-btn--ghost"
              onClick={onClose}
              disabled={submitting}
            >
              {t("billing.delete.cancel")}
            </button>
            <button
              type="submit"
              className="billing-btn billing-btn--danger"
              disabled={!canSubmit}
            >
              {submitting
                ? t("billing.delete.submitting")
                : t("billing.delete.submit")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
