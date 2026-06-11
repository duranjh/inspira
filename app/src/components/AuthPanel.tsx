// Inspira — sign-in / sign-up modal.
//
// A self-contained, dismissable modal that lets a user either log into an
// existing account or register a new one. Rendered as a full-viewport
// backdrop + centered paper card in Inspira's warm-editorial language
// (cream paper, serif display heading, sage/gold/rust accents).
//
// The backend fallback behavior means signing in is optional — without a
// session cookie the server resolves to a shared `user-system` account
// and the app still works. This modal is for users who want private data.
//
// Styling is inline / CSS-in-TS to keep the scope local; App.css stays
// untouched.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";

import { api, type AuthedUser } from "../features/inspira/api";
import { t } from "../i18n";
import { parseStatus } from "../lib/httpStatus";

export type AuthPanelProps = {
  open: boolean;
  onClose: () => void;
  // Called after a successful login or signup with the fresh authed user.
  // Parent is responsible for refetching state / reloading.
  onAuthenticated: (user: AuthedUser) => void;
  // Which tab to show first. Defaults to "login".
  initialMode?: "login" | "signup" | "forgot";
};

type Mode = "login" | "signup" | "forgot";

// Parse the HTTP status code out of the Error.message produced by
// api.ts's postJson helper. The current shape is:
//   "POST /api/auth/login failed: 401 Unauthorized — <detail>"
// If we can't find a number we return null so the caller falls through
// to the generic "couldn't reach the server" branch.
// A fetch failure (CORS, DNS, offline) surfaces as a TypeError from fetch
// with no embedded status. Treat the absence of a status as a network
// error; treat presence of one as a server-side response we should read.
function isNetworkError(err: unknown): boolean {
  return err instanceof Error && parseStatus(err) === null;
}

// ---- Inline soft validation -----------------------------------------------
//
// Both helpers run on every keystroke and only return a hint string once the
// user has typed enough that a hint feels useful (i.e. not on the first 1-2
// characters). Returning null = "say nothing yet". Submission is NEVER gated
// on these — they're just there to nudge the user before the server roundtrip.

// Loose RFC-5322 shape check. We're only flagging obvious typos like a missing
// "@" or a missing TLD; the real validator is the backend.
const EMAIL_SHAPE_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function emailHint(value: string): string | null {
  const trimmed = value.trim();
  // Wait until they've typed something resembling an attempt.
  if (trimmed.length < 4) return null;
  if (EMAIL_SHAPE_RE.test(trimmed)) return null;
  return "invalid";
}

type PasswordStrength = "short" | "weak" | "ok" | "strong";

// Tiny local heuristic — not a security claim, just a UX nudge.
//   - "short"  : < 8 chars
//   - "weak"   : 8+ chars but only one character class
//   - "ok"     : 8+ chars with at least 2 classes (letters + digits, etc.)
//   - "strong" : 12+ chars with 3+ classes
function passwordStrength(value: string): PasswordStrength {
  if (value.length < 8) return "short";
  let classes = 0;
  if (/[a-z]/.test(value)) classes += 1;
  if (/[A-Z]/.test(value)) classes += 1;
  if (/\d/.test(value)) classes += 1;
  if (/[^A-Za-z0-9]/.test(value)) classes += 1;
  if (value.length >= 12 && classes >= 3) return "strong";
  if (classes >= 2) return "ok";
  return "weak";
}

