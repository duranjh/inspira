# Debugging

Common problems during local dev, with the direct fix first and the
deeper explanation second. Windows-specific notes flagged explicitly.

## Backend

### `Address already in use` on port 4174

**Symptom:** `OSError: [Errno 48] Address already in use` (or the
Windows equivalent `OSError: [WinError 10048]`) at boot.

**Fix:** another copy of the backend is still running. Find and stop it.

```powershell
# PowerShell
Get-NetTCPConnection -LocalPort 4174 | Select-Object OwningProcess
Stop-Process -Id <pid>
```

```bash
# bash
lsof -iTCP:4174 -sTCP:LISTEN
kill <pid>
```

Or pick a different port for the new run:

```bash
export PLANNING_STUDIO_PORT=4180   # PowerShell: $env:PLANNING_STUDIO_PORT = "4180"
python -m planning_studio_service
```

If you change the backend port, you must also rebuild the frontend with
`VITE_INSPIRA_API_URL=http://127.0.0.1:4180` (or restart `npm run dev`
with that env var in scope; Vite only reads build-time).

### `OpenAI 401 Unauthorized`

**Symptom:** in the backend log:

```
openai.AuthenticationError: Error code: 401 - {...}
```

**Fix options:**

- Put `OPENAI_API_KEY=sk-proj-...` in `.env` at the repo root. The
  backend's `_env_bootstrap` loads it automatically at start-up.
- Or export it in the current shell:
  ```bash
  export OPENAI_API_KEY="sk-proj-..."   # PowerShell: $env:OPENAI_API_KEY = "sk-proj-..."
  ```
- Or inject it via `docker compose` — `env_file: .env` is already
  wired (`docker-compose.yml`).

**Why it's confusing:** the live tests under `services/tests/test_openai_adapter.py`
skip when `OPENAI_API_KEY` is unset, so you can pass CI without the key
and still fail at runtime. The service logs a clear error, but the
frontend just sees a generic 500 `planner_call_failed` with a
`request_id`. Match the id against the backend log to find the real
exception.

### Backend refuses to start in production

**Symptom:**

```
RuntimeError: Refusing to start — production environment missing required configuration:
  - INSPIRA_SESSION_SECRET is empty or still the dev fallback.
  - ...
```

**Fix:** set the required production env vars per `docs/deploy/env-vars.md`:

- `INSPIRA_SESSION_SECRET` (48-byte random)
- `INSPIRA_ALLOWED_ORIGINS` (comma-separated prod URLs)
- `INSPIRA_COOKIE_SECURE=true`
- `OPENAI_API_KEY`

The guard is at `services/planning_studio_service/api.py:199`
`_assert_production_safe`. It only activates when
`ENVIRONMENT=production` (case-insensitive).

### Alembic "stuck" or refuses to run

**Symptom:** `alembic upgrade head` reports "Target database is not up
to date" or hangs.

**Fix (usual case):** the baseline is idempotent. Running it against
any DB is safe; the `IF NOT EXISTS` clauses short-circuit.

```powershell
Set-Location .\services
alembic upgrade head
```

If alembic complains about a version mismatch because the DB was
bootstrapped via `store.py` before alembic existed, stamp it:

```powershell
alembic stamp head
```

This tells alembic "the baseline is already applied; don't run it
again." Subsequent incremental migrations will apply normally.

**If it actually hangs:** SQLite holds a write lock during a
transaction. Close any open copies of the service and any SQL-browser
app pointing at `local/data/planning-studio.sqlite`, then retry.

### "unable to open database file"

**Symptom:** tests or the service crash with SQLite `OperationalError:
unable to open database file`.

**Common causes:**

- `PLANNING_STUDIO_STORAGE_ROOT` points at a directory that doesn't
  exist and can't be auto-created. Check the path.
- Windows tempfile cleanup ran while a connection was still open.
  `_helpers.py` uses `ignore_cleanup_errors=True` to mask this during
  tests; if your code path doesn't, add the flag.
