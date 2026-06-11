/**
 * Central feature flags read from build-time Vite env vars.
 *
 * Flags are read at build time (Vite substitutes string literals into
 * the bundle) so changing them requires a redeploy, not a refresh. Set
 * the env vars in Cloudflare Pages → Settings → Environment variables
 * → Production. Default for every flag is `false` so unset envs leave
 * existing behavior intact.
 *
 * Naming: `VITE_INSPIRA_*` matches the existing convention used by
 * `VITE_INSPIRA_API_URL`, `VITE_RELEASE`, `VITE_PLAUSIBLE_DOMAIN`,
 * `VITE_SENTRY_DSN`.
 */

const flag = (value: string | undefined): boolean => value === "1";

/**
 * Hide every Upgrade-trigger surface (Stripe-dark gating).
 *
 * When set to `"1"`, the frontend hides:
 * - All Upgrade buttons across BillingOverviewPage, TrialBanner,
 *   ModelTierChip, TopicDetail, etc.
 * - The PlanComparisonModal mount path.
 * - The QuotaExceededModal entirely (returns null early — without
 *   this, a Free tier hitting cap sees a modal whose only action
 *   triggers a 501 from the noop billing provider).
 *
 * Set this on demo deploys until Stripe is wired live or
 * billing is re-enabled.
 */
export const HIDE_UPGRADE: boolean = flag(
  import.meta.env.VITE_INSPIRA_HIDE_UPGRADE as string | undefined,
);
