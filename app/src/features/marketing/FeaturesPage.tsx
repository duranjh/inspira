// Inspira — public features page (/features).
//
// Eleven feature blocks (Canvas → Shelves → Topic cards → Decisions →
// Suggestions → Sources → Export → Dark mode → Multi-project →
// Collaboration → Mobile), each rendered as a two-column row with a
// small inline SVG illustration to the left of the title + body. Stacks
// to single-column on mobile.
//
// Illustrations are tiny (160×140 viewBox) reuses of the warm-editorial
// palette from the landing-page canvas SVG so the bundle stays self-
// contained — no external assets, no design-system dependency.

import type { JSX } from "react";
import { useNavigate } from "react-router-dom";

import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import "./marketing.css";
import "./marketing-legal.css";

type FeatureSlug =
  | "f1"
  | "shelves"
  | "f2"
  | "f3"
  | "f4"
  | "f5"
  | "f6"
  | "f7"
  | "f8"
  | "f9"
  | "f10";

const FEATURE_SLUGS: FeatureSlug[] = [
  "f1",
  "shelves",
  "f2",
  "f3",
  "f4",
  "f5",
  "f6",
  "f7",
  "f8",
  "f9",
  "f10",
];

export function FeaturesPage(): JSX.Element {
  const navigate = useNavigate();

  return (
    <MarketingLayout>
      <Head
        title={t("marketing.features_page.meta.title")}
        description={t("marketing.features_page.meta.description")}
        canonical="https://tryinspira.com/features"
        ogImage="/og/og-features.png"
      />
      <section className="features-page" aria-labelledby="features-page-title">
        <p className="landing-eyebrow">{t("marketing.features_page.eyebrow")}</p>
        <h1 className="features-page__title" id="features-page-title">
          {t("marketing.features_page.headline")}
        </h1>
        <p className="features-page__lede">
          {t("marketing.features_page.subhead")}
        </p>

        <div className="features-page__grid" role="list">
          {FEATURE_SLUGS.map((slug) => (
            <article
              key={slug}
              role="listitem"
              className="features-page__item"
              aria-labelledby={`features-page-${slug}-title`}
            >
              <div className="features-page__illus-wrap" aria-hidden="true">
                <FeatureIllustration slug={slug} />
              </div>
              <div className="features-page__copy">
                <h2
                  className="features-page__item-title"
                  id={`features-page-${slug}-title`}
                >
                  {t(`marketing.features_page.items.${slug}.title`)}
                </h2>
                <p className="features-page__item-body">
                  {t(`marketing.features_page.items.${slug}.body`)}
                </p>
              </div>
            </article>
          ))}
        </div>

        <div className="features-page__cta">
          <p className="features-page__cta-line">
            {t("marketing.features_page.cta.line")}
          </p>
          <p className="features-page__cta-sub">
            {t("marketing.features_page.cta.sub")}
          </p>
          <button
            type="button"
            onClick={() => navigate("/app?new=1")}
            className="landing-btn landing-btn--primary"
          >
            {t("marketing.features_page.cta.button")}
          </button>
        </div>
      </section>
    </MarketingLayout>
  );
}

export default FeaturesPage;

// ---------------------------------------------------------------------
// Inline SVG illustrations — one per feature
// ---------------------------------------------------------------------

function FeatureIllustration({ slug }: { slug: FeatureSlug }): JSX.Element {
  switch (slug) {
    case "f1":
      return <CanvasIllus />;
    case "shelves":
      return <ShelvesIllus />;
    case "f2":
      return <TopicCardsIllus />;
    case "f3":
      return <DecisionsIllus />;
    case "f4":
      return <SuggestionsIllus />;
    case "f5":
      return <SourcesIllus />;
    case "f6":
      return <ExportIllus />;
    case "f7":
      return <DarkModeIllus />;
    case "f8":
      return <MultiProjectIllus />;
    case "f9":
      return <CollabIllus />;
    case "f10":
      return <MobileIllus />;
  }
}

