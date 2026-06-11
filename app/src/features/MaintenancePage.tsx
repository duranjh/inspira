// Inspira — Maintenance page (C7).
//
// Quiet-center layout, shown when:
//   - The user lands on /maintenance directly, OR
//   - The INSPIRA_MAINTENANCE_MODE env flag is set at build time.
//
// No login form, no CTAs beyond a link to the status page.

import type { JSX } from "react";

import { t } from "../i18n";
import "./errors/errors.css";

export function MaintenancePage(): JSX.Element {
  // The subtext says "check {status_page_link}" — we render an <a> in
  // place of that token. Because our tiny i18n shim doesn't understand
  // rich-text placeholders, we split the string manually around the
  // token marker.
  const subtextTemplate = t("maintenance.subtext");
  const linkLabel = t("maintenance.status_page_link_label");
  const parts = subtextTemplate.split("{status_page_link}");

  return (
    <main className="error-page" role="main">
      <section
        className="error-page__card"
        aria-labelledby="maintenance-heading"
      >
        <p className="error-page__eyebrow">{t("app.brand")}</p>
        <h1 id="maintenance-heading" className="error-page__heading">
          {t("maintenance.heading")}
        </h1>
        <p className="error-page__body">
          <em>{t("maintenance.body")}</em>
        </p>
        <p className="error-page__body">
          <em>
            {parts[0]}
            <a
              href="/status"
              style={{
                color: "var(--sage)",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              {linkLabel}
            </a>
            {parts[1] ?? ""}
          </em>
        </p>
        <p className="error-page__footnote">{t("maintenance.footnote")}</p>
      </section>
    </main>
  );
}

export default MaintenancePage;