- You're running multiple instances of the service against the same DB
  file, and one of them crashed mid-write. Delete the DB file (only
  safe in dev) and restart.

### "duplicate column" on boot

**Symptom:** `sqlite3.OperationalError: duplicate column name: user_id`.

**Fix:** this is a benign case the code intentionally catches
(`store.py:702`). If you see it as an unhandled exception, you probably
hit it in a test that bypassed `_ensure_user_id_columns`. Run
`alembic stamp head && alembic upgrade head` to realign.

## Frontend

### "Couldn't reach the backend" banner

**Symptom:** the kickoff screen shows an error:
`"Couldn't reach the backend. Is it running on port 4174?"`.

**Fix:** start the backend first (`python -m planning_studio_service`).
Verify `curl http://127.0.0.1:4174/api/health` returns 200 before
touching the frontend.

If the backend is on a non-default port, restart `npm run dev` with
the env:

```bash
export VITE_INSPIRA_API_URL="http://127.0.0.1:4180"   # PowerShell: $env:VITE_INSPIRA_API_URL = "..."
npm run dev
```

Vite bakes that value into the dev bundle at start-up; changing it
without restart has no effect.

### Session cookie doesn't stick (dev)

**Symptom:** you sign up, get a 201 response with a user, then
subsequent calls return `is_system: true` — the cookie isn't carrying.

**Causes + fixes:**

1. **Missing `credentials: "include"`.** Every fetch must set this for
   cookies to travel cross-origin. The helper in
   `app/src/features/inspira/api.ts:117` sets it; new fetches in your
   own code must too.
2. **`INSPIRA_COOKIE_SECURE=true` set locally.** The browser refuses
   `Secure` cookies over `http://localhost`. Leave it unset (or set to
   `"false"`) for dev.
3. **CORS preflight rejected.** If you added an origin and the backend
   doesn't include it in `INSPIRA_ALLOWED_ORIGINS`, the browser
   silently drops the response cookie. Either include your dev origin
   (`http://127.0.0.1:4175`) or leave `INSPIRA_ALLOWED_ORIGINS` unset
   so the wildcard `*` path kicks in. **Note:** with `allow_origins=["*"]`
   the backend sets `allow_credentials=False`, which means the browser
   will strip the cookie anyway. For cross-origin dev with real
   sessions, set `INSPIRA_ALLOWED_ORIGINS=http://127.0.0.1:4175`.
4. **Different host vs. port across requests.** Cookies are scoped
   per-origin. Hitting `http://localhost:4174` and
   `http://127.0.0.1:4174` treats them as separate origins. Pick one
   and stick with it.

### React Flow node style overrides

**Symptom:** the topic node looks wrong — default React Flow styles
bleeding through, or the custom card not rendering.

**Fix:**

- Make sure `import "reactflow/dist/style.css"` runs before your own
  CSS. It's imported at the top of
  `app/src/features/inspira/ProjectCanvas.tsx:33` specifically so our
  overrides in `App.css` come after and win the cascade.
- Our handle styles (left/right dots) use inline `style={...}` props on
  `Handle` components so they outweigh CSS. If you add a new node
  type, mirror the pattern from `TopicNode.tsx:12`.
- If the card visually disappears on hover, check for a
  `.react-flow__node:hover` rule shadowing your background color.

### PDF attach silently becomes `(binary file, N KB — content not inlined)`

**Symptom:** dropping a PDF into a composer attaches a binary stub, not
the extracted text.

**Causes:**

- **pdfjs worker not reachable.** Check DevTools → Network for a
  `pdf.worker.min.mjs` request. A 404 means the Vite asset pipeline
  didn't rewrite the worker URL. Hard-reload the page with the
  browser cache bypassed.
- **Encrypted or corrupt PDF.** `file_extract.ts:175` catches the
  exception and falls back to the stub with a note appended. Open the
  PDF in a separate viewer to confirm it's readable.
- **Older browser without `import.meta.url`.** pdfjs falls back to an
  in-thread sync worker, which is slower but works. Check the console
  for `could not configure pdfjs worker URL`.

### Vite dev server recompile stalls

