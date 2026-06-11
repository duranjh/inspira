// Inspira — document <head> mutator for marketing pages. v2.
//
// No runtime dep (react-helmet etc). Just a useLayoutEffect that sets the
// document title, a handful of meta tags, and link[rel=canonical]. On
// unmount the previous values are restored so another page can take over
// cleanly.
//
// v2 adds:
//   - structuredData prop: emits one <script type="application/ld+json">
//     per object. FAQPage JSON-LD is dev-deduped — only one route per
//     mount cycle may emit @type: FAQPage.
//   - Per-route title suffix: if `title` doesn't contain "Inspira",
//     append " — Inspira" automatically (unless `noTitleSuffix` is set).
//   - Per-route description fallback via i18n.
//   - Canonical URL normalisation: strip ?query + #hash and force
//     https://tryinspira.com as the origin regardless of the request host.
//   - ogImage: relative paths resolve against the production origin so
//     preview / staging deploys never emit preview URLs into og:image.
//   - Twitter card meta (title, description, image, site handle).
//   - robots / theme-color / og:type / og:site_name / og:locale.

import { useLayoutEffect, type JSX } from "react";

import { t } from "../../i18n";

// -- Constants --------------------------------------------------------------

const PROD_ORIGIN = "https://tryinspira.com";
const DEFAULT_OG_IMAGE = "/og/og-default.png";
const DEFAULT_THEME_COLOR = "#F5F0E6";
const DEFAULT_TWITTER_HANDLE = "@tryinspira";
const SITE_NAME = "Inspira";
const LOCALE = "en_US";
const TITLE_SUFFIX = " \u2014 Inspira";

/** Module-level de-dup set for FAQPage structured data.
 *  Keyed by canonical URL. Only used at dev time. */
const emittedFaqPages: Set<string> = new Set();

// -- Types ------------------------------------------------------------------

export type HeadRobots = "index,follow" | "noindex,nofollow";
export type HeadOgType = "website" | "article";

export interface HeadProps {
  /** Page title, rendered exactly as passed (with auto-suffix unless skipped). */
  title: string;
  /** Meta description. If empty, falls back to marketing.meta.default_description. */
  description: string;
  /** Absolute or relative URL. Relatives resolve against PROD_ORIGIN. */
  canonical?: string;
  /** Absolute or relative path to a 1200x630 PNG. Relatives resolve to PROD_ORIGIN. */
  ogImage?: string;
  /** og:type, default "website". */
  type?: HeadOgType;
  /** JSON-LD payload(s). Serialised as one <script> per object. */
  structuredData?: object | object[];
  /** Robots directive, default "index,follow". */
  robots?: HeadRobots;
  /** Theme color for mobile chrome, default "#F5F0E6". */
  themeColor?: string;
  /** Twitter site handle, default "@tryinspira". */
  twitterHandle?: string;
  /** Skip the " — Inspira" auto-append. */
  noTitleSuffix?: boolean;
}

type MetaKind = "name" | "property";

// -- DOM helpers ------------------------------------------------------------

function upsertMeta(
  kind: MetaKind,
  key: string,
  value: string,
): { restore: () => void } {
  const selector = `meta[${kind}="${key}"]`;
  const existing = document.head.querySelector<HTMLMetaElement>(selector);
  if (existing) {
    const prev = existing.getAttribute("content") ?? "";
    existing.setAttribute("content", value);
    return {
      restore: () => {
        existing.setAttribute("content", prev);
      },
    };
  }
  const el = document.createElement("meta");
  el.setAttribute(kind, key);
  el.setAttribute("content", value);
  document.head.appendChild(el);
  return {
    restore: () => {
      if (el.parentNode) el.parentNode.removeChild(el);
    },
  };
}

function upsertLink(
  rel: string,
  href: string,
): { restore: () => void } {
  const selector = `link[rel="${rel}"]`;
  const existing = document.head.querySelector<HTMLLinkElement>(selector);
  if (existing) {
    const prev = existing.getAttribute("href") ?? "";
    existing.setAttribute("href", href);
    return {
      restore: () => {
        existing.setAttribute("href", prev);
      },
    };
  }
  const el = document.createElement("link");
  el.setAttribute("rel", rel);
  el.setAttribute("href", href);
  document.head.appendChild(el);
  return {
    restore: () => {
      if (el.parentNode) el.parentNode.removeChild(el);
    },
  };
}

function appendScript(
  mime: string,
  text: string,
): { restore: () => void } {
  const el = document.createElement("script");
  el.setAttribute("type", mime);
  el.textContent = text;
  document.head.appendChild(el);
  return {
    restore: () => {
      if (el.parentNode) el.parentNode.removeChild(el);
    },
  };
}

// -- URL normalisation ------------------------------------------------------

