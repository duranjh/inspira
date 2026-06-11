// Inspira — Plan Comparison modal (B2).
//
// Three plan cards + feature comparison table. Pro is the featured card
// (sage border). Team has a seat stepper. Period toggle (annual/monthly)
// swaps the Pro and Team prices. CTAs route through billingApi.startCheckout
// → window.location.assign(url). BillingNotConfigured renders a warm
// inline fallback rather than a hard error.
//
// Pricing numbers below are mirrored from the hi-fi (tier1-hifi.html) and
// the i18n bundle; the backend's authoritative numbers live in plans.py.
// The per-seat arithmetic shown in the Team card is UI-only so the user
// sees the math before the actual server-side invoice is built.

import { useEffect, useMemo, useRef, useState } from "react";
import { t } from "../../i18n";
import {
  billingApi,
  BillingNotConfiguredError,
  type BillingPeriod,
  type PlanSlug,
} from "./api";

export type PlanComparisonModalProps = {
  open: boolean;
  onClose: () => void;
  currentPlan?: PlanSlug;
  initialPeriod?: BillingPeriod;
  initialSeats?: number;
  onChoose?: (choice: {
    plan: PlanSlug;
    period: BillingPeriod;
    seats: number;
  }) => void;
  readOnly?: boolean;
};

const SUPPORT_EMAIL = "hello@tryinspira.com";

// Whole-dollar prices rendered in the stepper math. Pricing aligned
// with billing source-of-truth in services/planning_studio_service/
// billing/plans.py. Frontier (slug "team", rebranded 2026-04-28)
// has no annual discount — both fields are the same monthly rate.
const PRICE = {
  pro: { annual: 24, monthly: 29 },
  team: { annual: 200, monthly: 200 },
} as const;

type CheckoutState =
  | { kind: "idle" }
  | { kind: "loading"; plan: PlanSlug }
  | { kind: "not_configured" }
  | { kind: "error"; message: string };

/** Split the plans.foot string (contains "{email}") and render as a proper
 *  anchor. The raw copy uses `{email}` as its only interpolation — we slot
 *  the actual <a> in place of that placeholder. */
function renderFoot(email: string) {
  const raw = t("billing.plans.foot", { email });
  const idx = raw.indexOf(email);
  if (idx < 0) return raw;
  const before = raw.slice(0, idx);
  const after = raw.slice(idx + email.length);
  return (
    <>
      {before}
      <a href={`mailto:${email}`}>{email}</a>
      {after}
    </>
  );
}

