// Inspira — Checkout canceled page (Tier 2).
//
// Rendered when Stripe redirects to /billing?checkout=canceled. Same quiet
// shell as CheckoutSuccessPage with inverted intent — no failure language,
// no red. Two ways forward: return to billing or retry the upgrade.

import { t } from "../../i18n";

export type CheckoutCanceledPageProps = {
  onClose: () => void;
  onRetry: () => void;
};

export function CheckoutCanceledPage({
  onClose,
  onRetry,
}: CheckoutCanceledPageProps) {
  return (
    <div
      className="billing-page"
      role="dialog"
      aria-modal="true"
      aria-labelledby="billing-checkout-canceled-title"
    >
      <div className="billing-page__topbar">
        <h2 className="billing-page__brand">{t("billing.page.heading")}</h2>
        <button
          type="button"
          className="billing-page__close"
          aria-label={t("billing.page.close_aria")}
          title={t("billing.page.close_title")}
          onClick={onClose}
        >
          ×
        </button>
      </div>

      <div
        className="billing-page__inner"
        style={{
          alignItems: "center",
          textAlign: "center",
          paddingTop: "12vh",
        }}
      >
        <p className="billing-eyebrow">{t("billing.eyebrow.billing")}</p>
        <h2
          id="billing-checkout-canceled-title"
          className="billing-display billing-display--xl"
        >
          {t("billing.checkout_return.canceled.title")}
        </h2>
        <p
          className="billing-serif"
          style={{ maxWidth: "48ch", marginTop: "14px" }}
        >
          {t("billing.checkout_return.canceled.body")}
        </p>
        <div
          style={{
            marginTop: "26px",
            display: "flex",
            gap: "12px",
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            className="billing-btn billing-btn--ghost"
            onClick={onClose}
          >
            {t("billing.checkout_return.canceled.cta")}
          </button>
          <button
            type="button"
            className="billing-btn billing-btn--sage"
            onClick={onRetry}
          >
            {t("billing.checkout_return.canceled.retry")}
          </button>
        </div>
      </div>
    </div>
  );
}
