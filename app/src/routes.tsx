// Inspira — top-level route table.
//
// `/` is the partner-journey entry point: anon → SignInPage,
// authed-without-workspace → /onboarding, authed-with-workspace →
// /workspaces (per founder direction 2026-05-03 + Wave 3 audit
// closure of #121).
//
// The legacy v5 marketing LandingPage stays mounted on direct-nav
// routes (/teams, /how-it-works, /pricing) but is no longer the
// anon `/` surface.
//
// Why the thin wrappers? The rest of the app has never known about
// react-router; `App.tsx` already sniffs `window.location.pathname` to
// split shared-canvas, reset-password, and the main app. Rather than
// rewriting that, we render `<App />` for every non-marketing path and
// let its existing logic take over. Marketing routes stay fully
// router-aware so the header / footer links and the landing-to-signup
// hand-off all use `<Link>` navigation instead of full reloads.

import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { App } from "./App";
import { CookieBanner } from "./components/CookieBanner";
import { PromoteToProjectController } from "./features/inspira/promote";
import { BillingRoute } from "./features/billing";
import { EmailConfirmPage } from "./features/email-confirm";
import { MembersRoute } from "./features/members";
import { IntegrationsPage } from "./features/integrations/IntegrationsPage";
import { CodeRoute } from "./features/code/CodeRoute";
import { ConnectorsPage } from "./features/connectors/ConnectorsPage";
import { WorkspaceDetailPage } from "./features/settings/WorkspaceDetailPage";
import { WorkspacesSettingsPage } from "./features/settings/WorkspacesSettingsPage";
import { InboxPage } from "./features/inbox/InboxPage";
import { WorkspaceKanbanRoute } from "./features/kanban";
import { SignInPage } from "./features/auth/SignInPage";
import { OnboardingWizard } from "./features/onboarding/wizard";
import { FeaturesPage } from "./features/marketing/FeaturesPage";
import { ForSoftwareTeamsPage } from "./features/marketing/ForSoftwareTeamsPage";
import { HowItWorksPage } from "./features/marketing/HowItWorksPage";
import { LegalPage } from "./features/marketing/LegalPage";
import { MarketingLayout } from "./features/marketing/MarketingLayout";
import { NotFoundPage } from "./features/marketing/NotFoundPage";
import { PricingPage } from "./features/marketing/PricingPage";
import { PrivacyPage } from "./features/marketing/PrivacyPage";
import { TermsPage } from "./features/marketing/TermsPage";
import { UnsubscribePage } from "./features/marketing/UnsubscribePage";
import StatusPage from "./features/status/StatusPage";
import { api } from "./features/inspira/api";

/**
 * Routes the partner journey from `/`:
 * - is_system → SignInPage (anon → sign-in surface)
 * - default_workspace_id absent → /onboarding (first-time)
 * - default_workspace_id present → /workspaces (returning)
 *
 * On backend unreachable, falls through to the sign-in surface (a
 * cold visitor still sees something meaningful).
 */
function RootGate() {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "signin" }
    | { kind: "redirect"; to: string }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        if (me.is_system) {
          setState({ kind: "signin" });
        } else if (!me.default_workspace_id) {
          setState({ kind: "redirect", to: "/onboarding" });
        } else {
          setState({ kind: "redirect", to: "/workspaces" });
        }
      } catch {
        if (cancelled) return;
        setState({ kind: "signin" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <div
        aria-busy="true"
        aria-live="polite"
        style={{
          minHeight: "100dvh",
          background: "var(--paper, #F5F0E6)",
        }}
      />
    );
  }
  if (state.kind === "redirect") {
    return <Navigate replace to={state.to} />;
  }
  return <SignInPage />;
}

/**
 * Root route table. Marketing surfaces are explicit; everything else
 * (including `/app/*`, `/shared/*`, `/reset-password`, and arbitrary
 * canvas deep-links the app might add later) falls through to
 * `<App />`, which sniffs the pathname itself and picks the right
 * internal surface.
 */