export function AuthPanel({
  open,
  onClose,
  onAuthenticated,
  initialMode = "login",
}: AuthPanelProps) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );
  // Terms-of-service acceptance — required in signup mode. The submit
  // button stays disabled until this is checked.
  const [termsAccepted, setTermsAccepted] = useState(false);
  // Password visibility toggle — applies to both password + confirm
  // fields together so toggling once reveals the pair (the common case
  // for "I want to see what I'm typing").
  const [showPassword, setShowPassword] = useState(false);
  // Forgot-password flow state
  const [forgotSent, setForgotSent] = useState(false);

  const emailInputRef = useRef<HTMLInputElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // Reset transient state on every open. Email is intentionally preserved
  // across tab switches but cleared on close.
  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setPassword("");
    setConfirmPassword("");
    setTermsAccepted(false);
    setShowPassword(false);
    setErrorMessage(null);
    setValidationMessage(null);
    setSubmitting(false);
    setForgotSent(false);
  }, [open, initialMode]);

  // Move focus into the modal on open. We aim at the first input (email
  // is always first in every mode). The ref chain goes through cardRef
  // so a screen-reader user lands inside the dialog rather than wherever
  // the page's prior focus sat. Using rAF lets the portal render before
  // we try to focus — focusing an unmounted node is a silent no-op and
  // would leave focus outside the dialog.
  useEffect(() => {
    if (!open) return;
    const id = window.requestAnimationFrame(() => {
      // Prefer the explicit email input ref. Fall back to the first
      // focusable input inside the modal card if the DOM layout shifts
      // (defensive — today email is always rendered first).
      if (emailInputRef.current) {
        emailInputRef.current.focus();
        return;
      }
      const firstInput = cardRef.current?.querySelector<HTMLInputElement>(
        "input:not([type=hidden]):not([disabled])",
      );
      firstInput?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [open, mode]);

  // Esc closes. Attached at the document level (capture phase) so the
  // Esc binding in InspiraApp doesn't swallow it first.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  // Lock body scroll while open. Preserves the original overflow value.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const switchMode = useCallback((next?: Mode) => {
    setMode((prev) => next ?? (prev === "login" ? "signup" : "login"));
    // Don't leak the password across tabs; keep the typed email.
    setPassword("");
    setConfirmPassword("");
    setTermsAccepted(false);
    setShowPassword(false);
    setErrorMessage(null);
    setValidationMessage(null);
    setForgotSent(false);
  }, []);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (submitting) return;

      setErrorMessage(null);
      setValidationMessage(null);

      const trimmedEmail = email.trim();
      if (!trimmedEmail) {
        setValidationMessage(t("auth.email_required"));
        return;
      }

      // Forgot-password mode: only needs email, then shows success view.
      if (mode === "forgot") {
        setSubmitting(true);
        try {
          await api.forgotPassword(trimmedEmail);
          setForgotSent(true);
        } catch (err) {
          if (isNetworkError(err)) {
            setErrorMessage(t("auth.network_error"));
          } else {
            setErrorMessage(t("auth.forgot_generic_error"));
          }
        } finally {
          setSubmitting(false);
        }
        return;
      }

      if (password.length < 8) {
        setValidationMessage(t("auth.password_too_short"));
        return;
      }

      // Signup-only gates: confirm password matches, terms accepted.
      // The submit button is also disabled when either is unmet — these
      // checks are defensive (e.g. a user presses Enter on the last
      // field before either check runs synchronously in state).
      if (mode === "signup") {
        if (password !== confirmPassword) {
          setValidationMessage(t("auth.confirm_password_mismatch"));
          return;
        }
        if (!termsAccepted) {
          setValidationMessage(t("auth.terms_required"));
          return;
        }
      }

      setSubmitting(true);
      try {
        const user: AuthedUser =
          mode === "login"
            ? await api.login({ email: trimmedEmail, password })
            : await api.signup({
                email: trimmedEmail,
                password,
                display_name: displayName.trim() || undefined,
                terms_accepted: true,
              });
        // Fresh signups: clear in-situ coachmark keys so a browser
        // that previously dismissed them (anon visitor → signup, or
        // a shared family browser) gives the new account the full
        // tour. The legacy v3 OnboardingWalkthrough modal flag was
        // removed when we deleted that surface.
        if (mode === "signup" && typeof window !== "undefined") {
          try {
            window.localStorage.removeItem("inspira_onboarded_canvas");
            window.localStorage.removeItem("inspira_onboarded_shortcuts");
            window.localStorage.removeItem("inspira_onboarded_planner_views");
            window.localStorage.removeItem("inspira_onboarded_topic_detail");
            window.localStorage.removeItem("inspira_onboarded_homepage");
          } catch {
            /* storage disabled — non-fatal */
          }
        }
        onAuthenticated(user);
      } catch (err) {
        if (isNetworkError(err)) {
          setErrorMessage(t("auth.network_error"));
        } else {
          const status = parseStatus(err);
          if (mode === "login" && (status === 401 || status === 403)) {
            setErrorMessage(t("auth.invalid_credentials"));
          } else if (mode === "signup" && status === 409) {
            setErrorMessage(t("auth.email_in_use"));
          } else if (mode === "signup" && status === 400) {
            // Backend also enforces password-length / malformed email.
            setErrorMessage(t("auth.signup_bad_request"));
          } else if (status === 429) {
            setErrorMessage(t("auth.rate_limited"));
          } else if (status !== null && status >= 500) {
            setErrorMessage(t("auth.server_error"));
          } else {
            setErrorMessage(t("auth.generic_error"));
          }
        }
      } finally {
        setSubmitting(false);
      }
    },
    [
      displayName,
      email,
      mode,
      onAuthenticated,
      password,
      confirmPassword,
      termsAccepted,
      submitting,
    ],
  );

  const handleBackdropPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Only close when the click lands on the backdrop itself, never on
      // the card.
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  const title =
    mode === "login"
      ? t("auth.welcome_login")
      : mode === "forgot"
        ? t("auth.forgot_title")
        : t("auth.welcome_signup");
  const submitLabel = submitting
    ? mode === "login"
      ? t("auth.signing_in")
      : mode === "forgot"
        ? t("auth.forgot_sending")
        : t("auth.creating_account")
    : mode === "login"
      ? t("auth.login_button")
      : mode === "forgot"
        ? t("auth.forgot_submit")
        : t("auth.signup_button");

  const toggleCopy = useMemo(
    () =>
      mode === "login" ? (
        <>
          {t("auth.to_signup_prompt")}{" "}
          <button type="button" className="auth-panel-toggle-btn" style={linkStyle} onClick={() => switchMode()}>
            {t("auth.to_signup_action")}
          </button>
        </>
      ) : mode === "forgot" ? null : (
        <>
          {t("auth.to_login_prompt")}{" "}
          <button type="button" className="auth-panel-toggle-btn" style={linkStyle} onClick={() => switchMode()}>
            {t("auth.to_login_action")}
          </button>
        </>
      ),
    [mode, switchMode],
  );

  const devHintEnabled =
    (import.meta.env.VITE_INSPIRA_DEV_PASSWORD_RESET as string | undefined) ===
    "true";

  // Soft inline hints — recomputed each render. Cheap, no memo needed.
  // Only surface the email shape warning while the user is mid-edit
  // (not while submitting, where the error banner does the talking).
  const emailWarn = !submitting && emailHint(email) === "invalid";
  // Password strength is only shown during signup; on login a "weak"
  // password is the user's existing one — judging it would be obnoxious.
  const showPwStrength = mode === "signup" && password.length > 0;
  const pwStrength = showPwStrength ? passwordStrength(password) : null;

  if (!open) return null;

  return (
    <div
      className="auth-panel-backdrop"
      onPointerDown={handleBackdropPointerDown}
      style={backdropStyle}
    >
      {/* Mobile overrides — inline styles on the card/inputs can't be targeted
          by external media queries, so we inject a scoped style tag here.
          These rules only take effect on narrow/touch viewports. */}
      <style>{`
        /* Prefer 100dvh over 100vh so Safari's dynamic toolbar doesn't
           leave the card taller than the visible viewport. The inline
           style on the card keeps 100vh as the fallback for browsers
           too old to understand dvh — on those browsers this selector
           is invalid and the inline value wins. */
        .auth-panel-card {
          max-height: calc(100dvh - 48px) !important;
        }
        /* Keyboard focus ring — inline outline:none on the inputs beats the
           global :focus-visible rule, so we supply a box-shadow ring here
           (box-shadow has no inline counterpart on these inputs). */
        .auth-panel-card input:focus-visible {
          box-shadow: 0 0 0 3px var(--focus-ring, rgba(43, 37, 32, 0.35));
          border-color: var(--ink-4, #8b7f70);
        }
        .auth-panel-submit:focus-visible,
        .auth-panel-toggle-btn:focus-visible {
          outline: 2px solid var(--focus-ring, rgba(43, 37, 32, 0.35));
          outline-offset: 2px;
        }
        @media (max-width: 520px) {
          .auth-panel-backdrop {
            padding: 8px;
            align-items: flex-end;
          }
          .auth-panel-card {
            border-radius: 18px 18px 0 0;
            width: 100%;
            max-width: 100%;
          }
          .auth-panel-submit {
            width: 100%;
          }
        }
        @media (pointer: coarse) {
          .auth-panel-submit {
            min-height: 44px;
          }
          /* Mode-switch link — expand tap target */
          .auth-panel-toggle-btn {
            min-height: 44px;
            display: inline-flex;
            align-items: center;
          }
        }
      `}</style>
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="auth-panel-card"
        style={cardStyle}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label={t("auth.close")}
          style={closeButtonStyle}
        >
          {"\u00D7"}
        </button>

        <header style={headerStyle}>
          <div style={eyebrowStyle}>
            {mode === "login"
              ? t("auth.eyebrow_login")
              : mode === "forgot"
                ? t("auth.forgot_eyebrow")
                : t("auth.eyebrow_signup")}
          </div>
          <h2 style={titleStyle}>{title}</h2>
          <p style={subtitleStyle}>
            {mode === "login"
              ? t("auth.subtitle_login")
              : mode === "forgot"
                ? t("auth.forgot_subtitle")
                : t("auth.subtitle_signup")}
          </p>
        </header>

        {/* Forgot-password success view */}
        {mode === "forgot" && forgotSent ? (
          <div>
            <p style={{ ...subtitleStyle, fontSize: 15, marginBottom: 16 }}>
              <strong style={{ fontStyle: "normal", color: "var(--ink, #2B2520)" }}>
                {t("auth.forgot_success_title")}
              </strong>{" "}
              {t("auth.forgot_success_body")}
            </p>
            {devHintEnabled ? (
              <div role="note" style={devHintStyle}>
                {t("auth.forgot_dev_hint")}
              </div>
            ) : (
              <p style={supportFallbackStyle}>
                {t("auth.forgot_support_fallback")}
              </p>
            )}
            <div style={toggleRowStyle}>
              <button
                type="button"
                style={linkStyle}
                onClick={() => switchMode("login")}
              >
                {t("auth.forgot_back_to_signin")}
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={formStyle} noValidate>
            {errorMessage ? (
              <div role="alert" style={errorBannerStyle}>
                {errorMessage}
              </div>
            ) : null}
            {validationMessage ? (
              <div role="alert" style={validationBannerStyle}>
                {validationMessage}
              </div>
            ) : null}

            <label style={fieldStyle}>
              <span style={labelStyle}>{t("auth.email_label")}</span>
              <input
                ref={emailInputRef}
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                aria-invalid={emailWarn || undefined}
                aria-describedby={emailWarn ? "auth-email-hint" : undefined}
                style={inputStyle}
              />
              {emailWarn ? (
                <span id="auth-email-hint" style={inlineWarnStyle}>
                  {t("auth.email_hint_invalid")}
                </span>
              ) : null}
            </label>

            {mode === "signup" ? (
              <label style={fieldStyle}>
                <span style={labelStyle}>
                  {t("auth.display_name_label")}{" "}
                  <span style={labelHintStyle}>
                    {t("auth.display_name_optional")}
                  </span>
                </span>
                <input
                  type="text"
                  autoComplete="name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  disabled={submitting}
                  style={inputStyle}
                />
              </label>
            ) : null}

            {mode !== "forgot" ? (
              <label style={fieldStyle}>
                <span style={labelStyle}>
                  {t("auth.password_label")}{" "}
                  <span style={labelHintStyle}>
                    {mode === "signup" ? t("auth.password_hint_signup") : ""}
                  </span>
                </span>
                <div style={{ position: "relative" }}>
                  <input
                    type={showPassword ? "text" : "password"}
                    autoComplete={
                      mode === "login" ? "current-password" : "new-password"
                    }
                    required
                    minLength={8}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting}
                    aria-describedby={
                      pwStrength ? "auth-password-strength" : undefined
                    }
                    style={{ ...inputStyle, paddingRight: 44 }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={
                      showPassword
                        ? t("auth.password_hide")
                        : t("auth.password_show")
                    }
                    aria-pressed={showPassword}
                    style={{
                      position: "absolute",
                      right: 8,
                      top: "50%",
                      transform: "translateY(-50%)",
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      padding: 4,
                      color: "var(--ink-3)",
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <svg
                      width="20"
                      height="20"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      {showPassword ? (
                        <>
                          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                          <line x1="1" y1="1" x2="23" y2="23" />
                        </>
                      ) : (
                        <>
                          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                          <circle cx="12" cy="12" r="3" />
                        </>
                      )}
                    </svg>
                  </button>
                </div>
                {pwStrength ? (
                  <span
                    id="auth-password-strength"
                    style={passwordStrengthStyle(pwStrength)}
                    aria-live="polite"
                  >
                    <span
                      aria-hidden="true"
                      style={passwordStrengthDotStyle(pwStrength)}
                    />
                    {pwStrength === "short"
                      ? t("auth.password_strength_short")
                      : pwStrength === "weak"
                        ? t("auth.password_strength_weak")
                        : pwStrength === "ok"
                          ? t("auth.password_strength_ok")
                          : t("auth.password_strength_strong")}
                  </span>
                ) : null}
              </label>
            ) : null}

            {mode === "signup" ? (
              <label style={fieldStyle}>
                <span style={labelStyle}>
                  {t("auth.confirm_password_label")}
                </span>
                <input
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                  minLength={8}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  disabled={submitting}
                  aria-invalid={
                    confirmPassword.length > 0 && confirmPassword !== password
                  }
                  style={inputStyle}
                />
              </label>
            ) : null}

            {mode === "signup" ? (
              <label
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  fontFamily: "var(--ff-sans)",
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: "var(--ink-3)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={termsAccepted}
                  onChange={(e) => setTermsAccepted(e.target.checked)}
                  disabled={submitting}
                  style={{ marginTop: 3, flex: "0 0 auto" }}
                />
                <span>
                  {t("auth.terms_acceptance_prefix")}{" "}
                  <Link
                    to="/legal/terms"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--ink-2)", textDecoration: "underline" }}
                  >
                    {t("auth.terms_link")}
                  </Link>{" "}
                  {t("auth.terms_and")}{" "}
                  <Link
                    to="/legal/privacy"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--ink-2)", textDecoration: "underline" }}
                  >
                    {t("auth.privacy_link")}
                  </Link>
                  {t("auth.terms_period")}
                </span>
              </label>
            ) : null}

            {/* Forgot password link — only in login mode, below password field.
                Sits flush-right so it reads as paired to the password input,
                while staying readable (13px) and tappable on mobile (44px
                via the .auth-panel-toggle-btn rule above). */}
            {mode === "login" ? (
              <div style={forgotLinkRowStyle}>
                <button
                  type="button"
                  className="auth-panel-toggle-btn"
                  style={forgotLinkStyle}
                  onClick={() => switchMode("forgot")}
                >
                  {t("auth.forgot_link")}
                </button>
              </div>
            ) : null}

            <button
              type="submit"
              disabled={
                submitting ||
                (mode === "signup" &&
                  (!termsAccepted ||
                    password.length === 0 ||
                    password !== confirmPassword))
              }
              className="auth-panel-submit"
              style={{
                ...submitButtonStyle,
                ...(submitting ||
                (mode === "signup" &&
                  (!termsAccepted ||
                    password.length === 0 ||
                    password !== confirmPassword))
                  ? submitButtonDisabledStyle
                  : {}),
              }}
            >
              {submitLabel}
            </button>

            {mode === "forgot" ? (
              <div style={toggleRowStyle}>
                <button
                  type="button"
                  style={linkStyle}
                  onClick={() => switchMode("login")}
                >
                  {t("auth.forgot_back_to_signin")}
                </button>
              </div>
            ) : (
              <div style={toggleRowStyle}>{toggleCopy}</div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

// ---- Inline styles --------------------------------------------------------
//
// Warm editorial — cream paper card, serif display heading, soft shadow.
// Mirrors the tokens in App.css (--paper, --ink-*, --sage, --rust) with
// hardcoded fallbacks. Dark-theme safe because the paper/ink variables
// swap values at :root[data-theme="dark"].

const backdropStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(28, 22, 17, 0.48)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 2000,
  padding: 24,
};

const cardStyle: React.CSSProperties = {
  position: "relative",
  background: "var(--paper, #F5F0E6)",
  color: "var(--ink, #2B2520)",
  borderRadius: 14,
  boxShadow:
    "0 32px 72px -24px rgba(43, 37, 32, 0.40), 0 2px 4px rgba(43, 37, 32, 0.08)",
  border: "1px solid var(--paper-edge, #DBCFB6)",
  width: "min(440px, 100%)",
  maxHeight: "calc(100vh - 48px)",
  overflowY: "auto",
  padding: 32,
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
};

const closeButtonStyle: React.CSSProperties = {
  position: "absolute",
  top: 14,
  right: 14,
  appearance: "none",
  background: "transparent",
  border: "1px solid var(--paper-edge, rgba(43, 37, 32, 0.12))",
  borderRadius: 999,
  width: 32,
  height: 32,
  fontSize: 20,
  lineHeight: 1,
  color: "var(--ink-2, #4A413A)",
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
  fontFamily: "var(--ff-serif, Georgia, serif)",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginBottom: 22,
  paddingRight: 36,
};

const eyebrowStyle: React.CSSProperties = {
  fontFamily:
    "var(--ff-mono, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace)",
  fontSize: 11,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--ink-3, #7A6F64)",
};

const titleStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 30,
  lineHeight: 1.1,
  letterSpacing: "-0.01em",
  fontWeight: 400,
  margin: 0,
  color: "var(--ink, #2B2520)",
};

const subtitleStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  fontSize: 14,
  lineHeight: 1.5,
  color: "var(--ink-3, #7A6F64)",
  margin: "4px 0 0",
};

const formStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
};

const fieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const labelStyle: React.CSSProperties = {
  fontFamily:
    "var(--ff-mono, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace)",
  fontSize: 11,
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  color: "var(--ink-3, #7A6F64)",
  display: "inline-flex",
  alignItems: "baseline",
  gap: 8,
};

const labelHintStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  textTransform: "none",
  letterSpacing: 0,
  color: "var(--ink-4, #A89E91)",
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 16,
  lineHeight: 1.4,
  padding: "12px 14px",
  border: "1px solid var(--paper-edge, #DBCFB6)",
  borderRadius: 10,
  // --paper-lifted flips cream → espresso in dark mode so the input
  // doesn't glow bright white on the auth card.
  background: "var(--paper-lifted, #fbf7ee)",
  color: "var(--ink, #2B2520)",
  outline: "none",
  transition: "border-color 150ms ease, box-shadow 150ms ease",
  width: "100%",
  boxSizing: "border-box",
};