**Symptom:** save a file, the HMR indicator spins forever.

**Fix:**

- Check the dev-server console for a TypeScript error. Vite prints the
  error but doesn't always bubble it to the browser overlay.
- Nuke `.vite` cache: `npx vite --force` (or delete `node_modules/.vite`).
- If a large dependency changed, re-install: `npm ci`.

## Sentry

### Sentry DSN not firing

**Symptom:** production errors aren't showing up in Sentry.

**Checks:**

1. Is `SENTRY_DSN` set on the backend service? It's read at app
   construction (`api.py:178`), so a runtime-set env var won't
   take effect — restart the service.
2. Is `ENVIRONMENT` set to something meaningful
   (`production` / `staging` / etc.)? The Sentry project filters by
   environment.
3. Does the Sentry init log appear?
   `[planning_studio.api] INFO — Sentry initialized`
4. Frontend: we do NOT import the Sentry JS SDK. The ErrorBoundary
   looks for `window.Sentry?.captureException` (loose ambient). If you
   want frontend Sentry, add an `<script>` tag or install the SDK and
   wire `Sentry.init` from `main.tsx`.

### `SENTRY_TRACES_SAMPLE_RATE` too low

At `0.1` (default), only 10% of requests ship a performance span.
Bump to `0.5` in staging to see more data, or `1.0` for a focused
debug window. Expensive in production — leave at 0.1 for steady state.

## Tests

### Tests pass locally, fail in CI

**Usual causes:**

- **Different Python.** CI uses 3.12; you might be on 3.11. Check your
  shell: `python --version`.
- **Case-sensitive paths.** Linux CI distinguishes `FileName.tsx` from
  `filename.tsx`; Windows and macOS are case-insensitive by default.
  Match import casing exactly.
- **Clock skew.** `now_timestamp()` (`store.py:14`) is second-precision.
  A test that creates two rows in the same second relies on the
  `rowid DESC` tiebreaker in `latest_summary_version` — if you add a
  new similar query, include the same fallback.
- **Leaky env vars.** Tests mutate `os.environ["PLANNING_STUDIO_STORAGE_ROOT"]`
  (`_helpers.py:60`). If you ran them in a shell that expected a
  specific storage root, your manual checks afterwards point at the
  wrong DB. Unset or reset in your shell after test runs.

### `test_openai_adapter` live tests aren't running

They're intentionally gated on `OPENAI_API_KEY` being set. Set the key
in the shell (not in `.env`, because unittest doesn't walk up to
`.env` unless `ensure_loaded()` ran first — it does, via
`_helpers.py:45`, but only for tests that import `_helpers`. The
adapter live tests import it too, so `.env` should work).

If you want to force-skip them:

```powershell
Remove-Item env:OPENAI_API_KEY
python -m unittest tests.test_openai_adapter -v
```

### Flaky tempdir cleanup on Windows

`tempfile.TemporaryDirectory` sometimes can't delete the tempdir because
SQLite still has the file open. `_helpers.py` uses
`ignore_cleanup_errors=True` for exactly this case. If you see
`PermissionError: [WinError 32]` in a test, check that your test class
takes the fixture via `make_test_app` rather than rolling its own
tempdir.

## Circuit breaker tripped

**Symptom:** every kickoff returns
`500 planner_call_failed` immediately, with the backend log saying
`CircuitBreakerError`.

**Why:** 5 consecutive transient failures tripped the pybreaker
(`openai_adapter.py:282`). It stays open for 60 seconds.

**Fix:** wait 60 seconds. If the underlying reason was a real OpenAI
outage, it'll clear. If it was a bad payload on your side, fix that
first. The breaker is per-process — restarting the service resets the
state.

## Last resort: verbose logging

```bash
export LOG_LEVEL=debug   # PowerShell: $env:LOG_LEVEL = "debug"
python -m planning_studio_service
```

Emits uvicorn's per-request logs plus INFO-level logs from our modules.
Pair with a HAR-style capture in the browser DevTools Network tab for
the full round-trip picture.
