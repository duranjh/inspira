// Inspira — Switch to annual modal (Tier 3, B11).
//
// An offer, not an alarm. Shown at most once every 30 days to Pro monthly
// customers who have been on the plan for at least 30 days. No countdown,
// no urgency copy. Dismissing sets a localStorage timestamp so the offer
// stays quiet for another 30 days.
//
// Trigger helper `shouldShowSwitchToAnnual` lives alongside the component
// so the call-site (BillingOverviewPage) can decide whether to mount it.

import { useCallback, useEffect } from "react";

import { billingApi, type Subscription } from "./api";
import { t } from "../../i18n";

export const SWITCH_TO_ANNUAL_STORAGE_KEY =
  "inspira_switch_annual_dismissed_at";

const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000;

/** Returns true when the Pro-monthly → annual offer is eligible to show.
 *  Guards: plan must be Pro, billing period must be monthly, subscription
 *  start must be ≥30 days old, and the user must not have dismissed the
 *  offer within the last 30 days. Bad clock input is treated as not
 *  showing the offer rather than exposing the customer to a surprise. */
export function shouldShowSwitchToAnnual(
  subscription: Subscription | null | undefined,
  now: number = Date.now(),
): boolean {
  if (!subscription) return false;
  if (subscription.plan?.slug !== "pro") return false;
  if (subscription.billing_period !== "monthly") return false;
  const startedAtRaw = (subscription as unknown as { started_at?: string })
    .started_at;
  if (startedAtRaw) {
    const startedAtMs = new Date(startedAtRaw).getTime();
    if (!Number.isFinite(startedAtMs)) return false;
    if (now - startedAtMs < THIRTY_DAYS_MS) return false;
  }
  try {
    const dismissed = window.localStorage.getItem(
      SWITCH_TO_ANNUAL_STORAGE_KEY,
    );
    if (dismissed) {
      const dismissedMs = Number(dismissed);
      if (Number.isFinite(dismissedMs) && now - dismissedMs < THIRTY_DAYS_MS) {
        return false;
      }
    }
  } catch {
    // localStorage unavailable — treat as never dismissed.
  }
  return true;
}

/** Writes the current timestamp into localStorage so the offer stays
 *  quiet for another 30 days. Safe on environments without storage. */
export function dismissSwitchToAnnualOffer(now: number = Date.now()): void {
  try {
    window.localStorage.setItem(
      SWITCH_TO_ANNUAL_STORAGE_KEY,
      String(now),
    );
  } catch {
    /* storage disabled — ignore */
  }
}

export type SwitchToAnnualModalProps = {
  open: boolean;
  onClose: () => void;
  onSwitched?: () => void;
};

export function SwitchToAnnualModal({
  open,
  onClose,
  onSwitched,
}: SwitchToAnnualModalProps) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleDismiss = useCallback(() => {
    dismissSwitchToAnnualOffer();
    onClose();
  }, [onClose]);

  const handleSwitch = useCallback(async () => {
    try {
      const res = await billingApi.startCheckout({
        plan_slug: "pro",
        period: "annual",
      });
      if (res.checkout?.url) {
        window.location.assign(res.checkout.url);
        return;
      }
    } catch {
      // Fall through to the caller's handler so they can surface an
      // error banner. We still consider the offer "addressed".
    }
    dismissSwitchToAnnualOffer();
    onSwitched?.();
    onClose();
  }, [onClose, onSwitched]);

  if (!open) return null;

  return (
    <div
      className="billing-modal-scrim"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleDismiss();
      }}
    >
      <div
        className="billing-modal billing-modal--sm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-annual-title"
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={handleDismiss}
        >
          {"\u00D7"}
        </button>
        <p className="billing-eyebrow">{t("billing.tier3.annual.eyebrow")}</p>
        <h3
          id="billing-annual-title"
          className="billing-display billing-display--lg"
        >
          {t("billing.tier3.annual.title")}
        </h3>
        <p className="billing-serif" style={{ marginTop: 10 }}>
          {t("billing.tier3.annual.body")}
        </p>
        <div className="billing-annual-math">
          <span className="billing-annual-math__row">
            {t("billing.tier3.annual.math_monthly")}
          </span>
          <span className="billing-annual-math__row">
            {t("billing.tier3.annual.math_annual")}
          </span>
          <span className="billing-annual-math__save">
            {t("billing.tier3.annual.math_savings")}
          </span>
        </div>
        <div
          className="billing-pm__actions"
          style={{ marginTop: 22, justifyContent: "flex-end" }}
        >
          <button
            type="button"
            className="billing-btn billing-btn--ghost"
            onClick={handleDismiss}
          >
            {t("billing.tier3.annual.cta_dismiss")}
          </button>
          <button
            type="button"
            className="billing-btn billing-btn--sage"
            onClick={handleSwitch}
          >
            {t("billing.tier3.annual.cta_switch")}
          </button>
        </div>
      </div>
    </div>
  );
}
