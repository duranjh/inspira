// Inspira — public /how-it-works page (v5).
//
// Mirrors `docs/product/design/v5-pivot/Inspira v5 Pivot (12).zip`
// → Marketing.html lines 319–434 (Page 2 — How it works). A centered
// hero, three deep-step blocks (connectors → orchestrator → review),
// and a final CTA band. Copy drives off `marketing.how_it_works.*`
// keys added in Wave 1.

import { useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";

import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import "./marketing.css";

import artifactShot from "./assets/screenshots/artifact.jpg";
import canvasShot from "./assets/screenshots/canvas.jpg";
import connectorsShot from "./assets/screenshots/connectors.jpg";
import kanbanShot from "./assets/screenshots/kanban.jpg";
import summaryShot from "./assets/screenshots/summary.jpg";

export function HowItWorksPage() {
  const navigate = useNavigate();

  const startFree = useCallback(() => {
    navigate("/app?new=1");
  }, [navigate]);

  const talkToSales = useCallback(() => {
    // Stripe is dark — paid CTAs route to mailto until Stripe Live (~6/14).
    window.location.href =
      "mailto:hello@tryinspira.com?subject=Inspira%20design%20partner";
  }, []);

  return (
    <MarketingLayout>
      <Head
        title={t("marketing.how_it_works.meta.title")}
        description={t("marketing.how_it_works.meta.description")}
        canonical="https://tryinspira.com/how-it-works"
        ogImage="https://tryinspira.com/og/og-how-it-works.png"
      />

      {/* Centered hero */}
      <section
        className="mk-section mk-hero--centered"
        aria-labelledby="hiw-hero-title"
      >
        <h1 id="hiw-hero-title">
          {t("marketing.how_it_works.hero.title")}
        </h1>
        <p className="mk-hero__sub">
          {t("marketing.how_it_works.hero.subhead")}
        </p>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Step 1 — Connectors */}
      <section className="mk-section" aria-labelledby="hiw-step1-title">
        <div className="hiw-deep">
          <article className="hiw-deep-step">
            <div className="hiw-deep-step__label">
              {t("marketing.how_it_works.step1.label")}
            </div>
            <h3 id="hiw-step1-title">
              {t("marketing.how_it_works.step1.title")}
            </h3>
            <p className="hiw-deep-step__body">
              {t("marketing.how_it_works.step1.body")}
            </p>
            <div className="hiw-deep-step__shots hiw-deep-step__shots--single">
              <figure className="hiw-deep-step__shot">
                <img
                  src={connectorsShot}
                  alt={t("marketing.how_it_works.step1.shot_alt")}
                />
              </figure>
            </div>
            <p className="hiw-deep-step__caption">
              {t("marketing.how_it_works.step1.caption")}
            </p>
            <ul className="hiw-deep-step__list">
              <li>{t("marketing.how_it_works.step1.list_item_1")}</li>
              <li>{t("marketing.how_it_works.step1.list_item_2")}</li>
              <li>{t("marketing.how_it_works.step1.list_item_3")}</li>
              <li>{t("marketing.how_it_works.step1.list_item_4")}</li>
              <li>{t("marketing.how_it_works.step1.list_item_5")}</li>
              <li>{t("marketing.how_it_works.step1.list_item_6")}</li>
              <li>{t("marketing.how_it_works.step1.list_item_7")}</li>
            </ul>
          </article>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Step 2 — Orchestrator + sub-agents */}
      <section className="mk-section" aria-labelledby="hiw-step2-title">
        <div className="hiw-deep">
          <article className="hiw-deep-step">
            <div className="hiw-deep-step__label">
              {t("marketing.how_it_works.step2.label")}
            </div>
            <h3 id="hiw-step2-title">
              {t("marketing.how_it_works.step2.title")}
            </h3>
            <p className="hiw-deep-step__body">
              {t("marketing.how_it_works.step2.body")}
            </p>
            <div className="hiw-deep-step__shots">
              <figure className="hiw-deep-step__shot">
                <img
                  src={kanbanShot}
                  alt={t("marketing.how_it_works.step2.shot1_alt")}
                />
              </figure>
              <figure className="hiw-deep-step__shot">
                <img
                  src={canvasShot}
                  alt={t("marketing.how_it_works.step2.shot2_alt")}
                />
              </figure>
            </div>
            <p className="hiw-deep-step__caption">
              {t("marketing.how_it_works.step2.caption")}
            </p>
            <div className="hiw-deep-step__aside">
              {t("marketing.how_it_works.step2.aside")}
            </div>
          </article>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Step 3 — Review canvas, ship code */}
      <section className="mk-section" aria-labelledby="hiw-step3-title">
        <div className="hiw-deep">
          <article className="hiw-deep-step">
            <div className="hiw-deep-step__label">
              {t("marketing.how_it_works.step3.label")}
            </div>
            <h3 id="hiw-step3-title">
              {t("marketing.how_it_works.step3.title")}
            </h3>
            <p className="hiw-deep-step__body">
              {t("marketing.how_it_works.step3.body")}
            </p>
            <div className="hiw-deep-step__shots">
              <figure className="hiw-deep-step__shot">
                <img
                  src={summaryShot}
                  alt={t("marketing.how_it_works.step3.shot1_alt")}
                />
              </figure>
              <figure className="hiw-deep-step__shot">
                <img
                  src={artifactShot}
                  alt={t("marketing.how_it_works.step3.shot2_alt")}
                />
              </figure>
            </div>
            <p className="hiw-deep-step__caption">
              {t("marketing.how_it_works.step3.caption")}
            </p>
          </article>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Final CTA band */}
      <section
        className="mk-section mk-final-cta"
        aria-labelledby="hiw-final-cta-title"
      >
        <h2 id="hiw-final-cta-title">
          {t("marketing.how_it_works.final_cta.heading")}
        </h2>
        <div className="mk-final-cta__row">
          <button
            type="button"
            className="mk-cta mk-cta--sage"
            onClick={startFree}
          >
            {t("marketing.how_it_works.final_cta.start")} →
          </button>
          <button
            type="button"
            className="mk-cta mk-cta--ghost"
            onClick={talkToSales}
          >
            {t("marketing.how_it_works.final_cta.talk")} →
          </button>
        </div>
        <p className="mk-final-cta__note">
          {t("marketing.how_it_works.final_cta.note_prefix")}{" "}
          <Link to="/pricing">
            {t("marketing.how_it_works.final_cta.pricing_link")} →
          </Link>
        </p>
      </section>
    </MarketingLayout>
  );
}
