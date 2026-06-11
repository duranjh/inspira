// Minimal app shell. The previous version was a 900-line demo-driven mock
// of the v1 "Planning Studio" three-pane workspace — that's been replaced
// by the v2 Inspira canvas flow. The old mock lives in git history if
// anyone needs it.
//
// Routing: no router library — we check `window.location.pathname` at mount.
//   /maintenance         →  MaintenancePage (also INSPIRA_MAINTENANCE_MODE=1)
//   /account-deactivated →  AccountDeactivatedPage (or user has deleted_at)
//   /shared/<token>      →  SharedCanvasPage (read-only, no auth)
//   /reset-password      →  ResetPasswordPage (token from ?token= query param)
//   /?reset_token=<tok>  →  ResetPasswordPage (legacy URL the backend emails)
//   anything else        →  InspiraApp (full authenticated app)
//
// The FeedbackWidget mounts at the App root when the pathname is NOT a
// marketing route and the caller is authenticated. It does its own
// auth check via /api/auth/me so it's safe to mount unconditionally;
// we still branch on maintenance / deactivated / shared / reset so the
// pill doesn't appear on those quiet-center surfaces either.

import { useEffect, useState } from "react";

import "./App.css";
import { AccountDeactivatedPage } from "./features/account/AccountDeactivatedPage";
import { FeedbackWidget } from "./features/feedback/FeedbackWidget";
import { InspiraApp } from "./features/inspira/InspiraApp";
import { MaintenancePage } from "./features/MaintenancePage";
import { ResetPasswordPage } from "./features/reset-password/ResetPasswordPage";
import { SharedCanvasPage } from "./features/shared/SharedCanvasPage";
import { api, type AuthedUser } from "./features/inspira/api";
import { t } from "./i18n";

function resolveSharedToken(): string | null {
  if (typeof window === "undefined") return null;
  const match = window.location.pathname.match(/^\/shared\/([^/]+)\/?$/);
  return match ? (match[1] ?? null) : null;
}

// Returns the reset token from either:
//   /reset-password?token=<raw>    (spec URL, emitted by this UI)
//   /?reset_token=<raw>            (legacy URL the backend currently emails)
// Returns null when neither pattern matches.
function resolveResetToken(): string | null {
  if (typeof window === "undefined") return null;
  const { pathname, search } = window.location;
  const params = new URLSearchParams(search);

  if (pathname === "/reset-password" || pathname === "/reset-password/") {
    return params.get("token") ?? null;
  }
  // Legacy backend-generated link: /?reset_token=<raw>
  if (pathname === "/" || pathname === "") {
    const legacy = params.get("reset_token");
    if (legacy) return legacy;
  }
  return null;
}

function InvalidResetLink() {
  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--paper, #F5F0E6)",
        padding: 24,
      }}
    >
      <div
        style={{
          background: "var(--paper, #F5F0E6)",
          color: "var(--ink, #2B2520)",
          borderRadius: 14,
          boxShadow:
            "0 32px 72px -24px rgba(43, 37, 32, 0.36), 0 2px 4px rgba(43, 37, 32, 0.08)",
          border: "1px solid var(--paper-edge, #DBCFB6)",
          width: "min(440px, 100%)",
          padding: 36,
          fontFamily: "var(--ff-sans, system-ui, sans-serif)",
        }}
      >
        <h1
          style={{
            fontFamily: "var(--ff-serif, Georgia, serif)",
            fontSize: 24,
            fontWeight: 400,
            margin: "0 0 12px",
            color: "var(--ink, #2B2520)",
          }}
        >
          {t("reset.invalid_link_title")}
        </h1>
        <p
          style={{
            fontFamily: "var(--ff-serif, Georgia, serif)",
            fontStyle: "italic",
            fontSize: 14,
            color: "var(--ink-3, #7A6F64)",
            margin: "0 0 20px",
          }}
        >
          {t("reset.invalid_link_body")}
        </p>
        <a
          href="/"
          style={{
            color: "var(--sage, #6A9A7A)",
            textDecoration: "underline",
            textUnderlineOffset: 3,
            fontFamily: "var(--ff-serif, Georgia, serif)",
            fontSize: 14,
          }}
        >
          {t("auth.forgot_back_to_signin")}
        </a>
      </div>
    </div>
  );
}

// Returns true when the current build has the maintenance-mode flag set,
// OR when the user is on the explicit /maintenance path.
//
// The env flag is checked via Vite's `import.meta.env` so a deployment
// can toggle the whole app into maintenance without a code change.
// We accept "1" or "true" as truthy values to keep the ops surface tiny.
function isMaintenanceRoute(pathname: string): boolean {
  if (pathname === "/maintenance" || pathname === "/maintenance/") return true;
  const flag = (import.meta.env.INSPIRA_MAINTENANCE_MODE as string | undefined) ?? "";
  return flag === "1" || flag === "true";
}

function isAccountDeactivatedRoute(pathname: string): boolean {
  return (
    pathname === "/account-deactivated" ||
    pathname === "/account-deactivated/"
  );
}

// Small hook: fetch /api/auth/me once so we can decide whether the
// signed-in user is actually a deleted account. Any failure leaves
// `deletedAt` as null — this is a safety-net check, not the primary
// deactivation signal.
function useSignedInUser(enabled: boolean): AuthedUser | null {
  const [user, setUser] = useState<AuthedUser | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void (async () => {
      try {
        const me = await api.me();
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) setUser(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return user;
}

export function App() {
  const { pathname, search } = window.location;
  const isMaintenance = isMaintenanceRoute(pathname);
  const isDeactivatedPath = isAccountDeactivatedRoute(pathname);

  // Rules of Hooks: always call hooks unconditionally. We skip the
  // /api/auth/me call with `enabled: false` when the route is already
  // known to be maintenance / shared / reset-password — there's no user
  // context to check in those cases anyway.
  const skipAuthCheck =
    isMaintenance ||
    pathname.startsWith("/shared/") ||
    pathname === "/reset-password" ||
    pathname === "/reset-password/";
  const signedInUser = useSignedInUser(!skipAuthCheck);
  const deletedAt = signedInUser?.deleted_at ?? null;

  // 1. Maintenance mode — front of the routing table so it covers
  //    every other surface.
  if (isMaintenance) {
    return <MaintenancePage />;
  }

  // 2. Account deactivated — either by explicit path or by the
  //    signed-in user having a populated `deleted_at`.
  if (isDeactivatedPath || deletedAt) {
    return <AccountDeactivatedPage deletedAt={deletedAt} />;
  }

  const sharedToken = resolveSharedToken();
  if (sharedToken) {
    return <SharedCanvasPage token={sharedToken} />;
  }

  // Handle both /reset-password?token= and /?reset_token= (legacy backend URL).
  const isResetPath =
    pathname === "/reset-password" || pathname === "/reset-password/";
  const hasLegacyToken =
    (pathname === "/" || pathname === "") &&
    new URLSearchParams(search).has("reset_token");

  if (isResetPath || hasLegacyToken) {
    const resetToken = resolveResetToken();
    if (!resetToken || resetToken.trim() === "") {
      return <InvalidResetLink />;
    }
    return <ResetPasswordPage token={resetToken} />;
  }

  return (
    <>
      <InspiraApp />
      <FeedbackWidget />
    </>
  );
}
