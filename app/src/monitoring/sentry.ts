/**
 * Inspira — Sentry initialisation helper.
 *
 * Call `initSentry()` once, before ReactDOM.render.
 *
 * Gate: reads VITE_SENTRY_DSN (baked in by Vite at build time).
 *   - If the env var is absent/empty the function is a no-op and logs a
 *     single debug line so developers know monitoring is off.
 *   - If the DSN is present Sentry is initialised and then exposed as
 *     `window.Sentry` so the existing ErrorBoundary pickup continues to
 *     work without importing the SDK itself.
 *
 * __APP_VERSION__ is injected by Vite's `define` config (falls back to
 * 'dev' when the define block is absent, e.g. in unit-test environments).
 */

import * as Sentry from "@sentry/react";

declare const __APP_VERSION__: string | undefined;

let _initialised = false;

export function initSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN as string | undefined;

  if (!dsn) {
    // eslint-disable-next-line no-console
    console.debug("[Sentry] disabled — no DSN set");
    return;
  }

  if (_initialised) return;
  _initialised = true;

  let release: string;
  try {
    release = __APP_VERSION__ || "dev";
  } catch {
    release = "dev";
  }

  Sentry.init({
    dsn,
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 1.0,
    environment: import.meta.env.MODE,
    release,
  });

  // Expose on window so ErrorBoundary.tsx can call window.Sentry?.captureException
  // without importing the SDK directly (keeps ErrorBoundary SDK-agnostic).
  window.Sentry = Sentry as unknown as typeof window.Sentry;
}

/** Returns the Sentry client if Sentry was successfully initialised. */
export function getSentryClient(): typeof Sentry | undefined {
  return _initialised ? Sentry : undefined;
}
