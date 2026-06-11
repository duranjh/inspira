// Inspira — unsubscribe confirmation page (/unsubscribe).
//
// Quiet-center layout. Server-side has already validated the token and
// flipped the flag; if anything was wrong the server will have 302'd us
// to /404 before we ever render. On this surface we just show a calm
// confirmation, echo a redacted version of the email, and hint how to
// resubscribe.
//
// URL shape: /unsubscribe?email=foo@example.com&token=...
// We only read `email`. The token has already done its job on the server
// and is not shown back to the user.

import { useMemo, type JSX } from "react";

import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import "./marketing.css";
import "./marketing-legal.css";

/**
 * Hide most of the local-part of an address. `alexa@tryinspira.com`
 * becomes `a***@tryinspira.com`. If the string isn't an email or we
 * can't see both halves, fall back to a fixed mask rather than leaking
 * the raw input.
 */
function redactEmail(raw: string | null): string {
  if (!raw) return t("marketing.unsubscribe.email_fallback");
  const at = raw.indexOf("@");
  if (at <= 0) return t("marketing.unsubscribe.email_fallback");
  const local = raw.slice(0, at);
  const domain = raw.slice(at);
  const first = local.charAt(0);
  return `${first}***${domain}`;
}

function readEmailParam(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("email");
    return raw && raw.trim() !== "" ? raw.trim() : null;
  } catch {
    return null;
  }
}

export function UnsubscribePage(): JSX.Element {
  const redacted = useMemo(() => redactEmail(readEmailParam()), []);

  return (
    <MarketingLayout>
      <Head
        title={t("marketing.unsubscribe.meta.title")}
        description={t("marketing.unsubscribe.meta.description")}
        canonical="https://tryinspira.com/unsubscribe"
        robots="noindex,nofollow"
      />
      <section
        className="unsubscribe-page"
        aria-labelledby="unsubscribe-page-title"
      >
        <div className="unsubscribe-page__card">
          <p className="unsubscribe-page__eyebrow">
            {t("marketing.unsubscribe.eyebrow")}
          </p>
          <h1
            className="unsubscribe-page__title"
            id="unsubscribe-page-title"
          >
            {t("marketing.unsubscribe.title")}
          </h1>
          <p className="unsubscribe-page__body">
            {t("marketing.unsubscribe.body_prefix")}{" "}
            <span className="unsubscribe-page__email">{redacted}</span>{" "}
            {t("marketing.unsubscribe.body_suffix")}
          </p>
          <p className="unsubscribe-page__body">
            {t("marketing.unsubscribe.security_note")}
          </p>
          <p className="unsubscribe-page__subtext">
            {t("marketing.unsubscribe.resubscribe_hint")}
          </p>
        </div>
      </section>
    </MarketingLayout>
  );
}

export default UnsubscribePage;
