// Inspira — Refund request modal (Tier 3, B13).
//
// Single screen, a person reads every one. Billing owner only; opened
// from InvoiceDetailModal. Three stages: entry → submitting → success.
// No step indicator, no timers. Warm, honest copy. Pre-populates amount
// from the invoice total but lets the owner edit it for a partial
// refund.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import {
  billingApi,
  type InvoiceDetail,
  type RefundReason,
} from "./api";
import { formatDate } from "../../i18n/format";
import { t } from "../../i18n";

export type RefundRequestModalProps = {
  invoice: InvoiceDetail | null;
  onClose: () => void;
  onSubmitted?: () => void;
};

type Stage = "entry" | "submitting" | "success";

const REASONS: RefundReason[] = [
  "duplicate_charge",
  "too_expensive",
  "missing_feature",
  "other",
];

function formatAmount(cents: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
    }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

function parseAmountToCents(raw: string): number | null {
  const cleaned = raw.replace(/[^0-9.]/g, "");
  if (!cleaned) return null;
  const value = Number(cleaned);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}

export function RefundRequestModal({
  invoice,
  onClose,
  onSubmitted,
}: RefundRequestModalProps) {
  const [stage, setStage] = useState<Stage>("entry");
  const [reason, setReason] = useState<RefundReason>("duplicate_charge");
  const [description, setDescription] = useState("");
  const [amountInput, setAmountInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const firstFieldRef = useRef<HTMLSelectElement | null>(null);

  // Reset every time a new invoice arrives or the modal closes.
  useEffect(() => {
    if (!invoice) {
      setStage("entry");
      setReason("duplicate_charge");
      setDescription("");
      setAmountInput("");
      setError(null);
      return;
    }
    setStage("entry");
    setReason("duplicate_charge");
    setDescription("");
    setAmountInput(
      formatAmount(invoice.total_cents, invoice.currency),
    );
    setError(null);
  }, [invoice]);

  useEffect(() => {
    if (!invoice) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const focusId = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(focusId);
    };
  }, [invoice, onClose]);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!invoice || stage === "submitting") return;
      const amountCents = parseAmountToCents(amountInput);
      if (amountCents == null) {
        setError(t("billing.tier3.refund.error_amount"));
        return;
      }
      setError(null);
      setStage("submitting");
      try {
        await billingApi.requestRefund({
          invoice_id: invoice.id,
          reason,
          description: description.trim() || undefined,
          amount_cents: amountCents,
        });
        setStage("success");
        onSubmitted?.();
      } catch {
        setError(t("billing.tier3.refund.error_submit"));
        setStage("entry");
      }
    },
    [amountInput, description, invoice, onSubmitted, reason, stage],
  );

  if (!invoice) return null;

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
        aria-labelledby="billing-refund-title"
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
        >
          {"\u00D7"}
        </button>

        {stage === "success" ? (
          <>
            <p className="billing-eyebrow">
              {t("billing.tier3.refund.eyebrow")}
            </p>
            <h3
              id="billing-refund-title"
              className="billing-display billing-display--md"
            >
              {t("billing.tier3.refund.success_title")}
            </h3>
            <p className="billing-serif" style={{ marginTop: 10 }}>
              {t("billing.tier3.refund.success_body")}
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
                {t("billing.tier3.refund.success_cta")}
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={handleSubmit} noValidate>
            <p className="billing-eyebrow">
              {t("billing.tier3.refund.eyebrow")}
            </p>
            <h3
              id="billing-refund-title"
              className="billing-display billing-display--lg"
              style={{ marginBottom: 14 }}
            >
              {t("billing.tier3.refund.title")}
            </h3>
            <p className="billing-refund-ctx">
              {t("billing.tier3.refund.context", {
                number: invoice.number,
                amount: formatAmount(invoice.total_cents, invoice.currency),
                date: formatDate(invoice.issued_at),
              })}
            </p>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 14,
              }}
            >
              <label className="billing-field">
                <span className="billing-field__label">
                  {t("billing.tier3.refund.reason_label")}
                </span>
                <select
                  ref={firstFieldRef}
                  className="billing-field__input"
                  value={reason}
                  onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                    setReason(e.target.value as RefundReason)
                  }
                  disabled={stage === "submitting"}
                >
                  {REASONS.map((r) => (
                    <option key={r} value={r}>
                      {t(`billing.tier3.refund.reason.${r}`)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="billing-field">
                <span className="billing-field__label">
                  {t("billing.tier3.refund.description_label")}
                </span>
                <textarea
                  className="billing-field__input"
                  rows={4}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={stage === "submitting"}
                  placeholder={t("billing.tier3.refund.description_placeholder")}
                />
              </label>

              <label className="billing-field">
                <span className="billing-field__label">
                  {t("billing.tier3.refund.amount_label")}
                </span>
                <input
                  type="text"
                  inputMode="decimal"
                  className="billing-field__input"
                  value={amountInput}
                  onChange={(e) => setAmountInput(e.target.value)}
                  disabled={stage === "submitting"}
                />
                <span className="billing-refund-hint">
                  {t("billing.tier3.refund.amount_hint")}
                </span>
              </label>
            </div>

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
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
                marginTop: 22,
              }}
            >
              <p className="billing-refund-note">
                {t("billing.tier3.refund.note")}
              </p>
              <div style={{ display: "flex", gap: 10 }}>
                <button
                  type="button"
                  className="billing-btn billing-btn--ghost"
                  onClick={onClose}
                  disabled={stage === "submitting"}
                >
                  {t("billing.tier3.refund.cta_cancel")}
                </button>
                <button
                  type="submit"
                  className="billing-btn billing-btn--sage"
                  disabled={stage === "submitting"}
                >
                  {stage === "submitting"
                    ? t("billing.tier3.refund.cta_submitting")
                    : t("billing.tier3.refund.cta_submit")}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
