// Inspira — 500 "something's off" page.
//
// Same paper-card scaffold as NotFoundPage, but with a retry action
// alongside "back to your projects" and an optional reference string
// for support tickets. Tone is apologetic without being panicked:
// "the server stumbled, we've logged it, try again in a moment."
//
// When `requestId` is provided it's shown as a tiny monospace footnote
// ("Reference: abc123") so a user who files a support ticket can paste
// it in. When `message` is provided it replaces the default body copy
// with the backend's explanation (still rendered as italic serif for
// consistency with the voice of the rest of the app).

import type { JSX } from "react";

import { t } from "../../i18n";

import "./errors.css";

export interface ServerErrorPageProps {
  /** Fired when the "Try again" pill is clicked. */
  onRetry: () => void;
  /** Fired when the "Back to your projects" pill is clicked. */
  onGoHome: () => void;
  /**
   * Optional request/correlation ID shown as a "Reference: …" footnote
   * so the user can quote it when filing a support ticket.
   */
  requestId?: string;
  /**
   * Optional override for the body copy. When absent, we fall back to
   * the localized `error.server_default_body` string.
   */
  message?: string;
}

export function ServerErrorPage({
  onRetry,
  onGoHome,
  requestId,
  message,
}: ServerErrorPageProps): JSX.Element {
  const body = message ?? t("error.server_default_body");
  return (
    <main className="error-page" role="main">
      <section
        className="error-page__card"
        aria-labelledby="server-error-heading"
      >
        <p className="error-page__eyebrow">{t("app.brand")}</p>
        <h1 id="server-error-heading" className="error-page__heading">
          {t("error.server_title")}
        </h1>
        <p className="error-page__body">
          <em>{body}</em>
        </p>
        <div className="error-page__actions">
          <button
            type="button"
            className="error-page__pill error-page__pill--primary"
            onClick={onRetry}
          >
            {t("error.try_again")}
          </button>
          <button
            type="button"
            className="error-page__pill error-page__pill--secondary"
            onClick={onGoHome}
          >
            {t("error.go_home")}
          </button>
        </div>
        {requestId ? (
          <p className="error-page__footnote">
            {t("error.reference")}{" "}
            <span className="error-page__footnote-ref">{requestId}</span>
          </p>
        ) : null}
      </section>
    </main>
  );
}

export default ServerErrorPage;
