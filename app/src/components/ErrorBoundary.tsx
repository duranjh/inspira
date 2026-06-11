/**
 * Inspira — ErrorBoundary.
 *
 * Catches render errors anywhere in its subtree and shows a warm editorial
 * fallback (cream paper, serif display heading, muted ink body). The user
 * gets a "Reload" button and a collapsible "Technical details" section
 * (<details>) showing error.message and error.stack.
 *
 * Reset-on-key-change: pass `resetKey` (e.g. the active project id). When
 * that prop changes, the boundary re-mounts its subtree, so swapping the
 * active project after a crash doesn't leave the fallback UI sticky.
 *
 * Logging:
 *   - Always: console.error with the error + componentStack.
 *   - Always: best-effort POST to /api/client-errors (fire-and-forget).
 *   - Prod: calls window.Sentry?.captureException if present. We do NOT
 *     import the Sentry SDK; wiring is the API layer's responsibility.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import "./Toast.css";

import { t } from "../i18n";

/* ---------------------------------------------------------------------------
 * Loose ambient for an optional global Sentry — we never import the SDK.
 * ------------------------------------------------------------------------- */
declare global {
  interface Window {
    Sentry?: {
      captureException?: (err: unknown, ctx?: unknown) => void;
    };
  }
}

export interface ErrorBoundaryProps {
  children: ReactNode;
  /**
   * When this value changes, the boundary re-mounts its subtree and clears
   * any captured error. Useful for project switches, route changes, etc.
   */
  resetKey?: unknown;
  /** Optional custom fallback override. Receives the current error + a reset fn. */
  fallback?: (args: { error: Error; reset: () => void }) => ReactNode;
  /** Optional error listener (e.g. to emit a toast from the caller). */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Always log to the console — dev overlay still fires separately in dev.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] caught render error:", error, info);

    // Best-effort POST to the backend client-error logger (fire-and-forget).
    // Never awaited, never re-thrown — instrumentation must not extend a crash.
    try {
      const apiBase =
        (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
        "http://127.0.0.1:4174";
      fetch(`${apiBase}/api/client-errors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: error.message,
          stack: error.stack ?? null,
          componentStack: info.componentStack ?? null,
          href: typeof window !== "undefined" ? window.location.href : null,
        }),
        // keepalive lets the request outlive a page unload if the error
        // also triggers navigation (e.g. a redirect to an error page).
        keepalive: true,
      }).catch(() => {
        // Swallow — the server may not be reachable in dev or offline.
      });
    } catch {
      // Never let instrumentation break the boundary.
    }

    // Prod: best-effort Sentry capture if the host page injected it.
    // We tag with `area=root` so the triage dashboard can slice top-level
    // crashes from the area-scoped boundaries (canvas, shelves) that tag
    // their own captures. Without an `area` tag a root-boundary capture
    // shows up as "untagged" and is hard to bucket against the others.
    if (!import.meta.env.DEV && typeof window !== "undefined") {
      const sentry = window.Sentry;
      if (sentry && typeof sentry.captureException === "function") {
        try {
          sentry.captureException(error, {
            tags: { area: "root" },
            contexts: { react: { componentStack: info.componentStack } },
          });
        } catch {
          // Never let instrumentation break the boundary.
        }
      }
    }

    // Optional caller-supplied hook (e.g. surface a toast).
    if (this.props.onError) {
      try {
        this.props.onError(error, info);
      } catch {
        // ignore
      }
    }
  }

  componentDidUpdate(prev: ErrorBoundaryProps): void {
    // Clear a captured error when the reset key changes.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  reload = (): void => {
    if (typeof window !== "undefined") window.location.reload();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback({ error, reset: this.reset });
    }

    // Security: never show raw error messages, stack traces, or bundle paths
    // to end users in production. Stack traces leak the minified source map
    // shape and can help an attacker fingerprint which build / library
    // versions are in use. They also confuse non-technical users. Dev-only
    // detail remains available in `import.meta.env.DEV` so local debugging
    // stays quick.
    //
    // We still capture everything server-side via /api/client-errors and
    // Sentry (see componentDidCatch above), so on-call can trace the bug
    // without the user ever seeing internals. The optional "Reference"
    // hook below ties a user's report ("I saw this at 12:42") back to the
    // server-side log entry — currently sourced from window.__lastRequestId
    // which the observability middleware sets per request.
    const isDev = import.meta.env.DEV;
    const message = error.message || String(error);
    const stack = error.stack ?? "";
    const requestId =
      typeof window !== "undefined"
        ? (window as unknown as { __lastRequestId?: string }).__lastRequestId ?? null
        : null;

    // Clear any saved app state on hard reset so a corrupt blob in
    // localStorage can't keep triggering the same crash. Users reported
    // "refreshing still stuck on error page" — this is the escape hatch.
    //
    // Preserve-list rationale: blowing away EVERY key on a hard-reset
    // was hostile to users who'd typed half a page into a topic draft
    // and then hit a render crash on an unrelated surface — they'd lose
    // the unsaved work along with the bad state. Similarly, the cookie
    // consent flag, theme picks, and onboarding completion are settings
    // the user deliberately chose; wiping them forces them through the
    // first-run flow all over again. The regex below keeps:
    //   inspira_draft_*         — unsaved composer / topic drafts
    //   inspira_cookie_consent  — consent banner choice
    //   inspira_style           — Bookworm vs Modern
    //   inspira_theme_mode      — light / dark / system
    //   inspira_onboard*        — walkthrough + canvas + shortcuts coach flags
    // Everything else (feature flags cached from the server, ephemeral
    // UI layout prefs, etc.) still gets cleared so a corrupt cached blob
    // can't keep crashing on reload.
    const PRESERVE_RE =
      /^(inspira_draft_|inspira_cookie_consent$|inspira_style$|inspira_theme_mode$|inspira_onboard)/;
    const hardReset = () => {
      if (typeof window !== "undefined") {
        try {
          const saved: Record<string, string> = {};
          for (const k of Object.keys(window.localStorage)) {
            if (!PRESERVE_RE.test(k)) continue;
            const v = window.localStorage.getItem(k);
            if (v !== null) saved[k] = v;
          }
          window.localStorage.clear();
          for (const [k, v] of Object.entries(saved)) {
            window.localStorage.setItem(k, v);
          }
        } catch {
          // Access can throw on some private-browsing setups — harmless.
        }
        window.location.href = "/";
      }
    };

    return (
      <div className="inspira-boundary" role="alert" aria-live="assertive">
        <div className="inspira-boundary__inner">
          <p className="inspira-boundary__eyebrow">{t("error_boundary.eyebrow")}</p>
          <h1 className="inspira-boundary__heading">
            {t("error_boundary.heading")}
          </h1>
          <p className="inspira-boundary__body">
            {t("error_boundary.body")}
          </p>
          <div className="inspira-boundary__actions">
            <button
              type="button"
              className="inspira-boundary__btn inspira-boundary__btn--primary"
              onClick={this.reload}
            >
              {t("error_boundary.reload")}
            </button>
            <button
              type="button"
              className="inspira-boundary__btn inspira-boundary__btn--ghost"
              onClick={hardReset}
            >
              {t("error_boundary.hard_reset")}
            </button>
          </div>
          {requestId ? (
            <p className="inspira-boundary__reference">
              {t("error_boundary.reference")} <code>{requestId}</code>
            </p>
          ) : null}
          {isDev ? (
            <details className="inspira-boundary__details">
              <summary className="inspira-boundary__details-summary">
                {t("error_boundary.technical_details")}
              </summary>
              <pre className="inspira-boundary__message">
                {message}
                {"\n\n"}
                {stack}
              </pre>
            </details>
          ) : null}
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
