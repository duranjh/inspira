// Inspira — BillingOverviewPage.
//
// The /billing surface (hi-fi "B1"). Renders six distinct subscription
// states plus a "free" fallthrough:
//
//   active-paid · trialing · trial-3d · trial-1d · trial-expired-grace
//   past-due · suspended · free
//
// The page is a full-viewport overlay mirroring AccountSettingsPage:
// sticky topbar on top, centered inner column below. Esc closes. Body
// scroll is locked while mounted.
//
// Data:
//   - billingApi.getSubscription()     — plan + status + period end
//   - billingApi.getWorkspaceUsage()   — seats / projects / repos meters
//   - billingApi.getPaymentMethod()    — owner-only VISA tile
//   - billingApi.getInvoices()         — owner-only list of up to three
//
// Design-review fixtures: when `forcedStateKey` is set we skip the live
// fetch and paint the Marguerite Hale / Pro / $288 fixture from the
// hi-fi, so the tweaks panel in BillingRoute lights up every state
// without a backend.
//
// TODO(backend): neither the subscription nor the user today exposes a
// billing role. We fall back to "owner" until the backend adds it; the
// `forcedRole` escape hatch lets design review exercise the admin /
// member variants in the meantime.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { AuthedUser } from "../inspira/api";
import { t } from "../../i18n";
import { toast } from "../../components/ToastProvider";
import { HIDE_UPGRADE } from "../../lib/featureFlags";
import { Head } from "../marketing/Head";

import {
  billingApi,
  BillingNotConfiguredError,
  type BillingRole,
  type Invoice,
  type PaymentMethod,
  type PlanSummary,
  type Subscription,
  type SubscriptionStatus,
  type WorkspaceUsage,
} from "./api";
import { DunningBanner } from "./DunningBanner";
import { PlanComparisonModal } from "./PlanComparisonModal";
import {
  SwitchToAnnualModal,
  shouldShowSwitchToAnnual,
} from "./SwitchToAnnualModal";
import { TrialBanner, type TrialBannerVariant } from "./TrialBanner";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type BillingOverviewPageProps = {
  user: AuthedUser;
  onClose: () => void;
  forcedStateKey?: string | null;
  forcedRole?: string | null;
  /** When true, the Switch-to-annual offer (B11) is allowed to mount.
   *  Actual eligibility still runs through `shouldShowSwitchToAnnual` so
   *  callers can wire this directly to a feature flag without repeating
   *  the plan / period / localStorage checks. */
  showSwitchToAnnual?: boolean;
};

const VALID_STATES: ReadonlyArray<SubscriptionStatus> = [
  "active-paid",
  "trialing",
  "trial-3d",
  "trial-1d",
  "trial-expired-grace",
  "past-due",
  "suspended",
  "free",
];

const VALID_ROLES: ReadonlyArray<BillingRole> = ["owner", "admin", "member"];

function coerceState(value: string | null | undefined): SubscriptionStatus | null {
  if (!value) return null;
  return (VALID_STATES as ReadonlyArray<string>).includes(value)
    ? (value as SubscriptionStatus)
    : null;
}

function coerceRole(value: string | null | undefined): BillingRole | null {
  if (!value) return null;
  return (VALID_ROLES as ReadonlyArray<string>).includes(value)
    ? (value as BillingRole)
    : null;
}

// ---------------------------------------------------------------------------
// Fixtures for design review (match the hi-fi sample data)
// ---------------------------------------------------------------------------

const FIXTURE_PLAN: PlanSummary = {
  slug: "pro",
  title: "Pro",
  monthly_price_cents: 2900,
  annual_price_cents: 28800,
  description: "Room for every idea you have.",
  features: [],
  limits: {
    max_projects: null,
    daily_token_budget: 500_000,
    max_topics_total: null,
    allow_share_links: true,
    allow_team: false,
    allow_export_pdf: true,
    allow_advanced_exports: false,
    priority_planner: true,
  },
};

const FIXTURE_USAGE: WorkspaceUsage = {
  seats_used: 3,
  seats_limit: 5,
  projects_used: 4,
  projects_limit: null,
  repos_used: 2,
  repos_limit: 5,
};

const FIXTURE_PAYMENT: PaymentMethod = {
  brand: "VISA",
  last4: "4242",
  exp_month: 9,
  exp_year: 2028,
  holder_name: "Marguerite Hale",
};