export function PlanComparisonModal(props: PlanComparisonModalProps) {
  const {
    open,
    onClose,
    currentPlan,
    initialPeriod = "annual",
    initialSeats = 1,
    onChoose,
    readOnly,
  } = props;

  const [period, setPeriod] = useState<BillingPeriod>(initialPeriod);
  const [seats, setSeats] = useState<number>(initialSeats);
  const [checkout, setCheckout] = useState<CheckoutState>({ kind: "idle" });

  const modalRef = useRef<HTMLDivElement | null>(null);
  const firstBtnRef = useRef<HTMLButtonElement | null>(null);
  const priorFocus = useRef<HTMLElement | null>(null);

  // Reset ephemeral state + capture prior focus on open.
  useEffect(() => {
    if (!open) return;
    priorFocus.current = (document.activeElement as HTMLElement | null) ?? null;
    setPeriod(initialPeriod);
    setSeats(initialSeats);
    setCheckout({ kind: "idle" });
    // focus first button after mount
    const h = window.setTimeout(() => {
      firstBtnRef.current?.focus();
    }, 0);
    return () => {
      window.clearTimeout(h);
      // Restore prior focus on close.
      const prev = priorFocus.current;
      if (prev && typeof prev.focus === "function") prev.focus();
    };
  }, [open, initialPeriod, initialSeats]);

  // Esc closes + body scroll lock + simple focus trap.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const root = modalRef.current;
      if (!root) return;
      const focusables = root.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  const teamTotals = useMemo(() => {
    const perSeat = PRICE.team[period];
    const total = perSeat * seats;
    return { perSeat, total };
  }, [period, seats]);

  if (!open) return null;

  async function startCheckout(plan: PlanSlug) {
    onChoose?.({
      plan,
      period,
      seats: plan === "team" ? seats : 1,
    });
    setCheckout({ kind: "loading", plan });
    try {
      const res = await billingApi.startCheckout({
        plan_slug: plan,
        period,
        seats: plan === "team" ? seats : 1,
      });
      window.location.assign(res.checkout.url);
    } catch (err) {
      if (err instanceof BillingNotConfiguredError) {
        setCheckout({ kind: "not_configured" });
      } else {
        setCheckout({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  const isLoadingFor = (plan: PlanSlug) =>
    checkout.kind === "loading" && checkout.plan === plan;

  // Pro price display ------------------------------------------------
  const proPrice =
    period === "annual"
      ? t("billing.plans.pro.price_annual")
      : t("billing.plans.pro.price_monthly");
  const proUnit =
    period === "annual"
      ? t("billing.plans.pro.unit_annual")
      : t("billing.plans.pro.unit_monthly");
  const teamPrice =
    period === "annual"
      ? t("billing.plans.team.price_annual")
      : t("billing.plans.team.price_monthly");
  const teamUnit =
    period === "annual"
      ? t("billing.plans.team.unit_annual")
      : t("billing.plans.team.unit_monthly");

  // Free CTA ---------------------------------------------------------
  const freeCtaLabel =
    currentPlan === "free"
      ? t("billing.plans.free.cta_current")
      : t("billing.plans.free.cta_downgrade");
  const freeCtaDisabled = readOnly || currentPlan === "free";

  // Pro CTA ----------------------------------------------------------
  const proIsCurrent = currentPlan === "pro";
  const proCtaLabel = readOnly
    ? t("billing.action.contact_owner")
    : proIsCurrent
      ? t("billing.plans.pro.cta_current")
      : t("billing.plans.pro.cta");
  const proCtaDisabled =
    readOnly || proIsCurrent || isLoadingFor("pro");

  // Team CTA ---------------------------------------------------------
  const teamIsCurrent = currentPlan === "team";
  const teamCtaLabel = readOnly
    ? t("billing.action.contact_owner")
    : teamIsCurrent
      ? t("billing.plans.team.cta_current")
      : t("billing.plans.team.cta");
  const teamCtaDisabled =
    readOnly || teamIsCurrent || isLoadingFor("team");

  const notConfiguredBody = t("billing.fallback.not_configured_body", {
    email: SUPPORT_EMAIL,
  });

  return (
    <div
      className="billing-modal-scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        className="billing-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-plans-heading"
        ref={modalRef}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
          ref={firstBtnRef}
        >
          ×
        </button>

        <div className="billing-modal__head">
          <div>
            <p className="billing-eyebrow">{t("billing.eyebrow.plans")}</p>
            <h3
              id="billing-plans-heading"
              className="billing-display billing-display--lg"
            >
              {t("billing.plans.heading")}
            </h3>
            <p className="billing-serif billing-serif--dim">
              {t("billing.plans.sub")}
            </p>
          </div>
          <div
            className="billing-toggle"
            role="group"
            aria-label={t("billing.eyebrow.plans")}
          >
            <button
              type="button"
              aria-pressed={period === "annual"}
              onClick={() => setPeriod("annual")}
            >
              {t("billing.plans.toggle_annual")}
            </button>
            <button
              type="button"
              aria-pressed={period === "monthly"}
              onClick={() => setPeriod("monthly")}
            >
              {t("billing.plans.toggle_monthly")}
            </button>
          </div>
        </div>

        <div className="billing-plans">
          {/* Free -------------------------------------------------- */}
          <article className="billing-plan">
            <h4 className="billing-plan__name">
              {t("billing.plans.free.name")}
            </h4>
            <p className="billing-plan__tagline">
              {t("billing.plans.free.tagline")}
            </p>
            <div className="billing-plan__price">
              <span className="billing-plan__price-amt">
                {t("billing.plans.free.price")}
              </span>
              <span className="billing-plan__price-unit">
                {t("billing.plans.free.unit")}
              </span>
            </div>
            <ul className="billing-plan__list">
              <li>{t("billing.plans.free.bullet_1")}</li>
              <li>{t("billing.plans.free.bullet_2")}</li>
              <li>{t("billing.plans.free.bullet_3")}</li>
              <li>{t("billing.plans.free.bullet_4")}</li>
              <li>{t("billing.plans.free.bullet_5")}</li>
            </ul>
            <div className="billing-plan__cta">
              <button
                type="button"
                className="billing-btn billing-btn--ghost"
                disabled={freeCtaDisabled}
              >
                {readOnly
                  ? t("billing.action.contact_owner")
                  : freeCtaLabel}
              </button>
            </div>
          </article>

          {/* Pro --------------------------------------------------- */}
          <article className="billing-plan billing-plan--featured">
            <span className="billing-plan__eyebrow">
              {t("billing.plans.featured_badge")}
            </span>
            <h4 className="billing-plan__name">
              {t("billing.plans.pro.name")}
            </h4>
            <p className="billing-plan__tagline">
              {t("billing.plans.pro.tagline")}
            </p>
            <div className="billing-plan__price">
              <span className="billing-plan__price-amt">{proPrice}</span>
              <span className="billing-plan__price-unit">{proUnit}</span>
            </div>
            <ul className="billing-plan__list">
              <li>{t("billing.plans.pro.bullet_1")}</li>
              <li>{t("billing.plans.pro.bullet_2")}</li>
              <li>{t("billing.plans.pro.bullet_3")}</li>
              <li>{t("billing.plans.pro.bullet_4")}</li>
              <li>{t("billing.plans.pro.bullet_5")}</li>
              <li>{t("billing.plans.pro.bullet_6")}</li>
            </ul>
            <div className="billing-plan__cta">
              <button
                type="button"
                className={
                  proIsCurrent
                    ? "billing-btn billing-btn--ghost"
                    : "billing-btn billing-btn--sage"
                }
                disabled={proCtaDisabled}
                onClick={() => {
                  if (readOnly || proIsCurrent) return;
                  void startCheckout("pro");
                }}
              >
                {proCtaLabel}
              </button>
            </div>
            {checkout.kind === "not_configured" && !readOnly && (
              <p
                className="billing-serif billing-serif--sm billing-serif--dim"
                style={{ fontStyle: "italic", marginTop: 6 }}
              >
                {notConfiguredBody}
              </p>
            )}
          </article>

          {/* Team -------------------------------------------------- */}
          <article className="billing-plan">
            <h4 className="billing-plan__name">
              {t("billing.plans.team.name")}
            </h4>
            <p className="billing-plan__tagline">
              {t("billing.plans.team.tagline")}
            </p>
            <div className="billing-plan__price">
              <span className="billing-plan__price-amt">{teamPrice}</span>
              <span className="billing-plan__price-unit">{teamUnit}</span>
            </div>
            <ul className="billing-plan__list">
              <li>{t("billing.plans.team.bullet_1")}</li>
              <li>{t("billing.plans.team.bullet_2")}</li>
              <li>{t("billing.plans.team.bullet_3")}</li>
              <li>{t("billing.plans.team.bullet_4")}</li>
              <li>{t("billing.plans.team.bullet_5")}</li>
            </ul>
            {/* 2026-04-28 Frontier rebrand: per-seat stepper removed.
                Frontier is an individual-power-user plan ($200/mo flat).
                Future multi-seat collaboration ships as a separate Team
                plan; the stepper UI + seats_* i18n keys come back then. */}
            <div className="billing-plan__cta">
              <button
                type="button"
                className="billing-btn billing-btn--ghost"
                disabled={teamCtaDisabled}
                onClick={() => {
                  if (readOnly || teamIsCurrent) return;
                  void startCheckout("team");
                }}
              >
                {teamCtaLabel}
              </button>
            </div>
          </article>
        </div>

        {/* Comparison table ----------------------------------------- */}
        <div className="billing-cmp" role="table">
          <div
            className="billing-cmp__row billing-cmp__row--head"
            role="row"
          >
            <div role="columnheader">{t("billing.cmp.heading")}</div>
            <div role="columnheader">{t("billing.plans.free.name")}</div>
            <div role="columnheader">{t("billing.plans.pro.name")}</div>
            <div role="columnheader">{t("billing.plans.team.name")}</div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.projects")}</div>
            <div>3</div>
            <div>{t("billing.cmp.val.unlimited")}</div>
            <div>{t("billing.cmp.val.unlimited")}</div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.seats")}</div>
            <div>1</div>
            <div>1</div>
            <div>1</div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.repos")}</div>
            <div>1</div>
            <div>5</div>
            <div>25</div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.custom_templates")}</div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.exports")}</div>
            <div>PDF</div>
            <div>PDF, MD</div>
            <div>PDF, MD, CSV, JSON</div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.priority_planner")}</div>
            <div className="billing-cmp__n">
              {t("billing.cmp.val.no")}
            </div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.audit_log")}</div>
            <div className="billing-cmp__n">
              {t("billing.cmp.val.no")}
            </div>
            <div className="billing-cmp__n">
              {t("billing.cmp.val.no")}
            </div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
          </div>
          <div className="billing-cmp__row" role="row">
            <div>{t("billing.cmp.feature.multi_reviewer")}</div>
            <div className="billing-cmp__n">
              {t("billing.cmp.val.no")}
            </div>
            <div className="billing-cmp__n">
              {t("billing.cmp.val.no")}
            </div>
            <div className="billing-cmp__y">
              {t("billing.cmp.val.yes")}
            </div>
          </div>
        </div>

        <p className="billing-modal__foot">{renderFoot(SUPPORT_EMAIL)}</p>
      </div>
    </div>
  );
}
