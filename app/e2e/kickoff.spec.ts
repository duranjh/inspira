// kickoff.spec.ts — the first-run signup → kickoff → canvas flow.
//
// Covers:
//   1. Landing on / (bootstrapping → kickoff).
//   2. Dismissing the first-run onboarding overlay if present.
//   3. Signing up through the UserMenu → AuthPanel.
//   4. Typing an idea into the kickoff form.
//   5. Submitting (kickoff API intercepted; no OpenAI call).
//   6. Asserting at least 4 topic cards + 1 relationship line land on the canvas.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAI,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — KickoffForm CSS class still exists in code
// (KickoffForm.tsx) but is unreachable from the v4 entry paths. Fresh signup
// in v4 routes through /onboarding Wizard, not kickoff. Skipped en bloc.
test.describe.skip("Kickoff flow", () => {
  test("signup → kickoff form → canvas with topics and relationships", async ({
    page,
  }) => {
    // Intercept the LLM call before any navigation. page.route() filters
    // by URL pattern regardless of when the fetch is queued.
    await mockOpenAI(page);

    await page.goto("/");

    // Dismiss the walkthrough if it's up. New users with no projects
    // get it on first load; signed-in users returning do not.
    await dismissOnboardingIfPresent(page);

    // At this point the system user is logged in by default (pre-auth
    // fallback). Signing up gives us a real user record + cookie.
    await signupAndLogin(page);

    // Post-signup the app refetches and lands on "kickoff" (zero projects
    // for a brand-new user). Verify the kickoff surface is visible.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // Fill the idea, hit "Map it →".
    await fillKickoff(
      page,
      "Building a small podcast about creative careers",
    );

    // Canvas lands.
    await waitForCanvas(page);

    // At least 4 topic cards. The TopicNode render class is .topic-node.
    const topicCards = page.locator(".topic-node");
    await expect(topicCards).toHaveCount(4, { timeout: 15_000 });

    // At least 1 relationship edge. React Flow renders edges as
    // .react-flow__edge under the pane. We assert 1+ via count.
    const edges = page.locator(".react-flow__edge");
    expect(await edges.count()).toBeGreaterThanOrEqual(1);
  });

  test("kickoff form rejects very short ideas", async ({ page }) => {
    // This test does NOT need mockOpenAI — we never submit.
    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // A 3-char idea is well below the 20-char submit guard.
    await page.locator(".kickoff__textarea").fill("hi");

    // Submit should stay disabled. canSubmit = idea >= 20 || attachments.
    const submit = page.getByRole("button", { name: /map it/i });
    await expect(submit).toBeDisabled();
  });
});
