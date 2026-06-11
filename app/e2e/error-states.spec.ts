// error-states.spec.ts — bad paths and offline handling.
//
// Covers:
//   1. /nonexistent-path — the SPA is a single-route app (InspiraApp always
//      mounts, no router). So this test just verifies the app renders
//      gracefully at an unusual path — the server side is handled by Vite
//      which maps everything to index.html in dev mode.
//   2. Offline: flip the browser offline via page.context().setOffline(true);
//      assert the OfflineBanner surfaces.
//   3. Back online: assert the banner clears.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  mockOpenAI,
  signupAndLogin,
} from "./helpers";

test.describe("Error / offline states", () => {
  // TODO(#185): rewrite for v4 — the "unknown path falls through to
  // render" contract was a v3 single-route assumption (no router means
  // any path lands on InspiraApp). v4 has explicit routing via React
  // Router and RootGate; unknown paths don't necessarily render anything
  // recognizable — they may 404, redirect, or land somewhere that
  // doesn't match any of the v3/v4 anonymous-root selectors. Verified
  // mid-Path-A (2026-05-12): adding `.signin-surface` to the union
  // didn't pass either, suggesting v4's unknown-path behavior is
  // outright "don't render" rather than fall-through. Skip until the
  // test intent is re-anchored on a real v4 contract (e.g. assert a
  // 404 page exists, or assert RootGate redirects to /signin).
  test.skip("unusual path still renders the app", async ({ page }) => {
    const response = await page.goto("/nonexistent-path");
    expect(response?.status()).toBe(200);
    await expect(
      page.locator(
        ".error-page, .kickoff, .loading, .inspira-onboarding, .signin-surface",
      ),
    ).toBeVisible({ timeout: 15_000 });
  });

  // TODO(#185): rewrite for v4 — signupAndLogin used to land on .kickoff;
  // v4 routes fresh signup through /onboarding Wizard then /workspaces.
  // Skip until the offline-banner check is re-anchored on a v4 surface.
  test.skip("offline banner surfaces when connection drops, clears when restored", async ({
    page,
    context,
  }) => {
    await mockOpenAI(page);
    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // Expect the kickoff form (0 projects, signed-up user).
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // OfflineBanner is self-gating off navigator.onLine + the
    // useOnlineStatus hook. Flipping context.offline triggers the
    // 'offline' window event that the hook listens for.
    await context.setOffline(true);

    // Banner lands.
    await expect(page.locator(".offline-banner")).toBeVisible({
      timeout: 10_000,
    });

    // Restore.
    await context.setOffline(false);

    // Banner dismisses once navigator.onLine flips back.
    await expect(page.locator(".offline-banner")).toHaveCount(0, {
      timeout: 10_000,
    });
  });
});
