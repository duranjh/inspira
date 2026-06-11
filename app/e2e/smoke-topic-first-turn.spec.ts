// smoke-topic-first-turn.spec.ts — critical-flow smoke: open a topic and
// answer the planner's first question.
//
// Flow under test:
//   1. Sign up + kickoff (helpers).
//   2. Double-click the first .topic-node on the canvas.
//   3. TopicDetail drawer mounts; auto-kick-off fires the opening turn
//      (POST /api/v2/topics/*/turn — intercepted).
//   4. The planner's question card appears in the thread.
//   5. Tap a suggested-response chip to submit a user answer.
//   6. The user's turn echoes the chip label; a second planner turn
//      lands.
//
// What this proves: the topic-detail thread loop (planner ask → user
// answer → planner ask again) is wired end-to-end. Detailed coverage of
// custom-typed answers, decisions surfacing, and topic close lives in
// topic-detail.spec.ts; this smoke is the fast canary.
//
// Mocking: mockOpenAI shims kickoff + turn. We additionally shim the
// GET /turns and GET /decisions endpoints because the mocked topic_id
// (topic-mock-1) doesn't exist in the backend DB, so the real list
// endpoints would 404 on first-open and stall the auto-kick-off path.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fakeTurnResponse,
  fillKickoff,
  mockOpenAI,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — entry path threads through the v3 kickoff
// → canvas flow. The topic-thread / turn-suggestion surface still exists
// on the canvas; only the prelude needs a v4 entry rewrite. Skipped en bloc.
test.describe.skip("smoke: open topic + ask first turn", () => {
  test("planner opens thread → user taps suggestion → planner replies", async ({
    page,
  }) => {
    await mockOpenAI(page);

    // Stub the GET-side endpoints TopicDetail calls on mount so the
    // mocked topic_id can mount cleanly. The POST /turn route is
    // already covered by mockOpenAI — let those through with .fallback().
    await page.route("**/api/v2/topics/*/turns", async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ turns: [] }),
      });
    });
    await page.route("**/api/v2/topics/*/decisions", async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ decisions: [] }),
      });
    });

    // Sign up + drive through kickoff so the canvas is up.
    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(page, "Drafting a monthly book club for sci-fi readers");
    await waitForCanvas(page);

    // Open the first topic card. TopicNode handles onDoubleClick to
    // morph into the TopicDetail drawer.
    const firstTopic = page.locator(".topic-node").first();
    await expect(firstTopic).toBeVisible();
    await firstTopic.dblclick();

    // Drawer mounts. .topic-detail is the root container.
    const drawer = page.locator(".topic-detail");
    await expect(drawer).toBeVisible({ timeout: 10_000 });

    // The opening planner question lands as a .turn--planner card.
    // Use the canned envelope's question text for the assertion so any
    // schema drift fails this test instead of silently passing.
    const { turn_result } = fakeTurnResponse();
    const plannerQuestion = turn_result.question as string;

    await expect(drawer.locator(".turn--planner")).toBeVisible({
      timeout: 15_000,
    });
    await expect(drawer.locator(".turn--planner")).toContainText(
      plannerQuestion,
    );

    // Tap the first suggested-response chip. submitAnswer(label) POSTs
    // another /turn; our mock returns the same envelope, which gives us
    // a second planner card to assert on.
    const firstSuggestion = drawer
      .locator(".turn--planner .turn__suggestion")
      .first();
    await expect(firstSuggestion).toBeVisible();
    const suggestionLabel = (await firstSuggestion.textContent())?.trim() ?? "";
    await firstSuggestion.click();

    // The user's turn echoes the chip label.
    const userTurn = drawer.locator(".turn--user").first();
    await expect(userTurn).toBeVisible({ timeout: 10_000 });
    await expect(userTurn).toContainText(suggestionLabel);

    // A second planner turn follows (mock returns the same envelope, so
    // there are now two planner cards in the thread).
    await expect(drawer.locator(".turn--planner")).toHaveCount(2, {
      timeout: 15_000,
    });
  });
});
