# Deploy runbook

How a production change moves from a merged PR to live traffic, and what
to do when something needs to move faster or be rolled back.

## GOTCHAS — read first

1. **`fly deploy` does NOT run migrations.** Never merge a PR that
   references new columns without first running `alembic upgrade head`
   against the unpooled Neon URL. The pre-deploy drift check
   (`services/scripts/check_migrations.py`, wired into
   `.github/workflows/deploy.yml`) will BLOCK the deploy if the DB is
   behind, but the check relies on the `DATABASE_URL_PROD` secret — if
   you skip configuring that secret, you lose the safety net.
2. **Cloudflare Pages deploys use npm.** We left pnpm behind in April
   2026 after a stale `pnpm-lock.yaml` stalled the build for 4 hours.
   Do not reintroduce `pnpm-lock.yaml` to `app/` or change the CF Pages
   build command from `npm run build`. See
   [runbook.md](runbook.md) if you're tempted.
3. **Dependabot major-bump PRs are not auto-merged.** Two Vite v8 /
   `@vitejs/plugin-react` v6 bumps slipped through before we started
   running the full build on every PR. The current CI gate
   (`.github/workflows/ci.yml`) fails a dependabot PR whose lockfile
   conflicts with package.json, so breakers no longer reach main on
   auto-merge. Don't disable that gate.
4. **React error #300 is a prod-only bug.** The smoke spec
   (`app/e2e-smoke/landing.smoke.spec.ts`, run in CI as
   `frontend-smoke`) boots `npm run preview` and asserts no console
   errors on the landing page. If the smoke fails on a PR, the fix is
   almost always in the code, not the test — investigate before
   silencing.

## Production topology recap

| Component                    | Host               | Auto-deploy trigger              |
|------------------------------|--------------------|----------------------------------|
| Backend (`services/`)        | Fly.io (`inspira-backend`, region `iad`) | Push to `main` via `.github/workflows/deploy.yml` |
| Frontend (`app/`)            | Cloudflare Pages   | Push to `main` (Pages native)    |
| Database                     | Neon Postgres      | Migrations run manually (see below) |
| Scheduled cleanup            | Fly scheduled machine | See [cleanup-jobs.md](cleanup-jobs.md) |

## Standard flow — normal code change

1. Open a PR from a feature branch.
2. CI runs (`.github/workflows/ci.yml`):
   - `backend` — `pytest -q`
   - `frontend` — `npm ci && npx tsc --noEmit && npm test && npm run build`
   - `frontend-smoke` — preview the built bundle, assert clean load
   - `migrations-postgres` — forward + reversibility against Postgres
   - `docker` — both images build
3. `.github/workflows/e2e.yml` also runs the full Playwright suite.
4. PR is mergeable when every required check is green.
5. Squash-merge to `main`.
6. Deploy pipeline (`.github/workflows/deploy.yml`):
   - `check-migrations` runs `services/scripts/check_migrations.py`
     against `DATABASE_URL_PROD`. If the DB is behind the code, the
     job fails and `deploy-backend` never starts.
   - `deploy-backend` runs `flyctl deploy --remote-only`.
   - Cloudflare Pages deploy fires in parallel via its native GH
     integration (no workflow job here).
7. Backend roll: Fly builds the Docker image, boots a new machine,
   health-checks `/api/health`, swaps edge traffic. ~90-180 seconds.
8. Frontend cutover: CF Pages is roughly simultaneous; the exact
   second a user sees the new bundle depends on their browser cache.

## Changes that require manual steps

### Migrations (alembic)

**The auto-deploy does NOT run migrations.** Running `alembic upgrade head`
from a booting machine would race the old machine still serving traffic;
we do not want that. Migrations must be run manually against the
unpooled Neon URL BEFORE a PR that relies on new columns merges:

```bash
cd services
DATABASE_URL="$DATABASE_URL_UNPOOLED" alembic upgrade head
```

The pre-deploy drift guard will block the deploy if you forget:

```bash
# Run locally before pushing — same check CI will run on deploy.
cd services
DATABASE_URL="$DATABASE_URL_UNPOOLED" python scripts/check_migrations.py
```

Exit codes:
- `0` — DB is at head; safe to deploy.
- `1` — `DATABASE_URL` not set.
- `2` — couldn't connect / inspect DB.
- `3` — DB is behind; run `alembic upgrade head`.
- `4` — DB is ahead; checkout is older than the applied schema. Stop
  and figure out what happened before deploying.

Order of operations for a migration-bearing PR:

1. Open the PR with the migration AND the code change together. CI still
   passes because the migration is additive or the tests use SQLite.
2. **Before merging** — take a manual Neon snapshot
   (see [database-backups.md](database-backups.md)).
3. **Before merging** — run `alembic upgrade head` against production's
   unpooled URL.
4. Verify: `python services/scripts/check_migrations.py` against the
   same URL must exit 0.
5. Merge the PR. Auto-deploy picks up the code; the schema is already
   in place.

For destructive migrations (DROP COLUMN, rename, rewrite data), split
into two PRs:

