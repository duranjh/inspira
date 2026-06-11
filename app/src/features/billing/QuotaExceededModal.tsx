// Inspira — Quota Exceeded modal (B9).
//
// Shared shell with seven content variants. Callers can either mount the
// component directly (passing `variant` as a prop) or fire it imperatively
// via `showQuotaModal(variant)` / `dismissQuotaModal()`. The imperative
// path self-mounts a React root in `<body>` so any surface — canvas cell,
// export menu, repo connector — can open the modal without wiring its own
// portal.
//
// Variants ending in audit_log_export / multi_reviewer upgrade the caller
// to Team (seats=5). The rest upgrade to Pro (seats=1).

import { useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { t } from "../../i18n";
import { HIDE_UPGRADE } from "../../lib/featureFlags";
import {
  billingApi,
  BillingNotConfiguredError,
  type BillingPeriod,
  type PlanSlug,
} from "./api";

export type QuotaVariant =
  | "fourth_project"
  | "third_paid_member"
  | "second_repo"
  | "custom_templates"
  | "cross_project"
  | "audit_log_export"
  | "multi_reviewer";

export type QuotaExceededModalProps = {
  variant: QuotaVariant | null;
  onClose: () => void;
  role?: "owner" | "admin" | "member";
};

const SUPPORT_EMAIL = "hello@tryinspira.com";
const MEMBER_CONTACT_EMAIL = "billing@tryinspira.com";

const TEAM_VARIANTS = new Set<QuotaVariant>([
  "audit_log_export",
  "multi_reviewer",
]);

function variantPlan(variant: QuotaVariant): PlanSlug {
  return TEAM_VARIANTS.has(variant) ? "team" : "pro";
}

function variantSeats(variant: QuotaVariant): number {
  return TEAM_VARIANTS.has(variant) ? 5 : 1;
}

export function QuotaExceededModal(props: QuotaExceededModalProps) {
  const { variant, onClose, role = "owner" } = props;
  const [loading, setLoading] = useState(false);
  const [notConfigured, setNotConfigured] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const modalRef = useRef<HTMLDivElement | null>(null);
  const priorFocus = useRef<HTMLElement | null>(null);

  // Reset ephemeral state + capture focus on variant change.
  useEffect(() => {
    if (!variant) return;
    priorFocus.current = (document.activeElement as HTMLElement | null) ?? null;
    setLoading(false);
    setNotConfigured(false);
    setErrorMsg(null);
    const h = window.setTimeout(() => {
      const root = modalRef.current;
      if (!root) return;
      const first = root.querySelector<HTMLElement>(
        'button:not([disabled]), a[href]',
      );
      first?.focus();
    }, 0);
    return () => {
      window.clearTimeout(h);
      const prev = priorFocus.current;
      if (prev && typeof prev.focus === "function") prev.focus();
    };
  }, [variant]);

  useEffect(() => {
    if (!variant) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [variant, onClose]);

  if (!variant) return null;

  // HIDE_UPGRADE: don't mount the modal at all. The Upgrade button is
  // its only meaningful action and `startCheckout()` would 501 on a
  // Stripe-dark deploy. Without the button, the user sees a "you hit
  // a limit" message with only a close action — bad UX. Cleaner to
  // suppress the cap-hit surface entirely until billing is wired live.
  if (HIDE_UPGRADE) return null;

  const plan = variantPlan(variant);
  const seats = variantSeats(variant);
  const period: BillingPeriod = "annual";

  const headlineId = `billing-quota-head-${variant}`;
  const eyebrow = t(`billing.quota.${variant}.eyebrow`);
  const headline = t(`billing.quota.${variant}.headline`);
  const body = t(`billing.quota.${variant}.body`);
  const limit = t(`billing.quota.${variant}.limit`);
  const cta = t(`billing.quota.${variant}.cta`);

  async function onUpgrade() {
    if (loading) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await billingApi.startCheckout({
        plan_slug: plan,
        period,
        seats,
      });
      window.location.assign(res.checkout.url);
    } catch (err) {
      if (err instanceof BillingNotConfiguredError) {
        setNotConfigured(true);
      } else {
        setErrorMsg(err instanceof Error ? err.message : String(err));
      }
      setLoading(false);
    }
  }

  const notConfiguredBody = t("billing.fallback.not_configured_body", {
    email: SUPPORT_EMAIL,
  });

  const isMember = role === "member";

  return (
    <div
      className="billing-modal-scrim"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="billing-modal billing-quota"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headlineId}
        ref={modalRef}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
        >
          ×
        </button>

        <p className="billing-eyebrow">{eyebrow}</p>
        <h3
          id={headlineId}
          className="billing-display billing-display--lg"
          style={{ marginTop: 4 }}
        >
          {headline}
        </h3>
        <p className="billing-serif" style={{ marginTop: 14 }}>
          {body}
        </p>
        <p className="billing-quota__limit">{limit}</p>

        <div className="billing-quota__actions">
          {isMember ? (
            <a
              href={`mailto:${MEMBER_CONTACT_EMAIL}`}
              className="billing-btn billing-btn--sage"
            >
              {t("billing.quota.cta_contact_owner")}
            </a>
          ) : (
            <button
              type="button"
              className="billing-btn billing-btn--sage"
              disabled={loading || notConfigured}
              onClick={() => {
                void onUpgrade();
              }}
            >
              {cta}
            </button>
          )}
          <button
            type="button"
            className="billing-btn billing-btn--link"
            onClick={onClose}
          >
            {t("billing.quota.cta_not_now")}
          </button>
        </div>

        {notConfigured && !isMember && (
          <p
            className="billing-serif billing-serif--sm billing-serif--dim"
            style={{ fontStyle: "italic", marginTop: 10 }}
          >
            {notConfiguredBody}
          </p>
        )}

        {errorMsg && (
          <p
            className="billing-status billing-status--error"
            style={{ marginTop: 10 }}
          >
            {errorMsg}
          </p>
        )}

        <p className="billing-quota__note">{t("billing.quota.member_note")}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Imperative mount — lets any surface open the modal without wiring a portal.
// ---------------------------------------------------------------------------

let hostRoot: Root | null = null;
let hostEl: HTMLDivElement | null = null;
let currentVariant: QuotaVariant | null = null;

function render() {
  if (typeof document === "undefined") return;
  if (!hostEl) {
    hostEl = document.createElement("div");
    hostEl.setAttribute("data-billing-quota-host", "");
    document.body.appendChild(hostEl);
    hostRoot = createRoot(hostEl);
  }
  hostRoot!.render(
    <QuotaExceededModal
      variant={currentVariant}
      onClose={dismissQuotaModal}
    />,
  );
}

export function showQuotaModal(v: QuotaVariant): void {
  currentVariant = v;
  render();
}

export function dismissQuotaModal(): void {
  currentVariant = null;
  render();
}

/** Intentional no-op. Kept exported for routes that want to reserve a
 *  stable mount-point inside React's tree; the imperative host above
 *  handles the actual rendering. */
export function QuotaModalHost(): null {
  return null;
}
