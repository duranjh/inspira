// Account > Security section (C1).
//
// Status block for two-factor authentication plus an inline list of
// active sign-in sessions. The 2FA primitives go through three small
// modals (TwoFactorSetup + TwoFactorDisableModal) that handle the actual
// credential-handling flows; this file is the section shell that wires
// them to the account page.
//
// Every backend route is stubbed today. api.ts methods 404 and we surface
// a "Coming soon" toast so the UI is fully inspectable in design review.

import { useCallback, useEffect, useState } from "react";

import { api } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { ActiveSessionsTable } from "./ActiveSessionsTable";
import { TwoFactorSetup } from "./TwoFactorSetup";
import { TwoFactorDisableModal } from "./TwoFactorDisableModal";
import { parseStatus } from "../../../lib/httpStatus";

export type SecuritySectionProps = {
  isSystem: boolean;
};
type Modal =
  | { kind: "none" }
  | { kind: "setup" }
  | { kind: "disable" }
  | { kind: "regen" };

export function SecuritySection({ isSystem }: SecuritySectionProps) {
  // We don't have a backend source of truth yet, so we keep the 2FA
  // state client-side. A successful setup flips this to true; a
  // successful disable flips it back. When the backend lands, swap
  // this for a field on /api/auth/me or a dedicated /api/auth/2fa/status
  // endpoint.
  const [twoFactorOn, setTwoFactorOn] = useState(false);
  const [modal, setModal] = useState<Modal>({ kind: "none" });

  const handleSetupSuccess = useCallback(() => {
    setTwoFactorOn(true);
  }, []);

  const handleDisableSuccess = useCallback(() => {
    setTwoFactorOn(false);
  }, []);

  if (isSystem) {
    return (
      <section
        className="account-section"
        aria-labelledby="account-security-heading"
      >
        <h2 className="account-section__heading" id="account-security-heading">
          {t("account.security.heading")}
        </h2>
        <p className="account-section__subtitle">
          {t("account.danger.system_note")}
        </p>
      </section>
    );
  }

  return (
    <section
      className="account-section"
      aria-labelledby="account-security-heading"
    >
      <h2 className="account-section__heading" id="account-security-heading">
        {t("account.security.heading")}
      </h2>
      <p className="account-section__subtitle">
        {t("account.security.subtitle")}
      </p>
      <div className="account-section__body">
        <div className="account-security__status">
          <div className="account-security__status-row">
            <span className="account-security__status-label">
              {t("account.security.twofa_label")}
            </span>
            <span
              className={
                twoFactorOn
                  ? "account-security__badge account-security__badge--on"
                  : "account-security__badge"
              }
            >
              {twoFactorOn
                ? t("account.security.twofa_on")
                : t("account.security.twofa_off")}
            </span>
          </div>
          <div className="account-security__actions">
            {twoFactorOn ? (
              <>
                <button
                  type="button"
                  className="account-btn account-btn--ghost"
                  onClick={() => setModal({ kind: "regen" })}
                >
                  {t("account.security.regenerate_codes")}
                </button>
                <button
                  type="button"
                  className="account-security__danger-link"
                  onClick={() => setModal({ kind: "disable" })}
                >
                  {t("account.security.turn_off")}
                </button>
              </>
            ) : (
              <button
                type="button"
                className="account-btn account-security__sage-cta"
                onClick={() => setModal({ kind: "setup" })}
              >
                {t("account.security.turn_on")}
              </button>
            )}
          </div>
        </div>

        <div className="account-security__sessions">
          <h3 className="account-security__subheading">
            {t("account.security.sessions_heading")}
          </h3>
          <p className="account-security__subheading-help">
            {t("account.security.sessions_help")}
          </p>
          <ActiveSessionsTable />
        </div>
      </div>

      {modal.kind === "setup" ? (
        <TwoFactorSetup
          onClose={() => setModal({ kind: "none" })}
          onSuccess={handleSetupSuccess}
        />
      ) : null}

      {modal.kind === "disable" ? (
        <TwoFactorDisableModal
          onClose={() => setModal({ kind: "none" })}
          onSuccess={handleDisableSuccess}
        />
      ) : null}

      {modal.kind === "regen" ? (
        <RegenerateRecoveryCodesModal
          onClose={() => setModal({ kind: "none" })}
        />
      ) : null}
    </section>
  );
}

// ---- Regenerate recovery codes ----------------------------------------
//
// Small inline component — kept here rather than a separate file because
// it shares the TwoFactorDisableModal shape almost exactly (password re-
// entry + a single call) and lives in only one place.

function RegenerateRecoveryCodesModal({
  onClose,
}: {
  onClose: () => void;
}) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Esc to dismiss — match TwoFactorSetup/Disable dialogs.
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

  const handleSubmit = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!password) return;
      setSubmitting(true);
      setError(null);
      try {
        const res = await api.regenerateRecoveryCodes({ password });
        setCodes(res.recovery_codes);
      } catch (err) {
        const code = parseStatus(err);
        if (code === 404) {
          toast.info(t("account.security.unavailable"));
          onClose();
          return;
        }
        if (code === 401) {
          setError(t("account.twofa_disable.wrong_password"));
        } else {
          setError(t("account.twofa_disable.error"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [password, onClose],
  );

  return (
    <div
      className="twofa-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="regen-recovery-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="twofa-modal__card">
        <header className="twofa-modal__header">
          <h3 className="twofa-modal__title" id="regen-recovery-title">
            {t("account.security.regenerate_codes")}
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
        {codes ? (
          <div className="twofa-modal__body">
            <p className="twofa-modal__body-text">
              <em>{t("account.twofa_setup.step3_body")}</em>
            </p>
            <ul className="twofa-codes">
              {codes.map((code) => (
                <li key={code} className="twofa-codes__item">
                  {code}
                </li>
              ))}
            </ul>
            <div className="twofa-modal__actions">
              <button
                type="button"
                className="account-btn"
                onClick={onClose}
              >
                {t("account.twofa_setup.done")}
              </button>
            </div>
          </div>
        ) : (
          <form
            className="twofa-modal__body"
            onSubmit={handleSubmit}
            noValidate
          >
            <p className="twofa-modal__body-text">
              <em>{t("account.twofa_disable.body")}</em>
            </p>
            <div className="account-field">
              <label
                className="account-field__label"
                htmlFor="regen-password"
              >
                {t("account.twofa_disable.password_label")}
              </label>
              <input
                id="regen-password"
                type="password"
                className="account-field__input"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                autoFocus
              />
            </div>
            {error ? (
              <p
                className="account-status account-status--error"
                role="alert"
              >
                {error}
              </p>
            ) : null}
            <div className="twofa-modal__actions">
              <button
                type="button"
                className="account-btn account-btn--ghost"
                onClick={onClose}
                disabled={submitting}
              >
                {t("account.twofa_setup.cancel")}
              </button>
              <button
                type="submit"
                className="account-btn"
                disabled={!password || submitting}
              >
                {submitting
                  ? t("account.twofa_disable.submitting")
                  : t("account.security.regenerate_codes")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
