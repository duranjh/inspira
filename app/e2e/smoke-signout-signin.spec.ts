// smoke-signout-signin.spec.ts — critical-flow smoke: sign out, then
// sign back in to the same account.
//
// Flow under test:
//   1. Sign up + create one tiny project (so the account has something
//      to land on after re-login).
//   2. Open the user menu → click "Log out".
//   3. handleLogout clears the cookie and reloads the page; the app
//      bootstraps as the system / anonymous user (kickoff or onboarding).
//   4. Sign back in via the same UserMenu → AuthPanel login form.
//   5. Land on the projects list (a returning user with >= 1 project
//      goes there, not kickoff).
//
// Smoke intent: prove the auth round-trip works end-to-end. Token
// refresh, session expiry, and error paths live in their own specs.
//
// LLM mock: kickoff is intercepted so the seed project doesn't fan
// out to OpenAI.

import { expect, test } from "@playwright/test";

import {
  dismissOnboardingIfPresent,
  fillKickoff,
  mockOpenAIWithPersistedTopics,
  signInAs,
  signupAndLogin,
  waitForCanvas,
} from "./helpers";

// TODO(#185): rewrite for v4 — sign-out is now on the Kanban top-bar
// "Sign out" pill (per #145) and the post-signin landing is /workspaces,
// not the v3 projects list. The whole flow needs to be re-anchored on
// the v4 surfaces. Skipped en bloc.
test.describe.skip("smoke: sign out + sign in", () => {
  test("user logs out, comes back, and lands on their projects list", async ({
    page,
  }) => {
    // mockOpenAIWithPersistedTopics writes real backend rows for the
    // project + topics, so on re-login the project actually exists in
    // the DB and shows up on the projects list.
    await mockOpenAIWithPersistedTopics(page);

    await page.goto("/");
    await dismissOnboardingIfPresent(page);

    // Sign up + remember the credentials so we can sign back in later.
    const { email, password } = await signupAndLogin(page);

    // Seed one project so the user has something to land on.
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 15_000 });
    await fillKickoff(
      page,
      "Sketching a small workshop series about creative routines",
    );
    await waitForCanvas(page);

    // -- Sign out -------------------------------------------------------
    // handleLogout posts /api/auth/logout then window.location.reload().
    // After the reload the cookie is gone and the app boots as the
    // system fallback user.
    await page.locator(".user-menu__avatar").click();
    await page.getByRole("button", { name: /log out/i }).click();

    // Wait for the post-logout landing. For a system user with zero
    // projects the app routes to kickoff; the .loading state is the
    // brief bootstrapping placeholder. Either is a valid "logged-out"
    // signal — we settle on kickoff being visible as the final state.
    await expect(page.locator(".kickoff, .loading")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator(".kickoff")).toBeVisible({ timeout: 20_000 });

    // -- Sign back in ---------------------------------------------------
    await signInAs(page, email, password);

    // Returning user with one project. The app refetches and routes to
    // either projects_list (because projects.length > 0) or directly
    // back into the canvas of the most-recent project. We accept
    // either as a successful sign-in landing.
    await expect(
      page.locator(".projects-list, .react-flow"),
    ).toBeVisible({ timeout: 20_000 });
  });
});
