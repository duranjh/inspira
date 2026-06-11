// Account > Security > Turn-on-two-factor modal.
//
// Three-step flow:
//   1. QR panel — fetch `setup2FA()` which returns a secret, a QR svg
//      string, and a batch of recovery codes. The user scans the QR in
//      their authenticator app; the secret is rendered in monospace as a
//      manual fallback.
//   2. Verify — user types the 6-digit code from their app; we POST it
//      to /api/auth/2fa/verify. A mismatch surfaces a warm inline error
//      and stays on step 2 so the user can try the next code rotation.
//   3. Recovery codes — show the batch from step 1 in a monospace list
//      with a "Download as text" button. The "Keep these somewhere safe"
//      copy is explicit about why.

import { useCallback, useEffect, useState } from "react";

import { api, type TwoFactorSetupResponse } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";

export type TwoFactorSetupProps = {
  onClose: () => void;
  onSuccess: () => void;
};
type Step = 1 | 2 | 3;

export function TwoFactorSetup({ onClose, onSuccess }: TwoFactorSetupProps) {
  const [step, setStep] = useState<Step>(1);
  const [setupData, setSetupData] = useState<TwoFactorSetupResponse | null>(
    null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  // Esc to dismiss; captured so the outer AccountSettingsPage Esc handler
  // doesn't run at the same time.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // Fetch the setup data on mount. 404 means "backend not wired yet" —
  // surface a toast and close so the UI isn't stuck.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await api.setup2FA();
        if (cancelled) return;
        setSetupData(res);
      } catch (err) {
        if (cancelled) return;
        const status = parseStatus(err);
        if (status === 404) {
          toast.info(t("account.security.unavailable"));
          onClose();
          return;
        }
        setLoadError(t("account.security.unavailable"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [onClose]);

  const handleVerify = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!code.trim() || code.trim().length < 6) return;
      setVerifying(true);
      setVerifyError(null);
      try {
        await api.verify2FA({ code: code.trim() });
        setStep(3);
      } catch (err) {
        const status = parseStatus(err);
        if (status === 404) {
          toast.info(t("account.security.unavailable"));
          onClose();
          return;
        }
        setVerifyError(t("account.twofa_setup.verify_error"));
      } finally {
        setVerifying(false);
      }
    },
    [code, onClose],
  );

  const handleDownloadCodes = useCallback(() => {
    if (!setupData) return;
    const text = setupData.recovery_codes.join("\n") + "\n";
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "inspira-recovery-codes.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [setupData]);

  const handleDone = useCallback(() => {
    onSuccess();
    onClose();
  }, [onSuccess, onClose]);

  return (
    <div
      className="twofa-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="twofa-setup-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="twofa-modal__card">
        <header className="twofa-modal__header">
          <h3 className="twofa-modal__title" id="twofa-setup-title">
            {t("account.twofa_setup.title")}
          </h3>
          <button
            type="button"
            className="twofa-modal__close"
            onClick={onClose}
            aria-label={t("account.twofa_setup.cancel")}
          >
            {"\u00D7"}
          </button>
        </header>

        {!setupData && !loadError ? (
          <div className="twofa-modal__body">
            <p className="twofa-modal__body-text">
              <em>{t("account.twofa_setup.loading")}</em>
            </p>
          </div>
        ) : loadError ? (
          <div className="twofa-modal__body">
            <p className="account-status account-status--error" role="alert">
              {loadError}
            </p>
          </div>
        ) : step === 1 && setupData ? (
          <div className="twofa-modal__body">
            <h4 className="twofa-modal__step-heading">
              {t("account.twofa_setup.step1_heading")}
            </h4>
            <p className="twofa-modal__body-text">
              <em>{t("account.twofa_setup.step1_body")}</em>
            </p>
            <div
              className="twofa-qr"
              aria-label={t("account.twofa_setup.step1_heading")}
              // QR svg comes from the server as a trusted text blob.
              // Rendering via dangerouslySetInnerHTML is deliberate — the
              // alternative is to ship a QR generator in the bundle.
              dangerouslySetInnerHTML={{ __html: setupData.qr_svg }}
            />
            <div className="account-field">
              <label className="account-field__label">
                {t("account.twofa_setup.secret_label")}
              </label>
              <code className="twofa-secret">{setupData.secret}</code>
            </div>
            <div className="twofa-modal__actions">
              <button
                type="button"
                className="account-btn account-btn--ghost"
                onClick={onClose}
              >
                {t("account.twofa_setup.cancel")}
              </button>
              <button
                type="button"
                className="account-btn"
                onClick={() => setStep(2)}
              >
                {t("account.twofa_setup.continue")}
              </button>
            </div>
          </div>
        ) : step === 2 && setupData ? (
          <form
            className="twofa-modal__body"
            onSubmit={handleVerify}
            noValidate
          >
            <h4 className="twofa-modal__step-heading">
              {t("account.twofa_setup.step2_heading")}
            </h4>
            <p className="twofa-modal__body-text">
              <em>{t("account.twofa_setup.step2_body")}</em>
            </p>
            <div className="account-field">
              <label className="account-field__label" htmlFor="twofa-code">
                {t("account.twofa_setup.code_label")}
              </label>
              <input
                id="twofa-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                className="account-field__input twofa-code-input"
                value={code}
                onChange={(e) =>
                  setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                }
                placeholder={t("account.twofa_setup.code_placeholder")}
                disabled={verifying}
                autoFocus
                maxLength={6}
              />
            </div>
            {verifyError ? (
              <p
                className="account-status account-status--error"
                role="alert"
              >
                {verifyError}
              </p>
            ) : null}
            <div className="twofa-modal__actions">
              <button
                type="button"
                className="account-btn account-btn--ghost"
                onClick={onClose}
                disabled={verifying}
              >
                {t("account.twofa_setup.cancel")}
              </button>
              <button
                type="submit"
                className="account-btn"
                disabled={code.length < 6 || verifying}
              >
                {verifying
                  ? t("account.twofa_setup.verifying")
                  : t("account.twofa_setup.verify")}
              </button>
            </div>
          </form>
        ) : step === 3 && setupData ? (
          <div className="twofa-modal__body">
            <h4 className="twofa-modal__step-heading">
              {t("account.twofa_setup.step3_heading")}
            </h4>
            <p className="twofa-modal__body-text twofa-modal__body-text--warm">
              <em>{t("account.twofa_setup.step3_body")}</em>
            </p>
            <ul className="twofa-codes">
              {setupData.recovery_codes.map((rc) => (
                <li key={rc} className="twofa-codes__item">
                  {rc}
                </li>
              ))}
            </ul>
            <div className="twofa-modal__actions">
              <button
                type="button"
                className="account-btn account-btn--ghost"
                onClick={handleDownloadCodes}
              >
                {t("account.twofa_setup.download")}
              </button>
              <button
                type="button"
                className="account-btn"
                onClick={handleDone}
              >
                {t("account.twofa_setup.done")}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
