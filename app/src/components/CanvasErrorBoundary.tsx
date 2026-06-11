/**
 * Inspira — CanvasErrorBoundary.
 *
 * A scoped error boundary that wraps the ProjectCanvas (React Flow + MiniMap
 * + custom nodes + listeners). If any of those crashes mid-render, this
 * boundary catches it, reports to Sentry with an `area=canvas` tag, and
 * shows a warm editorial fallback that lets the user stay on the page —
 * they can retry (remounting the canvas subtree) or head back to their
 * projects list.
 *
 * We intentionally DO NOT reuse the top-level `ErrorBoundary` here. That
 * one's fallback takes the full viewport and reloads the whole app; for a
 * canvas-local crash we want the rest of the app shell (top bar, side
 * panels, toast stack) to keep working.
 *
 * Sentry wiring mirrors the pattern in ErrorBoundary.tsx: we read the SDK
 * off the global `window.Sentry` surface that src/observability/sentry.ts
 * publishes after `initSentry()`. That keeps this file free of any direct
 * Sentry import at runtime and happily no-ops in dev / tests where Sentry
 * was never initialised.
 *
 * Navigation: "Back to projects" prefers react-router's `useNavigate` —
 * but we can't call a hook inside a class component, so we dispatch a
 * custom `inspira:navigate-projects` event that InspiraApp's routing
 * layer can subscribe to, and fall back to a pathname reset + reload so
 * the button still works even when no listener is wired. Either path
 * moves the user away from the crashed canvas.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { t } from "../i18n";

/* ---------------------------------------------------------------------------
 * Loose ambient for the optional window.Sentry surface — identical shape to
 * the one declared in ErrorBoundary.tsx so both files play nicely side-by-
 * side without TS2717 "subsequent property declarations must have the same
 * type" grumbles.
 * ------------------------------------------------------------------------- */
declare global {
  interface Window {
    Sentry?: {
      captureException?: (err: unknown, ctx?: unknown) => void;
    };
  }
}

export interface CanvasErrorBoundaryProps {
  children: ReactNode;
  /**
   * Optional project title. When provided, the fallback heading reads
   * "This canvas (<title>) hit a snag." instead of the generic form —
   * gives the user a subtle confirmation of WHICH project blew up.
   */
  projectTitle?: string;
}

interface CanvasErrorBoundaryState {
  error: Error | null;
}

export class CanvasErrorBoundary extends Component<
  CanvasErrorBoundaryProps,
  CanvasErrorBoundaryState
> {
  state: CanvasErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): CanvasErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Always log — the dev overlay fires separately in dev, and prod
    // console streams are how we triage pre-Sentry reports.
    // eslint-disable-next-line no-console
    console.error("[CanvasErrorBoundary] caught render error:", error, info);

    // Best-effort Sentry capture with an `area=canvas` tag so the
    // triage dashboard can slice canvas crashes from the rest of the
    // top-level noise. We talk to the global surface so this file
    // stays import-light; see src/observability/sentry.ts for where
    // window.Sentry gets assigned.
    if (typeof window !== "undefined") {
      const sentry = window.Sentry;
      if (sentry && typeof sentry.captureException === "function") {
        try {
          sentry.captureException(error, {
            tags: { area: "canvas" },
            contexts: { react: { componentStack: info.componentStack } },
          });
        } catch {
          // Instrumentation must never extend a crash.
        }
      }
    }
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  navigateProjects = (): void => {
    // Prefer an app-level handler. InspiraApp (or any other router shim)
    // can listen for `inspira:navigate-projects` and do the right thing
    // without a full reload. If nothing listens we fall through to a
    // pathname reset — dispatchEvent returns true when no handler called
    // preventDefault, but we can't tell from that whether anyone actually
    // listened. So we always also try a history/location fallback.
    let handled = false;
    if (typeof window !== "undefined") {
      try {
        const ev = new CustomEvent("inspira:navigate-projects", {
          cancelable: true,
        });
        // If a listener calls preventDefault() we treat that as "handled"
        // and skip the reload — the listener is responsible for routing.
        const dispatched = window.dispatchEvent(ev);
        if (!dispatched || ev.defaultPrevented) handled = true;
      } catch {
        // CustomEvent unavailable in very old environments — fall through.
      }
    }
    if (!handled && typeof window !== "undefined") {
      try {
        // Hard reset to the app root. Marketing routes (/privacy, /terms,
        // /pricing) are handled by react-router at "/"; everything else
        // drops back into the authenticated app shell. A location.assign
        // both navigates and tears down the crashed subtree cleanly.
        window.location.assign("/");
      } catch {
        // Nothing else we can do from a class component.
      }
    }
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const title = this.props.projectTitle?.trim();
    const heading = title
      ? t("canvas_error.heading_with_title", { title })
      : t("canvas_error.heading");

    return (
      <div
        className="inspira-boundary inspira-boundary--canvas"
        role="alert"
        aria-live="assertive"
      >
        <div className="inspira-boundary__inner">
          <p className="inspira-boundary__eyebrow">
            {t("canvas_error.eyebrow")}
          </p>
          <h2 className="inspira-boundary__heading">{heading}</h2>
          <p className="inspira-boundary__body">{t("canvas_error.body")}</p>
          <div className="inspira-boundary__actions">
            <button
              type="button"
              className="inspira-boundary__btn inspira-boundary__btn--primary"
              onClick={this.reset}
            >
              {t("canvas_error.reload")}
            </button>
            <button
              type="button"
              className="inspira-boundary__btn"
              onClick={this.navigateProjects}
            >
              {t("canvas_error.back_to_projects")}
            </button>
          </div>
        </div>
      </div>
    );
  }
}

export default CanvasErrorBoundary;