const submitButtonStyle: React.CSSProperties = {
  marginTop: 6,
  fontFamily: "var(--ff-sans, system-ui, sans-serif)",
  fontSize: 14,
  fontWeight: 500,
  padding: "12px 22px",
  border: "1px solid var(--ink, #2B2520)",
  borderRadius: 999,
  background: "var(--ink, #2B2520)",
  color: "var(--paper, #F5F0E6)",
  cursor: "pointer",
  transition: "transform 120ms ease, opacity 150ms ease",
};

const submitButtonDisabledStyle: React.CSSProperties = {
  opacity: 0.45,
  cursor: "not-allowed",
};

const errorBannerStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 13.5,
  color: "var(--rust, #B06A50)",
  padding: "10px 14px",
  borderLeft: "2px solid var(--rust, #B06A50)",
  background: "rgba(176, 106, 80, 0.08)",
  borderRadius: "0 6px 6px 0",
  lineHeight: 1.45,
};

const validationBannerStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 13.5,
  color: "var(--ink-2, #4A413A)",
  padding: "10px 14px",
  borderLeft: "2px solid var(--ink-5, #C8BEAE)",
  background: "var(--border-soft, rgba(43, 37, 32, 0.04))",
  borderRadius: "0 6px 6px 0",
  lineHeight: 1.45,
};

