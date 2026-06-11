// Inspira — Checkout hand-off (B3).
//
// Transitional page that summarises the order, collects the ToS checkbox,
// and hands the user off to Stripe's hosted checkout. No card fields live
// here — the actual payment happens on stripe.com and we redirect via
// window.location.assign() when the POST /checkout call resolves.
//
// The page is rendered as a fixed overlay (`.billing-page`) on top of the
// current route so it feels like a quiet pause rather than a full page
// transition. Esc closes via onBack; body scroll is locked while mounted.

import { useEffect, useState, type ReactNode } from "react";
import { t } from "../../i18n";
import {
  billingApi,
  BillingNotConfiguredError,
  type BillingPeriod,
  type PlanSlug,
} from "./api";

export type CheckoutFormProps = {
  plan: PlanSlug;
  period: BillingPeriod;
  seats: number;
  onBack: () => void;
};

const SUPPORT_EMAIL = "hello@tryinspira.com";

// Dollar amounts rendered in the transitional ledger. The real invoice is
// built server-side at checkout; these figures mirror the hi-fi so reviewers
// see a stable number while the backend is stubbed.
const LEDGER = {
  pro: {
    annual: { line: 288, proration: 18, tax: 54, total: 324 },
    monthly: { line: 29, proration: 0, tax: 6, total: 35 },
  },
  team: {
    annual: { perSeat: 19 * 12, taxRate: 0.2 },
    monthly: { perSeat: 24, taxRate: 0.2 },
  },
} as const;

function formatDollars(value: number) {
  return `$${value.toFixed(2)}`;
}

function titleCasePlan(plan: PlanSlug): string {
  if (plan === "pro") return t("billing.plans.pro.name");
  if (plan === "team") return t("billing.plans.team.name");
  return t("billing.plans.free.name");
}

/** Splits a string containing `<a>…</a>` and `<b>…</b>` anchor placeholders
 *  and renders them as actual links — avoids dangerouslySetInnerHTML while
 *  still honoring translator-authored markup. The tag order is fixed in the
 *  English source ("<a>Terms of Service</a> … <b>Billing Terms</b>.").*/
function renderAcceptLabel(raw: string): ReactNode {
  const parts: ReactNode[] = [];
  const rx = /<(a|b)>(.*?)<\/\1>/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  let idx = 0;
  while ((match = rx.exec(raw)) !== null) {
    if (match.index > cursor) {
      parts.push(raw.slice(cursor, match.index));
    }
    const tag = match[1];
    const inner = match[2];
    const href = tag === "a" ? "/terms" : "/terms#billing";
    parts.push(
      <a key={`${tag}-${idx++}`} href={href}>
        {inner}
      </a>,
    );
    cursor = match.index + match[0].length;
  }
  if (cursor < raw.length) {
    parts.push(raw.slice(cursor));
  }
  return <>{parts}</>;
}

