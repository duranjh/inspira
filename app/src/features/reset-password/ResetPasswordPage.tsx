// Full-viewport reset-password page. Rendered by App.tsx when the
// URL is /reset-password?token=<raw> or the legacy /?reset_token=<raw>.
//
// Flow:
//   1. Two password fields (new + confirm), min 8 chars, must match.
//   2. On submit → api.resetPassword(token, newPw).
//   3. On success → show toast + navigate to /  (backend sets session cookie).
//   4. On token-invalid error → inline message + link back to forgot flow.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { api } from "../inspira/api";
import { t } from "../../i18n";

export type ResetPasswordPageProps = {
  token: string;
};

export function ResetPasswordPage({ token }: ResetPasswordPageProps) {
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [succeeded, setSucceeded] = useState(false);
  const newPwRef = useRef<HTMLInputElement | null>(null);
  const redirectTimerRef = useRef<number | null>(null);

  // Cancel any pending post-success redirect on unmount so we don't
  // navigate a gone component's tree.
  useEffect(() => {
    return () => {
      if (redirectTimerRef.current !== null) {
        window.clearTimeout(redirectTimerRef.current);
      }
    };
  }, []);

  const validate = useCallback((): string | null => {
    if (newPw.length < 8) return t("reset.too_short");
    if (newPw !== confirmPw) return t("reset.mismatch");
    return null;
  }, [newPw, confirmPw]);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (submitting) return;
      setInlineError(null);

      const err = validate();
      if (err) {
        setInlineError(err);
        return;
      }

      setSubmitting(true);
      try {
        await api.resetPassword(token, newPw);
        setSucceeded(true);
        // Redirect to root after a short delay so the user reads the success.
        // Track the timeout id on a ref so the effect below can cancel it if
        // the component unmounts before the redirect fires.
        redirectTimerRef.current = window.setTimeout(() => {
          window.location.href = "/";
        }, 2000);
      } catch (rawErr) {
        // The backend returns 400 + "invalid_or_expired_token" for bad tokens.
        // Surface it inline rather than a generic error so the user knows to
        // request a fresh link.
        const msg = rawErr instanceof Error ? rawErr.message : String(rawErr);
        if (msg.includes("400") || msg.includes("invalid_or_expired_token")) {
          setInlineError(t("reset.invalid_link_title") + " " + t("reset.invalid_link_body"));
        } else {
          setInlineError(t("reset.invalid_link_title") + " " + t("reset.invalid_link_body"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [newPw, confirmPw, submitting, token, validate],
  );

  return (
    <div style={viewportStyle}>
      {/* Keyboard focus ring — inline outline:none on the password inputs
          beats the global :focus-visible rule, so we supply a box-shadow
          ring here (box-shadow has no inline counterpart on the inputs). */}
      <style>{`
        .reset-password-card input:focus-visible {
          box-shadow: 0 0 0 3px var(--focus-ring, rgba(43, 37, 32, 0.35));
          border-color: var(--ink-4, #706055);
        }
      `}</style>
      <div className="reset-password-card" style={cardStyle}>
        <div style={eyebrowStyle}>Inspira</div>
        <h1 style={headingStyle}>{t("reset.title")}</h1>
        <p style={subtitleStyle}>{t("reset.subtitle")}</p>

        {succeeded ? (
          <div style={successBoxStyle}>
            <p style={{ margin: 0 }}>{t("reset.success_toast")}</p>
            <p style={{ margin: "8px 0 0", fontSize: 13 }}>
              {/* Redirect is happening; show fallback sign-in link in case it stalls. */}
              <a href="/" style={linkStyle}>
                {t("auth.to_login_action")} →
              </a>
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={formStyle} noValidate>
            {inlineError ? (
              <div role="alert" style={errorBannerStyle}>
                {inlineError}
                {inlineError.includes(t("reset.invalid_link_title").slice(0, 10)) ? (
                  <div style={{ marginTop: 8 }}>
                    <a href="/" style={linkStyle}>
                      {t("auth.forgot_back_to_signin")}
                    </a>
                  </div>
                ) : null}
              </div>
            ) : null}

            <label style={fieldStyle}>
              <span style={labelStyle}>{t("reset.new_label")}</span>
              <input
                ref={newPwRef}
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={newPw}
                onChange={(e) => setNewPw(e.target.value)}
                disabled={submitting}
                style={inputStyle}
              />
            </label>

            <label style={fieldStyle}>
              <span style={labelStyle}>{t("reset.confirm_label")}</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
                disabled={submitting}
                style={inputStyle}
              />
              {newPw.length >= 1 && confirmPw.length >= 1 && newPw !== confirmPw ? (
                <span style={mismatchStyle}>{t("reset.mismatch")}</span>
              ) : null}
            </label>

            <button
              type="submit"
              disabled={submitting}
              style={{
                ...submitButtonStyle,
                ...(submitting ? submitButtonDisabledStyle : {}),
              }}
            >
              {submitting ? t("reset.saving") : t("reset.submit")}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

// ---- Inline styles --------------------------------------------------------
// Warm editorial — matches AuthPanel and kickoff card aesthetic.

const viewportStyle: React.CSSProperties = {
  minHeight: "100dvh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "var(--paper, #F5F0E6)",
  padding: 24,
};

const cardStyle: React.CSSProperties = {
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

const eyebrowStyle: React.CSSProperties = {
  fontFamily:
    "var(--ff-mono, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace)",
  fontSize: 11,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--ink-3, #7A6F64)",
  marginBottom: 10,
};

const headingStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 30,
  lineHeight: 1.1,
  letterSpacing: "-0.01em",
  fontWeight: 400,
  margin: "0 0 6px",
  color: "var(--ink, #2B2520)",
};

const subtitleStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  fontSize: 14,
  lineHeight: 1.5,
  color: "var(--ink-3, #7A6F64)",
  margin: "0 0 24px",
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
};

const inputStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 16,
  lineHeight: 1.4,
  padding: "12px 14px",
  border: "1px solid var(--paper-edge, #DBCFB6)",
  borderRadius: 10,
  // --paper-lifted flips cream → warm espresso in dark mode.
  background: "var(--paper-lifted, #fbf7ee)",
  color: "var(--ink, #2B2520)",
  outline: "none",
  transition: "border-color 150ms ease, box-shadow 150ms ease",
  width: "100%",
  boxSizing: "border-box",
};

const mismatchStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontStyle: "italic",
  fontSize: 12,
  color: "var(--rust, #B06A50)",
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

const successBoxStyle: React.CSSProperties = {
  fontFamily: "var(--ff-serif, Georgia, serif)",
  fontSize: 15,
  color: "var(--ink, #2B2520)",
  lineHeight: 1.55,
};

const linkStyle: React.CSSProperties = {
  color: "var(--sage, #6A9A7A)",
  textDecoration: "underline",
  textUnderlineOffset: 3,
};
