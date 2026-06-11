/**
 * Inspira — privacy-first analytics (Plausible).
 *
 * Why Plausible: cookieless, GDPR-friendly, small script, cheap. Plays
 * nicely with the cookie banner another agent is landing — no consent
 * prompt is required for page views, and custom events are gated by the
 * same consent key the banner writes.
 *
 * Consent model
 * -------------
 * We read `localStorage.inspira_cookie_consent`:
 *   - unset / "all" → fire everything (page views + custom events)
 *   - "essential"   → fire page views only (Plausible is cookieless so
 *                     this is strictly speaking fine to include, but we
 *                     still respect the user's intent by muting custom
 *                     events — that's the promise of "essential only")
 *   - "none"        → fire nothing (kept for parity with future banner
 *                     implementations that add a stricter deny option)
 *
 * Event vocabulary
 * ----------------
 * The event names below are the full shipped set as of this module's
 * landing. Events are not instrumented in the app yet — this file ships
 * the primitives only, and the marketing / kickoff / export agents will
 * wire the actual emit sites when they land.
 *
 *   landing_cta_clicked      cta: "start_mapping" | "sign_in"
 *   signup_started           (no props)
 *   signup_completed         (no props)
 *   first_map_created        source: "kickoff" | "template" | "markdown" | "example"
 *   topic_turn_sent          (no props)
 *   canvas_exported          format: "pdf" | "markdown" | "json" | "csv"
 *   template_picked          slug: <string>
 *
 * If you want to add an event: extend `AnalyticsEvent`, add a JSDoc line
 * above, and leave instrumentation to the feature owner. Keep the list
 * short — every event is a line item in the funnel dashboard.
 */

declare global {
  interface Window {
    /**
     * Plausible injects this when its script loads. Signature per
     * https://plausible.io/docs/custom-event-goals — the second arg is
     * an options bag that carries `props`.
     */
    plausible?: (
      eventName: string,
      options?: { props?: Record<string, string | number | boolean> },
    ) => void;
  }
}

// ---------------------------------------------------------------------------
// Event vocabulary — see the JSDoc above for the full list.
// ---------------------------------------------------------------------------

export type AnalyticsEvent =
  | { name: "landing_cta_clicked"; props: { cta: "start_mapping" | "sign_in" } }
  | { name: "signup_started" }
  | { name: "signup_completed" }
  | {
      name: "first_map_created";
      props: { source: "kickoff" | "template" | "markdown" | "example" };
    }
  | { name: "topic_turn_sent" }
  | {
      name: "canvas_exported";
      props: { format: "pdf" | "markdown" | "json" | "csv" };
    }
  | { name: "template_picked"; props: { slug: string } };

const CONSENT_KEY = "inspira_cookie_consent";

type ConsentLevel = "all" | "essential" | "none";

function readConsent(): ConsentLevel {
  try {
    const raw = localStorage.getItem(CONSENT_KEY);
    if (raw === "essential" || raw === "none" || raw === "all") return raw;
    // Anything else (unset, corrupt) → treat as "all". Plausible is
    // cookieless so this is a safe default and matches the banner's
    // "don't prompt until the user decides" behaviour in dev.
    return "all";
  } catch {
    return "all";
  }
}

// ---------------------------------------------------------------------------
// Init + emit primitives
// ---------------------------------------------------------------------------

let _initialised = false;
let _warnedMissingDomain = false;

/**
 * Inject the Plausible script tag. Safe to call more than once; later
 * calls no-op. Read `VITE_PLAUSIBLE_DOMAIN` — if unset, skip silently
 * (dev builds shouldn't nag).
 */
export function initAnalytics(): void {
  if (_initialised) return;
  if (typeof document === "undefined") return; // SSR guard

  const domain = import.meta.env.VITE_PLAUSIBLE_DOMAIN as string | undefined;
  if (!domain) {
    if (!_warnedMissingDomain) {
      _warnedMissingDomain = true;
      // eslint-disable-next-line no-console
      console.debug("[Analytics] disabled — VITE_PLAUSIBLE_DOMAIN not set");
    }
    return;
  }

  const consent = readConsent();
  if (consent === "none") {
    // The banner's strictest tier — skip injecting the script entirely.
    return;
  }

  const script = document.createElement("script");
  script.defer = true;
  script.src = "https://plausible.io/js/script.js";
  script.setAttribute("data-domain", domain);
  script.setAttribute("data-api", "https://plausible.io/api/event");
  document.head.appendChild(script);

  _initialised = true;
}

/** Test-only — reset module state so the next initAnalytics() runs. */
export function __resetAnalyticsForTests(): void {
  _initialised = false;
  _warnedMissingDomain = false;
}

/**
 * Low-level emit. Consent-gated: essential-only users see page views
 * but no custom events. Untyped on purpose — call `trackEvent` when
 * you want the typed vocabulary enforced.
 */
export function track(
  eventName: string,
  props?: Record<string, string | number | boolean>,
): void {
  if (typeof window === "undefined") return;
  const consent = readConsent();
  if (consent === "none") return;
  if (consent === "essential") return; // page views only

  const fn = window.plausible;
  if (typeof fn !== "function") return;
  try {
    if (props) fn(eventName, { props });
    else fn(eventName);
  } catch {
    // Never let analytics crash the page.
  }
}

/**
 * Typed event emit — pass an `AnalyticsEvent` and the compiler will
 * enforce the name + prop shape. This is the preferred call site.
 */
export function trackEvent<E extends AnalyticsEvent>(event: E): void {
  // Narrow with `in` so TS doesn't widen the union.
  if ("props" in event && event.props) {
    track(event.name, event.props as Record<string, string | number | boolean>);
  } else {
    track(event.name);
  }
}
