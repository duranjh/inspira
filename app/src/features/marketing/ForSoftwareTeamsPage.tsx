// Inspira — public /teams page (v5).
//
// Mirrors the internal v5-pivot hi-fi (design files not included in
// this repo).
// A narrow-hero pitch, three "what changes" value rows, three
// product-screenshot blocks (orchestrator / artifact / export), and
// a closing pricing-teaser + CTA band. Targets the eng-manager
// persona profile (internal research, not included in this repo).

import { useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";

import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import { PLANS } from "./plans";
import "./marketing.css";

import artifactShot from "./assets/screenshots/artifact.jpg";
import canvasShot from "./assets/screenshots/canvas.jpg";
import exportShot from "./assets/screenshots/export.jpg";
import kanbanShot from "./assets/screenshots/kanban.jpg";

export function ForSoftwareTeamsPage() {
  const navigate = useNavigate();

  const startFree = useCallback(() => {
    navigate("/app?new=1");
  }, [navigate]);

  const seePricing = useCallback(() => {
    navigate("/pricing");
  }, [navigate]);

  const talkToSales = useCallback(() => {
    window.location.href =
      "mailto:hello@tryinspira.com?subject=Inspira%20design%20partner";
  }, []);

  return (
    <MarketingLayout>
      <Head
        title={t("marketing.teams.meta.title")}
        description={t("marketing.teams.meta.description")}
        canonical="https://tryinspira.com/teams"
        ogImage="https://tryinspira.com/og/og-teams.png"
      />

      {/* Hero — left-aligned, narrow */}
      <section className="mk-section" aria-labelledby="teams-hero-title">
        <div className="mk-hero mk-hero--narrow">
          <div className="mk-hero__text">
            <h1 id="teams-hero-title">
              {t("marketing.teams.hero.title")}
            </h1>
            <p className="mk-hero__sub">
              {t("marketing.teams.hero.subhead")}
            </p>
            <div className="mk-hero__ctas">
              <button
                type="button"
                className="mk-cta mk-cta--sage"
                onClick={startFree}
              >
                {t("marketing.teams.hero.cta_primary")} →
              </button>
              <button
                type="button"
                className="mk-cta mk-cta--ghost"
                onClick={seePricing}
              >
                {t("marketing.teams.hero.cta_secondary")} →
              </button>
            </div>
          </div>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* What changes for your engineering team */}
      <section className="mk-section" aria-labelledby="teams-changes-title">
        <header className="mk-sh">
          <h2 id="teams-changes-title">
            {t("marketing.teams.changes.heading")}
          </h2>
        </header>
        <div className="mk-values">
          <div className="mk-value">
            <h3>{t("marketing.teams.changes.c1.title")}</h3>
            <p>{t("marketing.teams.changes.c1.body")}</p>
          </div>
          <div className="mk-value">
            <h3>{t("marketing.teams.changes.c2.title")}</h3>
            <p>{t("marketing.teams.changes.c2.body")}</p>
          </div>
          <div className="mk-value">
            <h3>{t("marketing.teams.changes.c3.title")}</h3>
            <p>{t("marketing.teams.changes.c3.body")}</p>
          </div>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Orchestrator + sub-agents */}
      <section
        className="mk-section"
        aria-labelledby="teams-orchestrator-title"
      >
        <header className="mk-sh">
          <h2 id="teams-orchestrator-title">
            {t("marketing.teams.orchestrator.heading")}
          </h2>
        </header>
        <p className="teams-body-text">
          {t("marketing.teams.orchestrator.body")}
        </p>
        <div className="teams-shots-2">
          <figure className="teams-shot-card">
            <img
              src={kanbanShot}
              alt={t("marketing.teams.orchestrator.shot1_alt")}
            />
          </figure>
          <figure className="teams-shot-card">
            <img
              src={canvasShot}
              alt={t("marketing.teams.orchestrator.shot2_alt")}
            />
          </figure>
        </div>
        <p className="teams-caption">
          {t("marketing.teams.orchestrator.caption")}
        </p>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Code is the artifact */}
      <section className="mk-section" aria-labelledby="teams-artifact-title">
        <header className="mk-sh">
          <h2 id="teams-artifact-title">
            {t("marketing.teams.artifact.heading")}
          </h2>
        </header>
        <p className="teams-body-text">
          {t("marketing.teams.artifact.body")}
        </p>
        <div className="teams-shots-1">
          <figure className="teams-shot-card">
            <img
              src={artifactShot}
              alt={t("marketing.teams.artifact.shot_alt")}
            />
          </figure>
        </div>
        <p className="teams-caption">
          {t("marketing.teams.artifact.caption")}
        </p>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Ship to where you already work */}
      <section className="mk-section" aria-labelledby="teams-export-title">
        <header className="mk-sh">
          <h2 id="teams-export-title">
            {t("marketing.teams.export.heading")}
          </h2>
        </header>
        <p className="teams-body-text">{t("marketing.teams.export.body")}</p>
        <div className="teams-shots-1">
          <figure className="teams-shot-card">
            <img
              src={exportShot}
              alt={t("marketing.teams.export.shot_alt")}
            />
          </figure>
        </div>
        <p className="teams-caption">
          {t("marketing.teams.export.caption")}
        </p>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Pricing teaser strip + final CTA */}
      <section
        className="mk-section"
        aria-labelledby="teams-pricing-teaser-title"
      >
        <header className="mk-sh">
          <h2 id="teams-pricing-teaser-title">
            {t("marketing.teams.pricing_teaser.heading")}
          </h2>
          <div className="mk-sh__sub">
            {t("marketing.teams.pricing_teaser.sub")}
          </div>
        </header>
        <div className="mk-pricing-row">
          {PLANS.map((plan) => (
            <article key={plan.slug} className="mk-price-card">
              <div className="mk-price-card__name">
                {t(plan.nameKey).toUpperCase()}
              </div>
              <div className="mk-price-card__price">{t(plan.priceKey)}</div>
              <div className="mk-price-card__desc">
                {t(plan.teaserDescKey)}
              </div>
            </article>
          ))}
        </div>
        <div className="mk-pricing-row__see-full">
          <Link to="/pricing" className="mk-cta mk-cta--sage mk-cta--sm">
            {t("marketing.teams.pricing_teaser.see_full")} →
          </Link>
        </div>

        <div className="mk-final-cta">
          <h2>{t("marketing.teams.final_cta.heading")}</h2>
          <div className="mk-final-cta__row">
            <button
              type="button"
              className="mk-cta mk-cta--sage"
              onClick={startFree}
            >
              {t("marketing.teams.final_cta.start")} →
            </button>
            <button
              type="button"
              className="mk-cta mk-cta--ghost"
              onClick={talkToSales}
            >
              {t("marketing.teams.final_cta.talk")} →
            </button>
          </div>
        </div>
      </section>
    </MarketingLayout>
  );
}
