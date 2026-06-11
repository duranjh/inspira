// shelves-crud.spec.ts — full shelf lifecycle: create, drag a project into it,
// rename, and delete (project falls back to "Unfiled").
//
// Covers:
//   1. Sign in with an existing project on the list.
//   2. Click "New shelf" → name it "Work".
//   3. Drag a project card into the shelf.
//   4. Assert project appears inside the shelf.
//   5. Rename shelf to "Personal".
//   6. Assert shelf header updates.
//   7. Delete the shelf → project appears in "Unfiled" section.
//
// Drag uses Playwright's dragTo helper (wraps mouse down → move → up).
// Shelf interactions go through the projects-list page.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAIWithPersistedTopics,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — shelves were a v3 organizational feature
// (group projects into shelves on the single-user projects list). v4
// replaced shelves with multi-user workspaces. Skipped en bloc; likely
// candidate for outright removal in #185.
test.describe.skip("Shelf CRUD", () => {
  test("create shelf 'Work', drag project in, rename to 'Personal', delete shelf", async ({
    page,
  }) => {
    // We need a real persisted project so the card shows up in the list.
    await mockOpenAIWithPersistedTopics(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // Create the first project.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(page, "A project about urban beekeeping in the city");
    await waitForCanvas(page);

    // Navigate to the projects list.
    await page.getByRole("button", { name: /projects/i }).first().click();
    const projectsList = page.locator(".projects-list");
    await expect(projectsList).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".project-card")).toHaveCount(1, {
      timeout: 10_000,
    });

    // -- Create shelf "Work" -----------------------------------------------
    const newShelfBtn = page.getByRole("button", { name: /new shelf/i });

    const shelfBtnCount = await newShelfBtn.count();
    if (shelfBtnCount === 0) {
      test.skip(true, "Shelf feature not yet implemented — 'New shelf' button absent");
      return;
    }

    await newShelfBtn.click();

    // The shelf creation flow: either a dialog prompt or an inline editable
    // name field. Handle both.
    const shelfNameDialog = page.waitForEvent("dialog").catch(() => null);
    const inlineInput = page.locator(
      ".shelf-name-input, input[placeholder*='shelf'], input[placeholder*='Shelf']",
    );

    const dialog = await shelfNameDialog;
    if (dialog) {
      await dialog.accept("Work");
    } else {
      await expect(inlineInput).toBeVisible({ timeout: 5_000 });
      await inlineInput.fill("Work");
      await page.keyboard.press("Enter");
    }

    // Shelf header "Work" should appear.
    const workShelf = page.locator(".shelf, [data-testid='shelf']").filter({
      hasText: "Work",
    });
    await expect(workShelf).toBeVisible({ timeout: 10_000 });

    // -- Drag project into the shelf ---------------------------------------
    const projectCard = page.locator(".project-card").first();
    await expect(projectCard).toBeVisible();

    const shelfDropZone = workShelf.locator(
      ".shelf__drop-zone, .shelf__body, .shelf-drop",
    );
    const dropZoneCount = await shelfDropZone.count();

    if (dropZoneCount > 0) {
      // Use dragTo if a distinct drop zone exists.
      await projectCard.dragTo(shelfDropZone.first());
    } else {
      // Fall back to dragging onto the shelf container itself.
      await projectCard.dragTo(workShelf);
    }

    // After the drop, the project card should appear inside the Work shelf.
    await expect(workShelf.locator(".project-card")).toHaveCount(1, {
      timeout: 10_000,
    });

    // -- Rename shelf to "Personal" ----------------------------------------
    const shelfOptions = workShelf.getByRole("button", {
      name: /shelf options|rename|menu/i,
    });
    await shelfOptions.click();
    await page.getByRole("menuitem", { name: /rename/i }).click();

    const renameDialog = page.waitForEvent("dialog").catch(() => null);
    const renameInput = page.locator(
      ".shelf-name-input, input[placeholder*='shelf'], input[placeholder*='Shelf']",
    );

    const rd = await renameDialog;
    if (rd) {
      await rd.accept("Personal");
    } else {
      await expect(renameInput).toBeVisible({ timeout: 5_000 });
      await renameInput.fill("Personal");
      await page.keyboard.press("Enter");
    }

    const personalShelf = page.locator(".shelf, [data-testid='shelf']").filter({
      hasText: "Personal",
    });
    await expect(personalShelf).toBeVisible({ timeout: 10_000 });

    // -- Delete shelf → project falls back to Unfiled ----------------------
    const deleteBtn = personalShelf.getByRole("button", {
      name: /shelf options|delete|menu/i,
    });
    await deleteBtn.click();
    await page.getByRole("menuitem", { name: /delete/i }).click();

    // Confirm deletion if a confirm dialog appears.
    const confirmDialog = page.waitForEvent("dialog").catch(() => null);
    const cd = await confirmDialog;
    if (cd) await cd.accept();

    // Shelf "Personal" should be gone.
    await expect(personalShelf).toHaveCount(0, { timeout: 10_000 });

    // The project card should now appear under "Unfiled" (or the root list
    // without a shelf header).
    const unfiled = page.locator(
      ".shelf--unfiled, [data-testid='shelf-unfiled'], .projects-list__unfiled",
    );
    // Either the card is in an explicit "Unfiled" section, or just visible
    // in the root list (implementations differ). Accept either.
    const cardVisible = await page.locator(".project-card").isVisible();
    expect(cardVisible).toBe(true);

    if ((await unfiled.count()) > 0) {
      await expect(unfiled.locator(".project-card")).toHaveCount(1, {
        timeout: 10_000,
      });
    }
  });
});
