// FE mirror of the BE's project.metadata.domain → doc_type mapping.
//
// Source of truth lives in
// services/planning_studio_service/store.py:DOMAIN_TO_DOC_TYPE — keep
// these two in lockstep. Used by:
//   - DocumentView empty-state CTA labels (before any doc exists)
//   - LlmModesPanel tab label resolution (doc-type-aware "Business Plan"
//     / "PRD" / "Story Outline" rather than a generic 3rd-tab label)
//
// Career and personal domains are intentionally unmapped in v1; the
// panel renders a friendly "no document type for this project type
// yet" fallback for those.

import type { DocType } from "./api";

/** Project-domain → doc-type mapping. Mirror of BE
 *  DOMAIN_TO_DOC_TYPE (services/.../store.py:282-292). */
export const DOMAIN_TO_DOC_TYPE: Record<string, DocType> = {
  business_plan: "business_plan",
  software_product: "business_plan",
  software_feature: "prd",
  novel: "story_outline",
  screenplay: "story_outline",
  event: "event_plan",
  campaign: "marketing_plan",
  research: "research_proposal",
  course: "course_outline",
};

/** All v1 doc types in canonical order. Mirror of BE VALID_DOC_TYPES
 *  (services/.../store.py:267-275). */
export const VALID_DOC_TYPES: readonly DocType[] = [
  "business_plan",
  "prd",
  "story_outline",
  "event_plan",
  "marketing_plan",
  "research_proposal",
  "course_outline",
] as const;

/** Resolve a project domain to its doc_type, or null if unmapped
 *  (career, personal, missing/unknown domain). Callers branch on null
 *  to render the "domain not mapped" fallback. */
export function docTypeForDomain(
  domain: string | undefined | null,
): DocType | null {
  if (!domain) return null;
  return DOMAIN_TO_DOC_TYPE[domain] ?? null;
}

/** Type-guard for runtime values that should be a DocType. Useful
 *  when narrowing values pulled out of `Record<string, unknown>`
 *  (project.metadata) without trusting the wire shape. */
export function isDocType(value: unknown): value is DocType {
  return typeof value === "string" && VALID_DOC_TYPES.includes(value as DocType);
}

/** Document monthly cap by plan slug. Mirror of BE
 *  BUSINESS_PLAN_CAPS_BY_PLAN (services/.../agents/tiers.py:124-128).
 *  The cap is shared across all 7 doc types per founder lock-in
 *  2026-04-29 — Pro 1/mo any doc type, Frontier 100/mo. The slug
 *  "team" is kept for backward-compat with the entitlements payload
 *  even though marketing copy calls the tier "Frontier" (#081). */
export function documentCapForPlan(plan: string): number {
  if (plan === "team") return 100;
  if (plan === "pro") return 1;
  return 0;
}
