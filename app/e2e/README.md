# Inspira E2E — Playwright

Browser-level tests that boot a real backend + frontend and drive the UI
the way a human would. Runs against a running system via HTTP; no LLM
calls hit OpenAI — every planner interaction is mocked per-test via
`page.route()`.

## Running locally

From the repo root (or from `app/`):

```bash
# One-time: install the browser binaries (+ system deps on Linux).
npm --prefix app exec -- playwright install --with-deps chromium

# Run the full suite. Playwright will boot both the backend and the
# Vite dev server automatically via the webServer config.
npm --prefix app exec -- playwright test

# Run a single spec.
npm --prefix app exec -- playwright test e2e/kickoff.spec.ts

# Headed (watch the browser run) — useful when writing new tests.
npm --prefix app exec -- playwright test --headed

# Stop on the first failure, open the HTML report after.
npm --prefix app exec -- playwright test -x && npm --prefix app exec -- playwright show-report
```

The config auto-starts:

- **Backend** (`python -m planning_studio_service`) on `:4174` with
  `PLANNING_STUDIO_STORAGE_ROOT` pointing at an OS tempdir — your real
  local DB at `local/data/planning-studio.sqlite` is untouched.
- **Frontend** (`npm run dev`) on `:4175`.

If you're already running one or both locally, `reuseExistingServer` is
true in non-CI mode, so Playwright skips spawning a duplicate.

### Prereqs

- Node 20+ with the bundled `npm`.
- Python 3.12 with `services/` installed (`pip install -e services/`).
  The backend webServer depends on `uvicorn` + `fastapi` being importable.

## Writing a new E2E test

Place new specs under `app/e2e/*.spec.ts`. A typical spec:

```ts
import { expect, test } from "@playwright/test";
import { mockOpenAI, signupAndLogin, fillKickoff, waitForCanvas } from "./helpers";

test("new flow", async ({ page }) => {
  await mockOpenAI(page);            // short-circuit LLM calls
  await page.goto("/");
  await signupAndLogin(page);        // fresh user per run
  await fillKickoff(page, "some 20+ char idea");
  await waitForCanvas(page);
  // … your assertions here …
});
```

**Guidelines:**

- **Always mock LLM calls.** Tests must not depend on `OPENAI_API_KEY`.
  `mockOpenAI()` covers kickoff + topic_turn; add more routes if you
  write a test that touches a new adapter surface.
- **Use unique emails.** `signupAndLogin()` generates one per call so
  specs can't collide on the users-email unique index even though they
  share a DB within a run.
- **Prefer class selectors over brittle text.** The app's stable class
  names (`.topic-node`, `.kickoff`, `.topic-detail`, etc.) are documented
  in `helpers.ts` as the canonical locators. Adding new ones in specs is
  fine — just hoist shared ones into helpers once a second spec needs
  them.
- **Don't share state across specs.** Workers are pinned to 1 and the
  backend DB lives for the whole run, so a spec that signs up user A
  will leave user A in the DB. Every spec should sign up its own user
  (or explicitly opt into a seeded one).

## Known limitations

- **Chromium only.** `playwright.config.ts` exposes only the chromium
  project for now. Safari/WebKit and Firefox projects are a future
  pass — we expect them to mostly work but haven't verified the morph
  animations, React Flow rendering, or clipboard interactions there.
- **Shared backend.** All workers hit one backend process, so tests
  can't run in parallel (workers = 1). Moving to per-worker backends
  would require provisioning separate ports and tempdirs per worker.
- **No visual regression tests.** Playwright has screenshot-diffing
  support but we're not using it yet. The canvas morph animations and
  the dagre auto-layout would make naive screenshot diffs flaky.
- **Mocked planner only.** Happy-path planner envelopes live in
  `helpers.ts` → `fakeKickoffResponse` / `fakeTurnResponse`. We don't
  currently exercise planner-error paths (500, schema failures) via
  E2E — those are covered in backend unit tests.
- **Rename uses `window.prompt`.** `ProjectCard` rename still opens a
  native prompt; specs hook `page.on('dialog')` to supply the new
  value. When we replace the native prompt with `RenameProjectDialog`
  everywhere, update the relevant specs to drive the modal input instead.
- **Account deletion, password change, share links.** Those endpoints
  are not implemented server-side yet; no E2E coverage. Add tests once
  the backend lands them.

## Debugging failing tests

1. `npm --prefix app exec -- playwright test --debug` — opens the Inspector.
2. CI artifact: on a failed PR, the GitHub Actions workflow uploads the
   HTML report as `playwright-report`. Download and open
   `index.html` — it includes screenshots, traces, and the full
   per-step log.
3. Traces are enabled `on-first-retry`. If a spec fails on the first
   try, re-run it (or wait for the automatic retry in CI) to capture
   the trace.
