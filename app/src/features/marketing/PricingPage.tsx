// Inspira — public pricing page (/pricing).
//
// v5 layout: 4 tier cards (Free / Pro / Frontier / Enterprise) joined
// edge-to-edge with dashed dividers, a 14-day-free-trial reassurance
// line, an expandable 5-question FAQ, a 16-row comparison table, and
// a "Talk to us about custom pricing" mailto.
//
// Render mirrors `docs/product/design/v5-pivot/Inspira v5 Pivot (12).zip`
// → Marketing Teams.html lines 281–456 (Page 2 — /pricing). Tier copy
// drives off the shared `PLANS` constant in `./plans.ts` so the home +
// teams pricing teasers and this page never drift.
//
// Stripe activation (2026-05-12, Wave I): Pro + Frontier CTAs now redirect
// to Stripe Checkout via the existing billingApi.startCheckout flow. Free
// stays on signup/billing routing. Enterprise stays on mailto (talk-to-us
// only — no public price). Anonymous visitors hit /?signup=1 first so we
// don't expose the 401 path on the checkout endpoint.
//
// Billing period toggle: a "Monthly · Annual" segmented control above the
// tier grid swaps the Pro + Frontier price row between the monthly and
// annual variants (Free + Enterprise are unaffected — no annual concept).
// The toggle value is passed to startCheckout so the BE picks the right
// STRIPE_PRICE_ID_*_ANNUAL env var.

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { t } from "../../i18n";
import { billingApi, BillingNotConfiguredError } from "../billing/api";
import { api } from "../inspira/api";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import { PLANS, type MarketingPlanSlug } from "./plans";

type BillingPeriod = "monthly" | "annual";
import "./marketing.css";

const TIER_CHIP_VARIANT: Record<MarketingPlanSlug, string> = {
  free: "pr-tier__chip--sage-outline",
  pro: "pr-tier__chip--gold",
  team: "pr-tier__chip--rust-outline",
  enterprise: "pr-tier__chip--sage-filled",
};

const TIER_CTA_VARIANT: Record<MarketingPlanSlug, string> = {
  free: "pr-tier__cta--ghost",
  pro: "pr-tier__cta--gold",
  team: "pr-tier__cta--gold",
  enterprise: "pr-tier__cta--sage pr-tier__cta--large",
};

// Static comparison-table rows. Cell values are short product specs
// (caps, "✓", "—", support tier) so they live alongside the rendering
// rather than the i18n catalog. The row labels still i18n through
// `marketing.pricing.compare.row.*`. Support tier cells render
// translated values via the support_<slug> keys.
type CompareRow = {
  rowKey: string;
  cells: [free: string, pro: string, team: string, enterprise: string];
};

const COMPARE_ROWS: CompareRow[] = [
  { rowKey: "workspaces", cells: ["1", "1", "1", "Unlimited"] },
  { rowKey: "users", cells: ["1", "1", "5", "Unlimited"] },
  { rowKey: "projects", cells: ["5", "Unlimited", "Unlimited", "Unlimited"] },
  { rowKey: "artifacts", cells: ["—", "15", "100", "Unlimited"] },
  { rowKey: "csv", cells: ["—", "✓", "✓", "✓"] },
  { rowKey: "linear", cells: ["—", "—", "✓", "✓"] },
  { rowKey: "github", cells: ["—", "—", "✓", "✓"] },
  { rowKey: "opus", cells: ["—", "—", "✓", "✓"] },
  { rowKey: "orchestrator", cells: ["—", "—", "—", "✓"] },
  { rowKey: "provenance", cells: ["—", "—", "✓", "✓"] },
  { rowKey: "cascade", cells: ["—", "—", "✓", "✓"] },
  { rowKey: "summary", cells: ["—", "—", "—", "✓"] },
  { rowKey: "export", cells: ["—", "—", "—", "✓"] },
  { rowKey: "byok", cells: ["—", "✓", "✓", "✓"] },
  { rowKey: "sso", cells: ["—", "—", "—", "✓"] },
];

