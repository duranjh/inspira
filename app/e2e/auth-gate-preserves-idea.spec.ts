// auth-gate-preserves-idea.spec.ts — the kickoff auth gate remembers the idea
// text so the user doesn't lose their work when they are asked to sign in.
//
// Covers:
//   1. Anonymous user types an idea into the kickoff textarea.
//   2. Clicks "Map it →" (or the relevant submit action).
//   3. Auth gate surfaces — confirms the app blocked the LLM call.
//   4. Clicks "Edit your idea" (back/cancel link on the gate).
//   5. Asserts the textarea still contains the original idea text.
//   6. (Bonus) Verifies the submit button re-enables after returning.

import { expect, test } from "@playwright/test";

import { dismissOnboardingIfPresent } from "./helpers";

const ORIGINAL_IDEA = "Launching a weekend ceramics studio for beginners";

// TODO(#185): rewrite for v4 — assumes the v3 anonymous→kickoff→auth-gate
// flow where the idea text was preserved through the gate. v4 gates auth
// upfront; there's no anonymous idea-entry path that triggers an auth gate.
// Skipped en bloc until rewrite.
test.describe.skip("Auth gate preserves idea text", () => {
  test("idea is restored in textarea after clicking 'Edit your idea'", async ({
    page,
  }) => {
    // Do NOT mock LLM and do NOT sign in — we want the real gate to fire
    // before the API call happens. The gate is a client-side guard; no
    // OpenAI call should be made regardless of mocking.

    await page.goto("/");
    await dismissOnboardingIfPresent(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // Fill the idea.
    await page.locator(".kickoff__textarea").fill(ORIGINAL_IDEA);

    // Submit as anonymous user.
    const submitBtn = page.getByRole("button", { name: /map it/i });
    await expect(submitBtn).toBeEnabled({ timeout: 10_000 });
    await submitBtn.click();

    // Auth gate must appear.
    const gate = page.locator(".auth-gate, .auth-panel-backdrop");
    await expect(gate.first()).toBeVisible({ timeout: 10_000 });

    // Find the "Edit your idea" back link. Different implementations may use
    // a button or anchor with slightly different text variants.
    const backButton = page
      .getByRole("button", { name: /edit your idea/i })
      .or(page.getByRole("link", { name: /edit your idea/i }))
      .or(page.getByRole("button", { name: /go back/i }))
      .or(page.getByRole("button", { name: /back/i }));

    await expect(backButton.first()).toBeVisible({ timeout: 10_000 });
    await backButton.first().click();

    // The gate should close and we should be back on the kickoff surface.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 10_000 });

    // The original idea must still be in the textarea.
    const restoredText = await page
      .locator(".kickoff__textarea")
      .inputValue();
    expect(restoredText).toBe(ORIGINAL_IDEA);

    // The submit button should be enabled again (idea length is still valid).
    await expect(
      page.getByRole("button", { name: /map it/i }),
    ).toBeEnabled({ timeout: 5_000 });
  });

  test("auth gate shows sign-in and sign-up options", async ({ page }) => {
    await page.goto("/");
    await dismissOnboardingIfPresent(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await page.locator(".kickoff__textarea").fill(ORIGINAL_IDEA);

    const submitBtn = page.getByRole("button", { name: /map it/i });
    await expect(submitBtn).toBeEnabled({ timeout: 10_000 });
    await submitBtn.click();

    const gate = page.locator(".auth-gate, .auth-panel-backdrop");
    await expect(gate.first()).toBeVisible({ timeout: 10_000 });

    // The gate should offer both paths — sign in OR create account.
    // We look for at least one of these options.
    const signInOption = page
      .getByRole("button", { name: /sign in/i })
      .or(page.getByRole("link", { name: /sign in/i }));
    const createOption = page
      .getByRole("button", { name: /create.*(account|free)/i })
      .or(page.getByRole("button", { name: /sign up/i }))
      .or(page.getByRole("link", { name: /create.*(account|free)/i }));

    // At least one must be visible.
    const signInCount = await signInOption.count();
    const createCount = await createOption.count();
    expect(signInCount + createCount).toBeGreaterThan(0);
  });
});
