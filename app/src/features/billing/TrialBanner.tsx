// Inspira — TrialBanner.
//
// Four variants match the hi-fi B7 tones:
//
//   active         · sage, dismissible, CTA "View plans"
//   three-day      · gold, dismissible, CTA "Upgrade to {plan}"
//   one-day        · rust, NOT dismissible, CTA "Upgrade to {plan}"
//   expired-grace  · rust, NOT dismissible, CTA "Upgrade to {plan}"
//
// Copy comes from `billing.banner.*` and is split around its numeric /
// date params so we can wrap those in <em> for emphasis without rolling
// the whole string through dangerouslySetInnerHTML.

import type { ReactNode } from "react";

import { t } from "../../i18n";

export type TrialBannerVariant =
  | "active"
  | "three-day"
  | "one-day"
  | "expired-grace";

export type TrialBannerProps = {
  variant: TrialBannerVariant;
  planName: string;
  daysRemaining?: number;
  trialEnd?: string | null;
  onUpgrade?: () => void;
  onViewPlans?: () => void;
  onDismiss?: () => void;
};

/**
 * Render a translated message where named params are emphasised with
 * <em>. Non-matching placeholders fall back to plain text so a missing
 * param never leaves a `{foo}` token in the DOM.
 */
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
      const name = match[1];
      const raw = params[name];
      if (raw === undefined || raw === null || raw === "") {
        // Unknown / empty param — drop the placeholder silently.
        continue;
      }
      nodes.push(<em key={`em-${key++}`}>{String(raw)}</em>);
    } else if (token.length > 0) {
      nodes.push(<span key={`t-${key++}`}>{token}</span>);
    }
  }
  return nodes;
}

export function TrialBanner({
  variant,
  planName,
  daysRemaining,
  trialEnd,
  onUpgrade,
  onViewPlans,
  onDismiss,
}: TrialBannerProps) {
  const tone: "sage" | "gold" | "rust" =
    variant === "active"
      ? "sage"
      : variant === "three-day"
        ? "gold"
        : "rust";

  const dismissible = variant === "active" || variant === "three-day";

  const params: Record<string, string | number> = {
    plan: planName,
    days: daysRemaining ?? 0,
    date: trialEnd ?? "",
  };

  // Map variant → (template, cta)
  const template =
    variant === "active"
      ? t("billing.banner.trial_active", params)
      : variant === "three-day"
        ? t("billing.banner.trial_3d", params)
        : variant === "one-day"
          ? t("billing.banner.trial_1d", params)
          : t("billing.banner.trial_expired", params);

  const ctaLabel =
    variant === "active"
      ? t("billing.banner.view_plans")
      : t("billing.action.upgrade_to_pro", { plan: planName });

  const ctaKind: "sage" | "ghost" = variant === "active" ? "ghost" : "sage";

  const onCta = variant === "active" ? onViewPlans : onUpgrade;

  return (
    <div className={`billing-banner billing-banner--${tone}`} role="status">
      <span className="billing-banner__dot" aria-hidden="true" />
      <span>{renderWithEmphasis(template, params)}</span>
      <span className="billing-banner__spacer" />
      {onCta ? (
        <button
          type="button"
          className={`billing-btn billing-btn--${ctaKind} billing-btn--sm`}
          onClick={onCta}
        >
          {ctaLabel}
        </button>
      ) : null}
      {dismissible && onDismiss ? (
        <button
          type="button"
          className="billing-banner__x"
          onClick={onDismiss}
          aria-label={t("billing.banner.dismiss")}
          title={t("billing.banner.dismiss")}
        >
          {"\u2715"}
        </button>
      ) : null}
    </div>
  );
}
