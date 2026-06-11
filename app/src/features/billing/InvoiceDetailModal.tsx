// Inspira — Invoice detail modal (Tier 2, B5b).
//
// Editorial-style invoice view. Reads like a letter: who it's to, what it's
// for, totals, and a sage "Download PDF". Refunds are a soft link that
// fires `requestRefund` — backend still stubbed, so we acknowledge with a
// quiet toast-style line rather than a dialog.

import { useCallback, useEffect, useRef, useState } from "react";

import { billingApi, type InvoiceDetail } from "./api";
import { RefundRequestModal } from "./RefundRequestModal";
import { formatDate } from "../../i18n/format";
import { t } from "../../i18n";

export type InvoiceDetailModalProps = {
  invoiceId: string | null;
  onClose: () => void;
};

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

export function InvoiceDetailModal({
  invoiceId,
  onClose,
}: InvoiceDetailModalProps) {
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [pdfNote, setPdfNote] = useState<string | null>(null);
  const [refundOpen, setRefundOpen] = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!invoiceId) {
      setDetail(null);
      setPdfNote(null);
      setRefundOpen(false);
      return;
    }
    let alive = true;
    setLoading(true);
    setPdfNote(null);
    setRefundOpen(false);
    void (async () => {
      try {
        const res = await billingApi.getInvoiceDetail(invoiceId);
        if (alive) setDetail(res.invoice);
      } catch {
        if (alive) setDetail(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [invoiceId]);

  useEffect(() => {
    if (!invoiceId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const id = window.setTimeout(() => closeBtnRef.current?.focus(), 0);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(id);
    };
  }, [invoiceId, onClose]);

  const handleDownload = useCallback(async () => {
    if (!detail) return;
    try {
      const res = await billingApi.downloadInvoicePdf(detail.id);
      if (res.url) {
        window.open(res.url, "_blank", "noopener,noreferrer");
      } else {
        setPdfNote(t("billing.invoice_detail.pdf_unavailable"));
      }
    } catch {
      setPdfNote(t("billing.invoice_detail.pdf_unavailable"));
    }
  }, [detail]);

  const handleRefund = useCallback(() => {
    if (!detail) return;
    setRefundOpen(true);
  }, [detail]);

  if (!invoiceId) return null;

  return (
    <div
      className="billing-modal-scrim"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="billing-modal billing-modal--md"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-inv-detail-title"
      >
        <button
          ref={closeBtnRef}
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
        >
          ×
        </button>

        <p className="billing-eyebrow">{t("billing.eyebrow.invoices")}</p>

        {loading || !detail ? (
          <p className="billing-status" style={{ padding: "10px 0 20px" }}>
            {t("billing.invoices.loading")}
          </p>
        ) : (
          <>
            <h3
              id="billing-inv-detail-title"
              className="billing-display billing-display--lg"
            >
              {t("billing.invoice_detail.title", { number: detail.number })}
            </h3>
            <p
              className="billing-mono issued"
              style={{ marginTop: "6px", marginBottom: "22px" }}
            >
              {t("billing.invoice_detail.issued", {
                date: formatDate(detail.issued_at),
              })}
            </p>

            <div style={{ marginBottom: "22px" }}>
              <p className="billing-field__label">
                {t("billing.invoice_detail.billed_to")}
              </p>
              <div
                className="billing-serif"
                style={{ marginTop: "6px", lineHeight: 1.6 }}
              >
                <div>{detail.billed_to.name}</div>
                {detail.billed_to.address_lines.map((line, i) => (
                  <div key={i}>{line}</div>
                ))}
                {detail.billed_to.vat_id ? (
                  <div className="billing-mono" style={{ marginTop: "6px" }}>
                    {detail.billed_to.vat_id}
                  </div>
                ) : null}
              </div>
            </div>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                marginBottom: "18px",
              }}
            >
              <p
                className="billing-field__label"
                style={{ marginBottom: "2px" }}
              >
                {t("billing.invoice_detail.lines")}
              </p>
              {detail.lines.map((line, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr auto",
                    gap: "18px",
                    alignItems: "baseline",
                    paddingBottom: "10px",
                    borderBottom: "1px solid var(--border-soft)",
                  }}
                >
                  <span className="billing-serif">{line.description}</span>
                  <span className="billing-inv__amt">
                    {formatAmount(line.amount_cents, detail.currency)}
                  </span>
                </div>
              ))}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: "18px",
                  alignItems: "baseline",
                  marginTop: "4px",
                }}
              >
                <span className="billing-serif billing-serif--dim">
                  {t("billing.invoice_detail.subtotal")}
                </span>
                <span className="billing-inv__amt">
                  {formatAmount(detail.amount_cents, detail.currency)}
                </span>
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: "18px",
                  alignItems: "baseline",
                }}
              >
                <span className="billing-serif billing-serif--dim">
                  {t("billing.invoice_detail.tax")}
                </span>
                <span className="billing-inv__amt">
                  {formatAmount(detail.tax_cents, detail.currency)}
                </span>
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: "18px",
                  alignItems: "baseline",
                  paddingTop: "10px",
                  borderTop: "1px solid var(--paper-edge)",
                }}
              >
                <span className="billing-serif">
                  {t("billing.invoice_detail.total")}
                </span>
                <span className="billing-inv__amt">
                  {formatAmount(detail.total_cents, detail.currency)}
                </span>
              </div>
            </div>

            {pdfNote ? (
              <p
                className="billing-status"
                role="status"
                aria-live="polite"
                style={{ marginTop: "6px" }}
              >
                {pdfNote}
              </p>
            ) : null}

            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "12px",
                flexWrap: "wrap",
                marginTop: "22px",
              }}
            >
              <button
                type="button"
                className="billing-btn billing-btn--link"
                onClick={handleRefund}
              >
                {t("billing.invoice_detail.request_refund")}
              </button>
              <button
                type="button"
                className="billing-btn billing-btn--sage"
                onClick={handleDownload}
              >
                {t("billing.invoice_detail.download_pdf")}
              </button>
            </div>
          </>
        )}
      </div>
      <RefundRequestModal
        invoice={refundOpen ? detail : null}
        onClose={() => setRefundOpen(false)}
      />
    </div>
  );
}
