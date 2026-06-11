// Inspira — Payment method modal (Tier 2, B4).
//
// Two-tab modal: the "Card" tab summarises the card on file plus an exit to
// Stripe's customer portal; the "VAT & receipts" tab edits the invoice
// imprint. Actual card capture lives on Stripe's hosted page, so no Stripe
// Elements are embedded here.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import {
  BillingNotConfiguredError,
  billingApi,
  type BillingContacts,
  type PaymentMethod,
} from "./api";
import { t } from "../../i18n";

export type PaymentMethodModalProps = {
  open: boolean;
  onClose: () => void;
};

type Tab = "card" | "vat";
type CardView = "summary" | "remove-confirm";

export function PaymentMethodModal({ open, onClose }: PaymentMethodModalProps) {
  const [tab, setTab] = useState<Tab>("card");
  const [cardView, setCardView] = useState<CardView>("summary");

  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | null>(
    null,
  );
  const [contacts, setContacts] = useState<BillingContacts>({
    company_name: null,
    vat_id: null,
    address_lines: [],
    receipt_emails: [],
  });

  const [loadingPm, setLoadingPm] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);
  const [portalNotConfigured, setPortalNotConfigured] = useState(false);

  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [companyName, setCompanyName] = useState("");
  const [vatId, setVatId] = useState("");
  const [addressText, setAddressText] = useState("");
  const [receiptEmailsText, setReceiptEmailsText] = useState("");

  const dialogRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // --- Data load on open ---------------------------------------------------
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoadingPm(true);
    void (async () => {
      try {
        const [pmRes, cRes] = await Promise.all([
          billingApi.getPaymentMethod(),
          billingApi.getBillingContacts(),
        ]);
        if (!alive) return;
        setPaymentMethod(pmRes.payment_method);
        setContacts(cRes.contacts);
        setCompanyName(cRes.contacts.company_name ?? "");
        setVatId(cRes.contacts.vat_id ?? "");
        setAddressText((cRes.contacts.address_lines ?? []).join("\n"));
        setReceiptEmailsText((cRes.contacts.receipt_emails ?? []).join(", "));
      } catch {
        // Swallow — the surface still renders, just without prefilled fields.
      } finally {
        if (alive) setLoadingPm(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [open]);

  // --- Body scroll lock + Esc ---------------------------------------------
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  // --- Focus trap (minimal) -----------------------------------------------
  useEffect(() => {
    if (!open) return;
    // Focus the close button for a predictable initial anchor.
    const id = window.setTimeout(() => closeBtnRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  useEffect(() => {
    // Whenever the modal re-opens, reset transient UI state.
    if (!open) {
      setCardView("summary");
      setSavedFlash(false);
      setPortalNotConfigured(false);
      setTab("card");
    }
  }, [open]);

  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab") return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusables = dialog.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);

  const handleOpenPortal = useCallback(async () => {
    setPortalLoading(true);
    setPortalNotConfigured(false);
    try {
      const res = await billingApi.openPortalSession();
      if (res.portal?.url) {
        window.location.assign(res.portal.url);
        return;
      }
    } catch (err) {
      if (err instanceof BillingNotConfiguredError) {
        setPortalNotConfigured(true);
      }
    } finally {
      setPortalLoading(false);
    }
  }, []);

  const handleRemoveCard = useCallback(async () => {
    setRemoving(true);
    try {
      await billingApi.deletePaymentMethod();
      setPaymentMethod(null);
      setCardView("summary");
    } finally {
      setRemoving(false);
    }
  }, []);

  const handleSaveVat = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (saving) return;
      setSaving(true);
      setSavedFlash(false);
      try {
        const next: Partial<BillingContacts> = {
          company_name: companyName.trim() || null,
          vat_id: vatId.trim() || null,
          address_lines: addressText
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
          receipt_emails: receiptEmailsText
            .split(",")
            .map((line) => line.trim())
            .filter(Boolean),
        };
        const res = await billingApi.updateBillingContacts(next);
        setContacts(res.contacts);
        setSavedFlash(true);
        window.setTimeout(() => setSavedFlash(false), 2000);
      } finally {
        setSaving(false);
      }
    },
    [addressText, companyName, receiptEmailsText, saving, vatId],
  );

  if (!open) return null;

  // Retained so a future "when contacts change" effect doesn't mis-diff.
  void contacts;

  return (
    <div
      className="billing-modal-scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="billing-modal billing-modal--md"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-pm-title"
        onKeyDown={handleKeyDown}
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

        <p className="billing-eyebrow">{t("billing.eyebrow.payment")}</p>
        <h3
          id="billing-pm-title"
          className="billing-display billing-display--lg"
          style={{ marginBottom: "20px" }}
        >
          {t("billing.pm_modal.title")}
        </h3>

        <div className="billing-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "card"}
            onClick={() => setTab("card")}
          >
            {t("billing.pm_modal.tab_card")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "vat"}
            onClick={() => setTab("vat")}
          >
            {t("billing.pm_modal.tab_vat")}
          </button>
        </div>

        {tab === "card" ? (
          <div role="tabpanel">
            {cardView === "summary" ? (
              <div className="billing-pm">
                {loadingPm ? (
                  <p className="billing-status">
                    {t("billing.invoices.loading")}
                  </p>
                ) : paymentMethod ? (
                  <div className="billing-pm__card">
                    <span className="billing-pm__brand">
                      {paymentMethod.brand}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div className="billing-pm__num">
                        {paymentMethod.brand}{" "}
                        <em>•• {paymentMethod.last4}</em>
                      </div>
                      <p className="billing-pm__meta">
                        {t("billing.pm.expires", {
                          month: String(paymentMethod.exp_month).padStart(
                            2,
                            "0",
                          ),
                          year: String(paymentMethod.exp_year),
                          name: paymentMethod.holder_name ?? "—",
                        })}
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="billing-serif billing-serif--dim">
                    {t("billing.pm.no_card")}
                  </p>
                )}

                {portalNotConfigured ? (
                  <div
                    className="billing-fallback"
                    style={{ margin: "10px 0 0" }}
                  >
                    <p className="billing-eyebrow billing-fallback__eyebrow">
                      {t("billing.checkout.fallback_eyebrow")}
                    </p>
                    <p className="billing-serif">
                      {t("billing.checkout.fallback_body", {
                        email: "hello@inspira.app",
                      })}
                    </p>
                  </div>
                ) : null}

                <div className="billing-pm__actions">
                  <button
                    type="button"
                    className="billing-btn billing-btn--sage"
                    onClick={handleOpenPortal}
                    disabled={portalLoading}
                  >
                    {t("billing.pm_modal.card_cta")}
                  </button>
                  {paymentMethod ? (
                    <button
                      type="button"
                      className="billing-btn billing-btn--link"
                      style={{ color: "var(--rust)" }}
                      onClick={() => setCardView("remove-confirm")}
                    >
                      {t("billing.pm_modal.remove_card")}
                    </button>
                  ) : null}
                </div>
                <p
                  className="billing-serif billing-serif--sm billing-serif--dim"
                  style={{ marginTop: "6px" }}
                >
                  {t("billing.pm_modal.card_note")}
                </p>
              </div>
            ) : (
              <div>
                <h4
                  className="billing-display billing-display--md"
                  style={{ margin: "6px 0 10px" }}
                >
                  {t("billing.pm_modal.remove_confirm_heading")}
                </h4>
                <p className="billing-serif">
                  {t("billing.pm_modal.remove_confirm_body")}
                </p>
                <div
                  className="billing-pm__actions"
                  style={{ marginTop: "18px" }}
                >
                  <button
                    type="button"
                    className="billing-btn billing-btn--ghost"
                    onClick={() => setCardView("summary")}
                    disabled={removing}
                  >
                    {t("billing.pm_modal.cancel")}
                  </button>
                  <button
                    type="button"
                    className="billing-btn billing-btn--danger"
                    onClick={handleRemoveCard}
                    disabled={removing}
                  >
                    {t("billing.pm_modal.remove_confirm_cta")}
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <form
            role="tabpanel"
            onSubmit={handleSaveVat}
            style={{ display: "flex", flexDirection: "column", gap: "14px" }}
          >
            <label className="billing-field">
              <span className="billing-field__label">
                {t("billing.pm_modal.vat_company")}
              </span>
              <input
                type="text"
                className="billing-field__input"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                disabled={saving}
              />
            </label>
            <label className="billing-field">
              <span className="billing-field__label">
                {t("billing.pm_modal.vat_id")}
              </span>
              <input
                type="text"
                className="billing-field__input"
                value={vatId}
                onChange={(e) => setVatId(e.target.value)}
                disabled={saving}
              />
            </label>
            <label className="billing-field">
              <span className="billing-field__label">
                {t("billing.pm_modal.vat_address")}
              </span>
              <textarea
                className="billing-field__input"
                rows={3}
                value={addressText}
                onChange={(e) => setAddressText(e.target.value)}
                disabled={saving}
              />
            </label>
            <label className="billing-field">
              <span className="billing-field__label">
                {t("billing.pm_modal.vat_emails")}
              </span>
              <input
                type="text"
                className="billing-field__input"
                value={receiptEmailsText}
                onChange={(e) => setReceiptEmailsText(e.target.value)}
                disabled={saving}
              />
              <span
                className="billing-serif billing-serif--sm billing-serif--dim"
                style={{ marginTop: "4px" }}
              >
                {t("billing.pm_modal.vat_emails_help")}
              </span>
            </label>

            <div
              className="billing-pm__actions"
              style={{ marginTop: "6px", alignItems: "center" }}
            >
              <button
                type="submit"
                className="billing-btn billing-btn--sage"
                disabled={saving}
              >
                {saving
                  ? t("billing.pm_modal.saving")
                  : t("billing.pm_modal.save")}
              </button>
              {savedFlash ? (
                <span className="billing-status" role="status" aria-live="polite">
                  {t("billing.pm_modal.saved")}
                </span>
              ) : null}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