export function AppRoutes() {
  return (
    <>
      <Routes>
        <Route path="/" element={<RootGate />} />
        {/* T2.3: explicit /signup and /login routes redirect to the
            landing page with the appropriate query so AuthPanel
            auto-opens. Without these, /signup fell through to the
            catch-all and rendered the anonymous-mode kickoff form
            instead of the signup form — confusing for anyone the
            user-facing copy points at "/signup". */}
        <Route
          path="/signup"
          element={<Navigate replace to="/?signup=1" />}
        />
        <Route
          path="/login"
          element={<Navigate replace to="/?signin=1" />}
        />
        <Route path="/features" element={<FeaturesPage />} />
        <Route path="/how-it-works" element={<HowItWorksPage />} />
        <Route path="/teams" element={<ForSoftwareTeamsPage />} />
        {/* About page hidden — redirect to home until the page is ready. */}
        <Route path="/about" element={<Navigate replace to="/" />} />
        <Route path="/pricing" element={<PricingPage />} />
        <Route path="/legal/privacy" element={<PrivacyPage />} />
        <Route path="/legal/terms" element={<TermsPage />} />
        <Route path="/legal/cookies" element={<LegalPage doc="cookies" />} />
        <Route path="/legal/dmca" element={<LegalPage doc="dmca" />} />
        <Route
          path="/legal/acceptable-use"
          element={<LegalPage doc="acceptable-use" />}
        />
        <Route path="/legal/gdpr" element={<LegalPage doc="gdpr" />} />
        {/* Back-compat redirects from the old short paths. */}
        <Route path="/privacy" element={<Navigate replace to="/legal/privacy" />} />
        <Route path="/terms" element={<Navigate replace to="/legal/terms" />} />
        <Route path="/unsubscribe" element={<UnsubscribePage />} />
        <Route path="/billing" element={<BillingRoute />} />
        <Route path="/members" element={<MembersRoute />} />
        <Route path="/email-confirm" element={<EmailConfirmPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        {/* W2 v4 routes — wrapped by AuthedShell internally.
            /connectors lands its real page in C6 (currently a
            stub); /workspaces mounts the 5-column Kanban Workspace
            Home (post-Wave-3 partner journey — closes #122). The
            wrapper resolves default_workspace_id from /api/auth/me;
            anon → /, no workspace → /onboarding. */}
        <Route path="/workspaces" element={<WorkspaceKanbanRoute />} />
        <Route path="/connectors" element={<ConnectorsPage />} />
        <Route path="/inbox" element={<InboxPage />} />
        {/* Founder direction 2026-05-06: lift Code to its own
            top-level route so the IDE is a first-class app feature
            (rail tab, full breadcrumb, no modal trap). Layer 2 (real
            file-system browser with main + PR folders) is deferred
            to a separate ticket — see vault design brief
            inspira-design-brief-artifact-viewer-ide-features-2026-05-06. */}
        <Route path="/code" element={<CodeRoute />} />
        <Route path="/code/:projectId" element={<CodeRoute />} />
        <Route
          path="/settings/workspaces"
          element={<WorkspacesSettingsPage />}
        />
        <Route
          path="/settings/workspaces/:workspaceId"
          element={<WorkspaceDetailPage />}
        />
        {/* Wave 3.1 — v4 4-step Onboarding Wizard. RootGate dispatches
            authed-without-workspace partners here; direct-nav
            (e.g., GitHub OAuth callback redirect_to=/onboarding?step=2)
            also lands here. The wizard handles its own anon-redirect. */}
        <Route path="/onboarding" element={<OnboardingWizard />} />

        <Route
          path="/status"
          element={
            <MarketingLayout>
              <StatusPage />
            </MarketingLayout>
          }
        />
        {/*
         * Unknown `/legal/*` paths are marketing-shaped 404s — render the
         * NotFoundPage explicitly so a typo'd legal link doesn't fall
         * through into `<App />` (which is the canvas shell).
         */}
        <Route path="/legal/*" element={<NotFoundPage />} />

        {/*
         * T2.4: explicit routes for the paths that App.tsx handles
         * via window.location.pathname sniffing. Without these, the
         * catch-all below would route them to NotFoundPage instead.
         * Each just renders <App /> — the path-sniffer inside App.tsx
         * picks the right surface.
         */}
        <Route path="/app" element={<App />} />
        <Route path="/app/*" element={<App />} />
        <Route path="/shared/:token" element={<App />} />
        <Route path="/reset-password" element={<App />} />
        <Route path="/maintenance" element={<App />} />
        <Route path="/account-deactivated" element={<App />} />

        {/* Anything else is a 404. */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      <CookieBanner />
      {/* B2.3 / W3 δ — listens globally for `inspira:promote-to-project`
          dispatched by the inbox drawer's "Promote to project" button.
          Mounted here (sibling of Routes, NOT inside <App />) because
          /inbox bypasses <App />, and a controller mounted lower would
          miss the event. EAGER MOUNT REQUIRED — do NOT lazy-load. */}
      <PromoteToProjectController />
    </>
  );
}
