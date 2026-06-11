// landing.smoke.spec.ts — prod-bundle sanity check.
//
// Paired with app/playwright.smoke.config.ts. Run with:
//   npx playwright test --config playwright.smoke.config.ts
//
// Assertions:
//   1. Page loads. Navigation resolves without a network-level error.
//   2. React actually mounted: #root has at least one child element.
//      This is the strongest local signal we can give without running
//      any app logic — main.tsx calls createRoot(#root).render(...), so
//      if anything in the top-level tree throws before commit, #root
//      stays empty and this assertion fails.
//   3. No console errors during the load. The whole point of this spec
//      is catching minified React errors (#300, #185, #423 class) that
//      pass local dev but blow up in the prod bundle. We accumulate
//      every console.error() message from page load and fail if the
//      list is non-empty.
//
// What we ignore:
//   - 404s for optional assets (og-image.png etc) are warnings, not
//     errors, so they don't trip page.on('console').
//   - Service worker registration failures: the SW is gated on
//     import.meta.env.PROD and, under `vite preview`, PROD is true — so
//     the SW WILL register. In test we swallow SW registration errors
//     because the Playwright chromium context doesn't grant a secure
//     context for localhost-with-port in every version (fine in prod
//     over HTTPS). Filter list below.

import { expect, test } from "@playwright/test";

// Console errors originating from these substrings are tolerated. Keep
// this list surgical — every entry is a known false positive worth
// annotating, not a "make the test pass" silencer.
const TOLERATED_CONSOLE_ERROR_PATTERNS: RegExp[] = [
  // Service worker registration can fail in the Playwright chromium
  // localhost context even though it works in real production. The SW
  // is optional (PWA); a failure here is not a bundle regression.
  /service.?worker/i,
  // Sentry init emits a harmless warning when DSN is not configured.
  // Under `vite preview` we don't wire a DSN, so the warning fires at
  // info/warn level in most builds but occasionally escalates to error
  // in CI.
  /sentry/i,
  // The prod bundle pings the backend (auth probe, credits) on boot.
  // In CI there is no backend reachable from the GitHub runner, so
  // the browser logs "Failed to load resource: net::ERR_CONNECTION_REFUSED".
  // That's a runtime network error, not a bundle regression — the
  // assertion we actually care about (React mounted, no uncaught page
  // errors) still holds. Filter these so the smoke spec stays green
  // offline.
  /ERR_CONNECTION_REFUSED/i,
  /ERR_NAME_NOT_RESOLVED/i,
  /Failed to load resource/i,
];

test.describe("prod bundle smoke", () => {
  test("landing page renders without throwing", async ({ page }) => {
    const consoleErrors: string[] = [];

    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (TOLERATED_CONSOLE_ERROR_PATTERNS.some((rx) => rx.test(text))) return;
      consoleErrors.push(text);
    });

    // pageerror fires on uncaught exceptions in page scripts — the
    // clearest signal that the bundle crashed.
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => {
      pageErrors.push(err);
    });

    await page.goto("/", { waitUntil: "load" });

    // React mounted: #root has at least one child. If main.tsx threw
    // before commit (e.g. a misused hook ref in prod), #root stays empty.
    const rootChildCount = await page
      .locator("#root > *")
      .count();
    expect(
      rootChildCount,
      "React did not mount: #root has no children. The prod bundle likely threw during render.",
    ).toBeGreaterThan(0);

    // Give the tree one tick to settle any post-mount effects (which
    // is where error #300-style hook misuse usually surfaces).
    await page.waitForTimeout(500);

    // No uncaught exceptions in page scripts.
    expect(
      pageErrors,
      `Uncaught page errors during load: ${pageErrors.map((e) => e.message).join(" | ")}`,
    ).toEqual([]);

    // No unexpected console errors.
    expect(
      consoleErrors,
      `Unexpected console.error during load: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
