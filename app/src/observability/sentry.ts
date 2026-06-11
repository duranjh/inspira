/**
 * Inspira — Sentry wiring (observability/sentry.ts).
 *
 * This module is the single entry point for Sentry. It replaces the older
 * src/monitoring/sentry.ts stub — the imports in main.tsx point here now.
 *
 * Two exports:
 *
 *   initSentry(): void
 *     Reads VITE_SENTRY_DSN and boots Sentry.init. If the DSN is absent we
 *     log once at debug level and skip — dev builds shouldn't nag, and the
 *     app must still mount. Release tag comes from VITE_RELEASE (baked in
 *     by vite.config.ts from $GITHUB_SHA or `git rev-parse --short HEAD`);
 *     falls back to "dev" if that define block is missing (unit tests).
 *
 *   SentryErrorBoundary
 *     Thin wrapper around Sentry.ErrorBoundary with a warm editorial
 *     fallback — serif "Something came loose.", italic "We've logged it.
 *     Try reloading the page.", and a reload button. Styling matches the
 *     rest of the Inspira fallback surfaces (cream paper, sage accent).
 *
 * Noise filtering: beforeSend drops the three well-known React/Vite
 * false-positives that otherwise flood the project:
 *   - "ResizeObserver loop limit exceeded" — a benign layout-thrash warning
 *     that every React + animation codebase triggers.
 *   - "ChunkLoadError" — thrown when a deployed bundle is replaced while
 *     the user has a tab open; a reload resolves it.
 *   - "Failed to fetch" — the generic network-offline TypeError the browser
 *     raises on fetch() when the network drops; not actionable.
 *
 * Note: we intentionally re-expose the SDK on window.Sentry so the existing
 * ErrorBoundary.tsx (which calls window.Sentry?.captureException without
 * importing the SDK) keeps working unchanged.
 */

import * as Sentry from "@sentry/react";
import React from "react";

/**
 * Errors whose `message` matches any of these are silently dropped before
 * they ever hit Sentry. Exported so the vitest suite can re-run the same
 * predicate without duplicating the regex list.
 */
export const SENTRY_NOISE_PATTERNS: readonly RegExp[] = [
  /ResizeObserver loop/i,
  /ChunkLoadError/i,
  /Failed to fetch/i,
];

/**
 * Header names that get scrubbed from Sentry events before send. Auth and
 * cookie material is NEVER allowed to leave the user's browser via the
 * error pipeline — even an unintentional ``request.headers`` capture would
 * be enough to leak a session.
 */
const SENSITIVE_HEADER_NAMES: readonly string[] = [
  "authorization",
  "cookie",
  "set-cookie",
  "x-api-key",
  "x-auth-token",
];

/**
 * Field names that, when present in an event payload, get their value
 * replaced with the literal string ``[scrubbed]``. The match is
 * case-insensitive and applied to both top-level and nested keys, so
 * ``request.data.password`` and ``extra.user.password`` are both
 * neutralised.
 */
const SENSITIVE_FIELD_NAMES: readonly string[] = [
  "password",
  "password_hash",
  "new_password",
  "old_password",
  "current_password",
  "token",
  "access_token",
  "refresh_token",
  "session",
  "session_id",
  "api_key",
  "apikey",
  "secret",
  "client_secret",
  "email",
];

const SCRUB_PLACEHOLDER = "[scrubbed]";

/**
 * Recursively walk ``value``, replacing the value of any object key whose
 * lower-cased name is in {@link SENSITIVE_FIELD_NAMES} with the scrub
 * placeholder. Returns the same object reference (mutated in place); the
 * recursion is bounded to depth 6 to avoid blowing the stack on cyclic
 * structures from third-party SDKs.
 */
function scrubFields(value: unknown, depth = 0): unknown {
  if (depth > 6 || value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      value[i] = scrubFields(value[i], depth + 1);
    }
    return value;
  }
  const obj = value as Record<string, unknown>;
  for (const key of Object.keys(obj)) {
    if (SENSITIVE_FIELD_NAMES.includes(key.toLowerCase())) {
      obj[key] = SCRUB_PLACEHOLDER;
    } else {
      obj[key] = scrubFields(obj[key], depth + 1);
    }
  }
  return obj;
}

/**
 * Strip query strings off URLs we report — backend tokens land in
 * ``?token=…`` on password-reset / magic-link flows and we never want
 * those captured. Returns the URL with everything from the first ``?``
 * removed, plus a ``[query-scrubbed]`` marker so the field still sorts.
 */
function scrubUrlQuery(url: unknown): unknown {
  if (typeof url !== "string") return url;
  const idx = url.indexOf("?");
  if (idx === -1) return url;
  return `${url.slice(0, idx)}?[query-scrubbed]`;
}

