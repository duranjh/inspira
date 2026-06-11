// Inspira — 404 "not found" page.
//
// Full-viewport centered paper card. The tone is deliberately quiet:
// this isn't a crash, just a corner of Inspira that doesn't exist or
// was moved. A single primary pill gets the user back to where they
// were trying to be.
//
// If the router can tell us which path the user asked for, we echo it
// as an inline mono string so they can spot a typo without squinting
// at the address bar.

import type { JSX } from "react";

import { t } from "../../i18n";

import "./errors.css";

export interface NotFoundPageProps {
  /** Fired when the "Back to your projects" pill is clicked. */
  onGoHome: () => void;
  /**
   * Optional — the path the user tried to reach. When provided it's
   * echoed in the body copy as a monospace inline, useful for spotting
   * typos.
   */
  pathAttempted?: string;
}

export function NotFoundPage({
  onGoHome,
  pathAttempted,
}: NotFoundPageProps): JSX.Element {
  return (
    <main className="error-page" role="main">
      <section className="error-page__card" aria-labelledby="not-found-heading">
        <p className="error-page__eyebrow">{t("app.brand")}</p>
        <h1 id="not-found-heading" className="error-page__heading">
          {t("error.not_found_title")}
        </h1>
        <p className="error-page__body">
          <em>
            {pathAttempted ? (
              // We can't route a React element through t()'s string
              // interpolation, so we split the path copy into a prefix
              // rendered before the monospace echo and the English-source
              // ending rendered after. A translator who needs a different
              // word order can adjust `error.not_found_body_with_path_*`
              // but the simplest case (matching English phrasing) uses a
              // single string with a `{path}` placeholder — see
              // `error.not_found_body_with_path` for the one-shot form.
              <>
                {t("error.not_found_body").replace(/\.$/, "")}{" "}
                (you were looking for{" "}
                <span className="error-page__body-echo">{pathAttempted}</span>
                ).
              </>
            ) : (
              t("error.not_found_body")
            )}
          </em>
        </p>
        <div className="error-page__actions">
          <button
            type="button"
            className="error-page__pill error-page__pill--primary"
            onClick={onGoHome}
          >
            {t("error.go_home")}
          </button>
        </div>
        <p className="error-page__footnote">{t("error.not_found_code")}</p>
      </section>
    </main>
  );
}

export default NotFoundPage;
