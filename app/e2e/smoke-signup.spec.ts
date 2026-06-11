// smoke-signup.spec.ts — critical-flow smoke: brand-new user signs up.
//
// Flow under test:
//   1. Anonymous visit lands on the app (kickoff or onboarding overlay).
//   2. Open the user menu, click "Sign in".
//   3. Switch the AuthPanel to its "Sign up" tab.
//   4. Fill email / display name / password and submit.
//   5. After signup the auth modal closes and the app surfaces the
//      kickoff form (a brand-new account has zero projects, so the
//      app routes there by default).
//
// Why this is a smoke test, not a deep test:
//   We're proving the signup pipeline is wired end-to-end (form →
//   /api/auth/register → cookie → bootstrap refetch → kickoff phase).
//   Detailed validation paths (weak password, duplicate email) live
//   in their own specs; this one fails loud the moment any segment
//   of that pipeline breaks.
//
// No LLM mocks needed: signup itself does not hit OpenAI. We do still
// dismiss onboarding because the overlay covers the user-menu trigger
// for first-load.

import { expect, test } from "@playwright/test";

import { dismissOnboardingIfPresent, uniqueEmail } from "./helpers";

// TODO(#185): rewrite for v4 — "anonymous → sign up → kickoff" is the
// v3 path. v4 lands fresh signup on /onboarding Wizard, not kickoff.
// The signup flow is still core to v4 but the post-signup assertion
// needs to be re-anchored on Wizard's first step. Skipped en bloc.
test.describe.skip("smoke: sign up new user", () => {
  test("anonymous → sign up → land on kickoff", async ({ page }) => {
    await page.goto("/");
    await dismissOnboardingIfPresent(page);

    // Open the user menu in the top bar — the avatar chip is always
    // present, even for the system fallback user. Clicking it opens
    // the panel that contains the "Sign in" action.
    await page.locator(".user-menu__avatar").click();
    await page.getByRole("button", { name: "Sign in" }).click();

    // AuthPanel opens in login mode by default; flip to signup.
    await page.getByRole("button", { name: "Sign up" }).click();

    const email = uniqueEmail("smoke-signup");
    const password = "smoke-test-pass-12345";

    await page.getByRole("textbox", { name: /email/i }).fill(email);
    await page
      .getByRole("textbox", { name: /display name/i })
      .fill("SmokeTester");
    // Use getByLabel(/^password/i) — the hint span ("at least 8 chars")
    // sits right below the field and trips a generic "password" matcher.
    await page.getByLabel(/^password/i).fill(password);

    await page.getByRole("button", { name: /create account/i }).click();

    // Auth modal closes on success. The backdrop unmount is the
    // fastest, race-free signal that the cookie is set.
    await expect(page.locator(".auth-panel-backdrop")).toHaveCount(0, {
      timeout: 15_000,
    });

    // Brand-new user, zero projects → the app refetches and lands on
    // kickoff. (If the user was a returning user we'd land on
    // projects_list; that path is covered by smoke-signin.spec.ts.)
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
  });
});
