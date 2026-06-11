// smoke-first-decision.spec.ts — critical-flow smoke: a decision lands on
// a topic when the planner proposes it during a turn.
//
// Why this shape: in Inspira, the user does not "manually create" a
// decision through a dedicated form. Decisions are produced by the
// planner during a Q&A turn and persisted server-side. The user
// "makes" a decision by either accepting a planner-proposed one or by
// the planner's `proposed_decisions` array on a turn envelope being
// non-empty (the backend then stores it on the relevant topic).
//
// Flow under test:
//   1. Sign up + kickoff (helpers).
//   2. Open the first topic on the canvas.
//   3. The topic-detail drawer mounts; auto-kick-off fires the first
//      turn. We override the /turn mock to return a `proposed_decisions`
//      payload AND also make GET /api/v2/topics/*/decisions return that
//      decision, so the Decisions column populates after the turn lands.
//   4. Submit an answer to the planner's question — the planner replies
//      with another turn that includes a proposed decision.
//   5. Assert the .topic-detail__decision row renders with the decision
//      statement.
//
// Mocking choice: we deliberately stub /decisions GET to return the
// fake decision, so the assertion proves "the UI renders persisted
// decisions on this topic". A deeper test would also exercise the
// real backend's decision persistence; that is out of scope here.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAI,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

const FAKE_DECISION_STATEMENT =
  "Publish bi-weekly to give writers room to breathe between drops.";
const FAKE_DECISION_RATIONALE =
  "A monthly cadence felt too sparse; weekly burned out the contributors.";

// TODO(#185): rewrite for v4 — entry path threads through the v3 kickoff
// → canvas flow which no longer exists from `/`. Decision-surfacing on a
// topic still exists; the prelude needs a v4 entry rewrite. Skipped en bloc.
test.describe.skip("smoke: make first decision", () => {
  test("planner proposes decision during turn → it surfaces on the topic", async ({
    page,
  }) => {
    await mockOpenAI(page);

    // Mock the GET /turns to return empty (so auto-kick-off fires) and
    // the GET /decisions to return our seeded decision (so the
    // Decisions column has something to render).
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

    const fakeDecision = {
      decision_id: "dec-mock-1",
      topic_id: "topic-mock-1",
      project_id: "proj-mock",
      statement: FAKE_DECISION_STATEMENT,
      rationale: FAKE_DECISION_RATIONALE,
      origin: "planner_proposed",
      created_at: new Date().toISOString(),
    };

    await page.route("**/api/v2/topics/*/decisions", async (route) => {
      const method = route.request().method();
      if (method !== "GET") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ decisions: [fakeDecision] }),
      });
    });

    // Override the /turn POST mock to include a proposed_decisions
    // payload. The first call (auto-kick-off) returns an opening
    // question; the user-tap will then call again and the same envelope
    // includes the proposed decision the planner is "making" for them.
    await page.route("**/api/v2/topics/*/turn", async (route) => {
      const now = new Date().toISOString();
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          turn_result: {
            action: "ask",
            question: "How often will you publish?",
            why_this_matters:
              "Cadence shapes both the writer's workload and the reader's habit.",
            suggested_responses: [
              { label: "Weekly", intent: "cadence_weekly" },
              { label: "Bi-weekly", intent: "cadence_biweekly" },
              { label: "Monthly", intent: "cadence_monthly" },
            ],
            proposed_decisions: [
              {
                statement: FAKE_DECISION_STATEMENT,
                rationale: FAKE_DECISION_RATIONALE,
                target_topic_title: "Voice",
              },
            ],
            consistency_flags: [],
            new_topic_proposal: null,
            close_recommendation_reason: null,
          },
          planner_turn: {
            turn_id: `turn-mock-${now}`,
            topic_id: "topic-mock-1",
            project_id: "proj-mock",
            role: "planner",
            order_index: 0,
            body: "How often will you publish?",
            why_this_matters:
              "Cadence shapes both the writer's workload and the reader's habit.",
            action: "ask",
            suggested_responses: [
              { label: "Weekly", intent: "cadence_weekly" },
              { label: "Bi-weekly", intent: "cadence_biweekly" },
              { label: "Monthly", intent: "cadence_monthly" },
            ],
            status: "open",
            created_at: now,
          },
        }),
      });
    });

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(page, "Building a tiny zine about analog photography");
    await waitForCanvas(page);

    // Open the first topic card.
    const firstTopic = page.locator(".topic-node").first();
    await firstTopic.dblclick();

    const drawer = page.locator(".topic-detail");
    await expect(drawer).toBeVisible({ timeout: 10_000 });

    // The Decisions column lists our seeded decision. .topic-detail__decision
    // is the row class; the body span carries the statement text.
    const decisionRow = drawer.locator(".topic-detail__decision").first();
    await expect(decisionRow).toBeVisible({ timeout: 15_000 });
    await expect(decisionRow).toContainText(FAKE_DECISION_STATEMENT);
  });
});
