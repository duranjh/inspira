// Inspira — DunningBanner.
//
// Page-top banner for the `past-due` state. Not dismissible. Matches
// the hi-fi B8 rust tone. Copy lives behind `billing.banner.dunning`
// with the failed date emphasised.

import type { ReactNode } from "react";

import { t } from "../../i18n";

export type DunningBannerProps = {
  failedOn: string;
  onUpdatePayment: () => void;
};

function renderWithEmphasis(
  template: string,
  params: Record<string, string>,
): ReactNode[] {
  const tokens = template.split(/(\{[a-zA-Z_][a-zA-Z0-9_]*\})/g);
  const nodes: ReactNode[] = [];
  let key = 0;
  for (const token of tokens) {
    const match = /^\{([a-zA-Z_][a-zA-Z0-9_]*)\}$/.exec(token);
    if (match) {
      const raw = params[match[1]];
      if (raw === undefined || raw === "") continue;
      nodes.push(<em key={`em-${key++}`}>{raw}</em>);
    } else if (token.length > 0) {
      nodes.push(<span key={`t-${key++}`}>{token}</span>);
    }
  }
  return nodes;
}

export function DunningBanner({
  failedOn,
  onUpdatePayment,
}: DunningBannerProps) {
  const template = t("billing.banner.dunning", { date: failedOn });
  return (
    <div
      className="billing-banner billing-banner--rust"
      role="alert"
      aria-live="polite"
    >
      <span className="billing-banner__dot" aria-hidden="true" />
      <span>{renderWithEmphasis(template, { date: failedOn })}</span>
      <span className="billing-banner__spacer" />
      <button
        type="button"
        className="billing-btn billing-btn--sage billing-btn--sm"
        onClick={onUpdatePayment}
      >
        {t("billing.action.update_payment")}
      </button>
    </div>
  );
}