/**
 * Apply PII scrubbing to a Sentry event in place. Public so the test suite
 * can call it directly without having to round-trip through ``beforeSend``.
 *
 * Rules:
 *   - ``request.headers``: drop Authorization / Cookie / API-key headers.
 *   - ``request.cookies``: replace entirely with the placeholder.
 *   - ``request.url`` / ``request.query_string``: strip the query string.
 *   - ``request.data`` (POST body) and ``extra`` / ``contexts``: walk
 *     and scrub any field named like a credential.
 *   - ``user.email`` / ``user.username``: dropped; we keep ``user.id``
 *     (set by the auth hook to the opaque ``user_id``) so errors stay
 *     traceable without PII.
 *   - Breadcrumbs: scrub URLs in fetch/XHR data and apply the field walk.
 */
export function scrubEventPii(event: Sentry.ErrorEvent): Sentry.ErrorEvent {
  const req = event.request as
    | {
        headers?: Record<string, unknown>;
        cookies?: unknown;
        url?: unknown;
        query_string?: unknown;
        data?: unknown;
      }
    | undefined;
  if (req) {
    if (req.headers && typeof req.headers === "object") {
      for (const key of Object.keys(req.headers)) {
        if (SENSITIVE_HEADER_NAMES.includes(key.toLowerCase())) {
          req.headers[key] = SCRUB_PLACEHOLDER;
        }
      }
    }
    if (req.cookies !== undefined) req.cookies = SCRUB_PLACEHOLDER;
    req.url = scrubUrlQuery(req.url);
    if (typeof req.query_string === "string") {
      req.query_string = "[scrubbed]";
    }
    if (req.data !== undefined) req.data = scrubFields(req.data);
  }
  if (event.extra) scrubFields(event.extra);
  if (event.contexts) scrubFields(event.contexts);
  if (event.tags) scrubFields(event.tags);
  // Drop email/username from the user context — we explicitly only want
  // the opaque id.
  if (event.user && typeof event.user === "object") {
    const u = event.user as { email?: unknown; username?: unknown };
    if ("email" in u) delete u.email;
    if ("username" in u) delete u.username;
  }
  // Breadcrumbs commonly include fetch URLs with query params; scrub.
  if (Array.isArray(event.breadcrumbs)) {
    for (const crumb of event.breadcrumbs) {
      if (crumb && typeof crumb === "object") {
        const data = (crumb as { data?: Record<string, unknown> }).data;
        if (data && typeof data === "object") {
          if ("url" in data) data.url = scrubUrlQuery(data.url);
          scrubFields(data);
        }
      }
    }
  }
  return event;
}

/**
 * The concrete `beforeSend` used by Sentry.init. Pulled out as a named
 * export so tests can validate the filter without pulling in the full
 * Sentry.init path (which talks to the DSN and is awkward to mock).
 */
export function sentryBeforeSend(
  event: Sentry.ErrorEvent,
  hint: Sentry.EventHint,
): Sentry.ErrorEvent | null {
  // Prefer the original thrown object when it's present (hint.originalException
  // carries the real Error with its message). Fall back to the last entry of
  // event.exception.values[].value, which is the serialised form Sentry uses
  // when the event wasn't produced from a thrown Error.
  const original = hint?.originalException;
  let message = "";
  if (original instanceof Error) {
    message = original.message;
  } else if (typeof original === "string") {
    message = original;
  } else if (event.message) {
    message = event.message;
  } else {
    const values = event.exception?.values;
    if (values && values.length > 0) {
      // Last entry is the outer/aggregate exception; its value is the message.
      const last = values[values.length - 1];
      message = last?.value ?? "";
    }
  }

  for (const pattern of SENTRY_NOISE_PATTERNS) {
    if (pattern.test(message)) return null;
  }
  // Scrub PII (auth headers, cookies, password fields, query strings,
  // user email) AFTER the noise filter so we don't waste cycles scrubbing
  // events we're about to drop anyway.
  return scrubEventPii(event);
}

/**
 * Convenience for the auth bootstrap to call once a session is resolved.
 * Sets only the opaque ``user_id`` on the Sentry scope — never the email,
 * never the display name. Pass ``null`` to clear (e.g. on logout).
 *
 * Safe to call before {@link initSentry} — Sentry.setUser is a global
 * scope mutation that no-ops with no client bound, and the next setUser
 * after init will pick up the value.
 */
export function setSentryUser(userId: string | null): void {
  try {
    if (userId === null) {
      Sentry.setUser(null);
      return;
    }
    Sentry.setUser({ id: userId });
  } catch {
    // setUser must never throw out of the bootstrap path.
  }
}