export function PricingPage() {
  const navigate = useNavigate();

  // T2.2: probe /api/auth/me on mount so authed visitors don't get
  // bounced through the signup flow when they click "Get Pro" or
  // "Start free" — they should land on /billing or back on /app
  // (depending on the plan). Anonymous visitors keep the original
  // flow that opens the signup modal on the landing page.
  const [authed, setAuthed] = useState<boolean>(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.me();
        if (!cancelled) setAuthed(!me.is_system);
      } catch {
        if (!cancelled) setAuthed(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 2026-05-12 (Wave I): default to annual to anchor on the cheaper
  // sticker price; ARR predictability is a separate win. Toggle persists
  // for the session only — no localStorage so a refresh resets to annual
  // (intentional: keeps the funnel anchored).
  const [billingPeriod, setBillingPeriod] = useState<BillingPeriod>("annual");
  const [checkoutBusy, setCheckoutBusy] = useState<MarketingPlanSlug | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);

  const handlers = useMemo<Record<MarketingPlanSlug, () => void | Promise<void>>>(() => {
    const enterpriseMailto = () => {
      window.location.href =
        "mailto:hello@tryinspira.com?subject=Inspira%20Enterprise%20pricing";
    };
    // Anonymous visitors hit signup first so we don't 401 on the
    // checkout endpoint. The signup flow's post-auth redirect lands
    // back on /pricing where they can re-click to enter Stripe.
    const startCheckoutOrSignup = async (
      plan_slug: "pro" | "team",
    ): Promise<void> => {
      if (!authed) {
        navigate("/?signup=1");
        return;
      }
      setCheckoutBusy(plan_slug);
      setCheckoutError(null);
      try {
        const res = await billingApi.startCheckout({
          plan_slug,
          period: billingPeriod,
          seats: 1,
        });
        // Hard-redirect to Stripe Checkout. Stripe's success_url +
        // cancel_url bring the user back to /billing or /pricing.
        window.location.assign(res.checkout.url);
      } catch (err) {
        if (err instanceof BillingNotConfiguredError) {
          // Stripe Live not configured on this deployment yet. Surface
          // a clean error and route to mailto so the partner can still
          // reach the founder.
          setCheckoutError(
            "Checkout is not available yet — please reach us at hello@tryinspira.com.",
          );
          return;
        }
        setCheckoutError(
          "Couldn't start checkout. Please try again or email hello@tryinspira.com.",
        );
      } finally {
        setCheckoutBusy(null);
      }
    };
    return {
      free: () => (authed ? navigate("/billing") : navigate("/?signup=1")),
      pro: () => startCheckoutOrSignup("pro"),
      team: () => startCheckoutOrSignup("team"),
      enterprise: enterpriseMailto,
    };
  }, [authed, billingPeriod, navigate]);

  const [openFaq, setOpenFaq] = useState<number | null>(null);

  return (
    <MarketingLayout>
      <Head
        title={t("marketing.pricing_page.meta.title")}
        description={t("marketing.pricing_page.meta.description")}
        canonical="https://tryinspira.com/pricing"
        ogImage="https://tryinspira.com/og/og-pricing.png"
      />

      <section className="pr-page-hero" aria-labelledby="pricing-hero-title">
        <h1 id="pricing-hero-title">{t("marketing.pricing.heading")}</h1>
        <p>{t("marketing.pricing.lede")}</p>
      </section>

      {/* Billing-period toggle (above the tier grid). Only swaps the
          Pro + Frontier rows — Free + Enterprise ignore period. */}
      <section
        className="mk-section pr-billing-toggle-wrap"
        aria-label={t("marketing.pricing.billing_period.aria_label")}
      >
        <div
          className="pr-billing-toggle"
          role="group"
          aria-label={t("marketing.pricing.billing_period.aria_label")}
        >
          <button
            type="button"
            className={
              "pr-billing-toggle__opt" +
              (billingPeriod === "monthly" ? " pr-billing-toggle__opt--active" : "")
            }
            onClick={() => setBillingPeriod("monthly")}
            aria-pressed={billingPeriod === "monthly"}
          >
            {t("marketing.pricing.billing_period.monthly")}
          </button>
          <button
            type="button"
            className={
              "pr-billing-toggle__opt" +
              (billingPeriod === "annual" ? " pr-billing-toggle__opt--active" : "")
            }
            onClick={() => setBillingPeriod("annual")}
            aria-pressed={billingPeriod === "annual"}
          >
            {t("marketing.pricing.billing_period.annual")}
            <span className="pr-billing-toggle__savings">
              {" "}
              · {t("marketing.pricing.billing_period.annual_savings")}
            </span>
          </button>
        </div>
      </section>

      {/* Optional error surface (Stripe-not-configured, network fail, etc). */}
      {checkoutError ? (
        <div className="pr-checkout-error" role="alert">
          {checkoutError}
        </div>
      ) : null}

      {/* Tier cards */}
      <section className="mk-section" aria-label={t("marketing.pricing.heading")}>
        <div className="pr-tiers" role="list">
          {PLANS.map((plan) => {
            // Per-period price selection: annual variant when the toggle
            // is on AND the plan has an annual variant configured. Free
            // + Enterprise don't have annual variants → fall through to
            // their monthly/single-shape keys.
            const showAnnual =
              billingPeriod === "annual" && !!plan.priceAnnualKey;
            const effectivePriceKey = showAnnual && plan.priceAnnualKey
              ? plan.priceAnnualKey
              : plan.priceKey;
            const effectivePriceUnitKey =
              showAnnual && plan.priceUnitAnnualKey
                ? plan.priceUnitAnnualKey
                : plan.priceUnitKey;
            const isBusy = checkoutBusy === plan.slug;
            return (
            <article
              key={plan.slug}
              role="listitem"
              className={
                "pr-tier" +
                (plan.slug === "enterprise" ? " pr-tier--enterprise" : "")
              }
              aria-labelledby={`pricing-card-${plan.slug}-chip`}
            >
              {plan.slug === "enterprise" ? (
                <span className="pr-tier__outline" aria-hidden="true" />
              ) : null}
              <span
                id={`pricing-card-${plan.slug}-chip`}
                className={`pr-tier__chip ${TIER_CHIP_VARIANT[plan.slug]}`}
              >
                {t(plan.chipKey)}
              </span>
              <div className="pr-tier__price">{t(effectivePriceKey)}</div>
              {effectivePriceUnitKey ? (
                <div className="pr-tier__interval">
                  {t(effectivePriceUnitKey)}
                </div>
              ) : null}
              <div className="pr-tier__subhead">{t(plan.taglineKey)}</div>
              <ul className="pr-tier__features">
                {plan.includesLineKey ? (
                  <li className="pr-tier__feat pr-tier__feat--includes">
                    {t(plan.includesLineKey)}
                  </li>
                ) : null}
                {plan.bulletKeys.map((bulletKey) => (
                  <li key={bulletKey} className="pr-tier__feat">
                    {t(bulletKey)}
                  </li>
                ))}
                {plan.noteKey ? (
                  <li className="pr-tier__feat pr-tier__feat--note">
                    {t(plan.noteKey)}
                  </li>
                ) : null}
              </ul>
              <button
                type="button"
                onClick={() => {
                  const r = handlers[plan.slug]();
                  if (r && typeof (r as Promise<void>).then === "function") {
                    void r;
                  }
                }}
                disabled={isBusy}
                className={`pr-tier__cta ${TIER_CTA_VARIANT[plan.slug]}`}
              >
                {isBusy
                  ? "Redirecting to Stripe…"
                  : `${t(plan.ctaKey)} →`}
              </button>
            </article>
            );
          })}
        </div>

        <p className="pr-reassurance">
          {t("marketing.pricing.reassurance")}
        </p>
      </section>

      {/* FAQ */}
      <section className="mk-section" aria-labelledby="pricing-faq-title">
        <div className="pr-faq">
          <div className="pr-faq__title" id="pricing-faq-title">
            {t("marketing.pricing.faq.heading")}
          </div>
          {[1, 2, 3, 4, 5].map((i) => {
            const open = openFaq === i;
            const qKey = `marketing.pricing.faq.q${i}`;
            const aKey = `marketing.pricing.faq.a${i}`;
            return (
              <div
                key={i}
                className={
                  "pr-faq__item" + (open ? " pr-faq__item--open" : "")
                }
              >
                <button
                  type="button"
                  className="pr-faq__q"
                  aria-expanded={open}
                  onClick={() => setOpenFaq(open ? null : i)}
                >
                  <span>{t(qKey)}</span>
                  <span className="pr-faq__chev" aria-hidden="true">
                    ▸
                  </span>
                </button>
                {open ? <div className="pr-faq__a">{t(aKey)}</div> : null}
              </div>
            );
          })}
        </div>
      </section>

      {/* Comparison table */}
      <section
        className="pr-compare"
        aria-labelledby="pricing-compare-title"
      >
        <div className="pr-compare__title" id="pricing-compare-title">
          {t("marketing.pricing.compare.title")}
        </div>
        <table>
          <thead>
            <tr>
              <th>{t("marketing.pricing.compare.col_feature")}</th>
              <th style={{ textAlign: "center" }}>
                {t("marketing.pricing.free.name")}
              </th>
              <th style={{ textAlign: "center" }}>
                {t("marketing.pricing.pro.name")}
              </th>
              <th style={{ textAlign: "center" }}>
                {t("marketing.pricing.team.name")}
              </th>
              <th style={{ textAlign: "center" }}>
                {t("marketing.pricing.enterprise.name")}
              </th>
            </tr>
          </thead>
          <tbody>
            {COMPARE_ROWS.map((row) => (
              <tr key={row.rowKey}>
                <td className="feat-name">
                  {t(`marketing.pricing.compare.row.${row.rowKey}`)}
                </td>
                {row.cells.map((cell, idx) => (
                  <td
                    key={idx}
                    className={
                      cell === "✓" ? "check" : cell === "—" ? "dash" : ""
                    }
                    style={{ textAlign: "center" }}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
            {/* Support row pulls per-tier i18n labels */}
            <tr>
              <td className="feat-name">
                {t("marketing.pricing.compare.row.support")}
              </td>
              <td style={{ textAlign: "center" }}>
                {t("marketing.pricing.compare.support_free")}
              </td>
              <td style={{ textAlign: "center" }}>
                {t("marketing.pricing.compare.support_pro")}
              </td>
              <td style={{ textAlign: "center" }}>
                {t("marketing.pricing.compare.support_team")}
              </td>
              <td style={{ textAlign: "center" }}>
                {t("marketing.pricing.compare.support_enterprise")}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <div className="pr-mailto">
        <a href="mailto:sales@tryinspira.com">
          {t("marketing.pricing.contact_custom")}
        </a>
      </div>
    </MarketingLayout>
  );
}