// Shared SVG primitives ------------------------------------------------

const PALETTE = {
  paper: "#F5F0E6",
  paperLifted: "#FDFBF6",
  paperEdge: "#DBCFB6",
  textPlaceholder: "#E6DCC8",
  ink: "#2B2520",
  inkSoft: "#847B72",
  sage: "#568868",
  gold: "#8D6A23",
  rust: "#9A4E38",
  darkPaper: "#3A3128",
  darkPaperEdge: "#5C5147",
} as const;

function PaperBg() {
  return <rect width="160" height="140" rx="6" fill={PALETTE.paper} />;
}

function DotGrid({ rows = 6, cols = 8, x0 = 12, y0 = 14, gap = 18 }: { rows?: number; cols?: number; x0?: number; y0?: number; gap?: number }) {
  const dots: JSX.Element[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      dots.push(
        <circle
          key={`${r}-${c}`}
          cx={x0 + c * gap}
          cy={y0 + r * gap}
          r="1"
        />,
      );
    }
  }
  return (
    <g fill={PALETTE.ink} fillOpacity="0.06">
      {dots}
    </g>
  );
}

function Card({
  x,
  y,
  w = 40,
  h = 20,
  color = PALETTE.sage,
}: {
  x: number;
  y: number;
  w?: number;
  h?: number;
  color?: string;
}) {
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx="3"
        fill={PALETTE.paperLifted}
        stroke={PALETTE.paperEdge}
        strokeWidth="0.7"
      />
      <circle cx={x + 6} cy={y + 6} r="1.5" fill={color} />
      <rect
        x={x + 10}
        y={y + 5}
        width={Math.max(w - 18, 4)}
        height="2"
        rx="1"
        fill={PALETTE.textPlaceholder}
      />
      <rect
        x={x + 10}
        y={y + 10}
        width={Math.max((w - 18) * 0.6, 4)}
        height="2"
        rx="1"
        fill={PALETTE.textPlaceholder}
      />
    </g>
  );
}

const SVG_PROPS = {
  viewBox: "0 0 160 140",
  className: "features-page__illus",
  xmlns: "http://www.w3.org/2000/svg",
  role: "img" as const,
};

// 1. Canvas — three connected cards on dotted paper -------------------

function CanvasIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="Three connected topic cards on a canvas.">
      <PaperBg />
      <DotGrid />
      <g
        stroke={PALETTE.sage}
        strokeOpacity="0.55"
        strokeWidth="0.8"
        fill="none"
        strokeDasharray="2 2.5"
      >
        <path d="M 38,52 C 56,52 64,32 84,32" />
        <path d="M 40,68 C 60,72 84,90 110,92" />
        <path d="M 110,42 C 122,46 132,60 142,72" />
      </g>
      <Card x={18} y={46} w={40} h={20} color={PALETTE.sage} />
      <Card x={80} y={22} w={40} h={20} color={PALETTE.gold} />
      <Card x={100} y={80} w={40} h={20} color={PALETTE.rust} />
    </svg>
  );
}

// 2. Shelves — stacked cards with a vertical shelf line ---------------

function ShelvesIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="Three projects stacked under a shelf, with a consistency line through them.">
      <PaperBg />
      <DotGrid />
      <line
        x1="20"
        y1="20"
        x2="20"
        y2="120"
        stroke={PALETTE.sage}
        strokeWidth="2"
        strokeLinecap="round"
      />
      <Card x={28} y={26} w={108} h={22} color={PALETTE.sage} />
      <Card x={28} y={58} w={108} h={22} color={PALETTE.gold} />
      <Card x={28} y={90} w={108} h={22} color={PALETTE.rust} />
      <line
        x1="56"
        y1="37"
        x2="56"
        y2="101"
        stroke={PALETTE.rust}
        strokeWidth="0.8"
        strokeDasharray="2 2"
        opacity="0.7"
      />
    </svg>
  );
}

