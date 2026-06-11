// Inspira — Invoice history (Tier 2, B5).
//
// Full-viewport `.billing-page` overlay listing paid/pending/failed invoices
// with quiet status words. Opens InvoiceDetailModal inline when a row's
// "Details" action is triggered.

import { useCallback, useEffect, useState } from "react";

import { billingApi, type Invoice } from "./api";
import { formatDate } from "../../i18n/format";
import { t } from "../../i18n";
import { InvoiceDetailModal } from "./InvoiceDetailModal";

export type InvoiceHistoryProps = {
  open: boolean;
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

function statusClass(status: Invoice["status"]): string {
  switch (status) {
    case "paid":
      return "billing-inv__status billing-inv__status--paid";
    case "pending":
      return "billing-inv__status billing-inv__status--pending";
    case "failed":
      return "billing-inv__status billing-inv__status--failed";
    case "refund":
      return "billing-inv__status billing-inv__status--refund";
    default:
      return "billing-inv__status";
  }
}

function statusLabel(status: Invoice["status"]): string {
  switch (status) {
    case "paid":
      return t("billing.invoices.status_paid");
    case "pending":
      return t("billing.invoices.status_pending");
    case "failed":
      return t("billing.invoices.status_failed");
    case "refund":
      return t("billing.invoices.status_refund");
    default:
      return status;
  }
}

export function InvoiceHistory({ open, onClose }: InvoiceHistoryProps) {
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [pdfNote, setPdfNote] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setPdfNote(null);
    void (async () => {
      try {
        const res = await billingApi.getInvoices();
        if (alive) setInvoices(res.invoices);
      } catch {
        if (alive) setInvoices([]);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const handleDownloadPdf = useCallback(async (id: string) => {
    setPdfNote(null);
    try {
      const res = await billingApi.downloadInvoicePdf(id);
      if (res.url) {
        window.open(res.url, "_blank", "noopener,noreferrer");
      } else {
        setPdfNote(t("billing.invoice_detail.pdf_unavailable"));
      }
    } catch {
      setPdfNote(t("billing.invoice_detail.pdf_unavailable"));
    }
  }, []);

  if (!open) return null;

  const isEmpty = !loading && (invoices?.length ?? 0) === 0;

  return (
    <div
      className="billing-page"
      role="dialog"
      aria-modal="true"
      aria-labelledby="billing-inv-title"
    >
      <div className="billing-page__topbar">
        <h2 className="billing-page__brand" id="billing-inv-title">
          {t("billing.invoices_page.title")}
        </h2>
        <button
          type="button"
          className="billing-page__close"
          aria-label={t("billing.invoices_page.close")}
          onClick={onClose}
        >
          ×
        </button>
      </div>

      <div className="billing-page__inner">
        {loading ? (
          <p
            className="billing-status"
            style={{ textAlign: "center", padding: "40px 0" }}
          >
            {t("billing.invoices.loading")}
          </p>
        ) : isEmpty ? (
          <div className="billing-invoices__empty">
            <h3
              className="billing-display billing-display--md"
              style={{ marginBottom: "10px", fontStyle: "normal" }}
            >
              {t("billing.invoices_page.empty_headline")}
            </h3>
            <p className="billing-serif billing-serif--dim">
              {t("billing.invoices_page.empty_body")}
            </p>
          </div>
        ) : (
          <div className="billing-invoices">
            {pdfNote ? (
              <p
                className="billing-status"
                role="status"
                aria-live="polite"
                style={{ marginBottom: "12px" }}
              >
                {pdfNote}
              </p>
            ) : null}
            {(invoices ?? []).map((inv) => (
              <div key={inv.id} className="billing-inv">
                <span className="billing-inv__date">
                  {formatDate(inv.issued_at)}
                </span>
                <span className="billing-inv__desc">
                  {inv.period_label}
                  <br />
                  <span className="billing-inv__num">{inv.number}</span>
                </span>
                <span className="billing-inv__amt">
                  {formatAmount(inv.amount_cents, inv.currency)}
                </span>
                <span className={statusClass(inv.status)}>
                  {statusLabel(inv.status)}
                </span>
                <span className="billing-inv__actions">
                  <button
                    type="button"
                    onClick={() => handleDownloadPdf(inv.id)}
                  >
                    {t("billing.invoices.pdf")}
                  </button>
                  <button type="button" onClick={() => setDetailId(inv.id)}>
                    {t("billing.invoices.details")}
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <InvoiceDetailModal
        invoiceId={detailId}
        onClose={() => setDetailId(null)}
      />
    </div>
  );
}
