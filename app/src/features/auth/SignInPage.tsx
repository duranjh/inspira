// Inspira — full-page sign-in / sign-up surface (anon `/`).
//
// Replaces the AuthPanel-as-modal-over-LandingPage flow for the
// dedicated partner-journey root. AuthPanel.tsx stays in place for the
// marketing pages' `?signin=1` / `?signup=1` deep-link entry points;
// this page is what RootGate routes anon visitors to from `/`.
//
// Warm-editorial chrome (sage CTA, Source Serif 4 display, cream
// paper, dotted-grid scoped to `.signin-surface` only). No marketing
// claims, no `plan|plans|planning`, no banned-word filler.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, type AuthedUser } from "../inspira/api";
import { t } from "../../i18n";
import { parseStatus } from "../../lib/httpStatus";
import "./sign-in.css";

type Mode = "login" | "signup";

const EMAIL_SHAPE_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isNetworkError(err: unknown): boolean {
  return err instanceof Error && parseStatus(err) === null;
}

function emailLooksInvalid(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed.length < 4) return false;
  return !EMAIL_SHAPE_RE.test(trimmed);
}

type PasswordStrength = "short" | "weak" | "ok" | "strong";

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

export function SignInPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Honour ?signin=1 / ?signup=1 from marketing-page CTAs and back-links.
  // Default to login when neither query is present.
  const initialMode: Mode = searchParams.get("signup") === "1" ? "signup" : "login";

  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );

  const emailInputRef = useRef<HTMLInputElement | null>(null);

  // Update mode when query params change (e.g. user clicked a different
  // marketing CTA mid-flight).
  useEffect(() => {
    const next: Mode =
      searchParams.get("signup") === "1" ? "signup" : "login";
    setMode(next);
  }, [searchParams]);

  // Focus the email input on mount + on mode swap.
  useEffect(() => {
    emailInputRef.current?.focus();
  }, [mode]);

  const switchMode = useCallback((next: Mode) => {
    setMode(next);
    setPassword("");
    setConfirmPassword("");
    setTermsAccepted(false);
    setShowPassword(false);
    setErrorMessage(null);
    setValidationMessage(null);
  }, []);

  const navigateAfterAuth = useCallback(
    async (user: AuthedUser) => {
// Re-fetch /api/auth/me so we get the freshest default_workspace_id
      // (the signup response carries it, but a returning user logging
      // back in needs the round-trip).
      let resolved: AuthedUser = user;
      try {
        resolved = await api.me();
      } catch {
        /* fall through with the auth response we already have */
      }
      if (!resolved.default_workspace_id) {
        navigate("/onboarding", { replace: true });
      } else {
        navigate("/workspaces", { replace: true });
      }
    },
    [navigate],
  );

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
      if (password.length < 8) {
        setValidationMessage(t("auth.password_too_short"));
        return;
      }
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
        await navigateAfterAuth(user);
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
      confirmPassword,
      displayName,
      email,
      mode,
      navigateAfterAuth,
      password,
      submitting,
      termsAccepted,
    ],
  );

  const submitLabel = useMemo(() => {
    if (submitting) {
      return mode === "login"
        ? t("auth.signing_in")
        : t("auth.creating_account");
    }
    return mode === "login" ? t("auth.login_button") : t("auth.signup_button");
  }, [mode, submitting]);

  const showEmailWarn = !submitting && emailLooksInvalid(email);
  const showPwStrength = mode === "signup" && password.length > 0;
  const pwStrength = showPwStrength ? passwordStrength(password) : null;

  const headline =
    mode === "login"
      ? t("auth.welcome_login")
      : t("auth.welcome_signup");
  const subtitle =
    mode === "login"
      ? t("auth.subtitle_login")
      : t("auth.subtitle_signup");

  return (
    <div className="signin-surface">
      <header className="signin-top">
        <span className="signin-wordmark">Inspira</span>
      </header>

      <main className="signin-center">
        <div className="signin-tabs" role="tablist" aria-label="Sign in or sign up">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={`signin-tab ${mode === "login" ? "signin-tab--active" : ""}`}
            onClick={() => switchMode("login")}
          >
            {t("auth.login_button")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "signup"}
            className={`signin-tab ${mode === "signup" ? "signin-tab--active" : ""}`}
            onClick={() => switchMode("signup")}
          >
            {t("auth.signup_button")}
          </button>
        </div>

        <h1 className="signin-headline">{headline}</h1>
        <p className="signin-subtitle">{subtitle}</p>

        <form className="signin-form" onSubmit={handleSubmit} noValidate>
          <label className="signin-field">
            <span className="signin-field__label">{t("auth.email_label")}</span>
            <input
              ref={emailInputRef}
              className="signin-field__input"
              type="email"
              autoComplete="email"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
              required
            />
            {showEmailWarn ? (
              <span className="signin-field__hint signin-field__hint--warn">
                {t("auth.email_hint_invalid")}
              </span>
            ) : null}
          </label>

          {mode === "signup" ? (
            <label className="signin-field">
              <span className="signin-field__label">
                {t("auth.display_name_label")}{" "}
                <span className="signin-field__optional">
                  ({t("auth.display_name_optional")})
                </span>
              </span>
              <input
                className="signin-field__input"
                type="text"
                autoComplete="name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={submitting}
              />
            </label>
          ) : null}

          <label className="signin-field">
            <span className="signin-field__label">
              {t("auth.password_label")}
            </span>
            <div className="signin-field__pw">
              <input
                className="signin-field__input"
                type={showPassword ? "text" : "password"}
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                required
              />
              <button
                type="button"
                className="signin-field__pw-toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={
                  showPassword
                    ? t("auth.password_hide")
                    : t("auth.password_show")
                }
              >
                {showPassword ? "👁" : "👁‍🗨"}
              </button>
            </div>
            {pwStrength ? (
              <span
                className={`signin-field__hint signin-field__hint--${pwStrength}`}
              >
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

          {mode === "signup" ? (
            <label className="signin-field">
              <span className="signin-field__label">
                {t("auth.confirm_password_label")}
              </span>
              <input
                className="signin-field__input"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={submitting}
                required
              />
            </label>
          ) : null}

          {mode === "signup" ? (
            <label className="signin-terms">
              <input
                type="checkbox"
                checked={termsAccepted}
                onChange={(e) => setTermsAccepted(e.target.checked)}
                disabled={submitting}
              />
              <span>
                {t("auth.terms_acceptance_prefix")}{" "}
                <a
                  href="/legal/terms"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="signin-link"
                >
                  {t("auth.terms_link")}
                </a>{" "}
                {t("auth.terms_and")}{" "}
                <a
                  href="/legal/privacy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="signin-link"
                >
                  {t("auth.privacy_link")}
                </a>
                {t("auth.terms_period")}
              </span>
            </label>
          ) : null}

          {validationMessage ? (
            <div className="signin-error" role="alert">
              {validationMessage}
            </div>
          ) : null}
          {errorMessage ? (
            <div className="signin-error" role="alert">
              {errorMessage}
            </div>
          ) : null}

          <button
            type="submit"
            className="signin-cta"
            disabled={
              submitting ||
              (mode === "signup" && !termsAccepted) ||
              !email.trim() ||
              !password
            }
          >
            {submitLabel}
          </button>
        </form>

        <p className="signin-toggle">
          {mode === "login" ? (
            <>
              {t("auth.to_signup_prompt")}{" "}
              <button
                type="button"
                className="signin-link"
                onClick={() => switchMode("signup")}
              >
                {t("auth.to_signup_action")}
              </button>
            </>
          ) : (
            <>
              {t("auth.to_login_prompt")}{" "}
              <button
                type="button"
                className="signin-link"
                onClick={() => switchMode("login")}
              >
                {t("auth.to_login_action")}
              </button>
            </>
          )}
        </p>
      </main>
    </div>
  );
}
