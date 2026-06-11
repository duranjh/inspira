/**
 * ScaffoldButton — the "Generate a first draft" CTA surfaced under a
 * Plan Summary when the project's inferred domain is software-y.
 *
 * PR 2 dropped the credit cost/balance accounting. The button is now
 * either enabled or shows an upgrade hint when the user's plan tier
 * doesn't include scaffold. The 402 upgrade-required toast in the
 * parent handles the actual user-visible upsell flow on click.
 */

import { type ReactElement } from "react";

import { t } from "../../i18n";

export type ScaffoldButtonProps = {
  /** True when the user's plan tier unlocks scaffold (Pro / Team). */
  canRun: boolean;
  running: boolean;
  onClick: () => void | Promise<void>;
};

export function ScaffoldButton(
  { canRun, running, onClick }: ScaffoldButtonProps,
): ReactElement {
  const disabled = running || !canRun;

  const label = running
    ? t("scaffold.button.generating")
    : canRun
      ? t("scaffold.button.generate")
      : t("scaffold.button.need_pro");

  const tooltip = !canRun
    ? t("scaffold.button.tooltip_upgrade")
    : running
      ? t("scaffold.button.tooltip_running")
      : t("scaffold.button.tooltip_ready");

  return (
    <button
      type="button"
      className="scaffold-cta"
      onClick={() => {
        if (disabled) return;
        void onClick();
      }}
      disabled={disabled}
      title={tooltip}
      aria-label={label}
    >
      <span>{label}</span>
    </button>
  );
}