// NOTE: we intentionally do NOT re-declare `Window.Sentry` here — the
// ErrorBoundary already declares a loose-typed `Sentry?.captureException`
// ambient that fits both the SDK and the runtime-free fallback. Having
// two `declare global` blocks with different shapes triggers
// "Subsequent property declarations must have the same type" (TS2717).

let _initialised = false;
let _warnedMissingDsn = false;

/** Initialise Sentry. Safe to call multiple times; later calls are no-ops. */
export function initSentry(): void {
  if (_initialised) return;

  const dsn = import.meta.env.VITE_SENTRY_DSN as string | undefined;
  if (!dsn) {
    if (!_warnedMissingDsn) {
      _warnedMissingDsn = true;
      // eslint-disable-next-line no-console
      console.debug("[Sentry] disabled — VITE_SENTRY_DSN not set");
    }
    return;
  }

  const release =
    (import.meta.env.VITE_RELEASE as string | undefined) || "dev";

  // Pick an integration set. If react-router is on the page we'd wire its
  // reactRouterV6BrowserTracingIntegration; this codebase uses plain
  // `window.location.pathname` routing today so browserTracingIntegration
  // is the right default.
  const integrations: Sentry.BrowserOptions["integrations"] = [
    Sentry.browserTracingIntegration(),
    Sentry.replayIntegration(),
  ];

  Sentry.init({
    dsn,
    release,
    environment: import.meta.env.MODE,
    integrations,
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0.0,
    replaysOnErrorSampleRate: 1.0,
    beforeSend: sentryBeforeSend,
  });

  _initialised = true;

  // Expose the SDK so ErrorBoundary.tsx can report without importing us.
  // The component declares a loose ambient type with just
  // `captureException`; cast through unknown to satisfy both shapes.
  if (typeof window !== "undefined") {
    (window as { Sentry?: unknown }).Sentry = Sentry;
  }
}

/** Test-only helper — resets module state so a second initSentry() runs. */
export function __resetSentryForTests(): void {
  _initialised = false;
  _warnedMissingDsn = false;
}

/** Returns the Sentry SDK if init has been completed, else undefined. */
export function getSentryClient(): typeof Sentry | undefined {
  return _initialised ? Sentry : undefined;
}

// ---------------------------------------------------------------------------
// Warm fallback UI used by SentryErrorBoundary.
// ---------------------------------------------------------------------------

function reloadPage(): void {
  if (typeof window !== "undefined") window.location.reload();
}

function WarmFallback(): React.ReactElement {
  const wrapper: React.CSSProperties = {
    minHeight: "100dvh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--paper, #F5F0E6)",
    padding: 24,
  };
  const card: React.CSSProperties = {
    background: "var(--paper, #F5F0E6)",
    color: "var(--ink, #2B2520)",
    borderRadius: 14,
    boxShadow:
      "0 32px 72px -24px rgba(43, 37, 32, 0.36), 0 2px 4px rgba(43, 37, 32, 0.08)",
    border: "1px solid var(--paper-edge, #DBCFB6)",
    width: "min(440px, 100%)",
    padding: 36,
    fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  };
  const heading: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontSize: 24,
    fontWeight: 400,
    margin: "0 0 12px",
    color: "var(--ink, #2B2520)",
  };
  const body: React.CSSProperties = {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontStyle: "italic",
    fontSize: 14,
    color: "var(--ink-3, #7A6F64)",
    margin: "0 0 24px",
  };
  const button: React.CSSProperties = {
    background: "var(--sage, #6A9A7A)",
    color: "#FFFFFF",
    border: "none",
    borderRadius: 8,
    padding: "10px 18px",
    fontFamily: "var(--ff-sans, system-ui, sans-serif)",
    fontSize: 14,
    cursor: "pointer",
  };

  return React.createElement(
    "div",
    { style: wrapper, role: "alert", "aria-live": "assertive" },
    React.createElement(
      "div",
      { style: card },
      React.createElement("h1", { style: heading }, "Something came loose."),
      React.createElement(
        "p",
        { style: body },
        "We've logged it. Try reloading the page.",
      ),
      React.createElement(
        "button",
        { type: "button", style: button, onClick: reloadPage },
        "Reload",
      ),
    ),
  );
}

/**
 * Thin wrapper over Sentry.ErrorBoundary that:
 *   - renders the warm editorial fallback (serif heading, sage reload button)
 *   - reports the error to Sentry via the normal boundary path
 *
 * If Sentry was never initialised (no DSN), this still renders the fallback
 * UI when a child throws — no reports are sent, which is the desired dev
 * behaviour.
 */
export function SentryErrorBoundary(
  props: { children: React.ReactNode },
): React.ReactElement {
  return React.createElement(
    Sentry.ErrorBoundary,
    { fallback: React.createElement(WarmFallback) },
    props.children,
  );
}
