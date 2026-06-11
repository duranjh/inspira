// Account > Password section.
//
// Change-password form with three inputs (current / new / confirm) and
// client-side validation before we hit the API:
//   - new password must be at least 8 characters (matches signup rule)
//   - new must match confirm
//   - current must be non-empty
//
// Backend route /api/auth/change-password doesn't exist yet — a 404 is
// treated as "Coming soon" via the toast. Non-404 errors (401 if the
// current password was wrong, 400 for rejected inputs) get specific
// copy. Password fields are never logged.

import {
  useCallback,
  useState,
  type FormEvent,
} from "react";

import { api } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";

export type PasswordSectionProps = {
  // isSystem guards the form when the user isn't actually signed in
  // (backend falls back to a shared system user in that case — changing
  // that password would be meaningless).
  isSystem: boolean;
};
export function PasswordSection({ isSystem }: PasswordSectionProps) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );

  const reset = useCallback(() => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setValidationMessage(null);
  }, []);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (submitting) return;
      setValidationMessage(null);

      if (!currentPassword) {
        setValidationMessage(t("account.password.error_no_current"));
        return;
      }
      if (newPassword.length < 8) {
        setValidationMessage(t("account.password.error_too_short"));
        return;
      }
      if (newPassword !== confirmPassword) {
        setValidationMessage(t("account.password.error_mismatch"));
        return;
      }
      if (newPassword === currentPassword) {
        setValidationMessage(t("account.password.error_same"));
        return;
      }

      setSubmitting(true);
      try {
        await api.changePassword({
          current_password: currentPassword,
          new_password: newPassword,
        });
        toast.success(t("account.password.success"));
        reset();
      } catch (err) {
        const code = parseStatus(err);
        if (code === 404) {
          toast.info(
            "Coming soon — password changes land with the next backend release.",
          );
        } else if (code === 401) {
          setValidationMessage(t("account.password.error_wrong_current"));
        } else if (code === 400) {
          setValidationMessage(t("account.password.error_rejected"));
        } else {
          toast.error(t("account.password.error_generic"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [
      confirmPassword,
      currentPassword,
      newPassword,
      reset,
      submitting,
    ],
  );

  if (isSystem) {
    return (
      <section className="account-section" aria-labelledby="account-password-heading">
        <h2 className="account-section__heading" id="account-password-heading">
          {t("account.password.heading")}
        </h2>
        <p className="account-section__subtitle">
          {t("account.password.subtitle_system")}
        </p>
      </section>
    );
  }

  return (
    <section className="account-section" aria-labelledby="account-password-heading">
      <h2 className="account-section__heading" id="account-password-heading">
        {t("account.password.heading")}
      </h2>
      <p className="account-section__subtitle">
        {t("account.password.subtitle")}
      </p>
      <form className="account-section__body" onSubmit={handleSubmit} noValidate>
        <div className="account-field">
          <label className="account-field__label" htmlFor="account-current-password">
            {t("account.password.current_label")}
          </label>
          <input
            id="account-current-password"
            type="password"
            className="account-field__input"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            disabled={submitting}
          />
        </div>

        <div className="account-field">
          <label className="account-field__label" htmlFor="account-new-password">
            {t("account.password.new_label")}
          </label>
          <input
            id="account-new-password"
            type="password"
            className="account-field__input"
            autoComplete="new-password"
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            disabled={submitting}
          />
        </div>

        <div className="account-field">
          <label className="account-field__label" htmlFor="account-confirm-password">
            {t("account.password.confirm_label")}
          </label>
          <input
            id="account-confirm-password"
            type="password"
            className="account-field__input"
            autoComplete="new-password"
            minLength={8}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            disabled={submitting}
          />
        </div>

        {validationMessage ? (
          <p
            className="account-status account-status--error"
            role="alert"
            aria-live="assertive"
          >
            {validationMessage}
          </p>
        ) : null}

        <button
          type="submit"
          className="account-btn"
          disabled={submitting}
        >
          {submitting ? t("account.password.submitting") : t("account.password.submit")}
        </button>
      </form>
    </section>
  );
}
