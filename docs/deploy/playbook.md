# Deploy Playbook

Step-by-step for getting Inspira running on a cloud target. Covers both
**Fly.io** and **Railway** as primary options. SQLite on a persistent
volume is the default storage; Postgres via `DATABASE_URL` is supported
when you are ready.

## Before you start

- [ ] Read `docs/deploy/env-vars.md` for the full env var contract.
- [ ] Rotate any `OPENAI_API_KEY` that has been in your local `.env`.
      Never commit real keys, and rotate any key that has ever touched
      a tracked file.
- [ ] Generate a fresh `INSPIRA_SESSION_SECRET`:
      ```bash
      python -c "import secrets; print(secrets.token_urlsafe(48))"
      ```
      Store the output in your platform's secret store. Do NOT commit.
- [ ] Decide on your public hostnames for the frontend and backend.
      Both must be served over HTTPS in production.
- [ ] Confirm the backend Docker image builds locally:
      ```bash
      docker build -f services/Dockerfile -t inspira-backend services/
      ```

## Option A: Fly.io

Fly.io deploys each service as a separate Fly app. The frontend and
backend get their own app names, their own TLS certs, and their own
scaling rules. The SQLite file lives on a Fly volume mounted at `/data`
inside the backend container.

### 1. Install and log in

```bash
curl -L https://fly.io/install.sh | sh
flyctl auth login
```

### 2. Create the backend app

From the repo root:

```bash
flyctl launch \
  --name inspira-backend \
  --dockerfile services/Dockerfile \
  --no-deploy \
  --internal-port 4174 \
  --region iad
```

Edit the generated `fly.toml`:

- Set `[build.dockerfile]` to `services/Dockerfile` if Fly guessed wrong.
- Set `[env]` entries for non-secret defaults:
  ```toml
  [env]
  PLANNING_STUDIO_HOST = "0.0.0.0"
  PLANNING_STUDIO_PORT = "4174"
  PLANNING_STUDIO_STORAGE_ROOT = "/data"
  ENVIRONMENT = "production"
  INSPIRA_COOKIE_SECURE = "true"
  INSPIRA_RATE_LIMIT = "120/minute"
  ```

### 3. Create a persistent volume for SQLite

```bash
flyctl volumes create inspira_data --size 1 --region iad --app inspira-backend
```

In `fly.toml`:

```toml
[mounts]
source = "inspira_data"
destination = "/data"
```

### 4. Set secrets

```bash
flyctl secrets set \
  OPENAI_API_KEY=sk-... \
  INSPIRA_SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))") \
  INSPIRA_ALLOWED_ORIGINS=https://tryinspira.com,https://www.tryinspira.com \
  --app inspira-backend
```

Optional:

```bash
flyctl secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  SENTRY_DSN=https://... \
  --app inspira-backend
```

### 5. Run migrations

```bash
flyctl ssh console --app inspira-backend
# inside the container:
alembic -c services/alembic.ini upgrade head
exit
```

Migrations are idempotent (`CREATE TABLE IF NOT EXISTS` everywhere). If
the service has already booted and `store.py` populated the schema, a
plain `alembic stamp head` also works.

### 6. Deploy

```bash
flyctl deploy --app inspira-backend
```

### 7. Build and deploy the frontend

The frontend bundle needs to know the backend URL at **build time**
because Vite inlines `import.meta.env.VITE_INSPIRA_API_URL` into the
bundle.

```bash
cd app
flyctl launch \
  --name inspira-frontend \
  --dockerfile Dockerfile \
  --no-deploy \
  --internal-port 80 \
  --region iad
```

Edit `app/fly.toml`:

```toml
[build]
  dockerfile = "Dockerfile"

[build.args]
  VITE_INSPIRA_API_URL = "https://inspira-backend.fly.dev"
```

Then:

```bash
flyctl deploy --app inspira-frontend
```

### 8. Attach custom domains

```bash
flyctl certs create tryinspira.com --app inspira-frontend
flyctl certs create www.tryinspira.com --app inspira-frontend
flyctl certs create api.tryinspira.com --app inspira-backend
```

Point your DNS CNAMEs at the matching `*.fly.dev` hostnames per Fly's
instructions.

## Option B: Railway

Railway is simpler for a single-contributor setup — one project, two
services, managed Postgres if you want it. SQLite on a Railway volume
also works.

### 1. Create a Railway project

```bash
npm i -g @railway/cli
railway login
railway init --name inspira
```

### 2. Add the backend service

From the repo root:

```bash
railway add --service backend
```

Then in the Railway dashboard for the `backend` service:

- **Source**: connect the GitHub repo, set **root directory** to
  `services/` and **Dockerfile path** to `Dockerfile`.
