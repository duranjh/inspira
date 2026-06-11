// account-settings.spec.ts — account page, theme toggle, logout.
//
// Covers:
//   1. Sign in → open UserMenu → click "Account settings".
//   2. Toggle theme to dark → assert <html data-theme="dark">.
//   3. Close settings.
//   4. Open UserMenu → click "Log out" → assert we land back on
//      the bootstrapping / kickoff flow (app reloads in handleLogout,
//      and the post-reload landing for a fresh session is kickoff).

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  mockOpenAI,
  signupAndLogin,
} from "./helpers";

// TODO(#185): rewrite for v4 — these tests assert the v3 single-user app
// shape (anonymous-allowed kickoff at /, UserMenu .user-menu__avatar in
// every top bar, KickoffForm reachable post-signup). v4 reshaped the
// product to gate-first auth → /signin → /onboarding Wizard → /workspaces
// Kanban; UserMenu was deferred (#145, only a "Sign out" pill on Kanban).
// Skipped en bloc until the suite is rewritten against the v4 surfaces.
test.describe.skip("Account settings flow", () => {
  test("open account settings, toggle theme to dark, then log out", async ({
    page,
  }) => {
    // No LLM calls on this path, but mocking is cheap insurance — if the
    // initial bootstrap ever fans out a kickoff we don't want a live call.
    await mockOpenAI(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // A fresh signup lands on kickoff (0 projects). That's fine —
    // UserMenu is in the top bar of every phase, not just canvas.
    // Open the user menu and click "Account settings".
    await page.locator(".user-menu__avatar").click();
    await page.getByRole("button", { name: /account settings/i }).click();

    // The account page takes over the viewport.
    const accountPage = page.locator(".account-page");
    await expect(accountPage).toBeVisible({ timeout: 10_000 });

    // Theme section — three radios: light / dark / system. Click the
    // dark radio by its label.
    await accountPage.getByRole("radio", { name: /dark/i }).click();

    // ThemeSection writes data-theme on <html>.
    await expect(page.locator("html")).toHaveAttribute(
      "data-theme",
      "dark",
      { timeout: 5_000 },
    );

    // Close the account page (× top-right).
    await accountPage
      .getByRole("button", { name: /close account settings/i })
      .click();
    await expect(accountPage).toHaveCount(0, { timeout: 5_000 });

    // -- Log out ----------------------------------------------------------
    // handleLogout posts /api/auth/logout then window.location.reload().
    // After reload the cookie is cleared and the app boots as the system
    // user — zero projects → kickoff phase.
    await page.locator(".user-menu__avatar").click();
    await page.getByRole("button", { name: /log out/i }).click();

    // Wait for the bootstrapping state (shown while we reload + refetch)
    // or for the kickoff form to appear. Either is a valid "logged-out"
    // signal; we prefer the kickoff surface since it's the final state.
    await expect(page.locator(".kickoff, .loading")).toBeVisible({
      timeout: 20_000,
    });
    // Eventually kickoff proper lands.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 20_000 });
  });
});
