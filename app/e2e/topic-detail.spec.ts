// topic-detail.spec.ts — open a topic, see the planner's opening question,
// answer via a suggested-response chip, assert the planner replies.
//
// The planner's first-open behavior is auto-kick-off: when there are zero
// turns for a topic, TopicDetail fires topicTurn() immediately so the
// opening question lands. We intercept that call (and the follow-up).
//
// Because topic-mock-1 doesn't actually exist in the backend (mockOpenAI
// invents IDs), we additionally shim the GET /turns and GET /decisions
// endpoints so TopicDetail can mount without a 404 on the first-open
// fetch. The shims return empty lists so the auto-kick-off path runs.

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
// flow before reaching topic detail. Topic-detail surface still exists
// (and is itself being reshaped to a drawer in Wave C / #124); the prelude
// needs a v4 entry rewrite. Skipped en bloc.
test.describe.skip("Topic detail flow", () => {
  test("open topic → planner opens thread → tap suggestion → turn appears", async ({
    page,
  }) => {
    await mockOpenAI(page);

    // Additional shims: TopicDetail calls listTurns + listDecisions on mount.
    // The mocked topic_id doesn't exist in the backend, so we return empty
    // arrays from a page.route() handler to let the component proceed to
    // the auto-kick-off path.
    await page.route("**/api/v2/topics/*/turns", async (route) => {
      // Only mock the GET (listTurns). The POST /turn route is already
      // covered by mockOpenAI(); let that one through.
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

    // Sign up and drive through kickoff so the canvas is up.
    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(page, "Building a small podcast about creative careers");
    await waitForCanvas(page);

    // Open the first topic card. TopicNode listens for onDoubleClick.
    const firstTopic = page.locator(".topic-node").first();
    await expect(firstTopic).toBeVisible();
    await firstTopic.dblclick();

    // TopicDetail drawer mounts — .topic-detail is the root.
    const drawer = page.locator(".topic-detail");
    await expect(drawer).toBeVisible({ timeout: 10_000 });

    // The opening question from the mocked turn envelope shows up in
    // the thread as a .turn--planner card.
    const { turn_result } = fakeTurnResponse();
    const plannerQuestion = turn_result.question as string;
    await expect(drawer.locator(".turn--planner")).toBeVisible({
      timeout: 15_000,
    });
    await expect(drawer.locator(".turn--planner")).toContainText(
      plannerQuestion,
    );

    // After the first turn lands TopicDetail re-renders and a fresh
    // planner_turn appears with its suggestion chips. Tapping one fires
    // submitAnswer(label) which POSTs another /turn; our mock responds
    // with another identical envelope. We assert a user turn surfaces
    // plus a second planner turn.
    const firstSuggestion = drawer
      .locator(".turn--planner .turn__suggestion")
      .first();
    await expect(firstSuggestion).toBeVisible();
    const suggestionLabel = (await firstSuggestion.textContent())?.trim() ?? "";
    await firstSuggestion.click();

    // The user turn echoes the selected suggestion label.
    const userTurn = drawer.locator(".turn--user").first();
    await expect(userTurn).toBeVisible({ timeout: 10_000 });
    await expect(userTurn).toContainText(suggestionLabel);

    // A second planner turn follows (same mock answer, but the UI now
    // has two planner cards).
    await expect(drawer.locator(".turn--planner")).toHaveCount(2, {
      timeout: 15_000,
    });

    // Close via the × button; the drawer morphs back to the canvas.
    await drawer
      .getByRole("button", { name: /close topic detail/i })
      .click();
    // Either the element unmounts entirely (no morph rect was captured)
    // or it is animating out and gone shortly after.
    await expect(drawer).toHaveCount(0, { timeout: 5_000 });

    // Canvas is still present underneath.
    await expect(page.locator(".react-flow")).toBeVisible();
  });
});
