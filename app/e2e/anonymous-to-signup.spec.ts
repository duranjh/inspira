// anonymous-to-signup.spec.ts — anonymous user types an idea, hits the auth
// gate, creates a free account, and lands on the canvas with the idea intact.
//
// Covers:
//   1. Anonymous user lands on kickoff form.
//   2. Types an idea (>20 chars, qualifies for submit).
//   3. Clicks "Map it →" as an anonymous / system user.
//   4. Auth gate surfaces before the kickoff API fires.
//   5. Clicks "Create free account" on the auth gate.
//   6. Fills the signup form inside the AuthPanel.
//   7. On success the app resumes — either the kickoff form (idea restored)
//      or the canvas if the app auto-submitted for the user.
//   8. Canvas eventually loads with topic cards visible.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  mockLLM,
  waitForCanvas,
} from "./helpers";

const IDEA = "Starting a community herb garden in my neighborhood";

// TODO(#185): rewrite for v4 — these tests assert the v3 anonymous-allowed
// kickoff flow (`/` renders .kickoff for anon, submit triggers .auth-gate,
// signup restores the idea). v4 has gate-first auth: anonymous traffic at
// `/` is dispatched by RootGate to SignInPage (.signin-surface). There is
// no anonymous KickoffForm in the v4 flow. Skipped en bloc until rewrite.
test.describe.skip("Anonymous → sign-up → first project", () => {
  test("auth gate surfaces when anonymous user submits, signup restores flow", async ({
    page,
  }) => {
    // Mock LLM before any navigation — route handlers are registered
    // immediately and filter regardless of when the fetch fires.
    await mockLLM(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);

    // We intentionally do NOT call signupAndLogin here — we want the
    // anonymous / system user session to test the gate.

    // Kickoff form should be visible for an unauthenticated session.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // Type the idea into the textarea.
    const textarea = page.locator(".kickoff__textarea");
    await textarea.fill(IDEA);

    // The submit button should become enabled once the idea is long enough.
    const submitBtn = page.getByRole("button", { name: /map it/i });
    await expect(submitBtn).toBeEnabled({ timeout: 10_000 });

    // Click submit as an anonymous user.
    await submitBtn.click();

    // The app should surface an auth gate (modal/dialog/section) rather
    // than immediately calling the kickoff API. The gate is rendered as
    // .auth-gate or triggers the AuthPanel. We accept either selector.
    const authGate = page.locator(".auth-gate, .auth-panel-backdrop");
    await expect(authGate.first()).toBeVisible({ timeout: 10_000 });

    // Click the "Create free account" call-to-action. Some implementations
    // expose this as a link or button on the gate surface itself, others
    // open the AuthPanel signup tab. We try the gate CTA first.
    const signupCta = page.getByRole("button", {
      name: /create free account/i,
    });
    const signupLink = page.getByRole("link", {
      name: /create free account/i,
    });
    const signupTab = page.getByRole("button", { name: /sign up/i });

    // Click whichever is available.
    if (await signupCta.isVisible()) {
      await signupCta.click();
    } else if (await signupLink.isVisible()) {
      await signupLink.click();
    } else {
      await signupTab.click();
    }

    // Fill the signup form.
    const email = `anon-gate-${Date.now()}@example.test`;
    const password = "gate-test-pass-99";

    await page.getByRole("textbox", { name: /email/i }).fill(email);
    await page.getByRole("textbox", { name: /display name/i }).fill("GateTester");
    await page.getByLabel(/^password/i).fill(password);
    await page.getByRole("button", { name: /create account/i }).click();

    // Auth modal closes.
    await expect(page.locator(".auth-panel-backdrop")).toHaveCount(0, {
      timeout: 15_000,
    });

    // After signup the app should either:
    //   (a) return to the kickoff form with the idea still in the textarea, OR
    //   (b) have auto-submitted and be loading/showing the canvas.
    // We accept either outcome — the important thing is no error and eventual
    // canvas load.
    const kickoffOrCanvas = page.locator(".kickoff, .app-shell, .react-flow");
    await expect(kickoffOrCanvas.first()).toBeVisible({ timeout: 20_000 });

    // If we're still on kickoff, verify the idea text was preserved, then
    // submit ourselves.
    const kickoff = page.locator(".kickoff");
    if (await kickoff.isVisible()) {
      // Idea should be restored in the textarea (app stores it in state /
      // sessionStorage before the gate intercepts).
      const restoredText = await page
        .locator(".kickoff__textarea")
        .inputValue();
      // Accept partial match — some implementations trim or truncate.
      expect(restoredText.length).toBeGreaterThan(0);

      // Submit now that we're signed in.
      const mapBtn = page.getByRole("button", { name: /map it/i });
      await expect(mapBtn).toBeEnabled({ timeout: 10_000 });
      await mapBtn.click();
    }

    // Canvas must eventually load.
    await waitForCanvas(page);

    // At least one topic card confirms the LLM mock was consumed.
    await expect(page.locator(".topic-node").first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("anonymous user can see kickoff form without signing in", async ({
    page,
  }) => {
    // Sanity test: the app doesn't redirect straight to a login wall.
    await page.goto("/");
    await dismissOnboardingIfPresent(page);

    // Kickoff (or onboarding) should be visible — not an immediate auth wall.
    await expect(
      page.locator(".kickoff, .inspira-onboarding"),
    ).toBeVisible({ timeout: 15_000 });
  });
});
