// Shared fixtures for the Inspira E2E suite.
//
// Why these live here (and not in individual specs):
//   - signupAndLogin: every spec needs an authenticated user. Inlining the
//     signup flow in each one would triple file length and make failures
//     harder to read.
//   - mockOpenAI: the two LLM-hitting routes (kickoff + topic_turn) must
//     be intercepted for every test that traverses them. Doing it in
//     one shared helper means only ONE fixture is on the hook when the
//     backend contract changes.
//   - fakeKickoff / fakeTurn: the planner envelope shapes are non-trivial
//     (see services/planning_studio_service/agents/schemas.py). Centralizing
//     the canned fixtures keeps the spec files focused on user flows.
//
// Strict TypeScript: every function exported here has explicit return
// types so spec authors get full autocomplete, and the envelopes match
// the real API types at app/src/features/inspira/api.ts.

import type { Page, Route } from "@playwright/test";
import { expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

/**
 * Generates a unique email per call so tests never collide on the uniqueness
 * constraint in the users table. The shared backend DB is isolated per run
 * (see playwright.config.ts → PLANNING_STUDIO_STORAGE_ROOT), but within a
 * run each spec picks its own namespace.
 */
export function uniqueEmail(prefix = "e2e"): string {
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${Date.now()}-${rand}@example.test`;
}

/**
 * Drives the AuthPanel signup flow end-to-end.
 *
 * Assumes the user is on the app root (or anywhere the UserMenu is visible).
 * Dismisses the first-run onboarding overlay if present, opens the user
 * menu, clicks "Sign in", switches to the signup tab, fills the form, and
 * waits for the modal to close. On return the signed-in user's cookie is
 * present on the context.
 *
 * The password defaults to a fixed value (length >= 8 required by the
 * backend). Callers can override either field for tests that want to
 * exercise duplicate-email or weak-password paths.
 */
export async function signupAndLogin(
  page: Page,
  opts: { email?: string; password?: string; displayName?: string } = {},
): Promise<{ email: string; password: string }> {
  const email = opts.email ?? uniqueEmail();
  const password = opts.password ?? "test-password-12345";
  const displayName = opts.displayName ?? "Tester";

  // Navigate if we're not already on the app.
  if (!page.url().startsWith("http://localhost:4175")) {
    await page.goto("/");
  }

  // Onboarding walkthrough hijacks the Esc key and covers the user menu;
  // dismiss it first. The overlay only appears for signed-up users with
  // 0 projects, but it's cached in localStorage — "Skip" clears it.
  await dismissOnboardingIfPresent(page);

  // The UserMenu avatar is the chip in the top-bar. For the system
  // fallback user it renders a "·" glyph; clicking opens the panel.
  await page.locator(".user-menu__avatar").click();
  // The system user sees a "Sign in" action; click it to open AuthPanel.
  await page.getByRole("button", { name: "Sign in" }).click();

  // AuthPanel opens in login mode; switch to signup.
  await page.getByRole("button", { name: "Sign up" }).click();

  // Fill the form. The label selector matches both the current <label>
  // wrapper pattern and the direct input type.
  await page.getByRole("textbox", { name: /email/i }).fill(email);
  await page.getByRole("textbox", { name: /display name/i }).fill(displayName);
  // "Password" is a <label> wrapping an <input type="password">. We select
  // by label text so the hint span ("at least 8 characters") doesn't
  // confuse the matcher.
  await page.getByLabel(/^password/i).fill(password);

  await page.getByRole("button", { name: /create account/i }).click();

  // The modal closes on success. The fastest stable signal is the
  // auth-panel backdrop unmounting.
  await expect(page.locator(".auth-panel-backdrop")).toHaveCount(0, {
    timeout: 15_000,
  });

  return { email, password };
}

/**
 * Clears the first-run onboarding overlay if it's currently covering the
 * viewport. Safe to call unconditionally — no-op when the overlay isn't
 * mounted.
 */
export async function dismissOnboardingIfPresent(page: Page): Promise<void> {
  const overlay = page.locator(".inspira-onboarding");
  const count = await overlay.count();
  if (count === 0) return;
  // "Skip" finishes the walkthrough and writes the localStorage flag so
  // it doesn't come back on reload.
  await page.getByRole("button", { name: /^skip$/i }).click();
  await expect(overlay).toHaveCount(0, { timeout: 5_000 });
}

// ---------------------------------------------------------------------------
// LLM mocks
// ---------------------------------------------------------------------------

/**
 * Installs page.route() handlers that short-circuit every LLM-hitting
 * backend call with a canned envelope. Call ONCE at the top of a spec,
 * ideally before the first navigation that might fetch any topics.
 *
 * Routes intercepted:
 *   - POST /api/v2/projects/{id}/kickoff  → fakeKickoffResponse()
 *   - POST /api/v2/topics/{id}/turn       → fakeTurnResponse()
 *
 * Side effects: the route handlers inject fake topic/relationship/turn IDs
 * that do NOT exist in the backend DB, so any follow-up GET that goes
 * through the real backend will 404. That's fine for the current specs —
 * they read from the kickoff envelope directly or use listTopics only
 * after mutating through the mocked endpoint.
 *
 * Specs that need real topic rows (e.g. projects-list.spec.ts switching
 * between two projects) should call mockOpenAIWithPersistedTopics instead,
 * which creates real topics via the unmocked create-topic endpoint before
 * swapping the kickoff mock in.
 */
export async function mockOpenAI(page: Page): Promise<void> {
  // Phase 1 SSE streaming endpoints: short-circuit with 503
  // streaming_disabled so the frontend's fallback path (api.kickoff /
  // api.topicTurn) runs and hits the non-streaming mock below. This
  // matches the dark-launch posture in production where the streaming
  // routes are gated behind INSPIRA_ENABLE_STREAM_KICKOFF, and keeps
  // the spec set independent of whether the feature flag is on or off.
  // IMPORTANT: register the /stream routes BEFORE the bare ones so the
  // glob doesn't match the streaming URLs.
  await page.route("**/api/v2/projects/*/kickoff/stream", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: { error: "streaming_disabled" } }),
    });
  });
  await page.route("**/api/v2/topics/*/turn/stream", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: { error: "streaming_disabled" } }),
    });
  });

  await page.route("**/api/v2/projects/*/kickoff", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(fakeKickoffResponse("mocked idea")),
    });
  });

  await page.route("**/api/v2/topics/*/turn", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(fakeTurnResponse()),
    });
  });
}

/**
 * Variant that lets the backend actually create the topics (so subsequent
 * listTopics calls return real rows) but still short-circuits the LLM call.
 *
 * How: we intercept the kickoff route, call createV2Project beforehand via
 * the test's own api helper, then hand back the fake envelope but stamped
 * with the real project_id. Topics are created via the manual-topic
 * endpoint (POST /api/v2/projects/{id}/topics) which does NOT hit OpenAI.
 * The envelope's topic_ids are the real IDs returned by those creates.
 *
 * For the current suite we only need this in projects-list.spec.ts; other
 * specs use the simpler mockOpenAI above.
 */
export async function mockOpenAIWithPersistedTopics(
  page: Page,
): Promise<void> {
  // See mockOpenAI for the rationale: short-circuit the streaming routes
  // with 503 streaming_disabled so the frontend falls back to the
  // non-streaming mock that has the persisted-topics behaviour wired in.
  await page.route("**/api/v2/projects/*/kickoff/stream", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: { error: "streaming_disabled" } }),
    });
  });
  await page.route("**/api/v2/topics/*/turn/stream", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: { error: "streaming_disabled" } }),
    });
  });

  await page.route("**/api/v2/projects/*/kickoff", async (route) => {
    const url = new URL(route.request().url());
    const projectId = url.pathname.split("/").slice(-2, -1)[0];

    // Build real topics via the manual-topic endpoint. The backend
    // service is up at the same origin (via CORS proxying). Use the
    // cookie jar Playwright maintains on the context so the request
    // is authenticated.
    const createdTopics = await createFakeTopicsForProject(page, projectId);

    const envelope = fakeKickoffResponse("mocked idea");
    // Swap in real persisted topics + relationships so subsequent
    // listTopics / listRelationships GETs return real rows.
    envelope.topics = createdTopics;
    envelope.relationships = [];

    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(envelope),
    });
  });

  await page.route("**/api/v2/topics/*/turn", async (route: Route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(fakeTurnResponse()),
    });
  });
}

/**
 * Helper for mockOpenAIWithPersistedTopics — creates four manual topics
 * against a real project so the canvas renders real rows.
 */
async function createFakeTopicsForProject(
  page: Page,
  projectId: string,
): Promise<Array<Record<string, unknown>>> {
  const titles = ["Voice", "Format", "Distribution", "Cadence"];
  const icons = ["lightbulb", "feather", "book", "compass"];
  const out: Array<Record<string, unknown>> = [];
  for (let i = 0; i < titles.length; i++) {
    const res = await page.request.post(
      `http://localhost:4174/api/v2/projects/${projectId}/topics`,
      {
        data: {
          title: titles[i],
          icon: icons[i],
          position_x: (i % 2) * 440,
          position_y: Math.floor(i / 2) * 320,
        },
      },
    );
    const json = (await res.json()) as { topic: Record<string, unknown> };
    out.push(json.topic);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Canned envelopes
// ---------------------------------------------------------------------------

/**
 * A plausible kickoff envelope with four topics and three relationships.
 * Shape matches KickoffEnvelope from app/src/features/inspira/api.ts.
 *
 * IDs are stable strings so tests can reference them by equality.
 */
export function fakeKickoffResponse(idea: string): {
  kickoff: Record<string, unknown>;
  topics: Array<Record<string, unknown>>;
  relationships: Array<Record<string, unknown>>;
} {
  const projectId = "proj-mock";
  const now = new Date().toISOString();

  const topics = [
    {
      topic_id: "topic-mock-1",
      project_id: projectId,
      title: "Voice",
      icon: "lightbulb",
      position_x: 0,
      position_y: 0,
      status: "empty",
      order_index: 0,
      origin: "planner_initial",
      metadata: { why_this_topic: "What the show sounds like matters." },
      created_at: now,
      updated_at: now,
    },
    {
      topic_id: "topic-mock-2",
      project_id: projectId,
      title: "Format",
      icon: "feather",
      position_x: 0,
      position_y: 320,
      status: "empty",
      order_index: 1,
      origin: "planner_initial",
      metadata: { why_this_topic: "Length and structure drive production cost." },
      created_at: now,
      updated_at: now,
    },
    {
      topic_id: "topic-mock-3",
      project_id: projectId,
      title: "Distribution",
      icon: "compass",
      position_x: 440,
      position_y: 0,
      status: "empty",
      order_index: 2,
      origin: "planner_initial",
      metadata: { why_this_topic: "Where you publish shapes who listens." },
      created_at: now,
      updated_at: now,
    },
    {
      topic_id: "topic-mock-4",
      project_id: projectId,
      title: "Cadence",
      icon: "clock",
      position_x: 440,
      position_y: 320,
      status: "empty",
      order_index: 3,
      origin: "planner_initial",
      metadata: { why_this_topic: "Sustainable release rhythm." },
      created_at: now,
      updated_at: now,
    },
  ];

  const relationships = [
    {
      relationship_id: "rel-mock-1",
      project_id: projectId,
      source_topic_id: "topic-mock-1",
      target_topic_id: "topic-mock-2",
      label: "shapes",
      origin: "planner_inferred",
      strength: null,
      created_at: now,
    },
    {
      relationship_id: "rel-mock-2",
      project_id: projectId,
      source_topic_id: "topic-mock-2",
      target_topic_id: "topic-mock-3",
      label: "informs",
      origin: "planner_inferred",
      strength: null,
      created_at: now,
    },
    {
      relationship_id: "rel-mock-3",
      project_id: projectId,
      source_topic_id: "topic-mock-3",
      target_topic_id: "topic-mock-4",
      label: "depends on",
      origin: "planner_inferred",
      strength: null,
      created_at: now,
    },
  ];

  return {
    kickoff: {
      domain: "personal",
      domain_confidence: "medium",
      opening_card: {
        body: `Let's map out ${idea}. These four topics cover the shape of it.`,
      },
      topics: topics.map((t) => ({
        title: t.title,
        icon: t.icon,
        why_this_topic: (t.metadata as { why_this_topic: string }).why_this_topic,
      })),
      relationships: relationships.map((r) => ({
        from_topic_title: topicTitleById(topics, r.source_topic_id),
        to_topic_title: topicTitleById(topics, r.target_topic_id),
        label: r.label,
      })),
      suggested_first_topic: "Voice",
      clarifying_question_if_too_vague: null,
    },
    topics,
    relationships,
  };
}

function topicTitleById(
  topics: Array<Record<string, unknown>>,
  topicId: string,
): string {
  const match = topics.find((t) => t.topic_id === topicId);
  return (match?.title as string | undefined) ?? "";
}

/**
 * A plausible topic-turn envelope: the planner asks an opening question
 * with three suggested replies. No proposed decisions or consistency
 * flags — those pathways aren't covered by the current spec set.
 */
export function fakeTurnResponse(): {
  turn_result: Record<string, unknown>;
  planner_turn: Record<string, unknown>;
} {
  const now = new Date().toISOString();

  const turnResult = {
    action: "ask",
    question: "What does the show sound like, in one sentence?",
    why_this_matters:
      "Voice is the first thing listeners register — it sets the frame for every episode.",
    suggested_responses: [
      { label: "Warm and conversational", intent: "tone_warm" },
      { label: "Reported and produced", intent: "tone_reported" },
      { label: "Playful and loose", intent: "tone_playful" },
    ],
    proposed_decisions: [],
    consistency_flags: [],
    new_topic_proposal: null,
    close_recommendation_reason: null,
  };

  // planner_turn is the persisted QnaTurn row mirror of turn_result, with
  // IDs and a created_at. TopicDetail renders this directly.
  const plannerTurn = {
    turn_id: "turn-mock-1",
    topic_id: "topic-mock-1",
    project_id: "proj-mock",
    role: "planner",
    order_index: 0,
    body: turnResult.question,
    why_this_matters: turnResult.why_this_matters,
    action: turnResult.action,
    suggested_responses: turnResult.suggested_responses,
    status: "open",
    created_at: now,
  };

  return { turn_result: turnResult, planner_turn: plannerTurn };
}

// ---------------------------------------------------------------------------
// Sign-in helper (existing account)
// ---------------------------------------------------------------------------

/**
 * Signs into an existing account via the UserMenu → AuthPanel login form.
 *
 * Assumes the page is already at the app root (or any page where the top-bar
 * is visible). Returns after the auth modal closes and the session cookie is
 * active. Use this when a test creates a user via signupAndLogin in a
 * beforeAll / prior step and then needs to re-authenticate (e.g. after
 * navigating to a new context).
 */
export async function signInAs(
  page: Page,
  email: string,
  password: string,
): Promise<void> {
  if (!page.url().startsWith("http://localhost:4175")) {
    await page.goto("/");
  }

  await dismissOnboardingIfPresent(page);

  await page.locator(".user-menu__avatar").click();
  await page.getByRole("button", { name: "Sign in" }).click();

  // AuthPanel opens in login mode by default.
  await page.getByRole("textbox", { name: /email/i }).fill(email);
  await page.getByLabel(/^password/i).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();

  await expect(page.locator(".auth-panel-backdrop")).toHaveCount(0, {
    timeout: 15_000,
  });
}

// ---------------------------------------------------------------------------
// mockLLM — alias kept consistent with the brief; delegates to mockOpenAI
// ---------------------------------------------------------------------------

/**
 * Alias for mockOpenAI, exported under the name the brief specifies.
 * Intercepts all LLM-hitting routes (kickoff + topic_turn) with canned
 * responses. Call before any navigation that might trigger those routes.
 */
export async function mockLLM(page: Page): Promise<void> {
  return mockOpenAI(page);
}

// ---------------------------------------------------------------------------
// Share-link mock helpers
// ---------------------------------------------------------------------------

/**
 * Mocks the share-link generation endpoint so tests don't need a real project
 * created in the DB. Returns a stable fake share token.
 */
export const FAKE_SHARE_TOKEN = "share-tok-e2e-abc123";

export async function mockShareApi(page: Page): Promise<void> {
  // POST /api/v2/projects/:id/share  → generate link
  await page.route("**/api/v2/projects/*/share", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          share_url: `http://localhost:4175/s/${FAKE_SHARE_TOKEN}`,
          token: FAKE_SHARE_TOKEN,
        }),
      });
      return;
    }
    await route.fallback();
  });

  // DELETE /api/v2/projects/:id/share  → revoke link
  await page.route("**/api/v2/projects/*/share", async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fallback();
  });

  // GET /s/:token  → shared canvas (read-only view loads)
  await page.route(`**/s/${FAKE_SHARE_TOKEN}`, async (route) => {
    // Let it hit the SPA — the page.route() here is a signal hook only.
    await route.fallback();
  });

  // GET /s/:token  returning 404 after revoke (we flip this per-test below)
  // No static route needed; individual tests replace the handler after revoke.
}

// ---------------------------------------------------------------------------
// Misc test utilities
// ---------------------------------------------------------------------------

/**
 * Waits for the ProjectCanvas to be the visible phase. Matches the shell
 * container InspiraApp renders when phase.kind === "canvas".
 */
export async function waitForCanvas(page: Page): Promise<void> {
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
  // React Flow mounts .react-flow on the canvas surface once ProjectCanvas
  // has its topics array. Waiting for it avoids a race with the first
  // interaction.
  await expect(page.locator(".react-flow")).toBeVisible({ timeout: 15_000 });
}

/**
 * Fills the kickoff textarea with enough text to pass the 20-char guard.
 */
export async function fillKickoff(page: Page, idea: string): Promise<void> {
  // The textarea is .kickoff__textarea; autofocused on kickoff mount.
  const textarea = page.locator(".kickoff__textarea");
  await textarea.fill(idea);
  // "Map it →" is the submit label; wait for it to become enabled, then click.
  const submit = page.getByRole("button", { name: /map it/i });
  await expect(submit).toBeEnabled({ timeout: 10_000 });
  await submit.click();
}