// March 18 2026, matching the hi-fi renewal date.
const FIXTURE_PERIOD_END = "2026-03-18T12:00:00Z";
// April 15 for past-due / trial-3d "upgrade before" copy.
const FIXTURE_APRIL_15 = "2026-04-15T12:00:00Z";
// April 10 for trial-expired "ended on" copy.
const FIXTURE_APRIL_10 = "2026-04-10T12:00:00Z";

const FIXTURE_INVOICES: Invoice[] = [
  {
    id: "in_00412",
    number: "INV-00412",
    issued_at: "2026-03-18T12:00:00Z",
    period_label: "Pro, annual renewal",
    amount_cents: 28800,
    currency: "USD",
    status: "paid",
    pdf_url: null,
  },
  {
    id: "in_00298",
    number: "INV-00298",
    issued_at: "2025-03-18T12:00:00Z",
    period_label: "Pro, annual renewal",
    amount_cents: 28800,
    currency: "USD",
    status: "paid",
    pdf_url: null,
  },
  {
    id: "in_00277",
    number: "INV-00277",
    issued_at: "2025-02-18T12:00:00Z",
    period_label: "Monthly-to-annual proration",
    amount_cents: 6800,
    currency: "USD",
    status: "paid",
    pdf_url: null,
  },
];

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const month = d.toLocaleDateString("en-US", { month: "short" });
  return `${month} ${d.getDate()} \u00B7 ${d.getFullYear()}`;
}

function formatAmount(cents: number, currency = "USD"): string {
  const dollars = cents / 100;
  const whole = Number.isInteger(dollars);
  const symbol = currency === "USD" ? "$" : "";
  if (whole) return `${symbol}${dollars.toFixed(0)}`;
  return `${symbol}${dollars.toFixed(2)}`;
}

function formatAnnualPrice(plan: PlanSummary): string {
  if (plan.annual_price_cents != null) {
    return `${formatAmount(plan.annual_price_cents)}`;
  }
  return `${formatAmount(plan.monthly_price_cents * 12)}`;
}

function formatMonthlyFromAnnual(plan: PlanSummary): string {
  const cents = plan.annual_price_cents ?? plan.monthly_price_cents * 12;
  const monthly = Math.round(cents / 12);
  return `$${(monthly / 100).toFixed(0)}/mo billed annually`;
}

function formatMonthly(plan: PlanSummary): string {
  return `$${(plan.monthly_price_cents / 100).toFixed(0)}/mo`;
}

// ---------------------------------------------------------------------------
// Emphasis renderer — mirrors the one in the banners so we can keep
// the subtitle templates in i18n and still bold date/number params.
// ---------------------------------------------------------------------------

function renderWithEmphasis(
  template: string,
  params: Record<string, string | number | null | undefined>,
): ReactNode[] {
  const tokens = template.split(/(\{[a-zA-Z_][a-zA-Z0-9_]*\})/g);
  const nodes: ReactNode[] = [];
  let key = 0;
  for (const token of tokens) {
    const match = /^\{([a-zA-Z_][a-zA-Z0-9_]*)\}$/.exec(token);
    if (match) {
      const raw = params[match[1]];
      if (raw === undefined || raw === null || raw === "") continue;
      nodes.push(<em key={`em-${key++}`}>{String(raw)}</em>);
    } else if (token.length > 0) {
      nodes.push(<span key={`t-${key++}`}>{token}</span>);
    }
  }
  return nodes;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type LoadState =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "not_configured" }
  | { kind: "error"; message: string };

type HeroActionKind = "sage" | "ghost" | "link";
type HeroAction = { kind: HeroActionKind; label: string; onClick?: () => void };

