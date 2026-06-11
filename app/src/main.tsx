import React from "react";
import ReactDOM from "react-dom/client";

// Self-hosted typography (replaces the Google Fonts @import that used to
// live in App.css). Source Serif 4 ships as a variable font (single file,
// all weights/widths) plus an italic variable. Geist + Geist Mono ship
// per-weight; we load the same set the marketing site's Google Fonts URL
// did (300/400/500/600/700 for Geist; 400/500 for Geist Mono) so weight
// calls stay covered without faux-bold fallbacks. Self-hosting removes
// the third-party-domain network dependency for partners on locked-down
// networks and gets us deterministic FOUT control via the Fontsource
// @font-face declarations.
import "@fontsource-variable/source-serif-4";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "@fontsource/geist/300.css";
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist/700.css";
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/500.css";

import { BrowserRouter } from "react-router-dom";
import { AppRoutes } from "./routes";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./components/ToastProvider";
import { LocaleProvider } from "./i18n";
import { ThemeProvider } from "./theme";
import { WorkspaceProvider } from "./features/workspaces/WorkspaceContext";
import { initSentry, SentryErrorBoundary, getSentryClient } from "./observability/sentry";
import { initAnalytics } from "./observability/analytics";
import { registerSW } from "./pwa/registerSW";

// Fail loud if VITE_INSPIRA_API_URL wasn't baked into the bundle at build
// time. Without it the frontend falls back to relative /api/* requests,
// which on Cloudflare Pages hit the SPA catch-all and silently return
// index.html — a painful prod bug to diagnose from the UI alone.
//
// Only hard-crash on non-localhost hosts so Vite dev + Docker defaults
// keep working. In dev we just warn.
(() => {
  const apiUrl = import.meta.env.VITE_INSPIRA_API_URL as string | undefined;
  if (apiUrl && apiUrl.trim() !== "") return;
  const host =
    typeof window !== "undefined" ? window.location.hostname : "";
  const isLocal =
    host === "localhost" || host === "127.0.0.1" || host === "";
  const msg =
    "[inspira] VITE_INSPIRA_API_URL is not set — frontend will call " +
    "relative /api/* URLs and hit the static host instead of the backend. " +
    "Set it in the Cloudflare Pages build env (Settings → Environment " +
    "variables → Production).";
  if (isLocal) {
    // eslint-disable-next-line no-console
    console.warn(msg);
  } else {
    throw new Error(msg);
  }
})();

// Must run before ReactDOM.render so the Sentry hub is ready before any
// component tree mounts (and before the root ErrorBoundary is created).
initSentry();
initAnalytics();

const sentryReady = getSentryClient() !== undefined;

// Skip-to-main-content link.
//
// First focusable element in the document so keyboard users can jump
// past the header / nav chrome on every screen. Targets `#main-content`
// — every top-level shell (kickoff wrap, projects list, canvas) sets
// this id on its main landmark, with `tabindex="-1"` so the focus
// actually lands there (otherwise non-interactive `<main>` elements
// can't receive programmatic focus in some browsers).
//
// Visual styling lives in App.css (.skip-link). It's transform-hidden
// off-screen until focused, then pops into the top-left as a small
// sage-edged paper chip — matching the warm-editorial aesthetic.
function SkipToMainLink() {
  return (
    <a className="skip-link" href="#main-content">
      Skip to main content
    </a>
  );
}

// Inner subtree shared by both render paths.
const appTree = (
  <ErrorBoundary>
    <ThemeProvider>
      <LocaleProvider>
        {/* WorkspaceProvider sits inside LocaleProvider so the
            initial listWorkspaces() call rides the locale's
            Accept-Language header (when we wire that), and outside
            ToastProvider so the workspace context's error path
            can surface toasts. */}
        <WorkspaceProvider>
          <ToastProvider>
            <SkipToMainLink />
            <BrowserRouter>
              <AppRoutes />
            </BrowserRouter>
          </ToastProvider>
        </WorkspaceProvider>
      </LocaleProvider>
    </ThemeProvider>
  </ErrorBoundary>
);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {sentryReady ? (
      // When Sentry is initialised, wrap with the observability boundary so
      // captures flow to Sentry AND the warm editorial fallback renders on
      // crash. The inner ErrorBoundary still catches first for typical
      // render errors; SentryErrorBoundary is the outermost safety net.
      <SentryErrorBoundary>
        {appTree}
      </SentryErrorBoundary>
    ) : (
      appTree
    )}
  </React.StrictMode>,
);

registerSW();
