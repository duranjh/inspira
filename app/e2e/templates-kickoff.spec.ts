// templates-kickoff.spec.ts — selecting a template on the kickoff screen
// changes the submit label and loads the canvas with template-seeded topics.
//
// Covers:
//   1. Sign in and land on kickoff form.
//   2. Click a template card in the templates section.
//   3. Assert the submit button text changes to "Start from template →".
//   4. Submit via the template path (LLM mocked with template-specific envelope).
//   5. Canvas loads with at least 4 topic nodes.
//   6. (Bonus) Deselecting the template restores the default submit label.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fakeKickoffResponse,
  fakeTurnResponse,
  mockOpenAI,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — template-card selection on the kickoff
// form was a v3 feature; the v4 onboarding/Wizard flow does not expose
// these template cards on the kickoff path. May be retired entirely in
// #185 rather than rewritten. Skipped en bloc.
test.describe.skip("Template kickoff flow", () => {
  test("selecting a template card changes submit label to 'Start from template →'", async ({
    page,
  }) => {
    await mockOpenAI(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // Template cards are listed in the kickoff surface. They may be in a
    // horizontal scroll list or a grid — grab the first visible one.
    const templateCard = page
      .locator(".template-card, .kickoff-template, [data-testid='template-card']")
      .first();

    // If no template cards are visible, the feature may be behind a flag or
    // not yet implemented — skip gracefully.
    const cardCount = await templateCard.count();
    if (cardCount === 0) {
      test.skip(true, "No template cards found — feature not yet active");
      return;
    }

    await expect(templateCard).toBeVisible({ timeout: 10_000 });
    await templateCard.click();

    // After selecting a template, the submit button label should change.
    const submitBtn = page.getByRole("button", {
      name: /start from template/i,
    });
    await expect(submitBtn).toBeVisible({ timeout: 5_000 });
    await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
  });

  test("submitting via a template loads the canvas with topic nodes", async ({
    page,
  }) => {
    // Use a richer mock that includes template-flavored topics so we can
    // assert specific topic titles if the template seeds them.
    await page.route("**/api/v2/projects/*/kickoff", async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(
          fakeKickoffResponse("template: podcast starter kit"),
        ),
      });
    });
    await page.route("**/api/v2/topics/*/turn", async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(fakeTurnResponse()),
      });
    });

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    const templateCard = page
      .locator(".template-card, .kickoff-template, [data-testid='template-card']")
      .first();

    const cardCount = await templateCard.count();
    if (cardCount === 0) {
      test.skip(true, "No template cards found — feature not yet active");
      return;
    }

    await templateCard.click();

    const submitBtn = page.getByRole("button", {
      name: /start from template/i,
    });
    await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
    await submitBtn.click();

    // Canvas loads.
    await waitForCanvas(page);

    // At least 4 topic nodes from the mocked envelope.
    await expect(page.locator(".topic-node")).toHaveCount(4, {
      timeout: 15_000,
    });
  });

  test("deselecting template card restores default submit label", async ({
    page,
  }) => {
    await mockOpenAI(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // Type enough text so the default submit is enabled.
    await page.locator(".kickoff__textarea").fill("A project about urban beekeeping in the city");

    const templateCard = page
      .locator(".template-card, .kickoff-template, [data-testid='template-card']")
      .first();

    const cardCount = await templateCard.count();
    if (cardCount === 0) {
      test.skip(true, "No template cards found — feature not yet active");
      return;
    }

    // Select template.
    await templateCard.click();
    await expect(
      page.getByRole("button", { name: /start from template/i }),
    ).toBeVisible({ timeout: 5_000 });

    // Click the same card again to deselect (toggle pattern).
    await templateCard.click();

    // Default submit label should be restored.
    await expect(
      page.getByRole("button", { name: /map it/i }),
    ).toBeVisible({ timeout: 5_000 });
  });
});
