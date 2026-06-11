// Inspira — shared marketing chrome.
//
// Wrapper used by every public, pre-signup page (landing, privacy, terms,
// pricing stub). Renders a minimal serif header and a warm editorial
// footer, then drops the page's main content in between. None of the app
// chrome is pulled in on purpose — a cold visitor should see exactly one
// thing: the content of the page they came for.
//
// The palette, type ramp, and focus-ring colour all pull from the existing
// design tokens in App.css so the marketing surface feels continuous with
// the product once a visitor signs in. No new tokens introduced here.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { LocalePicker } from "../../components/LocalePicker";
import { t } from "../../i18n";
import { api, type AuthedUser } from "../inspira/api";

import "./marketing-legal.css";

export type MarketingLayoutProps = {
  children: ReactNode;
  /**
   * When the CTA in the header should open the signup modal instead of
   * navigating to `/`, set this to a handler. Default behaviour is a plain
   * `/` link, which lets the landing-page hero take over.
   */
  onSignIn?: () => void;
};

/**
 * Outer chrome shared by every `/marketing/*` page. Kept intentionally
 * thin — a serif wordmark, one nav link, and a quiet footer with legal
 * pointers and the locale picker.
 */
export function MarketingLayout({ children, onSignIn }: MarketingLayoutProps) {
  return (
    <div className="marketing-root">
      <MarketingHeader onSignIn={onSignIn} />
      <main className="marketing-main">{children}</main>
      <MarketingFooter />
    </div>
  );
}

function MarketingHeader({ onSignIn }: { onSignIn?: () => void }) {
  const navigate = useNavigate();

  // T2.1: probe /api/auth/me on mount so we can swap the "Sign in"
  // CTA for an avatar link to /app when the visitor is already
  // authenticated. We treat any failure (offline, 401, anonymous
  // system fallback) as "anonymous" and render the original CTA.
  // Reuses the same `is_system` heuristic as BillingRoute.
  const [authedUser, setAuthedUser] = useState<AuthedUser | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        setAuthedUser(me.is_system ? null : me);
      } catch {
        // 401 / offline / network blip — stay anonymous.
        if (!cancelled) setAuthedUser(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSignIn = useCallback(() => {
    if (onSignIn) {
      onSignIn();
      return;
    }
    // Default: send the visitor to `/` with a `?signin=1` query that the
    // landing page picks up to open the AuthPanel in login mode.
    navigate("/?signin=1");
  }, [navigate, onSignIn]);

  // Build the displayed initials from the authed user's display_name
  // (preferred) or the first segment of their email. Capped at 2
  // characters so the avatar circle stays readable.
  const initials = authedUser
    ? deriveInitials(authedUser.display_name, authedUser.email)
    : "";

  return (
    <header className="marketing-header" role="banner">
      <div className="marketing-header__inner">
        <Link to="/" className="marketing-header__brand" aria-label="Inspira">
          <span className="marketing-header__brand-dot" aria-hidden="true" />
          <span className="marketing-header__brand-word">Inspira</span>
        </Link>
        <nav className="marketing-header__nav" aria-label={t("marketing.nav.aria")}>
          <Link to="/how-it-works" className="marketing-header__link">
            {t("marketing.nav.how_it_works")}
          </Link>
          <Link to="/teams" className="marketing-header__link">
            {t("marketing.nav.teams")}
          </Link>
          <Link to="/pricing" className="marketing-header__link">
            {t("marketing.nav.pricing")}
          </Link>
          {/* /features stays mounted for back-compat (legacy deep links) but
              is no longer a primary nav target — design v5 surfaces /how-it-works
              and /teams instead. About also hidden pending the new positioning. */}
          {authedUser ? (
            <Link
              to="/app"
              className="marketing-header__avatar"
              aria-label={t("marketing.nav.go_to_app", {
                name: authedUser.display_name || authedUser.email,
              })}
              title={authedUser.display_name || authedUser.email}
            >
              {initials || "·"}
            </Link>
          ) : (
            <button
              type="button"
              className="marketing-header__link marketing-header__link--button"
              onClick={handleSignIn}
            >
              {t("marketing.nav.sign_in")}
            </button>
          )}
        </nav>
      </div>
    </header>
  );
}

/** Derive 1-2 character avatar initials from name + email fallback. */
function deriveInitials(displayName: string | null | undefined, email: string): string {
  const name = (displayName ?? "").trim();
  if (name) {
    const parts = name.split(/\s+/).slice(0, 2);
    return parts.map((p) => p.charAt(0)).join("").toUpperCase();
  }
  const local = email.split("@")[0] ?? "";
  return local.charAt(0).toUpperCase();
}

function MarketingFooter() {
  const year = new Date().getFullYear();
  const sep = (
    <span className="marketing-footer__sep" aria-hidden="true">
      ·
    </span>
  );
  return (
    <footer className="marketing-footer" role="contentinfo">
      <div className="marketing-footer__inner">
        <div className="marketing-footer__left">
          <span className="marketing-footer__brand">tryinspira.com</span>
          {sep}
          <Link to="/how-it-works" className="marketing-footer__link">
            {t("marketing.nav.how_it_works")}
          </Link>
          {sep}
          <Link to="/teams" className="marketing-footer__link">
            {t("marketing.nav.teams")}
          </Link>
          {sep}
          <Link to="/pricing" className="marketing-footer__link">
            {t("marketing.footer.pricing")}
          </Link>
          {sep}
          <Link to="/status" className="marketing-footer__link">
            {t("marketing.footer.status")}
          </Link>
        </div>
        <div className="marketing-footer__right">
          <LocalePicker variant="inline" />
        </div>
      </div>
      <div className="marketing-footer__legal">
        <Link to="/legal/privacy" className="marketing-footer__link">
          {t("marketing.footer.privacy")}
        </Link>
        {sep}
        <Link to="/legal/terms" className="marketing-footer__link">
          {t("marketing.footer.terms")}
        </Link>
        {sep}
        <Link to="/legal/cookies" className="marketing-footer__link">
          {t("marketing.footer.cookies")}
        </Link>
        {sep}
        <Link to="/legal/dmca" className="marketing-footer__link">
          {t("marketing.footer.dmca")}
        </Link>
        {sep}
        <Link to="/legal/acceptable-use" className="marketing-footer__link">
          {t("marketing.footer.acceptable_use")}
        </Link>
        {sep}
        <Link to="/legal/gdpr" className="marketing-footer__link">
          {t("marketing.footer.gdpr")}
        </Link>
      </div>
      <div className="marketing-footer__copyright">
        {t("marketing.footer.copyright", { year: String(year) })}
      </div>
    </footer>
  );
}