const toggleRowStyle: React.CSSProperties = {
  marginTop: 8,
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 13.5,
  color: "var(--ink-3, #7A6F64)",
  textAlign: "center",
  lineHeight: 1.5,
};

const linkStyle: React.CSSProperties = {
  appearance: "none",
  background: "transparent",
  border: "none",
  padding: 0,
  margin: 0,
  fontFamily: "inherit",
  fontSize: "inherit",
  color: "var(--sage, #6A9A7A)",
  textDecoration: "underline",
  textUnderlineOffset: 3,
  cursor: "pointer",
};

const devHintStyle: React.CSSProperties = {
  fontFamily:
    "var(--ff-mono, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace)",
  fontSize: 11,
  lineHeight: 1.55,
  color: "var(--ink-2, #4A413A)",
  background: "var(--border-soft, rgba(43, 37, 32, 0.05))",
  border: "1px dashed var(--paper-edge, #DBCFB6)",
  borderRadius: 8,
  padding: "10px 12px",
  marginBottom: 16,
  whiteSpace: "pre-wrap",
};

// Soft inline hint shown below the email input when the typed value
// doesn't look like a valid address. Italic serif keeps it light — it's
// a nudge, not a blocking error.
const inlineWarnStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  fontSize: 12.5,
  lineHeight: 1.4,
  color: "var(--ink-3, #7A6F64)",
  marginTop: 2,
};