- PR 1: additive migration (add the new column, backfill).
- PR 2: switch the code to use the new column, then a third PR drops
  the old column once the rollback window passes.

### New Fly secrets

`flyctl secrets set` rolls every machine. Set secrets BEFORE merging code
that depends on them, or set them and merge together — never after.

```bash
flyctl secrets set -a inspira-backend NEW_KEY="..."
```

#### Required secrets (minimum, verified via `fly secrets list -a inspira-backend`)

| Secret                      | Purpose                                 |
|-----------------------------|-----------------------------------------|
| `DATABASE_URL`              | Unpooled Neon Postgres URL (runtime)    |
| `INSPIRA_SESSION_SECRET`    | Cookie signing; rotate breaks sessions  |
| `OPENAI_API_KEY`            | OpenAI adapter (primary + fallback)     |
| `RESEND_API_KEY`            | Transactional email (password reset)    |

#### Required for feature-complete operation

| Secret                      | Purpose                                 |
|-----------------------------|-----------------------------------------|
| `ANTHROPIC_API_KEY`         | Claude Sonnet = frontier tier; without it the frontier tier silently falls back to OpenAI |
| `INSPIRA_BYOK_SECRET`       | Used to encrypt user-supplied API keys at rest. Missing → BYOK flows 500 |

If any of the first four are missing the backend either won't boot or
will 500 on core flows. If either of the feature-complete keys is
missing, the backend boots fine but the corresponding feature is
degraded — add them before you announce the feature externally.

#### Pre-deploy drift-check secret (optional but recommended)

| Secret                      | Purpose                                 |
|-----------------------------|-----------------------------------------|
| `DATABASE_URL_PROD`         | Read-capable unpooled Neon URL used ONLY by `.github/workflows/deploy.yml` to run `alembic current` before deploying. Can be the same value as `DATABASE_URL` — storing it as a separate secret lets you swap it to a read-only role without touching runtime config. |

When absent, the deploy workflow warns and skips the drift check; the
deploy still goes through.

### New Cloudflare Pages environment variables

Set on the CF Pages project under *Settings → Environment variables*
for the Production environment. These do NOT auto-apply to an already-
built deployment; you must trigger a redeploy afterwards.

## Manual redeploy

### Backend (Fly)

```bash
cd services
flyctl deploy --config fly.toml
```

You can also trigger it from GitHub Actions UI — re-run the last
successful `deploy.yml` workflow — if you want the deploy to come from
CI rather than your laptop. The drift check still runs.

### Frontend (CF Pages)

On the CF Pages dashboard, go to the production deployment list and
click *Retry deployment* on the commit you want to re-publish. There
is no CLI equivalent short of a new commit.

#### Force a CF Pages rebuild via empty commit

Sometimes the cleanest option is a fresh build (stale node_modules
cache in CF's build container, env var change that only re-binds on
build, etc):

```bash
git commit --allow-empty -m "chore: force CF Pages rebuild"
git push origin main
```

This re-triggers both the backend deploy and the CF Pages build.

## Rollback

### Backend rollback (Fly)

```bash
flyctl releases -a inspira-backend
```

Lists every release. Roll back to the previous known-good release:

```bash
flyctl releases rollback <release-number> -a inspira-backend
```

This re-deploys the previous image tag. Machines roll in 30-60
seconds. This is safe only if the previous release is schema-compatible
with the current DB; if the bad release included a migration, follow
the schema rollback steps below as well.

### Frontend rollback (CF Pages)

CF Pages keeps a deployment history under *Pages → inspira-app →
Deployments*. Find the last known-good deploy and click *Rollback*.
Takes ~30 seconds to propagate globally.

### Schema rollback

Do NOT use `alembic downgrade` in production unless the migration was
explicitly written to be reversible. Instead:

1. Take a point-in-time Neon branch from BEFORE the bad migration ran.
2. Switch `DATABASE_URL` on Fly to the branch.
3. Roll back the backend code to a version that matches the old schema.

See [database-backups.md](database-backups.md) for the branch mechanics.

### Emergency revert commit

When the wrong thing landed in `main` and you want the fix to go
through the same auto-deploy pipeline:

```bash
git revert <commit-sha>
git push origin main
```

Auto-deploy re-fires on the revert commit, and you are back to the
previous behaviour within a few minutes. Prefer this over `reset --hard`
so the revert stays visible in history.

## Sanity checks after every production deploy

1. `curl -s https://api.tryinspira.com/api/health | jq` — must return
   `{"service":"planning-studio","status":"ok", ...}`.
2. Sign in from `tryinspira.com` and load one real project. If the
   project opens and the topic turn works, the critical path is live.
3. Check Sentry for any new error signatures in the 5 minutes
   post-deploy.
4. Tail `flyctl logs -a inspira-backend` for ~1 minute. Look for any
   500s, any `psycopg.errors.*`, or any Python traceback.

If any of these fail, roll back immediately per the rollback section.
Do not try to fix forward in production.

## Who to page

Until on-call rotation exists, the deploy owner is also the rollback
owner. If you pushed it, you watch it for 10 minutes minimum.
