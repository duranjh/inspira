// Inspira — shared marketing plans source.
//
// Both the landing page's pricing teaser and the full `/pricing` page read
// from this single constant so the two surfaces can never silently drift.
// Prices, taglines, and bullets still flow through the i18n layer — this
// module only wires together the right set of keys per plan and which
// plan is featured.
//
// When a new plan ships (or an existing one changes copy), update the
// i18n catalog and extend the PLANS entries here. Downstream consumers
// should loop over `PLANS` rather than hardcoding tier arrays.

export type MarketingPlanSlug = "free" | "pro" | "team" | "enterprise";

export type MarketingPlan = {
  slug: MarketingPlanSlug;
  /** i18n key for the plan name (e.g. "Free", "Pro", "Frontier"). */
  nameKey: string;
  /** i18n key for the monthly-billed price amount (e.g. "$0", "$49"). */
  priceKey: string;
  /** i18n key for the monthly-billed price unit. */
  priceUnitKey?: string;
  /** i18n key for the annual-billed price amount (e.g. "$44" — billed-monthly equivalent of the annual deal). Only Pro + Frontier have annual; Free + Enterprise leave this undefined and the PricingPage's billing toggle is hidden / no-op for those rows. */
  priceAnnualKey?: string;
  /** i18n key for the annual-billed price unit (e.g. "/seat/mo, billed annually."). */
  priceUnitAnnualKey?: string;
  /** i18n key for the annual note (Pro-only legacy slot — deprecated, prefer priceAnnualKey + priceUnitAnnualKey). */
  annualNoteKey?: string;
  /** i18n key for the short tagline under the price. */
  taglineKey: string;
  /** i18n key for the per-tier chip label rendered above the price. */
  chipKey: string;
  /** i18n key for the short tier descriptor in the small pricing teaser strip (Home + Teams pages). Distinct from `taglineKey` which appears on the full pricing page card. */
  teaserDescKey: string;
  /** i18n key for the "Everything in X, plus:" italic line above the bullet list. */
  includesLineKey?: string;
  /** Ordered list of i18n keys for the feature bullets. */
  bulletKeys: string[];
  /** i18n key for an optional small italic note rendered below the bullets (e.g. enterprise's "* coming for design partners"). */
  noteKey?: string;
  /**
   * @deprecated Phase-2 design replaces inline "Coming next 8 weeks" markers
   * with a single footnote (`noteKey`). Field stays on the type so the legacy
   * `LandingPage` / `PricingPage` render paths still type-check during the
   * wave-by-wave rebuild; gets removed entirely once Waves 4 + 5 ship.
   */
  flMarkerKeys?: string[];
  /** i18n key for the CTA label. */
  ctaKey: string;
  /** Whether this card renders with the sage accent border and badge. */
  featured: boolean;
};

export const PLANS: MarketingPlan[] = [
  {
    slug: "free",
    nameKey: "marketing.pricing.free.name",
    priceKey: "marketing.pricing.free.price",
    priceUnitKey: "marketing.pricing.free.price_unit",
    taglineKey: "marketing.pricing.free.tagline",
    chipKey: "marketing.pricing.free.chip",
    teaserDescKey: "marketing.pricing.free.teaser_desc",
    bulletKeys: [
      "marketing.pricing.free.bullet_1",
      "marketing.pricing.free.bullet_2",
      "marketing.pricing.free.bullet_3",
      "marketing.pricing.free.bullet_4",
      "marketing.pricing.free.bullet_5",
    ],
    ctaKey: "marketing.pricing.cta.free",
    featured: false,
  },
  {
    slug: "pro",
    nameKey: "marketing.pricing.pro.name",
    priceKey: "marketing.pricing.pro.price",
    priceUnitKey: "marketing.pricing.pro.price_unit",
    priceAnnualKey: "marketing.pricing.pro.price_annual",
    priceUnitAnnualKey: "marketing.pricing.pro.price_unit_annual",
    taglineKey: "marketing.pricing.pro.tagline",
    chipKey: "marketing.pricing.pro.chip",
    teaserDescKey: "marketing.pricing.pro.teaser_desc",
    includesLineKey: "marketing.pricing.pro.includes_line",
    bulletKeys: [
      "marketing.pricing.pro.bullet_1",
      "marketing.pricing.pro.bullet_2",
      "marketing.pricing.pro.bullet_3",
      "marketing.pricing.pro.bullet_4",
      "marketing.pricing.pro.bullet_5",
      "marketing.pricing.pro.bullet_6",
    ],
    ctaKey: "marketing.pricing.cta.pro",
    featured: true,
  },
  {
    slug: "team",
    nameKey: "marketing.pricing.team.name",
    priceKey: "marketing.pricing.team.price",
    priceUnitKey: "marketing.pricing.team.price_unit",
    priceAnnualKey: "marketing.pricing.team.price_annual",
    priceUnitAnnualKey: "marketing.pricing.team.price_unit_annual",
    taglineKey: "marketing.pricing.team.tagline",
    chipKey: "marketing.pricing.team.chip",
    teaserDescKey: "marketing.pricing.team.teaser_desc",
    includesLineKey: "marketing.pricing.team.includes_line",
    bulletKeys: [
      "marketing.pricing.team.bullet_1",
      "marketing.pricing.team.bullet_2",
      "marketing.pricing.team.bullet_3",
      "marketing.pricing.team.bullet_4",
      "marketing.pricing.team.bullet_5",
      "marketing.pricing.team.bullet_6",
      "marketing.pricing.team.bullet_7",
      "marketing.pricing.team.bullet_8",
    ],
    ctaKey: "marketing.pricing.cta.team",
    featured: false,
  },
  {
    slug: "enterprise",
    nameKey: "marketing.pricing.enterprise.name",
    priceKey: "marketing.pricing.enterprise.price",
    priceUnitKey: "marketing.pricing.enterprise.price_unit",
    taglineKey: "marketing.pricing.enterprise.tagline",
    chipKey: "marketing.pricing.enterprise.chip",
    teaserDescKey: "marketing.pricing.enterprise.teaser_desc",
    includesLineKey: "marketing.pricing.enterprise.includes_line",
    bulletKeys: [
      "marketing.pricing.enterprise.bullet_1",
      "marketing.pricing.enterprise.bullet_2",
      "marketing.pricing.enterprise.bullet_3",
      "marketing.pricing.enterprise.bullet_4",
      "marketing.pricing.enterprise.bullet_5",
      "marketing.pricing.enterprise.bullet_6",
      "marketing.pricing.enterprise.bullet_7",
      "marketing.pricing.enterprise.bullet_8",
      "marketing.pricing.enterprise.bullet_9",
    ],
    noteKey: "marketing.pricing.enterprise.note",
    ctaKey: "marketing.pricing.cta.enterprise",
    featured: false,
  },
];