// 3. Topic cards — card + Q&A speech bubble ---------------------------

function TopicCardsIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="A topic card with a question-and-answer dialog opening from it.">
      <PaperBg />
      <DotGrid />
      <Card x={20} y={48} w={60} h={36} color={PALETTE.sage} />
      <g>
        <rect
          x="86"
          y="32"
          width="56"
          height="36"
          rx="6"
          fill={PALETTE.paperLifted}
          stroke={PALETTE.paperEdge}
          strokeWidth="0.8"
        />
        <text
          x="94"
          y="48"
          fontFamily="Source Serif 4, Georgia, serif"
          fontSize="11"
          fill={PALETTE.sage}
          fontStyle="italic"
        >
          Q.
        </text>
        <rect x="102" y="44" width="32" height="2" rx="1" fill={PALETTE.textPlaceholder} />
        <text
          x="94"
          y="62"
          fontFamily="Source Serif 4, Georgia, serif"
          fontSize="11"
          fill={PALETTE.gold}
          fontStyle="italic"
        >
          A.
        </text>
        <rect x="102" y="58" width="24" height="2" rx="1" fill={PALETTE.textPlaceholder} />
        <path
          d="M 86,55 L 78,62 L 86,62 Z"
          fill={PALETTE.paperLifted}
          stroke={PALETTE.paperEdge}
          strokeWidth="0.8"
        />
      </g>
    </svg>
  );
}

// 4. Decisions — card with checkmark badge + version stamp ------------

function DecisionsIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="A topic card with a saved checkmark badge.">
      <PaperBg />
      <DotGrid />
      <Card x={26} y={42} w={92} h={50} color={PALETTE.sage} />
      <g>
        <circle cx="124" cy="46" r="11" fill={PALETTE.sage} />
        <path
          d="M 119,46 L 122,49 L 128,42"
          stroke={PALETTE.paperLifted}
          strokeWidth="2"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
      <g fill={PALETTE.gold}>
        <circle cx="34" cy="108" r="1.4" />
        <circle cx="40" cy="108" r="1.4" />
        <circle cx="46" cy="108" r="1.4" />
      </g>
      <text
        x="52"
        y="111"
        fontFamily="JetBrains Mono, monospace"
        fontSize="6.5"
        fill={PALETTE.inkSoft}
      >
        v3 · just now
      </text>
    </svg>
  );
}

// 5. Suggestions — card with a "+ add" chip floating off edge ---------

function SuggestionsIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="A topic card with a suggestion chip floating beside it.">
      <PaperBg />
      <DotGrid />
      <Card x={20} y={50} w={84} h={42} color={PALETTE.sage} />
      <line
        x1="104"
        y1="68"
        x2="118"
        y2="58"
        stroke={PALETTE.sage}
        strokeOpacity="0.45"
        strokeWidth="0.8"
        strokeDasharray="2 2"
      />
      <g>
        <rect
          x="112"
          y="44"
          width="40"
          height="20"
          rx="10"
          fill={PALETTE.sage}
        />
        <text
          x="132"
          y="57"
          textAnchor="middle"
          fontFamily="JetBrains Mono, monospace"
          fontSize="8.5"
          fill={PALETTE.paperLifted}
        >
          + idea
        </text>
      </g>
    </svg>
  );
}

// 6. Sources — card with paperclip + URL stub ------------------------

function SourcesIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="A topic card linked to a URL source via a paperclip.">
      <PaperBg />
      <DotGrid />
      <Card x={22} y={46} w={88} h={48} color={PALETTE.gold} />
      <g
        stroke={PALETTE.gold}
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
      >
        <path d="M 132,54 C 132,46 124,40 116,40 C 108,40 100,46 100,54 L 100,80 C 100,86 106,90 110,90 C 114,90 118,86 118,80 L 118,60" />
      </g>
      <rect
        x="28"
        y="56"
        width="58"
        height="12"
        rx="3"
        fill={PALETTE.paper}
        stroke={PALETTE.paperEdge}
        strokeWidth="0.5"
      />
      <text
        x="32"
        y="65"
        fontFamily="JetBrains Mono, monospace"
        fontSize="7"
        fill={PALETTE.gold}
      >
        https://intercom.io/...
      </text>
    </svg>
  );
}

