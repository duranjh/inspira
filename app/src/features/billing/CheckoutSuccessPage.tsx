// Inspira — Checkout success page (Tier 2).
//
// Rendered when Stripe redirects to /billing?checkout=success. Quiet center
// of the page: one mark, one sentence, one CTA. We fetch the subscription
// so the title can greet users by plan; while that's in-flight we render a
// soft placeholder so the surface never shows a blank heading.

import { useEffect, useState } from "react";

import { billingApi } from "./api";
import { t } from "../../i18n";

export type CheckoutSuccessPageProps = {
  onClose: () => void;
};

function planLabelFromSlug(slug: string | null): string {
  switch (slug) {
    case "free":
      return "Free";
    case "pro":
      return "Pro";
    case "team":
      // Slug stays "team" for backward-compat; user-facing label is "Frontier" post-2026-04-28 rebrand.
      return "Frontier";
    default:
      return "your new plan";
  }
}

export function CheckoutSuccessPage({ onClose }: CheckoutSuccessPageProps) {
  const [planName, setPlanName] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await billingApi.getSubscription();
        if (!alive) return;
        const slug = res.subscription?.plan?.slug ?? null;
        const title = res.subscription?.plan?.title ?? null;
        setPlanName(title || planLabelFromSlug(slug));
      } catch {
        if (alive) setPlanName(planLabelFromSlug(null));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div
      className="billing-page"
      role="dialog"
      aria-modal="true"
      aria-labelledby="billing-checkout-success-title"
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
          id="billing-checkout-success-title"
          className="billing-display billing-display--xl"
        >
          {t("billing.checkout_return.success.title", {
            plan: planName ?? planLabelFromSlug(null),
          })}
        </h2>
        <p
          className="billing-serif"
          style={{ maxWidth: "48ch", marginTop: "14px" }}
        >
          {t("billing.checkout_return.success.body")}
        </p>
        <div style={{ marginTop: "26px" }}>
          <button
            type="button"
            className="billing-btn billing-btn--sage"
            onClick={onClose}
          >
            {t("billing.checkout_return.success.cta")}
          </button>
        </div>
      </div>
    </div>
  );
}
