// Inspira — /email-confirm landing.
//
// Three states in a single quiet-center layout:
//   - loading (default on mount while the verify POST runs)
//   - success (redirects to /app after 2s)
//   - expired (shown on 400/410 — offers "Send a new link" and a ghost
//              "Back to sign in")
//
// In dev, `?state=loading|success|expired` forces a state without
// hitting the backend so design review can see every frame.
//
// Copy lives under i18n key `email_confirm.*` — no hardcoded English.

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { t } from "../../i18n";
import { Head } from "../marketing/Head";

import {
  EmailResendThrottledError,
  EmailTokenExpiredError,
  resendVerification,
  verifyEmail,
} from "./api";
import "./email-confirm.css";

type Phase = "loading" | "success" | "expired";

export function EmailConfirmPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>("loading");
  const [resendBusy, setResendBusy] = useState(false);
  const [resendMsg, setResendMsg] = useState<string | null>(null);

  // Design-review `?state=forced` override used to be honoured in DEV
  // builds so a designer could land directly on the success frame. It was
  // removed in the PR 7 hygiene sweep because Vite's `import.meta.env.DEV`
  // flag can be set to `true` in mis-configured prod builds, and a real
  // user should NEVER see the success frame without a verified token.

  useEffect(() => {
    const token = params.get("token");
    if (!token) {
      setPhase("expired");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await verifyEmail(token);
        if (cancelled) return;
        setPhase("success");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof EmailTokenExpiredError) {
          setPhase("expired");
        } else {
          // Treat other failures as expired too — a retry either fixes
          // transient glitches or lands the same expired state.
          setPhase("expired");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params]);

  // Success → redirect to /app after 2s. Matches the hi-fi pacing.
  useEffect(() => {
    if (phase !== "success") return;
    const t = window.setTimeout(() => navigate("/app"), 2000);
    return () => window.clearTimeout(t);
  }, [phase, navigate]);

  const handleResend = async () => {
    if (resendBusy) return;
    setResendBusy(true);
    setResendMsg(null);
    try {
      await resendVerification();
      setResendMsg(t("email_confirm.resend_success"));
    } catch (err) {
      if (err instanceof EmailResendThrottledError) {
        setResendMsg(
          t("email_confirm.resend_throttled", {
            minutes: Math.max(1, Math.ceil(err.retryAfterSeconds / 60)),
          }),
        );
      } else {
        setResendMsg(t("email_confirm.resend_error"));
      }
    } finally {
      setResendBusy(false);
    }
  };

  return (
    <main className="email-confirm-page" role="main">
      <Head
        title={t("email_confirm.meta_title")}
        description={t("email_confirm.meta_description")}
        robots="noindex,nofollow"
      />
      <section
        className="email-confirm-page__card"
        aria-labelledby="email-confirm-heading"
        data-phase={phase}
      >
        <p className="email-confirm-page__eyebrow">{t("email_confirm.eyebrow")}</p>

        {phase === "loading" ? (
          <>
            <h1 id="email-confirm-heading" className="email-confirm-page__heading">
              {t("email_confirm.loading_title")}
            </h1>
            <p className="email-confirm-page__body">
              <em>{t("email_confirm.loading_body")}</em>
            </p>
          </>
        ) : null}

        {phase === "success" ? (
          <>
            <h1 id="email-confirm-heading" className="email-confirm-page__heading">
              {t("email_confirm.success_title")}
            </h1>
            <p className="email-confirm-page__body">
              <em>{t("email_confirm.success_body")}</em>
            </p>
            <p className="email-confirm-page__footnote">
              {t("email_confirm.redirecting")}
            </p>
          </>
        ) : null}

        {phase === "expired" ? (
          <>
            <h1 id="email-confirm-heading" className="email-confirm-page__heading">
              {t("email_confirm.expired_title")}
            </h1>
            <p className="email-confirm-page__body">
              <em>{t("email_confirm.expired_body")}</em>
            </p>
            <div className="email-confirm-page__actions">
              <button
                type="button"
                className="email-confirm-page__btn email-confirm-page__btn--primary"
                onClick={() => void handleResend()}
                disabled={resendBusy}
              >
                {resendBusy
                  ? t("email_confirm.resending")
                  : t("email_confirm.resend_cta")}
              </button>
              <button
                type="button"
                className="email-confirm-page__btn email-confirm-page__btn--ghost"
                onClick={() => navigate("/")}
              >
                {t("email_confirm.back_to_signin")}
              </button>
            </div>
            {resendMsg ? (
              <p className="email-confirm-page__status" role="status">
                {resendMsg}
              </p>
            ) : null}
          </>
        ) : null}
      </section>
    </main>
  );
}

export default EmailConfirmPage;
