import { useState, type ReactElement } from "react";

import type { PrOverlayStalenessResponse } from "../api";

type StalenessBannerProps = {
  staleness: PrOverlayStalenessResponse | null;
  /** Wave F.6 — clicked when the partner wants Inspira to redraft the
   *  scaffold against the fresh main. The parent ``ArtifactViewerPage``
   *  wires this to ``useRefreshPr.startRefresh``. Banner shows a
   *  "refreshing…" label while ``refreshing`` is true. */
  onRefreshClick?: () => void;
  refreshing?: boolean;
};

/**
 * Wave F.5 — rust-tinted notice at the top of the PR folder body when
 * main has moved underneath the project's scaffold.
 *
 * Wave F.6 — the "Refresh PR with Inspira" CTA is now live. Clicking
 * it kicks off the redraft via ``useRefreshPr``. When
 * ``onRefreshClick`` is not provided (e.g. legacy callers or test
 * scaffolding), the button stays disabled with the F.5 coming-soon
 * tooltip — so an out-of-date integration degrades gracefully rather
 * than 404'ing on click.
 *
 * Suppresses itself entirely when ``staleness`` is null, legacy,
 * truncated-without-affecting, or simply not stale.
 */
export function StalenessBanner({
  staleness,
  onRefreshClick,
  refreshing = false,
}: StalenessBannerProps): ReactElement | null {
  const [dismissed, setDismissed] = useState<boolean>(false);

  if (
    !staleness
    || staleness.legacy
    || !staleness.is_stale
    || dismissed
  ) {
    return null;
  }

  const affected = staleness.affected_files_count;
  const total = staleness.scaffold_files_count;
  const ctaEnabled = Boolean(onRefreshClick) && !refreshing;

  return (
    <div
      className="av-staleness-banner"
      role="status"
      aria-live="polite"
    >
      <p className="av-staleness-banner__body">
        Main has moved since Inspira drafted this —{" "}
        <strong>{affected}</strong>{" "}
        {affected === 1 ? "change affects" : "changes affect"}{" "}
        <strong>{total}</strong>{" "}
        {total === 1 ? "file" : "files"}. Refresh against the latest
        main?
      </p>
      <div className="av-staleness-banner__actions">
        <button
          type="button"
          className="av-staleness-banner__cta"
          disabled={!ctaEnabled}
          aria-disabled={!ctaEnabled}
          onClick={onRefreshClick}
          title={
            onRefreshClick
              ? undefined
              : "Refresh is unavailable in this build."
          }
        >
          {refreshing ? "Refreshing…" : "Refresh PR with Inspira"}
        </button>
        <button
          type="button"
          className="av-staleness-banner__dismiss"
          onClick={() => setDismissed(true)}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
