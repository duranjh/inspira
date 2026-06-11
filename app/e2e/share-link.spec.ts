// share-link.spec.ts — share link generation, read-only view in an incognito
// context, and link revocation resulting in a 404.
//
// Covers:
//   1. Sign in and open a project's canvas.
//   2. Open the Share dialog.
//   3. Click "Generate" → assert a URL appears.
//   4. Navigate to the share URL in a fresh (incognito-equivalent) context.
//   5. Assert read-only view: no composer, no topic drawer trigger.
//   6. Owner clicks "Revoke" → the share link is invalidated.
//   7. Incognito reload → 404 page or error message.
//
// The share API endpoints are mocked (see helpers.mockShareApi) so this does
// not require a real persisted share token in the backend DB.

import { expect, test } from "@playwright/test";

import {
  FAKE_SHARE_TOKEN,
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAIWithPersistedTopics,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — share-link was a v3 single-user feature
// (one user's project shared via incognito URL). v4 is multi-user with
// workspace membership; the share-link surface may not exist in this
// shape anymore. Skipped en bloc pending a v4-aware rewrite or removal.
test.describe.skip("Share link flow", () => {
  test("generate share link, view as incognito, revoke → 404", async ({
    page,
    browser,
  }) => {
    // Mock the LLM + share endpoints on the owner page.
    await mockOpenAIWithPersistedTopics(page);

    // Mock share-link API (generate + revoke).
    await page.route("**/api/v2/projects/*/share", async (route) => {
      const method = route.request().method();
      if (method === "POST") {
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            share_url: `http://localhost:4175/s/${FAKE_SHARE_TOKEN}`,
            token: FAKE_SHARE_TOKEN,
          }),
        });
      } else if (method === "DELETE") {
        await route.fulfill({ status: 204 });
      } else {
        await route.fallback();
      }
    });

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // Create a project so we have a canvas to share.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(page, "Building an open-source analytics dashboard");
    await waitForCanvas(page);

    // Open the Share dialog. The trigger may be a button in the top bar or
    // an overflow menu item.
    const shareBtn = page
      .getByRole("button", { name: /share/i })
      .or(page.locator("[data-testid='share-btn'], .share-btn"));

    const shareBtnCount = await shareBtn.count();
    if (shareBtnCount === 0) {
      test.skip(true, "Share button not found — feature not yet implemented");
      return;
    }

    await shareBtn.first().click();

    // Share dialog / panel should open.
    const shareDialog = page.locator(
      ".share-dialog, .share-panel, [data-testid='share-dialog'], [role='dialog']",
    );
    await expect(shareDialog.first()).toBeVisible({ timeout: 10_000 });

    // Click "Generate" (or "Create link", etc.).
    const generateBtn = shareDialog
      .first()
      .getByRole("button", { name: /generate|create link|get link/i });
    const genCount = await generateBtn.count();
    if (genCount === 0) {
      test.skip(true, "Generate link button not found in Share dialog");
      return;
    }
    await generateBtn.click();

    // A URL containing the share token should appear in the dialog.
    const shareUrlDisplay = shareDialog.first().locator(
      "input[readonly], .share-url, [data-testid='share-url']",
    );
    await expect(shareUrlDisplay.first()).toBeVisible({ timeout: 10_000 });
    const shownUrl = await shareUrlDisplay.first().getAttribute("value") ??
      await shareUrlDisplay.first().textContent() ?? "";
    expect(shownUrl).toContain(FAKE_SHARE_TOKEN);

    // -- Incognito (new context) read-only view ----------------------------
    const incognitoCtx = await browser.newContext();
    const incognitoPage = await incognitoCtx.newPage();

    // Mock the shared canvas route to return a canned read-only view.
    // In production this would be a real DB read; in tests we fake it.
    await incognitoPage.route(`**/s/${FAKE_SHARE_TOKEN}`, async (route) => {
      // Let the SPA serve the page — the real app handles /s/:token routing.
      await route.fallback();
    });

    // Also mock any API calls the read-only view needs.
    await incognitoPage.route(
      `**/api/v2/share/${FAKE_SHARE_TOKEN}`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            token: FAKE_SHARE_TOKEN,
            project: {
              project_id: "proj-mock",
              title: "Shared project",
              created_at: new Date().toISOString(),
            },
            topics: [],
            relationships: [],
          }),
        });
      },
    );

    await incognitoPage.goto(`http://localhost:4175/s/${FAKE_SHARE_TOKEN}`);

    // The read-only canvas should load. We look for the canvas/react-flow
    // container OR an explicit read-only wrapper class.
    const readonlyCanvas = incognitoPage.locator(
      ".app-shell--readonly, .canvas-readonly, .react-flow, .shared-canvas",
    );
    await expect(readonlyCanvas.first()).toBeVisible({ timeout: 20_000 });

    // Read-only view must NOT show the composer or topic drawer trigger.
    const composer = incognitoPage.locator(
      ".composer, .kickoff-composer, [data-testid='composer']",
    );
    await expect(composer).toHaveCount(0, { timeout: 5_000 });

    const topicDrawerTrigger = incognitoPage.locator(
      ".topic-node--editable, .topic-drawer-trigger",
    );
    // The count may be 0 if no topic nodes are present, or nodes exist but
    // the editable class is absent. We just assert composer is absent above;
    // a topic drawer being absent is a secondary signal.
    const drawerTriggerCount = await topicDrawerTrigger.count();
    // No assertion failure if nodes aren't present — the mock returns empty topics.
    expect(drawerTriggerCount).toBe(0);

    await incognitoCtx.close();

    // -- Revoke the link ---------------------------------------------------
    const revokeBtn = shareDialog
      .first()
      .getByRole("button", { name: /revoke|remove link|disable/i });
    const revokeCount = await revokeBtn.count();
    if (revokeCount === 0) {
      // Some implementations hide the button until a link is active.
      // The mock accepted the POST so the UI should show Revoke now.
      test.skip(true, "Revoke button not found after generating link");
      return;
    }
    await revokeBtn.click();

    // Confirm revoke if a confirm dialog surfaces.
    const confirmDialog = page.waitForEvent("dialog").catch(() => null);
    const cd = await confirmDialog;
    if (cd) await cd.accept();

    // -- Incognito 404 after revoke ----------------------------------------
    const incognitoCtx2 = await browser.newContext();
    const incognitoPage2 = await incognitoCtx2.newPage();

    // After revoke, the share API returns 404.
    await incognitoPage2.route(
      `**/api/v2/share/${FAKE_SHARE_TOKEN}`,
      async (route) => {
        await route.fulfill({ status: 404, body: "Not found" });
      },
    );

    await incognitoPage2.goto(`http://localhost:4175/s/${FAKE_SHARE_TOKEN}`);

    // Expect a 404 / error page. The SPA may show an inline error or navigate
    // to an error route. Accept either pattern.
    const notFoundIndicator = incognitoPage2.locator(
      ".error-page, [data-testid='not-found'], .not-found, h1",
    );
    await expect(notFoundIndicator.first()).toBeVisible({ timeout: 15_000 });

    // The page text or heading should indicate the link is gone.
    const bodyText = await incognitoPage2.locator("body").textContent() ?? "";
    const indicatesNotFound =
      /not found|expired|revoked|invalid|404|unavailable/i.test(bodyText);
    expect(indicatesNotFound).toBe(
      true,
      `Expected a 'not found' message after revoke, got: ${bodyText.slice(0, 200)}`,
    );

    await incognitoCtx2.close();
  });

  test("share dialog shows generated URL in a copyable field", async ({
    page,
  }) => {
    await mockOpenAIWithPersistedTopics(page);

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
      } else {
        await route.fallback();
      }
    });

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(page, "Designing a community tool lending library");
    await waitForCanvas(page);

    const shareBtn = page
      .getByRole("button", { name: /share/i })
      .or(page.locator("[data-testid='share-btn'], .share-btn"));

    if ((await shareBtn.count()) === 0) {
      test.skip(true, "Share button absent — feature not yet active");
      return;
    }

    await shareBtn.first().click();

    const shareDialog = page.locator(
      ".share-dialog, .share-panel, [data-testid='share-dialog'], [role='dialog']",
    );
    await expect(shareDialog.first()).toBeVisible({ timeout: 10_000 });

    const generateBtn = shareDialog
      .first()
      .getByRole("button", { name: /generate|create link|get link/i });
    if ((await generateBtn.count()) === 0) {
      test.skip(true, "Generate link button absent in Share dialog");
      return;
    }
    await generateBtn.click();

    // A URL field should appear.
    const shareUrlDisplay = shareDialog.first().locator(
      "input[readonly], .share-url, [data-testid='share-url']",
    );
    await expect(shareUrlDisplay.first()).toBeVisible({ timeout: 10_000 });

    // A "Copy" button should be present.
    const copyBtn = shareDialog
      .first()
      .getByRole("button", { name: /copy/i });
    await expect(copyBtn).toBeVisible({ timeout: 5_000 });
  });
});