export function CheckoutForm(props: CheckoutFormProps) {
  const { plan, period, seats, onBack } = props;
  const [accepted, setAccepted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [notConfigured, setNotConfigured] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Body scroll-lock + Esc closes via onBack.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onBack();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onBack]);

  // Transitional ledger numbers. Fixed for pro; computed simply for team
  // so the UI stays arithmetic-truthful while the server total is stubbed.
  const ledger = (() => {
    if (plan === "pro") {
      return LEDGER.pro[period];
    }
    if (plan === "team") {
      const row = LEDGER.team[period];
      const line = row.perSeat * seats;
      const tax = Math.round(line * row.taxRate);
      return { line, proration: 0, tax, total: line + tax };
    }
    // Free / unknown — no charge.
    return { line: 0, proration: 0, tax: 0, total: 0 };
  })();

  const lineDescription = (() => {
    const pricePerSeat =
      plan === "pro"
        ? period === "annual"
          ? "$288 / yr"
          : "$29 / mo"
        : period === "annual"
          ? "$228 / yr"
          : "$24 / mo";
    return t("billing.checkout.ledger_line", {
      plan: titleCasePlan(plan),
      seats,
      price: pricePerSeat,
    });
  })();

  const periodLabel =
    period === "annual"
      ? t("billing.checkout.period_annual")
      : t("billing.checkout.period_monthly");

  async function onContinue() {
    if (!accepted || loading) return;
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

  const acceptLabel = renderAcceptLabel(t("billing.checkout.accept_tos"));
  const notConfiguredBody = t("billing.checkout.fallback_body", {
    email: SUPPORT_EMAIL,
  });
  const chargedNote = t("billing.checkout.charged_note", {
    card: "Visa ending 4242",
    date: "March 18, 2027",
  });

  return (
    <div className="billing-page" role="dialog" aria-modal="true">
      <header className="billing-page__topbar">
        <span className="billing-page__brand">Inspira</span>
        <span className="billing-page__crumbs">
          {t("billing.eyebrow.billing")} <em>·</em>{" "}
          {t("billing.action.upgrade_to_" + (plan === "team" ? "team" : "pro"))}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="billing-page__close"
          aria-label={t("billing.checkout.back")}
          title={t("billing.checkout.back")}
          onClick={onBack}
        >
          ×
        </button>
      </header>

      <div className="billing-page__inner">
        <div className="billing-checkout">
          {/* Left column — order summary ------------------------------ */}
          <div className="billing-checkout__summary">
            <div className="billing-card billing-hero">
              <p className="billing-eyebrow">
                {t("billing.checkout.eyebrow")}
              </p>
              <h3 className="billing-display billing-display--lg">
                {t("billing.checkout.title", {
                  plan: titleCasePlan(plan),
                  period: periodLabel,
                })}
              </h3>
              <p
                className="billing-serif billing-serif--dim"
                style={{ marginTop: 8 }}
              >
                {t("billing.checkout.sub")}
              </p>
            </div>

            <div className="billing-card">
              <div className="billing-ledger">
                <span>{lineDescription}</span>
                <span>{formatDollars(ledger.line)}</span>

                {ledger.proration > 0 && (
                  <>
                    <span className="billing-ledger__label-dim">
                      {t("billing.checkout.ledger_proration")}
                    </span>
                    <span className="billing-ledger__amt-dim">
                      − {formatDollars(ledger.proration)}
                    </span>
                  </>
                )}

                <span className="billing-ledger__label-dim">
                  {t("billing.checkout.ledger_tax")}
                </span>
                <span className="billing-ledger__amt-dim">
                  {formatDollars(ledger.tax)}
                </span>

                <span className="billing-ledger__total-label">
                  {t("billing.checkout.ledger_total")}
                </span>
                <span className="billing-ledger__total-amt">
                  {formatDollars(ledger.total)}
                </span>
              </div>
            </div>

            <div className="billing-card billing-card--quiet">
              <p className="billing-serif billing-serif--sm">
                <em>{chargedNote}</em>
              </p>
            </div>
          </div>

          {/* Right column — continue / fallback ----------------------- */}
          <div className="billing-checkout__side">
            {!notConfigured ? (
              <div className="billing-card billing-hero">
                <p className="billing-eyebrow">
                  {t("billing.checkout.continue_title")}
                </p>
                <p
                  className="billing-serif"
                  style={{ marginTop: 8 }}
                >
                  {t("billing.checkout.continue_body")}
                </p>

                <label className="billing-accept" style={{ marginTop: 20 }}>
                  <input
                    type="checkbox"
                    checked={accepted}
                    onChange={(e) => setAccepted(e.target.checked)}
                  />
                  <span>{acceptLabel}</span>
                </label>

                <button
                  type="button"
                  className="billing-btn billing-btn--sage"
                  style={{
                    marginTop: 20,
                    width: "100%",
                    justifyContent: "center",
                  }}
                  disabled={!accepted || loading}
                  onClick={() => {
                    void onContinue();
                  }}
                >
                  {t("billing.checkout.continue_cta")}
                </button>

                {errorMsg && (
                  <p
                    className="billing-status billing-status--error"
                    style={{ marginTop: 10 }}
                  >
                    {errorMsg}
                  </p>
                )}

                <p
                  className="billing-serif billing-serif--sm billing-serif--dim"
                  style={{ marginTop: 14, fontStyle: "italic" }}
                >
                  {t("billing.checkout.refund_note")}
                </p>
              </div>
            ) : (
              <div className="billing-card billing-card--quiet">
                <p
                  className="billing-eyebrow"
                  style={{ color: "var(--rust)" }}
                >
                  {t("billing.checkout.fallback_eyebrow")}
                </p>
                <p
                  className="billing-serif billing-serif--sm"
                  style={{ marginTop: 6 }}
                >
                  <em>{notConfiguredBody}</em>
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
