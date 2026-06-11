// Inspira — shared shell for every long-form legal page.
//
// Privacy, Terms, Cookies, DMCA, Acceptable Use, and the GDPR data-
// subject procedure all render through this one component. Each doc's
// body lives as a raw `.md` under `docs/legal/` and is imported via
// Vite's `?raw` suffix, so there's one source of truth and the on-page
// prose stays in sync with what counsel eventually signs off on.
//
// Routing decides which doc to render by passing the `doc` prop; we
// switch to pick the raw markdown, the page title, and the canonical
// URL.

import type { JSX } from "react";

import { t } from "../../i18n";

import { Head } from "./Head";
import { MarketingLayout } from "./MarketingLayout";
import { renderMarkdown } from "./renderMarkdown";
import "./marketing.css";
import "./marketing-legal.css";

import privacyMd from "../../../../docs/legal/privacy-policy.md?raw";
import termsMd from "../../../../docs/legal/terms-of-service.md?raw";
import cookiesMd from "../../../../docs/legal/cookie-policy.md?raw";
import dmcaMd from "../../../../docs/legal/dmca-policy.md?raw";
import acceptableUseMd from "../../../../docs/legal/acceptable-use.md?raw";
import gdprMd from "../../../../docs/legal/gdpr-data-subject-procedure.md?raw";

export type LegalDoc =
  | "privacy"
  | "terms"
  | "cookies"
  | "dmca"
  | "acceptable-use"
  | "gdpr";

export interface LegalPageProps {
  doc: LegalDoc;
}

type DocMeta = {
  body: string;
  titleKey: string;
  canonicalSlug: string;
};

function resolveDoc(doc: LegalDoc): DocMeta {
  switch (doc) {
    case "privacy":
      return {
        body: privacyMd,
        titleKey: "marketing.legal.privacy.title",
        canonicalSlug: "privacy",
      };
    case "terms":
      return {
        body: termsMd,
        titleKey: "marketing.legal.terms.title",
        canonicalSlug: "terms",
      };
    case "cookies":
      return {
        body: cookiesMd,
        titleKey: "marketing.legal.cookies.title",
        canonicalSlug: "cookies",
      };
    case "dmca":
      return {
        body: dmcaMd,
        titleKey: "marketing.legal.dmca.title",
        canonicalSlug: "dmca",
      };
    case "acceptable-use":
      return {
        body: acceptableUseMd,
        titleKey: "marketing.legal.acceptable_use.title",
        canonicalSlug: "acceptable-use",
      };
    case "gdpr":
      return {
        body: gdprMd,
        titleKey: "marketing.legal.gdpr.title",
        canonicalSlug: "gdpr",
      };
  }
}

/**
 * Parse a "**Last updated:** YYYY-MM-DD" line out of a legal doc if
 * present. Returns just the date string, or null. The .md docs all use
 * the same bolded-label convention.
 */
function extractLastUpdated(body: string): string | null {
  const m = /\*\*Last updated:\*\*\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|TBD)/i.exec(body);
  return m ? m[1] : null;
}

/**
 * Strip metadata headers (H1 + the Effective/Last-updated block + the
 * DRAFT blockquote) from the source body so the renderer doesn't repeat
 * the title we've already shown in the page header.
 */
function stripLeadingMetadata(body: string): string {
  let out = body;
  // Drop the leading DRAFT warning blockquote if present.
  out = out.replace(/^>\s.*\n/, "");
  // Drop the first H1 (we render our own).
  out = out.replace(/^#\s+.+\n+/, "");
  // Drop the "**Effective date:** …" / "**Last updated:** …" pair if they
  // appear right at the top, then any following horizontal rule.
  out = out.replace(/^\*\*Effective date:\*\*.*\n/, "");
  out = out.replace(/^\*\*Last updated:\*\*.*\n/, "");
  out = out.replace(/^---+\s*\n/, "");
  return out.replace(/^\s+/, "");
}

export function LegalPage({ doc }: LegalPageProps): JSX.Element {
  const meta = resolveDoc(doc);
  const title = t(meta.titleKey);
  const description = t("marketing.legal.meta.description");
  const lastUpdated = extractLastUpdated(meta.body);
  const body = stripLeadingMetadata(meta.body);

  const canonical = `https://tryinspira.com/legal/${meta.canonicalSlug}`;

  return (
    <MarketingLayout>
      <Head
        title={title}
        description={description}
        canonical={canonical}
        ogImage="/og/og-legal.png"
      />
      <article className="marketing-legal" aria-labelledby={`legal-${doc}-title`}>
        <p className="marketing-legal__eyebrow">{t("marketing.legal.eyebrow")}</p>
        <h1 className="marketing-legal__title" id={`legal-${doc}-title`}>
          {title}
        </h1>
        {lastUpdated ? (
          <p className="marketing-legal__updated">
            {t("marketing.legal.last_updated", { date: lastUpdated })}
          </p>
        ) : null}
        <div className="marketing-legal__body">{renderMarkdown(body)}</div>
      </article>
    </MarketingLayout>
  );
}

export default LegalPage;
