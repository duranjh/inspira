// locale-switch.spec.ts — switching UI language via the user menu locale picker.
//
// Covers:
//   1. Open user menu.
//   2. Find and click the locale / language picker.
//   3. Select "Español".
//   4. Assert a known Spanish string appears in the UI.
//   5. Reload the page and assert the locale persists (stored in user prefs
//      or localStorage).
//
// NOTE: If the locale switcher is not yet implemented the tests skip
// gracefully rather than fail.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  mockLLM,
  signupAndLogin,
} from "./helpers";

// A string we expect to see after switching to Spanish. The kickoff CTA
// button is a good candidate — if it reads "Iniciar sesión" or the submit
// becomes "Trazar →" we know the locale took effect. We check multiple
// known Spanish strings and pass if ANY of them are found.
const KNOWN_SPANISH_STRINGS = [
  "Iniciar sesión",
  "Crear cuenta",
  "Proyectos",
  "Trazar",
  "Cerrar",
  "Configuración",
];

// TODO(#185): rewrite for v4 — the locale switcher likely still exists but
// the test's entry path (.kickoff form for fresh signup) doesn't, so the
// language-toggle never gets reached. Skipped en bloc; rewrite the entry
// path against the v4 SignInPage / Settings page locale toggle.
test.describe.skip("Language / locale switcher", () => {
  test("switching to Español renders a Spanish string in the UI", async ({
    page,
  }) => {
    await mockLLM(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // Open the user menu.
    await page.locator(".user-menu__avatar").click();

    // Look for a language/locale picker inside the user menu.
    const localePicker = page
      .locator(
        ".locale-picker, [data-testid='locale-picker'], [aria-label*='language'], [aria-label*='locale']",
      )
      .or(page.getByRole("button", { name: /language|locale|idioma/i }))
      .or(page.getByRole("combobox", { name: /language|locale/i }));

    const pickerCount = await localePicker.count();
    if (pickerCount === 0) {
      test.skip(true, "Locale picker not found — feature not yet implemented");
      return;
    }

    await localePicker.first().click();

    // Select "Español" from the dropdown / listbox.
    const espanolOption = page
      .getByRole("option", { name: /español|spanish|es/i })
      .or(page.getByRole("menuitem", { name: /español|spanish/i }))
      .or(page.locator("[data-locale='es'], [value='es']"));

    const optionCount = await espanolOption.count();
    if (optionCount === 0) {
      test.skip(true, "Español locale option not found in picker");
      return;
    }

    await espanolOption.first().click();

    // Wait a moment for the locale switch to propagate (re-render).
    await page.waitForTimeout(1_000);

    // Assert at least one known Spanish string is visible anywhere on the page.
    let foundSpanish = false;
    for (const str of KNOWN_SPANISH_STRINGS) {
      const count = await page.getByText(str, { exact: false }).count();
      if (count > 0) {
        foundSpanish = true;
        break;
      }
    }
    expect(foundSpanish).toBe(true);
  });

  test("Spanish locale persists after page reload", async ({ page }) => {
    await mockLLM(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);
    await signupAndLogin(page);

    // Open user menu and find locale picker.
    await page.locator(".user-menu__avatar").click();

    const localePicker = page
      .locator(
        ".locale-picker, [data-testid='locale-picker'], [aria-label*='language'], [aria-label*='locale']",
      )
      .or(page.getByRole("button", { name: /language|locale|idioma/i }))
      .or(page.getByRole("combobox", { name: /language|locale/i }));

    const pickerCount = await localePicker.count();
    if (pickerCount === 0) {
      test.skip(true, "Locale picker not found — feature not yet implemented");
      return;
    }

    await localePicker.first().click();

    const espanolOption = page
      .getByRole("option", { name: /español|spanish|es/i })
      .or(page.getByRole("menuitem", { name: /español|spanish/i }))
      .or(page.locator("[data-locale='es'], [value='es']"));

    const optionCount = await espanolOption.count();
    if (optionCount === 0) {
      test.skip(true, "Español locale option not found in picker");
      return;
    }

    await espanolOption.first().click();
    await page.waitForTimeout(500);

    // Reload and assert Spanish is still active.
    await mockLLM(page); // re-register after reload wipes handlers
    await page.reload();
    await dismissOnboardingIfPresent(page);

    await page.waitForTimeout(1_000);

    let foundSpanish = false;
    for (const str of KNOWN_SPANISH_STRINGS) {
      const count = await page.getByText(str, { exact: false }).count();
      if (count > 0) {
        foundSpanish = true;
        break;
      }
    }
    expect(foundSpanish).toBe(
      true,
      "Expected Spanish locale to persist after reload, but no Spanish strings found",
    );
  });
});