- **Environment variables**: (see `docs/deploy/env-vars.md`)
  - `OPENAI_API_KEY`
  - `INSPIRA_SESSION_SECRET` (fresh 48-byte token)
  - `INSPIRA_ALLOWED_ORIGINS` (your frontend URL)
  - `INSPIRA_COOKIE_SECURE=true`
  - `ENVIRONMENT=production`
  - `PLANNING_STUDIO_HOST=0.0.0.0`
  - `PLANNING_STUDIO_PORT=${{PORT}}` (Railway injects `$PORT`)
  - `PLANNING_STUDIO_STORAGE_ROOT=/data`
- **Volume**: mount a 1 GB volume at `/data`.
- **Generate Domain**: use the Railway button, or attach a custom
  domain.

### 3. Add the frontend service

```bash
railway add --service frontend
```

- **Source**: same repo, root directory `app/`, Dockerfile path
  `Dockerfile`.
- **Build arg**: `VITE_INSPIRA_API_URL = <backend-URL-from-step-2>`.
  Railway exposes build args as service settings → Variables → Build
  variables.
- **Start command** stays the Dockerfile default (nginx).

### 4. Run migrations

```bash
railway run --service backend -- alembic -c services/alembic.ini upgrade head
```

### 5. Smoke test

```bash
curl https://<backend-url>/api/health
# expect: {"service":"planning-studio","status":"ok","generated_at":"..."}
```

## Optional: Switch to Postgres

Set `DATABASE_URL` as a secret on the backend:

```bash
# Fly
flyctl secrets set DATABASE_URL="postgresql+psycopg://user:pass@host:5432/inspira" --app inspira-backend
# Railway — use the Plugin Postgres → pass the generated URL through
```

Then run:

```bash
alembic -c services/alembic.ini upgrade head
```

against the Postgres URL. The SQLite file at `/data/planning-studio.sqlite`
becomes unused — delete the volume entry in your deploy config if you no
longer need it.

No code change is required: `ServiceConfig.database_url`
(`services/planning_studio_service/config.py:26`) returns `DATABASE_URL`
when set, and falls back to the SQLite file otherwise.

## Smoke Test Checklist

After a deploy, run through:

- [ ] `curl https://api.../api/health` → 200 with
      `{service, status, generated_at}`. Response must NOT contain
      `db_path` / `storage_root` (reconnaissance leak).
- [ ] Visit the frontend URL → the kickoff form loads.
- [ ] Sign up with a new email → 201, you land on the canvas view.
- [ ] Submit a kickoff idea → planner returns topics inside ~10s.
- [ ] Drag a topic card → position persists across reload.
- [ ] Open a topic → Q&A thread renders.
- [ ] Ask a turn → planner responds with a new question.
- [ ] Sign out → cookie cleared, `GET /api/auth/me` returns the system
      user (`is_system: true`).
- [ ] Check Sentry (if configured) for any errors logged during the
      walkthrough.

## Rollback

### Fly.io

List releases:

```bash
flyctl releases --app inspira-backend
```

Roll back to a specific version:

```bash
flyctl releases rollback <version> --app inspira-backend
```

Fly keeps a rolling window of images; pinning a previous version redeploys
from cache.

### Railway

Each deploy shows up in the service's "Deployments" tab. Click the prior
deploy, then **Rollback to this deployment**. DB migrations are not
rolled back automatically — if a broken release included a forward
migration, you must either roll the migration back manually
(`alembic downgrade -1`) or release a fix-forward.

### Migration rollback

For schema-only issues:

```bash
# Fly
flyctl ssh console --app inspira-backend
alembic -c services/alembic.ini downgrade -1

# Railway
railway run --service backend -- alembic -c services/alembic.ini downgrade -1
```

The baseline migration (`20260421_0001_baseline.py`) drops every table
on downgrade — only safe on a non-production DB. For incremental
migrations going forward, write explicit `downgrade()` bodies.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and every PR:

- Backend `python -m unittest discover -s tests -v`.
- Frontend `tsc --noEmit` and `npm run build`.
- Docker image builds for both services.

This repo does not ship a production deploy workflow; deploys run
manually via `flyctl` (see `docs/ops/deploy-runbook.md`). If you want
push-to-deploy automation, add your own workflow — wire up the Fly /
Railway token as a repo secret and trigger on `push: tags: v*` or on a
manual `workflow_dispatch`.

## Observability hooks

- **Sentry**: set `SENTRY_DSN` as a secret. The backend auto-wraps the
  FastAPI app via `_maybe_init_sentry` (`api.py:172`). No code change is
  required. `SENTRY_TRACES_SAMPLE_RATE` defaults to `"0.1"`.
- **Request logs**: set `LOG_LEVEL=debug` on the backend env for verbose
  uvicorn output. Default is `info`.
- **Rate-limit events**: slowapi returns 429 with
  `{"error": "rate_limited", "detail": "..."}`; hook your platform's
  log-based alerting on that status.
- **Token-budget events**: 429 with
  `{"error": "daily_token_budget_exhausted", ...}`. Alert threshold:
  one per user per day is normal traffic; bursts across users are a
  signal.
