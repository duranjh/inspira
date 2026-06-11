# Local Development Setup

Step-by-step for cloning the repo, setting up the toolchain, running
the backend and frontend, and confirming the planner loop works. Windows
PowerShell instructions first (this is the primary dev platform), with
macOS/Linux bash equivalents where the commands differ.

## Prerequisites

Install these before anything else. Versions are what CI uses; minor
drift is usually fine.

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or 3.12 | `python --version` |
| Node.js | 20 | `node --version` |
| npm | 10+ (bundled with Node 20) | `npm --version` |
| Git | any recent | `git --version` |
| OpenAI API key | any plan with credit | `sk-proj-...` |

Optional:

- Docker Desktop (for `docker compose up --build`).
- Rust toolchain (only if you'll build the Tauri desktop bundle).

## 1. Clone the repo

### PowerShell (Windows)

```powershell
git clone https://github.com/<your-fork>/planning-studio.git
Set-Location .\planning-studio
```

### bash (macOS/Linux)

```bash
git clone https://github.com/<your-fork>/planning-studio.git
cd planning-studio
```

## 2. Configure the environment

Copy the example `.env` and fill in at least `OPENAI_API_KEY`:

### PowerShell

```powershell
Copy-Item .env.example .env
# open .env in your editor, paste your OpenAI key
```

### bash

```bash
cp .env.example .env
# edit .env
```

Minimum contents:

```
OPENAI_API_KEY=sk-proj-your-actual-key
```

Nothing else is required for local dev. The backend reads `.env` on
start-up via `python-dotenv` (`services/planning_studio_service/_env_bootstrap.py`).

## 3. Install backend dependencies

### PowerShell

```powershell
Set-Location .\services
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e ".[dev]"
Set-Location ..
```

Note the quoting: PowerShell's parser would interpret unquoted `[dev]`
as an array literal. Wrap the whole extras-selector in double quotes.

### bash

```bash
cd services
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e '.[dev]'
cd ..
```

This installs FastAPI, uvicorn, pydantic, itsdangerous, argon2-cffi,
slowapi, pybreaker, openai, anthropic, sqlalchemy, alembic, psycopg,
sentry-sdk, and test-only extras (pytest). See `services/pyproject.toml`
for pins.

## 4. Run database migrations

```powershell
# PowerShell
Set-Location .\services
alembic upgrade head
Set-Location ..
```

```bash
# bash
(cd services && alembic upgrade head)
```

This creates `local/data/planning-studio.sqlite` with the baseline
schema. Re-running is safe — every migration uses `CREATE TABLE IF NOT
EXISTS`. If you prefer to let the service auto-bootstrap on first boot,
you can skip this step; the `store.py` path creates the same tables via
the same idempotent DDL.

## 5. Start the backend

### PowerShell

```powershell
Set-Location .\services
python -m planning_studio_service
# binds to 127.0.0.1:4174
```

Leave this running. For auto-reload during development:

```powershell
$env:UVICORN_RELOAD = "true"
python -m planning_studio_service
```

### bash

```bash
cd services
python -m planning_studio_service
```

With auto-reload:

```bash
UVICORN_RELOAD=true python -m planning_studio_service
```

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:4174/api/health
# or
curl http://127.0.0.1:4174/api/health
```

Expected:
```json
{"service":"planning-studio","status":"ok","generated_at":"..."}
```

## 6. Install frontend dependencies

In a **new terminal** (leave the backend running):

### PowerShell

```powershell
Set-Location .\app
npm ci
```

### bash

```bash
cd app
npm ci
```

`npm ci` enforces the checked-in `package-lock.json` so you get the
same React / Vite / React Flow versions CI uses.

## 7. Start the frontend

```powershell
# from app/
npm run dev
```

Vite listens on `http://127.0.0.1:4175` (`app/vite.config.ts:30`).

Open the URL in your browser. You should see:

- The Inspira kickoff screen (warm editorial look, cream paper,
  serif display).
- No console errors.
- A `GET /api/auth/me` → 200 with `"is_system": true` in the Network
  tab (you're the system user until you sign up).

## 8. Smoke test end-to-end

In the UI:

1. Click the user menu (top right) if signed-in UI is visible —
   otherwise the current release auto-uses the system fallback. The
   auth UI for signup/login is pending wiring at the top level; until
   then, use the HTTP endpoints directly if you want a real user.
2. Type a kickoff idea in the textarea: "I'm planning a small outdoor
   wine festival for 200 people in July."
3. Submit. The loading screen reads "Mapping your idea…".
4. Within ~10 seconds, the canvas loads with 5-10 topics auto-laid
   out and connected by dotted relationships.
5. Double-click a topic card. The topic detail view morph-opens
   from the card position with a Q&A composer.
6. Type an answer to the planner's opening question. The planner
   responds with a follow-up question + suggested responses.

If any step hangs or errors, check the backend terminal for the stack
trace, then see `docs/dev/debugging.md` for common fixes.

## 9. (Optional) Run the test suite

### Backend

```powershell
Set-Location .\services
python -m unittest discover -s tests -v
```

Expected: all tests pass. See `docs/dev/testing.md` for details.

### Frontend

```powershell
Set-Location .\app
npx tsc --noEmit
npm run build
```

The TypeScript pass is the main frontend gate — there's no Vitest suite
today.

## 10. (Optional) Docker Compose

If you'd rather run the whole stack containerized:

```powershell
docker compose up --build
```

- Frontend: `http://localhost:8080`
- Backend: `http://localhost:4174`
- SQLite lands in `./local/data/`

The Docker flow bakes `VITE_INSPIRA_API_URL=http://localhost:4174` into
the frontend image at build time — rebuild with a different build-arg
if your backend is reachable at a different URL.

## 11. (Optional) Tauri desktop

```powershell
Set-Location .\app
npm run tauri:dev
```

Requires the Rust toolchain. The shared frontend runs inside a Tauri
window; the backend still needs to be running separately on
`127.0.0.1:4174`.

## Windows-specific gotchas

- **PowerShell 5.1 quoting**: `pip install -e .[dev]` fails; use
  `pip install -e ".[dev]"`.
- **Virtual environment activation script** is `.ps1` on Windows, not
  `source .../activate`. Run `.\.venv\Scripts\Activate.ps1`.
- **Path separators**: Python and Node tooling happily accept `/`. Git
  Bash shells inherit bash separator conventions; cmd/PowerShell
  accepts either but examples in this repo's READMEs use `\` in the
  native shell and `/` in cross-platform commentary.
- **`&&` pipeline chain** is not available in Windows PowerShell 5.1.
  Use `;` or `if ($?) { next_command }` instead.
- **SQLite file locks**: Windows can hold the SQLite file open longer
  than unix does. If you delete `local/data/planning-studio.sqlite`
  while the backend is still running, you may need to Ctrl-C the
  service first.

## Directory layout you now have

```
planning-studio/
  .env                             # your local secrets (gitignored)
  local/
    data/
      planning-studio.sqlite       # your dev DB (created on first boot)
      sessions/                    # v1 transcript files (created on use)
  services/
    .venv/                         # your backend venv
    planning_studio_service/       # backend source
  app/
    node_modules/                  # frontend deps
    src/                           # frontend source
    dist/                          # built bundle (populated on npm run build)
```

The gitignored paths are: `.env`, `local/`, `services/.venv/`,
`app/node_modules/`, `app/dist/`.