// Forgot-password link row. Right-aligned and slightly larger than before
// so it doesn't disappear into the form chrome. The link itself uses the
// sage accent like other in-form links, with extra weight to read clearly.
const forgotLinkRowStyle: React.CSSProperties = {
  textAlign: "right",
  marginTop: -4,
};

const forgotLinkStyle: React.CSSProperties = {
  ...linkStyle,
  fontSize: 13,
  fontWeight: 500,
};

// Plain-prose support fallback shown after a forgot-password submission
// when no dev hint is configured. Mirrors the editorial subtitle so it
// reads as guidance, not as a banner.
const supportFallbackStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  fontSize: 13,
  lineHeight: 1.5,
  color: "var(--ink-3, #7A6F64)",
  margin: "0 0 16px",
};

// Password-strength hint — color shifts with the four levels so the
// signal is glanceable without reading the copy. The dot is a tiny
// affordance that mirrors the level color.
function passwordStrengthColor(level: PasswordStrength): string {
  switch (level) {
    case "short":
      return "var(--ink-3, #7A6F64)";
    case "weak":
      return "var(--rust, #B06A50)";
    case "ok":
      return "var(--ink-2, #4A413A)";
    case "strong":
      return "var(--sage, #6A9A7A)";
  }
}

function passwordStrengthStyle(level: PasswordStrength): React.CSSProperties {
  return {
    fontFamily: "var(--ff-serif, Georgia, serif)",
    fontStyle: "italic",
    fontSize: 12.5,
    lineHeight: 1.4,
    color: passwordStrengthColor(level),
    marginTop: 2,
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };
}

function passwordStrengthDotStyle(
  level: PasswordStrength,
): React.CSSProperties {
  return {
    display: "inline-block",
    width: 6,
    height: 6,
    borderRadius: 999,
    background: passwordStrengthColor(level),
    flex: "0 0 auto",
  };
}
