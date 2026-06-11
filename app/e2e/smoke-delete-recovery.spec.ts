// smoke-delete-recovery.spec.ts — critical-flow smoke: destructive delete
// with confirm dialog + recovery via archive→restore.
//
// Two flows are covered here as a single spec because they share most of
// the setup (sign up + seed two projects). Splitting them would triple
// the runtime for no extra coverage.
//
// Flow A — destructive delete:
//   1. From the projects list, open a project's kebab menu.
//   2. Click "Delete" → DeleteConfirmDialog opens.
//   3. Click the dialog's primary "Delete" button.
//   4. Toast "Project deleted" appears.
//   5. The project disappears from the list.
//
// Flow B — recovery via archive → restore:
//   The Inspira product currently models "recoverable removal" as the
//   archive flow, not as a delete-with-undo toast. The brief asked for
//   delete + recovery; we cover the destructive delete in Flow A and
//   then exercise archive → restore as the recovery path that actually
//   exists in the product. If a true delete-undo toast lands later, this
//   spec should be updated to assert against that instead.
//
//   Steps:
//   1. Open the surviving project's kebab → click "Archive".
//   2. The card disappears from the active list and a toast appears.
//   3. Open the "Archived projects" footer link — the card is there.
//   4. Click the archived card's kebab → click "Restore".
//   5. Card disappears from archive list; a toast appears.
//   6. Back to active list — the project is restored.
//
// LLM mock: persisted-topics variant so the projects actually exist
// in the backend DB (otherwise the post-delete refetch would be
// trivially "all gone").

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAIWithPersistedTopics,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — entry path is via the v3 projects list
// + per-project delete confirm dialog. v4 has /workspaces Kanban with
// bulk-delete on cards; the delete-confirm + archive-restore flow needs
// to be re-anchored on the v4 surfaces. Skipped en bloc.
test.describe.skip("smoke: delete project + recovery via archive", () => {
  test("delete with confirm dialog removes the project; archive→restore brings it back", async ({
    page,
  }) => {
    await mockOpenAIWithPersistedTopics(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // -- Seed two projects so the list has something to operate on -----
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(
      page,
      "Project Alpha — a private journal for daily reflections",
    );
    await waitForCanvas(page);

    // Back to projects list via the "Projects" pill in the top bar.
    await page.getByRole("button", { name: /projects/i }).first().click();
    await expect(page.locator(".projects-list")).toBeVisible();

    // New project pill from the populated header.
    await page
      .locator(".projects-list__new-btn, .projects-list__empty-cta")
      .first()
      .click();
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 10_000 });
    await fillKickoff(
      page,
      "Project Beta — sketching a tiny resource library for designers",
    );
    await waitForCanvas(page);

    // Back to list. Two cards now.
    await page.getByRole("button", { name: /projects/i }).first().click();
    await expect(page.locator(".project-card")).toHaveCount(2, {
      timeout: 10_000,
    });

    // Capture the title of the first card so we can target it precisely.
    const firstCard = page.locator(".project-card").first();
    const firstTitle =
      (await firstCard.locator(".project-card__title").textContent())?.trim() ??
      "";

    // ===================================================================
    // Flow A — destructive delete
    // ===================================================================
    // Open the kebab on the first card. The trigger is always present
    // in the DOM (not hover-only), so a direct click works.
    await firstCard
      .getByRole("button", { name: /project options/i })
      .click();

    // Click "Delete" in the menu — opens the DeleteConfirmDialog.
    await page.getByRole("menuitem", { name: /^delete$/i }).click();

    // The dialog primary button is labeled "Delete" (delete_dialog.action_delete).
    // Scope to a dialog role so we don't re-click the now-hidden menuitem.
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await dialog.getByRole("button", { name: /^delete$/i }).click();

    // Success toast surfaces. The toast variant is "success" with text
    // "Project deleted" (toast.project_deleted).
    await expect(page.locator(".inspira-toast")).toContainText(
      /project deleted/i,
      { timeout: 10_000 },
    );

    // List refetches; only one card remains.
    await expect(page.locator(".project-card")).toHaveCount(1, {
      timeout: 10_000,
    });

    // ===================================================================
    // Flow B — recovery via archive → restore
    // ===================================================================
    const survivor = page.locator(".project-card").first();
    const survivorTitle =
      (await survivor.locator(".project-card__title").textContent())?.trim() ??
      "";
    // Sanity: the deleted card is gone, the survivor is the OTHER project.
    expect(survivorTitle).not.toBe(firstTitle);

    // Open the survivor's kebab → click "Archive".
    await survivor
      .getByRole("button", { name: /project options/i })
      .click();
    await page.getByRole("menuitem", { name: /^archive$/i }).click();

    // Card vanishes locally (locallyArchivedIds set takes effect).
    await expect(page.locator(".project-card")).toHaveCount(0, {
      timeout: 10_000,
    });

    // The archive footer link routes us into the archived view.
    await page
      .getByRole("button", { name: /archived projects/i })
      .first()
      .click();

    // The archived project is here.
    await expect(page.locator(".project-card")).toHaveCount(1, {
      timeout: 10_000,
    });
    const archivedCard = page.locator(".project-card").first();
    await expect(archivedCard).toContainText(survivorTitle);

    // Open its kebab → click "Restore".
    await archivedCard
      .getByRole("button", { name: /project options/i })
      .click();
    await page.getByRole("menuitem", { name: /^restore$/i }).click();

    // The archived list empties; the toast confirms.
    await expect(page.locator(".project-card")).toHaveCount(0, {
      timeout: 10_000,
    });

    // Back to the active view — the survivor has been restored.
    await page
      .getByRole("button", { name: /back to active/i })
      .click();
    await expect(page.locator(".project-card")).toHaveCount(1, {
      timeout: 10_000,
    });
    await expect(page.locator(".project-card").first()).toContainText(
      survivorTitle,
    );
  });
});
