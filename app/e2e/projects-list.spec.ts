// projects-list.spec.ts — the Projects grid view.
//
// Covers:
//   1. Sign in → create two projects (both kickoffs mocked).
//   2. Click "Projects" in the top bar → assert two ProjectCards visible.
//   3. Click a card → assert the canvas opens with its topics.
//   4. Back to list → open the card menu → rename → assert card updates.
//
// Uses mockOpenAIWithPersistedTopics so the backend actually has rows for
// each project. Without that, the second project's canvas would try to
// list topics for a project_id the DB doesn't know about.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAIWithPersistedTopics,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — v3 had a single-user ProjectsListPage; v4
// replaced this with the Kanban-by-workspace surface at /workspaces.
// `ProjectsListPage` is still mounted as a fallback (per #130) but
// unreachable on the v4 partner journey. Skipped en bloc.
test.describe.skip("Projects list flow", () => {
  test("create two projects, switch between them, rename one", async ({
    page,
  }) => {
    await mockOpenAIWithPersistedTopics(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // -- First project ---------------------------------------------------
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(
      page,
      "First project about launching a literary podcast",
    );
    await waitForCanvas(page);

    // Go back to projects list via the "Projects" pill in the top bar.
    await page.getByRole("button", { name: /projects/i }).first().click();
    await expect(page.locator(".projects-list")).toBeVisible();

    // One project on the list so far.
    await expect(page.locator(".project-card")).toHaveCount(1);

    // -- Second project -------------------------------------------------
    // Header "New project" pill (the button in the populated header).
    await page
      .locator(".projects-list__new-btn, .projects-list__empty-cta")
      .first()
      .click();

    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 10_000 });
    await fillKickoff(page, "Second project about sustainable design studios");
    await waitForCanvas(page);

    // Back to the list again. Two cards now.
    await page.getByRole("button", { name: /projects/i }).first().click();
    await expect(page.locator(".project-card")).toHaveCount(2, {
      timeout: 10_000,
    });

    // -- Click a card → canvas opens -------------------------------------
    // Pick the alphabetically-first card by its <h3> title — both projects
    // land on the canvas identically, so any card works.
    const firstCard = page.locator(".project-card").first();
    const firstTitle = (await firstCard.locator(".project-card__title").textContent())?.trim() ?? "";
    await firstCard.click();
    await waitForCanvas(page);
    // The top bar's active-project chip shows the project title.
    await expect(page.locator(".project-switcher__title")).toContainText(
      firstTitle,
    );

    // -- Back to list → rename --------------------------------------------
    await page.getByRole("button", { name: /projects/i }).first().click();
    await expect(page.locator(".projects-list")).toBeVisible();

    // The rename flow on this page opens the parent-handled
    // RenameProjectDialog via the kebab menu → "Rename…" item. The kebab
    // is hover-revealed but always present in the DOM; click it directly.
    const targetCard = page
      .locator(".project-card")
      .filter({ hasText: firstTitle })
      .first();
    await targetCard
      .getByRole("button", { name: /project options/i })
      .click();

    // Native window.prompt is how ProjectCard does rename today (see the
    // component's comment: "v1: native prompt"). Hook page.on('dialog')
    // to supply the new name BEFORE we trigger the prompt.
    const newTitle = "Renamed project for E2E";
    const dialogPromise = page.waitForEvent("dialog");
    await page.getByRole("menuitem", { name: /rename/i }).click();
    const dialog = await dialogPromise;
    await dialog.accept(newTitle);

    // Card title refreshes. The list refetches from the backend after a
    // successful rename.
    await expect(page.locator(".project-card__title")).toContainText(newTitle, {
      timeout: 10_000,
    });
  });
});
