// Inspira — Billing owner transfer modal (Tier 3, B14).
//
// Triggered from a member row in the Members list. Only the current
// billing owner may open it. The current owner drops to Admin (keeping
// every other admin power); the chosen member becomes the new billing
// owner with payment-method access. Typed "TRANSFER" confirm box mirrors
// the DangerZoneSection pattern so the action feels deliberate without
// feeling ominous.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import { billingApi, type WorkspaceMember } from "./api";
import { t } from "../../i18n";

export type BillingOwnerTransferModalProps = {
  open: boolean;
  currentOwner: WorkspaceMember | null;
  candidates: WorkspaceMember[];
  onClose: () => void;
  onTransferred?: (newOwnerId: string) => void;
};

type Stage = "entry" | "submitting" | "success";

export function BillingOwnerTransferModal({
  open,
  currentOwner,
  candidates,
  onClose,
  onTransferred,
}: BillingOwnerTransferModalProps) {
  const [stage, setStage] = useState<Stage>("entry");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const eligible = useMemo(
    () =>
      candidates.filter((m) => m.role !== "billing_owner"),
    [candidates],
  );
  const selected = useMemo(
    () => eligible.find((m) => m.user_id === selectedId) ?? null,
    [eligible, selectedId],
  );

  useEffect(() => {
    if (!open) {
      setStage("entry");
      setSelectedId(null);
      setConfirmText("");
      setError(null);
      return;
    }
    setStage("entry");
    setSelectedId(eligible[0]?.user_id ?? null);
    setConfirmText("");
    setError(null);
  }, [open, eligible]);

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

  const confirmWord = t("billing.tier3.transfer.confirm_word");
  const canSubmit =
    stage === "entry" &&
    selected != null &&
    confirmText.trim() === confirmWord;

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!canSubmit || !selected) return;
      setStage("submitting");
      setError(null);
      try {
        await billingApi.transferBillingOwner({
          to_user_id: selected.user_id,
        });
        setStage("success");
        onTransferred?.(selected.user_id);
      } catch {
        setError(t("billing.tier3.transfer.error_submit"));
        setStage("entry");
      }
    },
    [canSubmit, onTransferred, selected],
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
        aria-labelledby="billing-transfer-title"
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
        >
          {"\u00D7"}
        </button>

        {stage === "success" && selected ? (
          <>
            <p className="billing-eyebrow">
              {t("billing.tier3.transfer.eyebrow")}
            </p>
            <h3
              id="billing-transfer-title"
              className="billing-display billing-display--md"
            >
              {t("billing.tier3.transfer.success_title", {
                name: selected.display_name,
              })}
            </h3>
            <p className="billing-serif" style={{ marginTop: 10 }}>
              {t("billing.tier3.transfer.success_body")}
            </p>
            <div
              className="billing-pm__actions"
              style={{ marginTop: 22, justifyContent: "flex-end" }}
            >
              <button
                type="button"
                className="billing-btn billing-btn--sage"
                onClick={onClose}
              >
                {t("billing.tier3.transfer.success_cta")}
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={handleSubmit} noValidate>
            <p className="billing-eyebrow">
              {t("billing.tier3.transfer.eyebrow")}
            </p>
            <h3
              id="billing-transfer-title"
              className="billing-display billing-display--lg"
            >
              {t("billing.tier3.transfer.title")}
            </h3>
            <p className="billing-serif" style={{ marginTop: 10 }}>
              {t("billing.tier3.transfer.body")}
            </p>

            {eligible.length === 0 ? (
              <p
                className="billing-status"
                style={{ marginTop: 18 }}
              >
                {t("billing.tier3.transfer.no_candidates")}
              </p>
            ) : (
              <div
                role="radiogroup"
                aria-label={t("billing.tier3.transfer.select_label")}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  margin: "18px 0 6px",
                }}
              >
                {eligible.map((m) => (
                  <label
                    key={m.user_id}
                    className="billing-transfer-row"
                  >
                    <input
                      type="radio"
                      name="transfer-target"
                      value={m.user_id}
                      checked={selectedId === m.user_id}
                      onChange={(e: ChangeEvent<HTMLInputElement>) =>
                        setSelectedId(e.target.value)
                      }
                      disabled={stage === "submitting"}
                    />
                    <span className="billing-transfer-row__avatar">
                      {m.avatar_initials}
                    </span>
                    <span className="billing-transfer-row__body">
                      <span className="billing-transfer-row__name">
                        {m.display_name}
                      </span>
                      <span className="billing-transfer-row__email">
                        {m.email}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            )}

            {currentOwner && selected ? (
              <div className="billing-transfer-summary">
                <div className="billing-transfer-summary__slot">
                  <span className="billing-transfer-summary__avatar">
                    {currentOwner.avatar_initials}
                  </span>
                  <div>
                    <div className="billing-transfer-summary__name">
                      {currentOwner.display_name}
                    </div>
                    <div className="billing-transfer-summary__role">
                      {t("billing.tier3.transfer.role_from")}
                    </div>
                  </div>
                </div>
                <span className="billing-transfer-summary__arrow">
                  {"\u2192"}
                </span>
                <div className="billing-transfer-summary__slot">
                  <span className="billing-transfer-summary__avatar billing-transfer-summary__avatar--ink">
                    {selected.avatar_initials}
                  </span>
                  <div>
                    <div className="billing-transfer-summary__name">
                      {selected.display_name}
                    </div>
                    <div className="billing-transfer-summary__role">
                      {t("billing.tier3.transfer.role_to")}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}

            <label
              className="billing-field"
              style={{ marginTop: 18 }}
            >
              <span className="billing-field__label">
                {t("billing.tier3.transfer.confirm_label", {
                  word: confirmWord,
                })}
              </span>
              <input
                ref={inputRef}
                type="text"
                className="billing-field__input"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                disabled={stage === "submitting" || eligible.length === 0}
                autoComplete="off"
                spellCheck={false}
              />
            </label>

            {error ? (
              <p
                className="billing-status billing-status--error"
                role="status"
                aria-live="polite"
                style={{ marginTop: 12 }}
              >
                {error}
              </p>
            ) : null}

            <div
              className="billing-pm__actions"
              style={{ marginTop: 20, justifyContent: "flex-end" }}
            >
              <button
                type="button"
                className="billing-btn billing-btn--ghost"
                onClick={onClose}
                disabled={stage === "submitting"}
              >
                {t("billing.tier3.transfer.cta_cancel")}
              </button>
              <button
                type="submit"
                className="billing-btn billing-btn--sage"
                disabled={!canSubmit}
              >
                {stage === "submitting"
                  ? t("billing.tier3.transfer.cta_submitting")
                  : t("billing.tier3.transfer.cta_submit")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
