// smoke-first-kickoff.spec.ts — critical-flow smoke: first-time kickoff.
//
// Flow under test:
//   1. Sign up a brand-new user (helper).
//   2. App lands on the kickoff form.
//   3. Type an idea (>= 20 chars to clear the submit guard).
//   4. Click "Map it →" — kickoff API mocked, no OpenAI.
//   5. Canvas mounts and at least one TopicNode renders.
//
// What this proves: signup → kickoff submit → canvas pipeline is wired.
// The mocked envelope ships four topics, so we assert >= 1 TopicNode
// rather than == 4 (kickoff.spec.ts already covers exact counts).
// Smoke = "did the path light up at all", not "did every byte match".
//
// LLM mock: page.route() intercepts POST /api/v2/projects/*/kickoff
// with a canned envelope (see helpers.fakeKickoffResponse). No
// OPENAI_API_KEY required.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAI,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — "signed-up user submits idea" is the v3
// path. v4 fresh signup goes through /onboarding Wizard, not a kickoff
// form. The "canvas with topics" target still exists but the entry path
// has fundamentally changed. Skipped en bloc.
test.describe.skip("smoke: first-time kickoff", () => {
  test("signed-up user submits idea → canvas with topics", async ({ page }) => {
    // Mock kickoff + topic_turn before any nav. page.route() handlers
    // are registered immediately and apply regardless of when the
    // matching fetch is queued.
    await mockOpenAI(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // Brand-new user lands on kickoff (zero projects).
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });

    // 20+ chars so the submit button enables. fillKickoff also clicks
    // "Map it →" once enabled.
    await fillKickoff(
      page,
      "Planning a small monthly newsletter for design students",
    );

    // Canvas surface mounts. waitForCanvas asserts both .app-shell and
    // the .react-flow canvas paint, so we know ProjectCanvas has its
    // topics array.
    await waitForCanvas(page);

    // At least one topic card. Mocked envelope ships four; we accept any
    // positive count to keep the smoke tolerant of envelope tweaks.
    const topicCards = page.locator(".topic-node");
    expect(await topicCards.count()).toBeGreaterThan(0);
  });
});
