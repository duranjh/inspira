/**
 * Inspira — ShelfErrorBoundary.
 *
 * Scoped error boundary that wraps the shelves-aware surface on the
 * projects list: the `ShelvesView` subtree and, on the flat grid, the
 * `NewShelfDialog`. Catches render errors (Rules-of-Hooks mismatches,
 * unguarded array access, any future regression in the shelves UI) and
 * shows a compact warm editorial fallback so the rest of the app keeps
 * working.
 *
 * Differs from `CanvasErrorBoundary`:
 *   - Fallback is smaller and lives inline on the projects-list page
 *     rather than taking over the whole shell.
 *   - Primary action is "Dismiss" — it clears the captured error AND
 *     closes the NewShelfDialog via the optional `onDismiss` callback.
 *     That matches the product expectation: the user got stuck because
 *     of a shelf render bug; once they dismiss they should land back on
 *     the plain projects list, not a full reload.
 *
 * We intentionally reuse the class-shape and Sentry plumbing from
 * `CanvasErrorBoundary` so the two feel identical and the triage
 * dashboard can keep slicing by `area` tag.
 *
 * `resetKey` lets callers (e.g. a route change, a user log-in swap)
 * force-clear a captured error without clicking Dismiss. Mirrors the
 * pattern in `ErrorBoundary`.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { t } from "../i18n";

/* ---------------------------------------------------------------------------
 * Loose ambient for the optional window.Sentry surface — shape matches the
 * declarations in ErrorBoundary.tsx / CanvasErrorBoundary.tsx so all three
 * files play nicely side-by-side without TS2717 grumbles.
 * ------------------------------------------------------------------------- */
declare global {
  interface Window {
    Sentry?: {
      captureException?: (err: unknown, ctx?: unknown) => void;
    };
  }
}

export interface ShelfErrorBoundaryProps {
  children: ReactNode;
  /**
   * Called when the user clicks Dismiss on the fallback. The caller is
   * expected to close the NewShelfDialog (if open) and clean up any
   * shelf-creation state. The boundary also clears its captured error on
   * dismiss so subsequent renders go through the children again.
   */
  onDismiss?: () => void;
  /**
   * When this value changes, the boundary re-mounts its subtree and
   * clears any captured error. Useful on route/phase swaps where the
   * user navigates away from a crashed shelf view.
   */
  resetKey?: unknown;
}

interface ShelfErrorBoundaryState {
  error: Error | null;
}

export class ShelfErrorBoundary extends Component<
  ShelfErrorBoundaryProps,
  ShelfErrorBoundaryState
> {
  state: ShelfErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ShelfErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Always log — the dev overlay fires separately in dev, and prod
    // console streams are how we triage pre-Sentry reports.
    // eslint-disable-next-line no-console
    console.error("[ShelfErrorBoundary] caught render error:", error, info);

    if (typeof window !== "undefined") {
      const sentry = window.Sentry;
      if (sentry && typeof sentry.captureException === "function") {
        try {
          sentry.captureException(error, {
            tags: { area: "shelves" },
            contexts: { react: { componentStack: info.componentStack } },
          });
        } catch {
          // Instrumentation must never extend a crash.
        }
      }
    }
  }

  componentDidUpdate(prev: ShelfErrorBoundaryProps): void {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  dismiss = (): void => {
    this.setState({ error: null });
    try {
      this.props.onDismiss?.();
    } catch (err) {
      // Caller handler must never keep the fallback stuck. A thrown
      // onDismiss gets logged but we still clear state above.
      // eslint-disable-next-line no-console
      console.error("[ShelfErrorBoundary] onDismiss handler threw:", err);
    }
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div
        className="inspira-boundary inspira-boundary--shelves"
        role="alert"
        aria-live="assertive"
      >
        <div className="inspira-boundary__inner">
          <p className="inspira-boundary__eyebrow">
            {t("shelf_error.eyebrow")}
          </p>
          <h2 className="inspira-boundary__heading">
            {t("shelf_error.heading")}
          </h2>
          <p className="inspira-boundary__body">{t("shelf_error.body")}</p>
          <div className="inspira-boundary__actions">
            <button
              type="button"
              className="inspira-boundary__btn inspira-boundary__btn--primary"
              onClick={this.dismiss}
              autoFocus
            >
              {t("shelf_error.dismiss")}
            </button>
          </div>
        </div>
      </div>
    );
  }
}

export default ShelfErrorBoundary;
