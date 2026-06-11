// Inspira — public about page (/about).
//
// Hero, single-paragraph founder bio, why canvas-first, why warm
// editorial, values, contacts. Team grid removed in the v5 marketing
// pivot — Inspira is a one-person company; the placeholder names
// were not honest about that.

import type { JSX } from "react";

import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import "./marketing.css";
import "./marketing-legal.css";

const VALUE_SLUGS = ["notebooks", "ai_asks", "unhurried", "yours"] as const;

export function AboutPage(): JSX.Element {
  return (
    <MarketingLayout>
      <Head
        title={t("marketing.about_page.meta.title")}
        description={t("marketing.about_page.meta.description")}
        canonical="https://tryinspira.com/about"
        ogImage="/og/og-about.png"
      />
      <section className="about-page" aria-labelledby="about-page-title">
        <p className="landing-eyebrow">{t("marketing.about_page.eyebrow")}</p>
        <h1 className="about-page__title" id="about-page-title">
          {t("marketing.about_page.headline")}
        </h1>
        <p className="about-page__lede">{t("marketing.about_page.subhead")}</p>

        <section className="about-page__section" aria-labelledby="about-founder-title">
          <h2 className="about-page__section-title" id="about-founder-title">
            {t("marketing.about_page.founder.title")}
          </h2>
          <p className="about-page__section-body">{t("marketing.about_page.founder.body_1")}</p>
        </section>

        <section className="about-page__section" aria-labelledby="about-canvas-title">
          <h2 className="about-page__section-title" id="about-canvas-title">
            {t("marketing.about_page.canvas_first.title")}
          </h2>
          <p className="about-page__section-body">
            {t("marketing.about_page.canvas_first.body_1")}
          </p>
          <p className="about-page__section-body">
            {t("marketing.about_page.canvas_first.body_2")}
          </p>
        </section>

        <section className="about-page__section" aria-labelledby="about-warm-title">
          <h2 className="about-page__section-title" id="about-warm-title">
            {t("marketing.about_page.warm.title")}
          </h2>
          <p className="about-page__section-body">{t("marketing.about_page.warm.body_1")}</p>
          <p className="about-page__section-body">{t("marketing.about_page.warm.body_2")}</p>
        </section>

        <section className="about-page__section" aria-labelledby="about-values-title">
          <h2 className="about-page__section-title" id="about-values-title">
            {t("marketing.about_page.values.title")}
          </h2>
          {VALUE_SLUGS.map((slug) => (
            <div key={slug} className="about-page__value">
              <p className="about-page__value-title">
                {t(`marketing.about_page.values.${slug}.title`)}
              </p>
              <p className="about-page__value-body">
                {t(`marketing.about_page.values.${slug}.body`)}
              </p>
            </div>
          ))}
        </section>

        <section className="about-page__section" aria-labelledby="about-contact-title">
          <h2 className="about-page__section-title" id="about-contact-title">
            {t("marketing.about_page.contact.title")}
          </h2>
          <ul className="about-page__contact-list">
            <li>
              <strong>{t("marketing.about_page.contact.general_label")}</strong>{" "}
              <a href="mailto:hello@tryinspira.com">hello@tryinspira.com</a>
            </li>
            <li>
              <strong>{t("marketing.about_page.contact.billing_label")}</strong>{" "}
              <a href="mailto:billing@tryinspira.com">billing@tryinspira.com</a>
            </li>
            <li>
              <strong>{t("marketing.about_page.contact.press_label")}</strong>{" "}
              <a href="mailto:press@tryinspira.com">press@tryinspira.com</a>
            </li>
            <li>
              <strong>{t("marketing.about_page.contact.security_label")}</strong>{" "}
              <a href="mailto:security@tryinspira.com">security@tryinspira.com</a>
            </li>
          </ul>
          <p className="about-page__section-body">
            {t("marketing.about_page.contact.sign_off")}
          </p>
        </section>
      </section>
    </MarketingLayout>
  );
}

export default AboutPage;
