// Playwright config for Inspira E2E.
//
// What this runs:
//   - A backend (FastAPI on :4174 via `python -m planning_studio_service` from
//     services/). Isolated per run via PLANNING_STUDIO_STORAGE_ROOT pointing
//     at an OS tempdir we compute once below, so tests never contaminate
//     the developer's real local DB.
//   - A frontend (Vite on :4175 via `npm run dev` from app/).
//
// The global webServer block boots both and waits for each health URL before
// tests start. Workers are pinned to 1 — tests share the backend + DB, so
// parallel runs would interleave writes and make assertions race.
//
// No mocks in this file: individual specs use page.route() to shim the
// LLM-hitting routes (kickoff, topic_turn) so CI never needs OPENAI_API_KEY.

import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// One tempdir per "process boot" (config load). Playwright loads this file
// twice in CI — once for the planner process, once for the workers — but
// both share the same env when the webServer block runs first, so the
// tempdir stays consistent for the duration of the run. Fresh state per
// invocation: perfect for E2E isolation.
const STORAGE_ROOT =
  process.env.PLANNING_STUDIO_STORAGE_ROOT ??
  mkdtempSync(join(tmpdir(), "inspira-e2e-"));

const IS_CI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  // Each spec must complete in 60s including its own fixtures + navigation.
  timeout: 60_000,
  expect: {
    // Generous so the first-paint animations (topic morph, fade-ins) have
    // time to settle before `expect(...).toBeVisible()` gives up.
    timeout: 10_000,
  },
  fullyParallel: false,
  forbidOnly: IS_CI,
  retries: IS_CI ? 2 : 0,
  workers: 1,
  reporter: IS_CI
    ? [["html", { open: "never" }], ["list"]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:4175",
    trace: "on-first-retry",
    // Surface failures quickly on slow CI; retry logic is explicit in the
    // specs via expect(...).toHaveText() etc., not via global retries.
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      // Backend — stdlib-friendly launch. The service reads
      // PLANNING_STUDIO_STORAGE_ROOT, so we inject the tempdir here.
      // `cwd: ../services` so `python -m planning_studio_service` resolves
      // the package without needing a PYTHONPATH dance.
      command: "python -m planning_studio_service",
      cwd: "../services",
      url: "http://localhost:4174/api/health",
      reuseExistingServer: !IS_CI,
      timeout: 120_000,
      env: {
        PLANNING_STUDIO_STORAGE_ROOT: STORAGE_ROOT,
        // Keep the dev CORS regex active (no allowlist) so the Vite origin
        // at :4175 can carry credentials to the backend at :4174.
        INSPIRA_ALLOWED_ORIGINS: "",
        // Disable per-user daily token budget for tests — the LLM is
        // mocked client-side, but the backend still checks the gate
        // before handing the prompt to the adapter. Zero disables it.
        INSPIRA_USER_DAILY_TOKEN_BUDGET: "0",
        // A deterministic session secret so cookie signing is stable
        // across backend restarts within the same run (rare, but we've
        // seen flake when a worker restart invalidates prior cookies).
        INSPIRA_SESSION_SECRET: "inspira-e2e-session-secret-not-for-prod",
        // We never actually hit OpenAI in tests — every LLM call is
        // intercepted via page.route(). Setting a dummy key prevents
        // the lazy-init in the adapter from throwing on construct.
        OPENAI_API_KEY: "sk-test-e2e-placeholder",
        ENVIRONMENT: "development",
      },
    },
    {
      // `npm run dev` matches the rest of the toolchain — we left pnpm
      // behind after the pnpm-lock drift that stalled the CF Pages build.
      command: "npm run dev",
      cwd: ".",
      url: "http://localhost:4175/",
      reuseExistingServer: !IS_CI,
      timeout: 120_000,
    },
  ],
});