// 7. Export — card with arrows out to .md and .pdf chips --------------

function ExportIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="A canvas exporting to Markdown and PDF files.">
      <PaperBg />
      <DotGrid />
      <Card x={14} y={50} w={50} h={40} color={PALETTE.sage} />
      <defs>
        <marker
          id="ex-arrow"
          viewBox="0 0 8 8"
          refX="6"
          refY="4"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 8 4 L 0 8 z" fill={PALETTE.sage} />
        </marker>
      </defs>
      <g
        stroke={PALETTE.sage}
        strokeWidth="1"
        fill="none"
        strokeDasharray="2 2"
      >
        <path d="M 66,62 L 100,42" markerEnd="url(#ex-arrow)" />
        <path d="M 66,80 L 100,98" markerEnd="url(#ex-arrow)" />
      </g>
      <rect
        x="104"
        y="32"
        width="42"
        height="22"
        rx="3"
        fill={PALETTE.paperLifted}
        stroke={PALETTE.paperEdge}
      />
      <text
        x="125"
        y="46"
        textAnchor="middle"
        fontFamily="JetBrains Mono, monospace"
        fontSize="9.5"
        fill={PALETTE.ink}
      >
        .md
      </text>
      <rect
        x="104"
        y="88"
        width="42"
        height="22"
        rx="3"
        fill={PALETTE.paperLifted}
        stroke={PALETTE.paperEdge}
      />
      <text
        x="125"
        y="102"
        textAnchor="middle"
        fontFamily="JetBrains Mono, monospace"
        fontSize="9.5"
        fill={PALETTE.ink}
      >
        .pdf
      </text>
    </svg>
  );
}

// 8. Dark mode — canvas split light / dark ----------------------------

function DarkModeIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="The canvas in light mode on the left and dark mode on the right.">
      <rect x="0" y="0" width="80" height="140" rx="6" fill={PALETTE.paper} />
      <rect x="80" y="0" width="80" height="140" rx="6" fill={PALETTE.ink} />
      <g fill={PALETTE.ink} fillOpacity="0.08">
        {Array.from({ length: 6 }).flatMap((_, r) =>
          Array.from({ length: 4 }).map((__, c) => (
            <circle key={`l-${r}-${c}`} cx={12 + c * 18} cy={14 + r * 18} r="1" />
          )),
        )}
      </g>
      <g fill={PALETTE.paper} fillOpacity="0.10">
        {Array.from({ length: 6 }).flatMap((_, r) =>
          Array.from({ length: 4 }).map((__, c) => (
            <circle key={`d-${r}-${c}`} cx={92 + c * 18} cy={14 + r * 18} r="1" />
          )),
        )}
      </g>
      <g>
        <rect
          x="18"
          y="50"
          width="48"
          height="40"
          rx="3"
          fill={PALETTE.paperLifted}
          stroke={PALETTE.paperEdge}
          strokeWidth="0.7"
        />
        <circle cx="24" cy="56" r="1.5" fill={PALETTE.sage} />
        <rect x="28" y="55" width="32" height="2" rx="1" fill={PALETTE.textPlaceholder} />
        <rect x="28" y="60" width="20" height="2" rx="1" fill={PALETTE.textPlaceholder} />
      </g>
      <g>
        <rect
          x="94"
          y="50"
          width="48"
          height="40"
          rx="3"
          fill={PALETTE.darkPaper}
          stroke={PALETTE.darkPaperEdge}
          strokeWidth="0.7"
        />
        <circle cx="100" cy="56" r="1.5" fill={PALETTE.gold} />
        <rect x="104" y="55" width="32" height="2" rx="1" fill={PALETTE.darkPaperEdge} />
        <rect x="104" y="60" width="20" height="2" rx="1" fill={PALETTE.darkPaperEdge} />
      </g>
    </svg>
  );
}

