// Inspira — public landing page (v5).
//
// Mirrors the internal v5-pivot hi-fi (design files not included in
// this repo). A 2-column hero
// (text + signals→canvas SVG illustration), a 3-shot product row,
// 2 persona cards, a 3-step preview that deep-links to /how-it-works,
// 3 value rows, and a closing pricing teaser that deep-links to
// /pricing.
//
// AuthPanel + ?signin=1 / ?signup=1 query handling preserved from the
// v3 page so navigation back from /privacy or /terms with that query
// still opens the modal in the right mode.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { AuthPanel } from "../../components/AuthPanel";
import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import { PLANS } from "./plans";
import "./marketing.css";

import artifactShot from "./assets/screenshots/artifact.jpg";
import canvasShot from "./assets/screenshots/canvas.jpg";
import kanbanShot from "./assets/screenshots/kanban.jpg";

export function LandingPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [authOpen, setAuthOpen] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");

  // Honour ?signin=1 / ?signup=1 from header / deep-link returns so the
  // AuthPanel auto-opens in the right mode.
  useEffect(() => {
    if (searchParams.get("signin") === "1") {
      setAuthMode("login");
      setAuthOpen(true);
    } else if (searchParams.get("signup") === "1") {
      setAuthMode("signup");
      setAuthOpen(true);
    }
  }, [searchParams]);

  const openLogin = useCallback(() => {
    setAuthMode("login");
    setAuthOpen(true);
  }, []);

  const closeAuth = useCallback(() => {
    setAuthOpen(false);
    const next = new URLSearchParams(searchParams);
    next.delete("signin");
    next.delete("signup");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleAuthenticated = useCallback(() => {
    setAuthOpen(false);
    navigate("/app");
  }, [navigate]);

  const startMapping = useCallback(() => {
    navigate("/app?new=1");
  }, [navigate]);

  return (
    <MarketingLayout onSignIn={openLogin}>
      <Head
        title={t("marketing.home.meta.title")}
        description={t("marketing.home.meta.description")}
        canonical="https://tryinspira.com/"
        ogImage="https://tryinspira.com/og/og-landing.png"
      />

      {/* Hero — 2-column: text + signals→canvas SVG */}
      <section className="mk-section" aria-labelledby="home-hero-title">
        <div className="mk-hero">
          <div className="mk-hero__text">
            <h1 id="home-hero-title">{t("marketing.home.hero.title")}</h1>
            <p className="mk-hero__sub">
              {t("marketing.home.hero.subhead")}
            </p>
            <div className="mk-hero__ctas">
              <button
                type="button"
                className="mk-cta mk-cta--sage"
                onClick={startMapping}
              >
                {t("marketing.home.hero.cta_primary")} →
              </button>
              <Link to="/how-it-works" className="mk-cta mk-cta--ghost">
                {t("marketing.home.hero.cta_secondary")} →
              </Link>
            </div>
          </div>
          <div className="mk-hero__illust">
            <SignalsToCanvasIllustration />
          </div>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* What Inspira does — 3 product screenshots */}
      <section className="mk-section" aria-labelledby="home-what-title">
        <header className="mk-sh">
          <h2 id="home-what-title">{t("marketing.home.what.heading")}</h2>
        </header>
        <div className="mk-shots">
          <figure className="mk-shot">
            <img src={kanbanShot} alt={t("marketing.home.what.shot1_alt")} />
            <figcaption className="mk-shot__label">
              {t("marketing.home.what.shot1_label")}
            </figcaption>
          </figure>
          <figure className="mk-shot">
            <img src={canvasShot} alt={t("marketing.home.what.shot2_alt")} />
            <figcaption className="mk-shot__label">
              {t("marketing.home.what.shot2_label")}
            </figcaption>
          </figure>
          <figure className="mk-shot">
            <img
              src={artifactShot}
              alt={t("marketing.home.what.shot3_alt")}
            />
            <figcaption className="mk-shot__label">
              {t("marketing.home.what.shot3_label")}
            </figcaption>
          </figure>
        </div>
        <p className="mk-shots__caption">
          {t("marketing.home.what.caption")}
        </p>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Built for software teams without a PM — persona cards */}
      <section
        className="mk-section"
        aria-labelledby="home-personas-title"
      >
        <header className="mk-sh">
          <h2 id="home-personas-title">
            {t("marketing.home.personas.heading")}
          </h2>
        </header>
        <div className="mk-personas">
          <article className="mk-persona">
            <h3>{t("marketing.home.personas.founder.title")}</h3>
            <p>{t("marketing.home.personas.founder.body")}</p>
          </article>
          <article className="mk-persona">
            <h3>{t("marketing.home.personas.eng_manager.title")}</h3>
            <p>{t("marketing.home.personas.eng_manager.body")}</p>
          </article>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* How it works — 3-step preview */}
      <section
        className="mk-section"
        aria-labelledby="home-howitworks-title"
      >
        <header className="mk-sh">
          <h2 id="home-howitworks-title">
            {t("marketing.home.howitworks.heading")}
          </h2>
        </header>
        <div className="mk-steps">
          <article className="mk-step">
            <div className="mk-step__num">
              {t("marketing.home.howitworks.step1.num")}
            </div>
            <h3>{t("marketing.home.howitworks.step1.title")}</h3>
            <p>{t("marketing.home.howitworks.step1.body")}</p>
          </article>
          <article className="mk-step">
            <div className="mk-step__num">
              {t("marketing.home.howitworks.step2.num")}
            </div>
            <h3>{t("marketing.home.howitworks.step2.title")}</h3>
            <p>{t("marketing.home.howitworks.step2.body")}</p>
          </article>
          <article className="mk-step">
            <div className="mk-step__num">
              {t("marketing.home.howitworks.step3.num")}
            </div>
            <h3>{t("marketing.home.howitworks.step3.title")}</h3>
            <p>{t("marketing.home.howitworks.step3.body")}</p>
          </article>
        </div>
        <div style={{ textAlign: "center", marginTop: 16 }}>
          <Link to="/how-it-works" className="mk-cta mk-cta--ghost mk-cta--sm">
            {t("marketing.home.howitworks.see_in_detail")} →
          </Link>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* What Inspira does for your dev team — 3 value rows */}
      <section className="mk-section" aria-labelledby="home-values-title">
        <header className="mk-sh">
          <h2 id="home-values-title">
            {t("marketing.home.values.heading")}
          </h2>
        </header>
        <div className="mk-values">
          <div className="mk-value">
            <h3>{t("marketing.home.values.v1.title")}</h3>
            <p>{t("marketing.home.values.v1.body")}</p>
          </div>
          <div className="mk-value">
            <h3>{t("marketing.home.values.v2.title")}</h3>
            <p>{t("marketing.home.values.v2.body")}</p>
          </div>
          <div className="mk-value">
            <h3>{t("marketing.home.values.v3.title")}</h3>
            <p>{t("marketing.home.values.v3.body")}</p>
          </div>
        </div>
      </section>

      <div className="mk-divider">
        <hr />
      </div>

      {/* Pricing teaser — closing CTA */}
      <section
        className="mk-section"
        aria-labelledby="home-pricing-teaser-title"
      >
        <header className="mk-sh">
          <h2 id="home-pricing-teaser-title">
            {t("marketing.home.pricing_teaser.heading")}
          </h2>
          <div className="mk-sh__sub">
            {t("marketing.home.pricing_teaser.sub")}
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
            {t("marketing.home.pricing_teaser.see_full")} →
          </Link>
        </div>
      </section>

      <AuthPanel
        open={authOpen}
        initialMode={authMode}
        onClose={closeAuth}
        onAuthenticated={handleAuthenticated}
      />
    </MarketingLayout>
  );
}

