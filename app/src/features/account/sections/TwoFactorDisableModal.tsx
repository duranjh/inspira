// Account > Security > Turn-off-two-factor confirmation modal — v2.
//
// Four states: entry, submitting, error, success. Success auto-closes
// after 1500ms. The confirm CTA is sage-on-ink (not rust) because turning
// off 2FA is reversible — we surface a fresh-setup path right back to on.
// Errors render inline under the password field in rust (not a banner).

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";

export type TwoFactorDisableModalProps = {
  onClose: () => void;
  onSuccess: () => void;
};

type ModalState = "entry" | "submitting" | "error" | "success";

const AUTO_CLOSE_MS = 1500;
export function TwoFactorDisableModal({
  onClose,
  onSuccess,
}: TwoFactorDisableModalProps) {
  const [password, setPassword] = useState("");
  const [state, setState] = useState<ModalState>("entry");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const autoCloseRef = useRef<number | null>(null);

  // Esc dismisses, except during submit (avoid half-committed disables).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && state !== "submitting") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose, state]);

  // Auto-close on success.
  useEffect(() => {
    if (state !== "success") return;
    autoCloseRef.current = window.setTimeout(() => {
      onClose();
    }, AUTO_CLOSE_MS);
    return () => {
      if (autoCloseRef.current !== null) {
        window.clearTimeout(autoCloseRef.current);
        autoCloseRef.current = null;
      }
    };
  }, [state, onClose]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!password || state === "submitting" || state === "success") return;
      setState("submitting");
      setErrorMsg("");
      try {
        await api.disable2FA({ password });
        onSuccess();
        setState("success");
      } catch (err) {
        const status = parseStatus(err);
        if (status === 404) {
          toast.info(t("account.security.unavailable"));
          onClose();
          return;
        }
        if (status === 401) {
          setErrorMsg(t("account.security.disable.error_wrong_password"));
        } else {
          setErrorMsg(t("account.security.disable.error_generic"));
        }
        setState("error");
      }
    },
    [password, state, onSuccess, onClose],
  );

  const submitting = state === "submitting";
  const showSuccess = state === "success";

  // The email interpolated into the body copy is not yet wired through
  // /auth/me in this surface — use a neutral phrasing until it is.
  const bodyText = showSuccess
    ? t("account.security.disable.success_body")
    : t("account.security.disable.body");

  const invalid = state === "error";

  return (
    <div
      className="twofa-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="twofa-disable-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="twofa-modal__card twofa-modal__card--v2"
        data-state={state}
      >
        <button
          type="button"
          className="twofa-modal__close"
          onClick={onClose}
          aria-label={t("account.security.disable.close")}
          disabled={submitting}
        >
          {"\u00D7"}
        </button>

        {showSuccess ? (
          <>
            <p className="twofa-modal__eyebrow">
              {t("account.security.disable.eyebrow")}
            </p>
            <div className="twofa-modal__success-head">
              <span className="twofa-modal__check" aria-hidden="true">
                {"\u2713"}
              </span>
              <h3 className="twofa-modal__title" id="twofa-disable-title">
                {t("account.security.disable.success_title")}
              </h3>
            </div>
            <p className="twofa-modal__serif">{bodyText}</p>
          </>
        ) : (
          <>
            <p className="twofa-modal__eyebrow">
              {t("account.security.disable.eyebrow")}
            </p>
            <h3 className="twofa-modal__title" id="twofa-disable-title">
              {t("account.security.disable.title")}
            </h3>
            <p className="twofa-modal__serif">{bodyText}</p>
            <form
              className="twofa-modal__body"
              onSubmit={handleSubmit}
              noValidate
            >
              <div className="account-field">
                <label
                  className="account-field__label"
                  htmlFor="twofa-disable-password"
                >
                  {t("account.security.disable.password_label")}
                </label>
                <input
                  id="twofa-disable-password"
                  type="password"
                  autoComplete="current-password"
                  className={
                    "account-field__input" +
                    (invalid ? " account-field__input--invalid" : "")
                  }
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (state === "error") {
                      setState("entry");
                      setErrorMsg("");
                    }
                  }}
                  disabled={submitting}
                  autoFocus
                  aria-invalid={invalid ? "true" : undefined}
                  aria-describedby={
                    invalid ? "twofa-disable-error" : undefined
                  }
                />
                {invalid ? (
                  <p
                    id="twofa-disable-error"
                    className="twofa-modal__inline-error"
                    role="alert"
                  >
                    {errorMsg}
                  </p>
                ) : null}
              </div>
              <div className="twofa-modal__actions">
                <button
                  type="button"
                  className="account-btn account-btn--ghost"
                  onClick={onClose}
                  disabled={submitting}
                >
                  {t("account.security.disable.keep_enabled")}
                </button>
                <button
                  type="submit"
                  className="account-btn account-btn--sage"
                  disabled={!password || submitting}
                >
                  {submitting
                    ? t("account.security.disable.submitting")
                    : t("account.security.disable.submit")}
                </button>
              </div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