// 9. Multi-project — three fanned-out cards ---------------------------

function MultiProjectIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="Three project cards fanned out across the canvas.">
      <PaperBg />
      <DotGrid />
      <g transform="rotate(-6 50 70)">
        <rect
          x="22"
          y="46"
          width="68"
          height="48"
          rx="4"
          fill={PALETTE.paperLifted}
          stroke={PALETTE.paperEdge}
          strokeWidth="0.7"
          opacity="0.85"
        />
        <circle cx="28" cy="52" r="1.5" fill={PALETTE.rust} />
      </g>
      <g transform="rotate(2 80 70)">
        <rect
          x="48"
          y="44"
          width="68"
          height="48"
          rx="4"
          fill={PALETTE.paperLifted}
          stroke={PALETTE.paperEdge}
          strokeWidth="0.7"
          opacity="0.92"
        />
        <circle cx="54" cy="50" r="1.5" fill={PALETTE.gold} />
      </g>
      <g transform="rotate(8 110 72)">
        <rect
          x="72"
          y="46"
          width="68"
          height="48"
          rx="4"
          fill={PALETTE.paperLifted}
          stroke={PALETTE.paperEdge}
          strokeWidth="0.9"
        />
        <circle cx="78" cy="52" r="1.5" fill={PALETTE.sage} />
        <rect x="82" y="51" width="48" height="2" rx="1" fill={PALETTE.textPlaceholder} />
        <rect x="82" y="56" width="32" height="2" rx="1" fill={PALETTE.textPlaceholder} />
      </g>
    </svg>
  );
}

// 10. Collaboration — two cards bridged by avatar dots ----------------

function CollabIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="Two cards joined by a small bridge of two avatars.">
      <PaperBg />
      <DotGrid />
      <Card x={10} y={56} w={56} h={32} color={PALETTE.sage} />
      <Card x={94} y={56} w={56} h={32} color={PALETTE.rust} />
      <line
        x1="66"
        y1="72"
        x2="94"
        y2="72"
        stroke={PALETTE.sage}
        strokeOpacity="0.55"
        strokeWidth="1"
        strokeDasharray="2 2"
      />
      <circle cx="74" cy="72" r="3.8" fill={PALETTE.paperLifted} stroke={PALETTE.paperEdge} strokeWidth="0.7" />
      <circle cx="86" cy="72" r="3.8" fill={PALETTE.paperLifted} stroke={PALETTE.paperEdge} strokeWidth="0.7" />
    </svg>
  );
}

// 11. Mobile — phone outline with a single card inside ---------------

function MobileIllus() {
  return (
    <svg {...SVG_PROPS} aria-label="A phone outline with a single topic card on screen.">
      <PaperBg />
      <DotGrid />
      <rect
        x="56"
        y="20"
        width="48"
        height="100"
        rx="8"
        fill={PALETTE.paperLifted}
        stroke={PALETTE.paperEdge}
        strokeWidth="1.5"
      />
      <line
        x1="74"
        y1="28"
        x2="86"
        y2="28"
        stroke={PALETTE.paperEdge}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <rect
        x="62"
        y="42"
        width="36"
        height="32"
        rx="3"
        fill={PALETTE.paper}
        stroke={PALETTE.textPlaceholder}
        strokeWidth="0.6"
      />
      <circle cx="66" cy="47" r="1.2" fill={PALETTE.sage} />
      <rect x="70" y="46" width="22" height="1.5" rx="0.5" fill={PALETTE.textPlaceholder} />
      <rect x="70" y="50" width="14" height="1.5" rx="0.5" fill={PALETTE.textPlaceholder} />
      <rect x="68" y="108" width="24" height="3" rx="1.5" fill={PALETTE.paperEdge} />
    </svg>
  );
}
