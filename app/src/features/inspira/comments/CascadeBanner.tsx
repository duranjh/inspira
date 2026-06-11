// Top-of-surface banner. Renders ONLY when banner_state ∈
// {narrow, wide}. The "none" state hides the banner — local
// regen ships without a confirm step (product decision).

import React from "react";

import { useComments, useCascadePreview } from "./CommentsContext";

export type CascadeBannerProps = {
  // Override the commit handler for tests / deferred wiring.
  onConfirm?: () => void;
};

export function CascadeBanner({
  onConfirm,
}: CascadeBannerProps): React.JSX.Element | null {
  const preview = useCascadePreview();
  const { clearPreview } = useComments();

  if (!preview) return null;
  const { affected_scope } = preview;
  if (affected_scope.banner_state === "none") return null;

  const variantClass =
    affected_scope.banner_state === "wide"
      ? "cc-banner cc-banner--rust"
      : "cc-banner cc-banner--gold";

  return (
    <div className={variantClass} role="status" data-cc-no-select>
      <div className="cc-banner__body">
        <span className="cc-banner__label">
          {affected_scope.banner_state === "wide"
            ? `Wide cascade — ${affected_scope.count} decisions affected.`
            : `${affected_scope.count} decision${affected_scope.count === 1 ? "" : "s"} affected.`}
        </span>
        <span className="cc-banner__cost">
          ~<span className="amt">${preview.estimated_cost_usd.toFixed(3)}</span>
          {" · "}
          ~{preview.estimated_seconds}s
        </span>
      </div>
      <div className="cc-banner__actions">
        <button
          type="button"
          className="cc-btn cc-btn--primary"
          onClick={onConfirm}
        >
          Confirm
        </button>
        <button
          type="button"
          className="cc-btn cc-btn--ghost"
          onClick={clearPreview}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
