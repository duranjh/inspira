// Account > Danger zone.
//
// Permanently delete the account. Two-step flow to make accidental clicks
// hard to land: first click reveals a typed-confirmation input — the user
// has to literally type DELETE before the submit button unlocks. We also
// ask for the password again as a final sanity check.
//
// Backend /api/auth/delete-account doesn't exist yet; a 404 surfaces the
// "Coming soon" toast. On a successful delete we reload the app so state
// resets cleanly (the server's session cookie is gone; /api/auth/me will
// return a fresh system user).

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";

import { api } from "../../inspira/api";
import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { parseStatus } from "../../../lib/httpStatus";

export type DangerZoneSectionProps = {
  isSystem: boolean;
};

const CONFIRM_WORD = "DELETE";
export function DangerZoneSection({ isSystem }: DangerZoneSectionProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );

  // Clear the confirmation fields whenever the user collapses the
  // confirmation back; guarantees they have to re-type DELETE every time.
  useEffect(() => {
    if (!confirmOpen) {
      setConfirmText("");
      setPasswordConfirmation("");
      setValidationMessage(null);
    }
  }, [confirmOpen]);

  const canConfirm = confirmText.trim() === CONFIRM_WORD && !submitting;

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!canConfirm) return;
      if (!passwordConfirmation) {
        setValidationMessage(t("account.danger.enter_password_error"));
        return;
      }
      setSubmitting(true);
      setValidationMessage(null);
      try {
        await api.deleteAccount({
          password_confirmation: passwordConfirmation,
        });
        toast.success(t("account.danger.success"));
        // Drop all in-memory state — the session cookie is gone on the
        // server, a fresh bootstrap will land us on the kickoff screen.
        setTimeout(() => window.location.reload(), 400);
      } catch (err) {
        const code = parseStatus(err);
        if (code === 404) {
          toast.info(
            "Coming soon \u2014 account deletion lands with the next backend release.",
          );
          setConfirmOpen(false);
        } else if (code === 401) {
          setValidationMessage(t("account.danger.wrong_password_error"));
        } else {
          toast.error(t("account.danger.error"));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [canConfirm, passwordConfirmation],
  );

  return (
    <section
      className="account-section"
      aria-labelledby="account-danger-heading"
    >
      <h2 className="account-section__heading" id="account-danger-heading">
        {t("account.danger.heading")}
      </h2>
      <p className="account-section__subtitle">
        {t("account.danger.subtitle")}
      </p>
      <div className="account-section__body">
        <div className="account-danger" role="group" aria-labelledby="account-danger-subheading">
          <h3
            className="account-danger__heading"
            id="account-danger-subheading"
          >
            {t("account.danger.delete_account_heading")}
          </h3>
          <p className="account-danger__body">
            {t("account.danger.delete_account_body")}
          </p>

          {isSystem ? (
            <p className="account-danger__note">
              <em>
                {t("account.danger.system_note")}
              </em>
            </p>
          ) : !confirmOpen ? (
            <div className="account-danger__actions">
              <button
                type="button"
                className="account-btn account-btn--danger"
                onClick={() => setConfirmOpen(true)}
              >
                {t("account.danger.delete_button")}
              </button>
            </div>
          ) : (
            <form
              className="account-danger__confirm"
              onSubmit={handleSubmit}
              noValidate
            >
              <div className="account-field">
                <label
                  className="account-danger__confirm-label"
                  htmlFor="account-delete-confirm"
                >
                  {t("account.danger.type_to_confirm")}
                </label>
                <input
                  id="account-delete-confirm"
                  type="text"
                  className="account-field__input"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  disabled={submitting}
                  autoComplete="off"
                  spellCheck={false}
                  autoFocus
                />
              </div>

              <div className="account-field">
                <label
                  className="account-field__label"
                  htmlFor="account-delete-password"
                >
                  {t("account.danger.password_confirm_label")}
                </label>
                <input
                  id="account-delete-password"
                  type="password"
                  className="account-field__input"
                  autoComplete="current-password"
                  value={passwordConfirmation}
                  onChange={(e) => setPasswordConfirmation(e.target.value)}
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

              <div className="account-danger__actions">
                <button
                  type="submit"
                  className="account-btn account-btn--danger"
                  disabled={!canConfirm || !passwordConfirmation}
                >
                  {submitting ? t("account.danger.submitting") : t("account.danger.submit")}
                </button>
                <button
                  type="button"
                  className="account-btn account-btn--ghost"
                  onClick={() => setConfirmOpen(false)}
                  disabled={submitting}
                >
                  {t("account.danger.cancel")}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}