// Hand-drawn signals → canvas illustration. Ported from
// Marketing.html lines 142–165. Uses tokens.css CSS variables so it
// adapts across warm-light / warm-dark / modern-light / modern-dark.
function SignalsToCanvasIllustration() {
  return (
    <svg
      viewBox="0 0 360 240"
      fill="none"
      role="img"
      aria-label={t("marketing.home.hero.illust_aria")}
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Input boxes — three feedback channels on the left */}
      <rect
        x="20"
        y="30"
        width="80"
        height="40"
        rx="6"
        fill="var(--bg-raised)"
        stroke="var(--ink-5)"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <text
        x="60"
        y="54"
        textAnchor="middle"
        fontFamily="var(--ff-serif)"
        fontSize="9"
        fill="var(--fg-muted)"
        fontStyle="italic"
      >
        tickets
      </text>
      <rect
        x="20"
        y="90"
        width="80"
        height="40"
        rx="6"
        fill="var(--bg-raised)"
        stroke="var(--ink-5)"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <text
        x="60"
        y="114"
        textAnchor="middle"
        fontFamily="var(--ff-serif)"
        fontSize="9"
        fill="var(--fg-muted)"
        fontStyle="italic"
      >
        reviews
      </text>
      <rect
        x="20"
        y="150"
        width="80"
        height="40"
        rx="6"
        fill="var(--bg-raised)"
        stroke="var(--ink-5)"
        strokeWidth="1"
        strokeDasharray="3 3"
      />
      <text
        x="60"
        y="174"
        textAnchor="middle"
        fontFamily="var(--ff-serif)"
        fontSize="9"
        fill="var(--fg-muted)"
        fontStyle="italic"
      >
        bug reports
      </text>

      {/* Flow lines from inputs → canvas */}
      <path
        d="M100 50 Q140 50 160 100"
        stroke="var(--sage)"
        strokeWidth="1.2"
        strokeDasharray="4 3"
        fill="none"
      />
      <path
        d="M100 110 Q130 110 160 110"
        stroke="var(--sage)"
        strokeWidth="1.2"
        strokeDasharray="4 3"
        fill="none"
      />
      <path
        d="M100 170 Q140 170 160 120"
        stroke="var(--sage)"
        strokeWidth="1.2"
        strokeDasharray="4 3"
        fill="none"
      />

      {/* Canvas frame */}
      <rect
        x="160"
        y="60"
        width="170"
        height="120"
        rx="8"
        fill="var(--bg-raised)"
        stroke="var(--sage)"
        strokeWidth="1.5"
      />

      {/* Topic cards inside the canvas */}
      <rect
        x="175"
        y="75"
        width="60"
        height="30"
        rx="4"
        fill="var(--bg)"
        stroke="var(--border)"
        strokeWidth="0.8"
      />
      <rect
        x="255"
        y="75"
        width="60"
        height="30"
        rx="4"
        fill="var(--bg)"
        stroke="var(--border)"
        strokeWidth="0.8"
      />
      <rect
        x="215"
        y="125"
        width="60"
        height="30"
        rx="4"
        fill="var(--bg)"
        stroke="var(--border)"
        strokeWidth="0.8"
      />

      {/* Connector lines between topic cards */}
      <path
        d="M235 95 Q245 110 245 125"
        stroke="var(--ink-4)"
        strokeWidth="0.8"
        strokeDasharray="2 2"
        fill="none"
      />
      <path
        d="M280 105 Q275 115 260 125"
        stroke="var(--ink-4)"
        strokeWidth="0.8"
        strokeDasharray="2 2"
        fill="none"
      />

      {/* Caption */}
      <text
        x="245"
        y="200"
        textAnchor="middle"
        fontFamily="var(--ff-serif)"
        fontSize="10"
        fill="var(--sage-ink)"
        fontStyle="italic"
      >
        canvas
      </text>
    </svg>
  );
}
