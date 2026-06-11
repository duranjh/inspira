// Playwright smoke config — post-build sanity check.
//
// Distinct from playwright.config.ts: this config does NOT boot the backend
// and does NOT run the full e2e suite. It exists to catch the narrow but
// scary class of "local dev passes, prod bundle crashes" regressions (e.g.
// React error #300 from a stale ref hook path in the minified output).
//
// What it does:
//   1. Runs `npm run preview` — Vite's static server, port 4173 — which
//      serves the already-built dist/ the same way Cloudflare Pages will.
//   2. Loads http://localhost:4173/ in chromium.
//   3. Asserts the React tree mounted (#root has at least one child) and
//      no console error ever fired during the load.
//
// Explicitly does NOT:
//   - Sign in (we have nothing to sign into without a backend).
//   - Exercise kickoff / topic turn / any API-dependent flow.
//   - Touch localStorage or cookies beyond what the page sets by itself.
//
// If the preview bundle throws at first paint, the assertion fails, the
// job fails, the PR is blocked. That is the entire point.

import { defineConfig, devices } from "@playwright/test";

const IS_CI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e-smoke",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  forbidOnly: IS_CI,
  // Zero retries — a smoke flake is a real bug, not a networking wobble.
  retries: 0,
  workers: 1,
  reporter: IS_CI
    ? [["html", { open: "never" }], ["list"]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:4173",
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
      // `vite preview` serves dist/ — the exact bytes Cloudflare Pages
      // would publish. Do NOT replace with `vite dev`: dev applies
      // transforms that hide prod-only bundle issues.
      command: "npm run preview -- --port 4173 --strictPort",
      cwd: ".",
      url: "http://localhost:4173/",
      reuseExistingServer: !IS_CI,
      timeout: 60_000,
    },
  ],
});