export function BillingOverviewPage({
  user: _user,
  onClose,
  forcedStateKey,
  forcedRole,
  showSwitchToAnnual: showSwitchToAnnualProp,
}: BillingOverviewPageProps) {
  const forcedState = coerceState(forcedStateKey);
  const forcedRoleVal = coerceRole(forcedRole);

  const [loadState, setLoadState] = useState<LoadState>({ kind: "loading" });
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usage, setUsage] = useState<WorkspaceUsage | null>(null);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | null>(
    null,
  );
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [plansOpen, setPlansOpen] = useState(false);
  const [annualOfferOpen, setAnnualOfferOpen] = useState(false);

  // Once subscription is loaded, decide whether the Switch-to-annual
  // offer should appear. The eligibility helper enforces plan / period /
  // age / localStorage cooldown rules, so opening the modal is safe to
  // do unconditionally — the helper self-skips unless every condition
  // is met. The `showSwitchToAnnual` prop remains as a kill switch for
  // callers who want to suppress the offer entirely (e.g. review modes
  // that force a particular state).
  useEffect(() => {
    if (showSwitchToAnnualProp === false) return;
    if (!subscription) return;
    if (shouldShowSwitchToAnnual(subscription)) {
      setAnnualOfferOpen(true);
    }
  }, [showSwitchToAnnualProp, subscription]);

  // ---- Esc closes ----------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        if (plansOpen) {
          setPlansOpen(false);
        } else {
          onClose();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose, plansOpen]);

  // ---- Body scroll lock ---------------------------------------------
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // ---- Data fetch ---------------------------------------------------
  useEffect(() => {
    // Design-review path: hardcode fixture, skip live fetch entirely.
    if (forcedState) {
      setSubscription({
        plan: FIXTURE_PLAN,
        status: forcedState,
        stripe_customer_id: "cus_fixture",
        stripe_subscription_id: "sub_fixture",
        current_period_end: FIXTURE_PERIOD_END,
        trial_end:
          forcedState === "trial-expired-grace"
            ? FIXTURE_APRIL_10
            : forcedState.startsWith("trial")
              ? FIXTURE_APRIL_15
              : null,
        billing_period: "annual",
      });
      setUsage(FIXTURE_USAGE);
      setPaymentMethod(FIXTURE_PAYMENT);
      setInvoices(FIXTURE_INVOICES);
      setLoadState({ kind: "ready" });
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const sub = await billingApi.getSubscription();
        if (cancelled) return;
        setSubscription(sub.subscription);

        // Fan out the rest in parallel — they're all stubs today so we
        // tolerate individual failures without blocking the page.
        const [usageRes, pmRes, invRes] = await Promise.allSettled([
          billingApi.getWorkspaceUsage(),
          billingApi.getPaymentMethod(),
          billingApi.getInvoices(),
        ]);
        if (cancelled) return;
        if (usageRes.status === "fulfilled") setUsage(usageRes.value.usage);
        if (pmRes.status === "fulfilled") {
          setPaymentMethod(pmRes.value.payment_method);
        }
        if (invRes.status === "fulfilled") setInvoices(invRes.value.invoices);

        setLoadState({ kind: "ready" });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof BillingNotConfiguredError) {
          setLoadState({ kind: "not_configured" });
        } else {
          const msg = err instanceof Error ? err.message : String(err);
          setLoadState({ kind: "error", message: msg });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [forcedState]);

  const state: SubscriptionStatus = useMemo(() => {
    if (forcedState) return forcedState;
    if (!subscription) return "free";
    const raw = subscription.status;
    if ((VALID_STATES as ReadonlyArray<string>).includes(raw)) {
      return raw as SubscriptionStatus;
    }
    // Legacy / unknown statuses fall through to free so the UI still
    // renders a warm default rather than a blank screen.
    return "free";
  }, [forcedState, subscription]);

  // TODO(backend): subscription has no role field yet. Default to owner
  // and let the forcedRole query param override for design review.
  const role: BillingRole = forcedRoleVal ?? "owner";

  // ---- Action wiring ------------------------------------------------
  // T1.2: BillingNotConfigured used to fire window.alert() — a system
  // dialog that can't be styled and reads as broken on the warm-paper
  // surface. Routed through the toast system so the messaging matches
  // the rest of the app and the user can keep typing.
  const openPortal = useCallback(async () => {
    try {
      const res = await billingApi.openPortalSession();
      if (res.portal?.url) {
        window.location.assign(res.portal.url);
      }
    } catch (err) {
      if (err instanceof BillingNotConfiguredError) {
        toast.error(t("billing.fallback.not_configured_title"));
      } else {
        const msg = err instanceof Error ? err.message : String(err);
        console.error("[billing] openPortalSession failed:", msg);
      }
    }
  }, []);

  const startCheckout = useCallback(
    async (planSlug: string, period: "annual" | "monthly" = "annual") => {
      try {
        const res = await billingApi.startCheckout({
          plan_slug: planSlug,
          period,
        });
        if (res.checkout?.url) {
          window.location.assign(res.checkout.url);
        }
      } catch (err) {
        if (err instanceof BillingNotConfiguredError) {
          toast.error(t("billing.fallback.not_configured_title"));
        } else {
          const msg = err instanceof Error ? err.message : String(err);
          console.error("[billing] startCheckout failed:", msg);
        }
      }
    },
    [],
  );

  // HIDE_UPGRADE no-ops both callbacks so any path we don't filter
  // explicitly (e.g. a future button we forgot) still doesn't open
  // the PlanComparisonModal or trigger a checkout. Defense in depth.
  const onUpgrade = useCallback(() => {
    if (HIDE_UPGRADE) return;
    setPlansOpen(true);
  }, []);
  const onViewPlans = useCallback(() => {
    if (HIDE_UPGRADE) return;
    setPlansOpen(true);
  }, []);
  const onDismissBanner = useCallback(() => setBannerDismissed(true), []);
  const onChangeCard = useCallback(() => {
    void openPortal();
  }, [openPortal]);
  const onAddVat = useCallback(() => {
    // TODO(backend): wire to billing contacts PATCH once the endpoint lands.
    console.info("[billing] Add VAT ID — pending backend");
  }, []);
  const onExportData = useCallback(() => {
    // TODO(backend): hook up data export. No-op for now so the button
    // is discoverable but doesn't 404.
    console.info("[billing] Export data — pending backend");
  }, []);
  const onSeeAllInvoices = useCallback(() => {
    // TODO: route to an invoices-only page / open InvoiceHistory modal.
    console.info("[billing] See all invoices — pending UI");
  }, []);
  const onInvoicePdf = useCallback(async (id: string) => {
    try {
      const res = await billingApi.downloadInvoicePdf(id);
      if (res.url) {
        window.open(res.url, "_blank", "noopener");
      } else {
        console.info("[billing] Invoice PDF not available yet:", id);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("[billing] downloadInvoicePdf failed:", msg);
    }
  }, []);
  const onInvoiceDetails = useCallback((id: string) => {
    // TODO: open InvoiceDetailModal once Agent C lands it.
    console.info("[billing] Invoice details — pending:", id);
  }, []);

  // ---- Hero content --------------------------------------------------
  const planName = subscription?.plan?.title ?? "Pro";
  const planSlug = subscription?.plan?.slug ?? "pro";
  const periodEnd = subscription?.current_period_end ?? FIXTURE_PERIOD_END;
  const trialEnd = subscription?.trial_end ?? null;
  const billingPeriod = subscription?.billing_period ?? "annual";

  // Derived counts / dates for copy
  const daysRemaining = useMemo(() => {
    const target = trialEnd ?? periodEnd;
    if (!target) return 0;
    const now = Date.now();
    const end = new Date(target).getTime();
    if (Number.isNaN(end)) return 0;
    return Math.max(0, Math.round((end - now) / 864e5));
  }, [trialEnd, periodEnd]);

  const hero = useMemo(() => {
    const plan = subscription?.plan;
    const planPrice = plan
      ? billingPeriod === "annual"
        ? formatMonthlyFromAnnual(plan)
        : formatMonthly(plan)
      : "$24/mo billed annually";

    switch (state) {
      case "active-paid": {
        const subKey =
          billingPeriod === "monthly"
            ? "billing.hero.sub.active_paid_monthly"
            : "billing.hero.sub.active_paid_annual";
        const amount =
          billingPeriod === "monthly"
            ? formatMonthly(plan ?? FIXTURE_PLAN)
            : `${formatAnnualPrice(plan ?? FIXTURE_PLAN)}`;
        return {
          title: t("billing.hero.title.active_paid", { plan: planName }),
          subTemplate: t(subKey, {
            date: formatDate(periodEnd),
            amount,
          }),
          subParams: {
            date: formatDate(periodEnd),
            amount,
          } as Record<string, string | number>,
          actions: (
            [
              billingPeriod === "monthly"
                ? {
                    kind: "ghost",
                    label: t("billing.action.switch_to_annual"),
                    onClick: onUpgrade,
                  }
                : null,
              {
                kind: "link",
                label: t("billing.action.downgrade_to_free"),
                onClick: onViewPlans,
              },
              {
                kind: "link",
                label: `${t("billing.action.view_all_plans")} \u2192`,
                onClick: onViewPlans,
              },
            ] as Array<HeroAction | null>
          ).filter((a): a is HeroAction => a !== null),
        };
      }
      case "trialing": {
        return {
          title: t("billing.hero.title.trialing", { plan: planName }),
          subTemplate: t("billing.hero.sub.trialing", {
            days: daysRemaining,
            price: planPrice,
          }),
          subParams: {
            days: daysRemaining,
            price: planPrice,
          } as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.upgrade_to_pro", { plan: planName }),
              onClick: onUpgrade,
            },
            {
              kind: "link" as const,
              label: `${t("billing.action.view_all_plans")} \u2192`,
              onClick: onViewPlans,
            },
          ] as HeroAction[],
        };
      }
      case "trial-3d": {
        return {
          title: t("billing.hero.title.trial_3d"),
          subTemplate: t("billing.hero.sub.trial_3d", {
            date: formatDate(trialEnd ?? FIXTURE_APRIL_15),
          }),
          subParams: {
            date: formatDate(trialEnd ?? FIXTURE_APRIL_15),
          } as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.upgrade_to_pro", { plan: planName }),
              onClick: onUpgrade,
            },
            {
              kind: "link" as const,
              label: `${t("billing.action.view_all_plans")} \u2192`,
              onClick: onViewPlans,
            },
          ] as HeroAction[],
        };
      }
      case "trial-1d": {
        return {
          title: t("billing.hero.title.trial_1d"),
          subTemplate: t("billing.hero.sub.trial_1d"),
          subParams: {} as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.upgrade_to_pro", { plan: planName }),
              onClick: () => {
                void startCheckout(planSlug, billingPeriod);
              },
            },
            {
              kind: "link" as const,
              label: `${t("billing.action.view_all_plans")} \u2192`,
              onClick: onViewPlans,
            },
          ] as HeroAction[],
        };
      }
      case "trial-expired-grace": {
        return {
          title: t("billing.hero.title.trial_expired_grace"),
          subTemplate: t("billing.hero.sub.trial_expired_grace", {
            date: formatDate(trialEnd ?? FIXTURE_APRIL_10),
            days: 6,
          }),
          subParams: {
            date: formatDate(trialEnd ?? FIXTURE_APRIL_10),
            days: 6,
          } as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.upgrade_to_pro", { plan: planName }),
              onClick: onUpgrade,
            },
            {
              kind: "link" as const,
              label: `${t("billing.action.view_all_plans")} \u2192`,
              onClick: onViewPlans,
            },
          ] as HeroAction[],
        };
      }
      case "past-due": {
        return {
          title: t("billing.hero.title.past_due"),
          subTemplate: t("billing.hero.sub.past_due", {
            date: formatDate(periodEnd),
          }),
          subParams: {
            date: formatDate(periodEnd),
          } as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.update_payment"),
              onClick: () => {
                void openPortal();
              },
            },
            {
              kind: "link" as const,
              label: t("billing.action.view_invoices"),
              onClick: onSeeAllInvoices,
            },
          ] as HeroAction[],
        };
      }
      case "suspended": {
        return {
          title: t("billing.hero.title.suspended"),
          subTemplate: t("billing.hero.sub.suspended"),
          subParams: {} as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.restore_billing"),
              onClick: () => {
                void openPortal();
              },
            },
            {
              kind: "link" as const,
              label: t("billing.action.export_data"),
              onClick: onExportData,
            },
          ] as HeroAction[],
        };
      }
      case "free":
      default: {
        return {
          title: t("billing.hero.title.free"),
          subTemplate: t("billing.hero.sub.free"),
          subParams: {} as Record<string, string | number>,
          actions: [
            {
              kind: "sage" as const,
              label: t("billing.action.upgrade_to_pro", { plan: "Pro" }),
              onClick: onUpgrade,
            },
            {
              kind: "link" as const,
              label: `${t("billing.action.view_all_plans")} \u2192`,
              onClick: onViewPlans,
            },
          ] as HeroAction[],
        };
      }
    }
  }, [
    state,
    subscription,
    billingPeriod,
    planName,
    planSlug,
    daysRemaining,
    periodEnd,
    trialEnd,
    onUpgrade,
    onViewPlans,
    onExportData,
    onSeeAllInvoices,
    openPortal,
    startCheckout,
  ]);

  // HIDE_UPGRADE: drop hero actions whose onClick is the upgrade or
  // view-plans callback. Buttons stop rendering on the page hero, so a
  // partner viewing /billing on the demo doesn't see Upgrade-shaped CTAs.
  const heroActions = useMemo(() => {
    if (!HIDE_UPGRADE) return hero.actions;
    return hero.actions.filter(
      (a) => a.onClick !== onUpgrade && a.onClick !== onViewPlans,
    );
  }, [hero.actions, onUpgrade, onViewPlans]);

  // ---- Banner -------------------------------------------------------
  // HIDE_UPGRADE drops upgrade/view-plans callbacks so TrialBanner
  // hides its own CTA (it already returns null on undefined onCta).
  const ctaUpgrade = HIDE_UPGRADE ? undefined : onUpgrade;
  const ctaViewPlans = HIDE_UPGRADE ? undefined : onViewPlans;

  const banner = useMemo((): ReactNode => {
    if (bannerDismissed && (state === "trialing" || state === "trial-3d")) {
      return null;
    }
    switch (state) {
      case "trialing":
        return (
          <TrialBanner
            variant="active"
            planName={planName}
            daysRemaining={daysRemaining}
            trialEnd={trialEnd}
            onViewPlans={ctaViewPlans}
            onDismiss={onDismissBanner}
          />
        );
      case "trial-3d":
        return (
          <TrialBanner
            variant="three-day"
            planName={planName}
            daysRemaining={3}
            trialEnd={trialEnd}
            onUpgrade={ctaUpgrade}
            onDismiss={onDismissBanner}
          />
        );
      case "trial-1d":
        return (
          <TrialBanner
            variant="one-day"
            planName={planName}
            daysRemaining={1}
            trialEnd={trialEnd}
            onUpgrade={ctaUpgrade}
          />
        );
      case "trial-expired-grace":
        return (
          <TrialBanner
            variant="expired-grace"
            planName={planName}
            daysRemaining={6}
            trialEnd={formatDate(trialEnd ?? FIXTURE_APRIL_10)}
            onUpgrade={ctaUpgrade}
          />
        );
      case "past-due":
        return (
          <DunningBanner
            failedOn={formatDate(periodEnd ?? FIXTURE_APRIL_15)}
            onUpdatePayment={() => {
              void openPortal();
            }}
          />
        );
      case "suspended":
        return (
          <div
            className="billing-banner billing-banner--rust"
            role="alert"
            aria-live="polite"
          >
            <span
              className="billing-banner__dot"
              aria-hidden="true"
            />
            <span>{t("billing.banner.suspended")}</span>
            <span className="billing-banner__spacer" />
            <button
              type="button"
              className="billing-btn billing-btn--sage billing-btn--sm"
              onClick={() => {
                void openPortal();
              }}
            >
              {t("billing.action.restore_billing")}
            </button>
          </div>
        );
      default:
        return null;
    }
  }, [
    state,
    bannerDismissed,
    planName,
    daysRemaining,
    trialEnd,
    periodEnd,
    ctaUpgrade,
    ctaViewPlans,
    onDismissBanner,
    openPortal,
  ]);

  // ---- Usage meters -------------------------------------------------
  const warnMeters = state === "suspended";
  // No fallback to FIXTURE_USAGE for real users — surfacing
  // "Seats 3 of 5" to a brand-new free account is misleading. The
  // forcedStateKey (design-review) path above already sets a real
  // FIXTURE_USAGE before this point, so design preview still works.
  // E2E 2026-04-25 #4.
  const meters = useMemo(
    () =>
      usage
        ? [
            {
              label: t("billing.usage.seats"),
              used: usage.seats_used,
              limit: usage.seats_limit,
            },
            {
              label: t("billing.usage.projects"),
              used: usage.projects_used,
              limit: usage.projects_limit,
            },
            {
              label: t("billing.usage.repos"),
              used: usage.repos_used,
              limit: usage.repos_limit,
            },
          ]
        : [],
    [usage],
  );

  // ---- Fallback: Stripe not configured ------------------------------
  if (loadState.kind === "not_configured") {
    return (
      <div
        className="billing-page"
        role="dialog"
        aria-modal="true"
        aria-label={t("billing.page.aria")}
      >
        <Head
          title={t("billing.meta.title")}
          description={t("billing.meta.description")}
          canonical="https://tryinspira.com/billing"
          robots="noindex,nofollow"
        />
        <header className="billing-page__topbar">
          <h1 className="billing-page__brand">Inspira</h1>
          <span className="billing-page__crumbs">
            {t("billing.page.crumbs_prefix")}
            {" \u00B7 "}
            <em>{t("billing.page.crumbs_self")}</em>
          </span>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="billing-page__close"
            onClick={onClose}
            aria-label={t("billing.page.close_aria")}
            title={t("billing.page.close_title")}
          >
            {"\u00D7"}
          </button>
        </header>
        <div className="billing-page__inner">
          <div className="billing-fallback">
            <p className="billing-eyebrow billing-fallback__eyebrow">
              {t("billing.page.heading")}
            </p>
            <h2 className="billing-display billing-display--md">
              {t("billing.fallback.not_configured_title")}
            </h2>
            <p className="billing-serif">
              {t("billing.fallback.not_configured_body", {
                email: "hello@tryinspira.com",
              })}
            </p>
          </div>
        </div>
      </div>
    );
  }

  const isLoading = loadState.kind === "loading";

  return (
    <div
      className="billing-page"
      role="dialog"
      aria-modal="true"
      aria-label={t("billing.page.aria")}
      aria-busy={isLoading ? "true" : undefined}
    >
      <Head
        title={t("billing.meta.title")}
        description={t("billing.meta.description")}
        canonical="https://tryinspira.com/billing"
        robots="noindex,nofollow"
      />
      <header className="billing-page__topbar">
        <h1 className="billing-page__brand">Inspira</h1>
        <span className="billing-page__crumbs">
          {t("billing.page.crumbs_prefix")}
          {" \u00B7 "}
          <em>{t("billing.page.crumbs_self")}</em>
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="billing-page__close"
          onClick={onClose}
          aria-label={t("billing.page.close_aria")}
          title={t("billing.page.close_title")}
        >
          {"\u00D7"}
        </button>
      </header>

      <div className="billing-page__inner">
        {/* Banner slot */}
        {banner}

        {/* Hero */}
        <section className="billing-card billing-hero">
          <p className="billing-eyebrow">{t("billing.eyebrow.billing")}</p>
          <h2 className="billing-display billing-display--xl">{hero.title}</h2>
          <p
            className="billing-serif"
            style={{ marginTop: 12, maxWidth: "60ch" }}
          >
            {renderWithEmphasis(hero.subTemplate, hero.subParams)}
          </p>
          <div className="billing-hero__actions">
            {heroActions.map((action, idx) => (
              <button
                key={`${action.label}-${idx}`}
                type="button"
                className={
                  action.kind === "sage"
                    ? "billing-btn billing-btn--sage"
                    : action.kind === "ghost"
                      ? "billing-btn billing-btn--ghost"
                      : "billing-btn billing-btn--link"
                }
                onClick={action.onClick}
              >
                {action.label}
              </button>
            ))}
          </div>
        </section>

        {/* Two-col: usage + payment/role.
         *  Usage block is hidden entirely when no real usage data
         *  exists (free user pre-Stripe) — meters with fixture data
         *  ("Seats 3 of 5") were actively misleading. E2E 2026-04-25 #4. */}
        <div className="billing-two-col">
          {usage ? (
          <section className="billing-card">
            <p className="billing-eyebrow">{t("billing.eyebrow.usage")}</p>
            <div className="billing-meters">
              {meters.map((m) => {
                const pct =
                  m.limit && m.limit > 0
                    ? Math.min(100, Math.round((m.used / m.limit) * 100))
                    : Math.min(100, m.used * 6);
                const numText =
                  m.limit == null
                    ? t("billing.usage.num_unlimited", { used: m.used })
                    : t("billing.usage.num", {
                        used: m.used,
                        limit: m.limit,
                      });
                return (
                  <div className="billing-meter" key={m.label}>
                    <span className="billing-meter__name">{m.label}</span>
                    <div
                      className="billing-meter__track"
                      role="progressbar"
                      aria-valuenow={m.used}
                      aria-valuemin={0}
                      aria-valuemax={m.limit ?? undefined}
                      aria-label={m.label}
                    >
                      <div
                        className={
                          warnMeters
                            ? "billing-meter__fill billing-meter__fill--warn"
                            : "billing-meter__fill"
                        }
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="billing-meter__num">{numText}</span>
                  </div>
                );
              })}
            </div>
          </section>
          ) : null}

          {role === "owner" ? (
            <section className="billing-card">
              <p className="billing-eyebrow">{t("billing.eyebrow.payment")}</p>
              {paymentMethod ? (
                <div className="billing-pm" style={{ marginTop: 6 }}>
                  <div className="billing-pm__card">
                    <span className="billing-pm__brand">
                      {paymentMethod.brand}
                    </span>
                    <span className="billing-pm__num">
                      {paymentMethod.brand === "VISA" ? "Visa" : paymentMethod.brand}
                      <em>
                        {"\u2022\u2022 "}
                        {paymentMethod.last4}
                      </em>
                    </span>
                  </div>
                  <p className="billing-pm__meta">
                    {t("billing.pm.expires", {
                      month: String(paymentMethod.exp_month).padStart(2, "0"),
                      year: String(paymentMethod.exp_year % 100).padStart(
                        2,
                        "0",
                      ),
                      name: paymentMethod.holder_name ?? "",
                    })}
                  </p>
                  <div className="billing-pm__actions">
                    <button
                      type="button"
                      className="billing-btn billing-btn--ghost billing-btn--sm"
                      onClick={onChangeCard}
                    >
                      {t("billing.action.change_card")}
                    </button>
                    <button
                      type="button"
                      className="billing-btn billing-btn--link billing-btn--sm"
                      onClick={onAddVat}
                    >
                      {`+ ${t("billing.action.add_vat")}`}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="billing-pm" style={{ marginTop: 6 }}>
                  <p className="billing-serif billing-serif--sm">
                    {t("billing.pm.no_card")}
                  </p>
                  <div className="billing-pm__actions">
                    <button
                      type="button"
                      className="billing-btn billing-btn--ghost billing-btn--sm"
                      onClick={onChangeCard}
                    >
                      {t("billing.pm.add_card")}
                    </button>
                  </div>
                </div>
              )}
            </section>
          ) : role === "admin" ? (
            <div className="billing-role-block">
              <p
                className="billing-eyebrow"
                style={{ marginBottom: 8 }}
              >
                {t("billing.eyebrow.payment")}
              </p>
              <p
                className="billing-serif billing-serif--sm"
                style={{ margin: "0 0 12px" }}
              >
                {t("billing.pm.admin_note")}
              </p>
              <a
                className="billing-btn billing-btn--ghost billing-btn--sm"
                href="mailto:billing@tryinspira.com"
              >
                {t("billing.action.contact_owner")}
              </a>
            </div>
          ) : (
            <div className="billing-role-block">
              <p
                className="billing-eyebrow"
                style={{ marginBottom: 8 }}
              >
                {t("billing.eyebrow.plan_summary")}
              </p>
              <p
                className="billing-serif billing-serif--sm"
                style={{ margin: 0 }}
              >
                {t("billing.pm.member_note")}
              </p>
            </div>
          )}
        </div>

        {/* Invoices (owner only) */}
        {role === "owner" ? (
          <section className="billing-card">
            <div className="billing-invoices__head">
              <p className="billing-eyebrow" style={{ margin: 0 }}>
                {t("billing.eyebrow.invoices")}
              </p>
              <button
                type="button"
                className="billing-btn billing-btn--link billing-btn--sm"
                onClick={onSeeAllInvoices}
              >
                {`${t("billing.action.see_all")} \u2192`}
              </button>
            </div>
            <div className="billing-invoices">
              {invoices.length === 0 ? (
                <div className="billing-invoices__empty">
                  {t("billing.invoices.empty")}
                </div>
              ) : (
                invoices.slice(0, 3).map((inv) => (
                  <div className="billing-inv" key={inv.id}>
                    <span className="billing-inv__date">
                      {formatShortDate(inv.issued_at)}
                    </span>
                    <span className="billing-inv__desc">
                      {inv.period_label}
                      {" "}
                      <span className="billing-inv__num">
                        {"\u00B7 "}
                        {inv.number}
                      </span>
                    </span>
                    <span className="billing-inv__amt">
                      {formatAmount(inv.amount_cents, inv.currency)}
                      {".00"}
                    </span>
                    <span
                      className={`billing-inv__status billing-inv__status--${inv.status}`}
                    >
                      {t(`billing.invoices.status_${inv.status}`)}
                    </span>
                    <span className="billing-inv__actions">
                      <button
                        type="button"
                        onClick={() => {
                          void onInvoicePdf(inv.id);
                        }}
                      >
                        {t("billing.invoices.pdf")}
                      </button>
                      <button
                        type="button"
                        onClick={() => onInvoiceDetails(inv.id)}
                      >
                        {t("billing.invoices.details")}
                      </button>
                    </span>
                  </div>
                ))
              )}
            </div>
          </section>
        ) : null}

        {/* Footer */}
        <div
          className="billing-plan-actions"
          style={{ justifyContent: "flex-end" }}
        >
          <p className="billing-serif billing-serif--sm billing-serif--dim">
            <em>{t("billing.footer.refund_note")}</em>
          </p>
        </div>

        {loadState.kind === "error" ? (
          <p className="billing-serif billing-serif--sm billing-serif--dim">
            {loadState.message}
          </p>
        ) : null}
      </div>

      {!HIDE_UPGRADE && (
        <PlanComparisonModal
          open={plansOpen}
          onClose={() => setPlansOpen(false)}
          currentPlan={
            planSlug === "free" || planSlug === "pro" || planSlug === "team"
              ? planSlug
              : undefined
          }
          onChoose={(choice) => {
            setPlansOpen(false);
            void startCheckout(choice.plan, choice.period);
          }}
        />
      )}
      <SwitchToAnnualModal
        open={annualOfferOpen}
        onClose={() => setAnnualOfferOpen(false)}
      />
    </div>
  );
}
