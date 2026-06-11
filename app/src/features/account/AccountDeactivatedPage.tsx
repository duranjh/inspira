// Inspira — Account deactivated page (C6).
//
// Quiet-center layout (same scaffolding as NotFoundPage). Shown when a
// signed-in session belongs to an account whose row has `deleted_at`
// set, or when someone lands on /account-deactivated directly (usually
// from an email link).
//
// Copy deliberately avoids "recovery" / "cold storage" / "for a limited
// time" language. When we say deleted, we mean deleted — that honesty
// is a brand promise the UI must not soften.

import type { JSX } from "react";

import { formatDate, t } from "../../i18n";
import "../errors/errors.css";

export interface AccountDeactivatedPageProps {
  /**
   * ISO 8601 timestamp of when the account was deleted. Rendered as a
   * medium-length date ("21 Apr 2026"). When absent we render a dash.
   */
  deletedAt?: string | null;
}

export function AccountDeactivatedPage({
  deletedAt,
}: AccountDeactivatedPageProps): JSX.Element {
  const formatted = deletedAt ? formatDate(deletedAt) : "\u2014";

  const handleSignup = () => {
    window.location.assign("/?signup=1");
  };

  const handleHome = () => {
    window.location.assign("/");
  };

  return (
    <main className="error-page" role="main">
      <section
        className="error-page__card"
        aria-labelledby="account-deactivated-heading"
      >
        <p className="error-page__eyebrow">{t("app.brand")}</p>
        <h1
          id="account-deactivated-heading"
          className="error-page__heading"
        >
          {t("account.deactivated.heading")}
        </h1>
        <p className="error-page__body">
          {t("account.deactivated.body", { deleted_at: formatted })}
        </p>
        <p className="error-page__body">
          <em>{t("account.deactivated.subtext")}</em>
        </p>
        <div className="error-page__actions">
          <button
            type="button"
            className="error-page__pill error-page__pill--primary"
            onClick={handleSignup}
          >
            {t("account.deactivated.primary_cta")}
          </button>
          <button
            type="button"
            className="error-page__pill error-page__pill--secondary"
            onClick={handleHome}
          >
            {t("account.deactivated.secondary_cta")}
          </button>
        </div>
        <p className="error-page__footnote">
          {t("account.deactivated.footnote")}
        </p>
      </section>
    </main>
  );
}

export default AccountDeactivatedPage;