function resolveUrl(value: string | undefined, fallback: string): string {
  const raw = value ?? fallback;
  if (/^https?:\/\//i.test(raw)) return raw;
  return PROD_ORIGIN + (raw.startsWith("/") ? raw : `/${raw}`);
}

/** Strip ?query + #hash and force https://tryinspira.com as the origin.
 *  Works for either absolute or relative input. */
function normalizeCanonical(raw: string): string {
  let out = raw;
  // If absolute, strip origin and keep pathname + nothing else.
  try {
    if (/^https?:\/\//i.test(raw)) {
      const u = new URL(raw);
      out = u.pathname || "/";
    }
  } catch {
    // leave out as-is
  }
  // Strip query + hash from whatever's left.
  const qIdx = out.indexOf("?");
  if (qIdx >= 0) out = out.slice(0, qIdx);
  const hIdx = out.indexOf("#");
  if (hIdx >= 0) out = out.slice(0, hIdx);
  if (!out.startsWith("/")) out = `/${out}`;
  return PROD_ORIGIN + out;
}

/** Append " — Inspira" if title doesn't already mention the brand. */
function applyTitleSuffix(title: string, skip: boolean | undefined): string {
  if (skip) return title;
  if (title.includes("Inspira")) return title;
  return title + TITLE_SUFFIX;
}

function toStructuredArray(input: HeadProps["structuredData"]): object[] {
  if (!input) return [];
  return Array.isArray(input) ? input : [input];
}

function getDescriptionOrFallback(raw: string): string {
  if (raw && raw.trim() !== "") return raw;
  // i18n fallback. If the key is unset, t() returns the key itself, which
  // is still strictly better than emitting an empty description tag that
  // silently inherits the previous route's.
  return t("marketing.meta.default_description");
}

// -- Component --------------------------------------------------------------

/**
 * Renders nothing. Mutates document.head on mount; restores on unmount.
 * Use as a sibling inside a page component so the lifecycle matches.
 */
export function Head(props: HeadProps): JSX.Element | null {
  const {
    title,
    description,
    canonical,
    ogImage,
    type,
    structuredData,
    robots,
    themeColor,
    twitterHandle,
    noTitleSuffix,
  } = props;

  useLayoutEffect(() => {
    const prevTitle = document.title;

    // Resolve everything up front.
    const effectiveTitle = applyTitleSuffix(title, noTitleSuffix);
    const effectiveDescription = getDescriptionOrFallback(description);
    const defaultCanonical =
      typeof window !== "undefined" && window.location
        ? window.location.pathname || "/"
        : "/";
    const effectiveCanonical = normalizeCanonical(
      canonical ?? defaultCanonical,
    );
    const effectiveOgImage = resolveUrl(ogImage, DEFAULT_OG_IMAGE);
    const effectiveType: HeadOgType = type ?? "website";
    const effectiveRobots: HeadRobots = robots ?? "index,follow";
    const effectiveTheme = themeColor ?? DEFAULT_THEME_COLOR;
    const effectiveTwitter = twitterHandle ?? DEFAULT_TWITTER_HANDLE;
    const structuredArray = toStructuredArray(structuredData);

    // FAQPage dedup (dev only).
    if (import.meta.env.DEV) {
      for (const node of structuredArray) {
        const typeField = (node as Record<string, unknown>)["@type"];
        if (typeField === "FAQPage") {
          const existing = [...emittedFaqPages][0];
          if (existing && existing !== effectiveCanonical) {
            throw new Error(
              `FAQPage already emitted on "${existing}". ` +
                `Move it off that route before adding to "${effectiveCanonical}".`,
            );
          }
          emittedFaqPages.add(effectiveCanonical);
        }
      }
    }

    document.title = effectiveTitle;

    const restorers: Array<() => void> = [];

    // Core SEO meta.
    restorers.push(upsertMeta("name", "description", effectiveDescription).restore);
    restorers.push(upsertMeta("name", "robots", effectiveRobots).restore);
    restorers.push(upsertMeta("name", "theme-color", effectiveTheme).restore);
    restorers.push(
      upsertMeta("name", "referrer", "strict-origin-when-cross-origin").restore,
    );
    restorers.push(upsertMeta("name", "color-scheme", "light dark").restore);

    // Canonical is emitted unless the caller asked for noindex AND passed no
    // canonical — but we still always emit it when the caller passed one.
    // Simpler and matches the spec: always emit.
    restorers.push(upsertLink("canonical", effectiveCanonical).restore);

    // Open Graph.
    restorers.push(upsertMeta("property", "og:type", effectiveType).restore);
    restorers.push(upsertMeta("property", "og:site_name", SITE_NAME).restore);
    restorers.push(upsertMeta("property", "og:locale", LOCALE).restore);
    restorers.push(upsertMeta("property", "og:title", effectiveTitle).restore);
    restorers.push(
      upsertMeta("property", "og:description", effectiveDescription).restore,
    );
    restorers.push(upsertMeta("property", "og:url", effectiveCanonical).restore);
    restorers.push(upsertMeta("property", "og:image", effectiveOgImage).restore);
    restorers.push(upsertMeta("property", "og:image:width", "1200").restore);
    restorers.push(upsertMeta("property", "og:image:height", "630").restore);

    // Twitter.
    restorers.push(
      upsertMeta("name", "twitter:card", "summary_large_image").restore,
    );
    restorers.push(upsertMeta("name", "twitter:site", effectiveTwitter).restore);
    restorers.push(upsertMeta("name", "twitter:title", effectiveTitle).restore);
    restorers.push(
      upsertMeta("name", "twitter:description", effectiveDescription).restore,
    );
    restorers.push(upsertMeta("name", "twitter:image", effectiveOgImage).restore);

    // Structured data — one <script> per object, preserved order.
    for (const node of structuredArray) {
      let payload: string;
      try {
        payload = JSON.stringify(node);
      } catch {
        continue;
      }
      restorers.push(appendScript("application/ld+json", payload).restore);
    }

    return () => {
      document.title = prevTitle;
      // Forget any FAQPage registrations this mount added.
      if (import.meta.env.DEV) {
        for (const node of structuredArray) {
          const typeField = (node as Record<string, unknown>)["@type"];
          if (typeField === "FAQPage") {
            emittedFaqPages.delete(effectiveCanonical);
          }
        }
      }
      for (const restore of restorers) {
        try {
          restore();
        } catch {
          // best-effort: a previous restore may have already removed the node
        }
      }
    };
  }, [
    title,
    description,
    canonical,
    ogImage,
    type,
    structuredData,
    robots,
    themeColor,
    twitterHandle,
    noTitleSuffix,
  ]);

  return null;
}

export default Head;
