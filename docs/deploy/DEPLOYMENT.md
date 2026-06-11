# Inspira — Production deployment runbook

One-time steps to get `tryinspira.com` serving real traffic. Execute top to bottom.

**Topology:**
- Frontend → Cloudflare Pages at `tryinspira.com` + `www.tryinspira.com`
- Backend → Fly.io at `api.tryinspira.com`
- Database → Neon Postgres (managed, external to Fly)

**Rough time budget:** 90 minutes the first time, mostly waiting on DNS propagation and Stripe verification.

---

## 1. Move DNS to Cloudflare (strongly recommended, 10 min)

GoDaddy's registrar is fine. Their DNS is not — it doesn't support CNAME flattening at the apex (you can't `CNAME tryinspira.com → project.pages.dev`). Using Cloudflare's free DNS sidesteps that and gives you faster resolution + free DDoS protection.

1. Create a free Cloudflare account at <https://cloudflare.com>.
2. Dashboard → **Add a site** → `tryinspira.com`. Select the Free plan.
3. Cloudflare scans your GoDaddy DNS and shows you the records it found. Just accept.
4. Cloudflare gives you two nameserver hostnames (e.g. `lyla.ns.cloudflare.com`, `rob.ns.cloudflare.com`).
5. Go to **GoDaddy → My Products → Domain → DNS → Change Nameservers** → replace GoDaddy's nameservers with the two Cloudflare ones.
6. Wait 5-30 min. Cloudflare sends you a "Site is active" email when propagation is done.

**What this buys you:** CNAME-at-apex support for Cloudflare Pages, free SSL at the edge, faster DNS resolution, no cost.

---

## 2. Sign up for Fly.io, Cloudflare, Neon (5 min each)

All three have free tiers. Sign up for each:

- <https://fly.io/app/sign-up> — needs a credit card on file (for abuse prevention), but you won't be charged on the free allowance
- Cloudflare — done in step 1
- <https://console.neon.tech/sign-up> — no credit card needed

---

## 3. Provision Postgres on Neon (5 min)

1. Neon dashboard → **New Project** → name it `inspira-prod`, region closest to your Fly.io region (pick `us-east-2` if you're staying with Fly's `iad`).
2. Neon shows you a connection string like `postgresql://user:pw@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`. Copy it — you'll paste it in step 5.
3. (Optional) Create a separate database named `inspira` inside the project if you want cleaner naming than the default `neondb`. Adjust the URL accordingly.

---

## 4. Provision the Fly app (10 min)

From your machine:

```bash
# Install flyctl if you don't already have it
curl -L https://fly.io/install.sh | sh    # macOS/Linux
iwr https://fly.io/install.ps1 -useb | iex  # Windows PowerShell

# Log in (opens browser)
flyctl auth login

# Create the app — don't deploy yet
cd services
flyctl apps create inspira-backend     # if "inspira-backend" is taken, pick another and update fly.toml
```

---

## 5. Set backend secrets on Fly (5 min)

All sensitive values live in Fly secrets, not in `fly.toml`:

```bash
flyctl secrets set -a inspira-backend \
  DATABASE_URL="postgresql://user:pw@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require" \
  SESSION_SECRET="$(openssl rand -hex 32)" \
  OPENAI_API_KEY="sk-..." \
  ANTHROPIC_API_KEY="sk-ant-..."    # optional if you don't use Claude yet
```

**Notes:**
- `SESSION_SECRET` rotates every deploy if you re-run the command — keep the same one across deploys or users get logged out.
- Email (SMTP) + Stripe secrets get added later when those providers are wired.

### CORS allowlist

The backend refuses every cross-origin request whose `Origin` header is
not on its allowlist. Configure it via two env vars (set in `fly.toml`
under `[env]` — these are **not** secrets and are safe to commit):

| Variable | Required | Purpose |
| --- | --- | --- |
| `INSPIRA_ALLOWED_ORIGINS` | yes (production) | Comma-separated list of exact origins the SPA loads from. No wildcards, no trailing slashes, scheme included. |
| `INSPIRA_ALLOWED_ORIGIN_REGEX` | no | Anchored regex OR'd with the explicit list. Use to allow a family of preview URLs without enumerating them. |

Recommended production values for `tryinspira.com`:

```
INSPIRA_ALLOWED_ORIGINS = "https://tryinspira.com,https://www.tryinspira.com,http://localhost:4175,http://127.0.0.1:4175"
INSPIRA_ALLOWED_ORIGIN_REGEX = "^https://[a-z0-9-]+\\.inspira-frontend\\.pages\\.dev$"
```

Why each entry:
- `https://tryinspira.com` — apex production frontend.
- `https://www.tryinspira.com` — `www` redirect target; included so the
  preflight succeeds even if a stray link lands a user on `www` first.
- `http://localhost:4175` and `http://127.0.0.1:4175` — local Vite dev
  server hitting a *production-like* backend (rare, but useful when
  reproducing a prod-only CORS bug from a dev box). Drop these if you
  never run the dev frontend against the prod API.
- `INSPIRA_ALLOWED_ORIGIN_REGEX` — Cloudflare Pages mints a unique
  `https://<hash>.inspira-frontend.pages.dev` per preview build. The
  regex matches all of them at once. Replace `inspira-frontend` with
  your actual Pages project name if it differs.

If `INSPIRA_ALLOWED_ORIGINS` is unset, `_assert_production_safe` refuses
to boot in production — by design, so a misconfigured deploy can't
silently fall through to a permissive default.

The middleware itself sends:
- `Access-Control-Allow-Credentials: true` — required for the session
  cookie to flow on cross-origin requests
- `Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS, PATCH`
- `Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With`
- `Access-Control-Max-Age: 3600` — browsers cache the preflight for an
  hour to avoid hammering the API with `OPTIONS`

To roll a new preview origin into the allowlist at runtime without a
redeploy, use Fly secrets (they take precedence over `fly.toml [env]`):

```bash
flyctl secrets set -a inspira-backend \
  INSPIRA_ALLOWED_ORIGINS="https://tryinspira.com,https://www.tryinspira.com,https://staging.tryinspira.com"
```

---

## 6. Run migrations against Neon (2 min)

From your machine, with `DATABASE_URL` pointing at Neon:

```bash
cd services
DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" \
  alembic upgrade head
```

This creates every table the app needs. Idempotent — re-run any time schema changes.

---

## 7. Deploy the backend (3 min)

```bash
cd services
flyctl deploy
```

When it finishes: `flyctl status` shows a machine running. `curl https://inspira-backend.fly.dev/api/health` should return a 200.

---

## 8. Attach the custom `api.tryinspira.com` hostname (5 min)

```bash
flyctl certs add -a inspira-backend api.tryinspira.com
```

Fly tells you to create specific DNS records. In Cloudflare dashboard (tryinspira.com → DNS → Records):

- **CNAME** `api` → `inspira-backend.fly.dev` — Proxied OFF (gray cloud, not orange).

Why proxy off: Fly handles TLS itself; double-proxying through Cloudflare's orange cloud breaks HTTPS. `api` stays DNS-only.

Wait 2-5 min. `flyctl certs show api.tryinspira.com` eventually shows `Certificate status: Ready`. Then `curl https://api.tryinspira.com/api/health` works.

---

## 9. Provision frontend on Cloudflare Pages (10 min)

1. Cloudflare dashboard → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
2. Authorize Cloudflare to see your GitHub repo; select the `planning-studio` repo.
3. Setup:
   - **Project name:** `inspira-frontend`
   - **Production branch:** `main`
   - **Build command:** `npm ci && npm run build`
   - **Build output directory:** `app/dist`
   - **Root directory:** `app`
4. **Environment variables → Production** → add:
   - **Variable name:** `VITE_INSPIRA_API_URL`
   - **Value:** `https://api.tryinspira.com` (no trailing slash)
   - **Environment scope:** Production (repeat for Preview if preview
     deploys should talk to prod; otherwise leave Preview pointing at a
     staging backend).
   - **Type:** plaintext (this is not a secret — it's inlined into the
     JS bundle by Vite at build time).

   > **Critical:** Vite reads this at **build** time, not runtime.
   > Adding or changing the variable requires a **new build** — use
   > *Deployments → Retry deployment* or push a commit. Without it the
   > frontend falls back to relative `/api/*` URLs, which Pages routes
   > to the SPA catch-all and returns `index.html` with HTTP 200, so
   > every backend call appears to "succeed" but returns HTML.

5. Save and deploy. First build takes ~3 min.
6. When done, Cloudflare shows a `inspira-frontend.pages.dev` URL.
   Verify by fetching the main bundle and grepping for the API host:

   ```bash
   curl -s https://tryinspira.com/ | grep -oE 'assets/index-[^\"]+\.js' | head -1
   # → assets/index-<hash>.js
   curl -s https://tryinspira.com/assets/index-<hash>.js \
     | grep -oE 'https://api\.tryinspira\.com'
   # Must print the host at least once. If empty, the env var was not
   # set at build time — fix step 4 and redeploy.
   ```

---

## 10. Attach `tryinspira.com` + `www.tryinspira.com` (5 min)

In Cloudflare Pages → **Custom domains** → **Set up a custom domain**:

1. Add `tryinspira.com` (apex). Cloudflare auto-creates the needed CNAME-flattened record. Accept.
2. Add `www.tryinspira.com`. Same, auto-handled.

DNS propagates in <1 min since you're already on Cloudflare. Visit `https://tryinspira.com` — the app loads.

---

## 11. Create the admin user on prod DB (2 min)

```bash
cd services
DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" \
  python scripts/create_admin_user.py --email you@yourdomain.com --password '<a-good-one>'
```

Skips argon2-cffi trouble on Windows? Run in a clean virtualenv with the service's pyproject installed (`pip install -e .`).

---

## 12. Smoke test (5 min)

- <https://tryinspira.com> loads — kickoff form visible
- Try Map a project as anonymous → auth gate appears
- Sign in with the admin creds → land on kickoff (since admin has 0 projects)
- Map an idea → canvas loads with topics
- Type in canvas composer → planner response
- Open Summary button → panel opens

Anything failing? Check:
- `flyctl logs -a inspira-backend` (backend errors)
- Browser devtools Network tab (frontend API calls, CORS)

---

## 13. Wire GitHub Actions deploy (2 min)

Repo secrets → **Settings → Secrets and variables → Actions → New repository secret**:

- `FLY_API_TOKEN` — output of `flyctl auth token`

From now on, pushes to `main` that touch `services/` auto-deploy the backend. Pages redeploys automatically on every push (via Cloudflare's GitHub integration, no GH Actions secret needed).

---

## 14. Backup & Recovery

Inspira's data of record is the Neon Postgres database. Backups are
**managed by Neon** — there is no nightly job we run ourselves — and
recovery is performed via Neon's point-in-time recovery (PITR), with
on-demand `pg_dump` snapshots as a defensive belt-and-braces.

### Recovery objectives

| Objective | Target | How it's achieved |
|-----------|--------|--------------------|
| **RTO** (recovery time) | ~30 min | Neon PITR restores into a new branch in seconds; the bulk of RTO is updating `DATABASE_URL` on Fly + redeploying. |
| **RPO** (data loss window) | < 1 min | Neon's WAL is continuously archived; PITR can target any second within the retention window. |

### What Neon retains

The retention window depends on the Neon plan:

| Plan | History (PITR) retention |
|------|--------------------------|
| Free | 7 days |
| Launch | 7 days (configurable up to 14) |
| Scale | 30 days |
| Business | 30 days (configurable up to 60) |

Inspira runs on the Free / Launch plan today, which means **anything
older than 7 days cannot be recovered via PITR** — only via a
`pg_dump` snapshot you took manually. Take a manual snapshot before any
risky migration.

### Restore via Neon PITR (preferred — RTO ~30 min)

For the common case (someone ran a destructive `DELETE`, a bad migration
landed, etc.):

1. Open the Neon console → project `inspira-prod`.
2. Sidebar → **Branches** → click the production branch (usually `main`).
3. Click **Restore** in the top-right (or **Branch → Restore from a
   point in time** depending on console version).
4. Pick a target time *just before* the bad event. Neon shows a
   timeline; you can pick to the second.
5. Choose **Restore to a new branch** (`recovery-YYYYMMDD-HHMM` is a
   sane name). Do NOT click "restore in place" until you've verified
   the new branch contains what you want — restoring in place is
   irreversible.
6. Once the new branch is ready, Neon shows a fresh connection string.
   Update Fly:

   ```bash
   flyctl secrets set -a inspira-backend \
     DATABASE_URL="postgresql://...recovery-branch...neon.tech/neondb?sslmode=require"
   ```

   Fly auto-restarts the machine. `flyctl logs -a inspira-backend`
   should show the app coming back up cleanly.
7. Smoke-test the app at <https://tryinspira.com>.
8. When confident, in the Neon console: **promote** the recovery branch
   to be the new primary (or restore in place from the recovery
   branch's tip). Delete the old branch when you're sure.

### Manual logical backup (`pg_dump`)

Use this:
- Before any risky migration or data backfill.
- To keep an off-Neon copy in case of a Neon-side incident that exceeds
  your PITR window.
- To seed a local dev DB from real (anonymised) data.

```bash
cd services
DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" \
  scripts/backup.sh ./backups
# → prints absolute path to ./backups/inspira-YYYYMMDD-HHMMSS.dump.gz
```

The script wraps `pg_dump --format=custom --no-owner --no-acl` and
gzips the output. Custom format is what `pg_restore` expects. Ship the
resulting file to S3 / R2 / wherever you keep cold storage.

### Restoring a `pg_dump` snapshot

Into a **scratch** Neon branch (recommended — never restore directly
into prod over an existing schema):

```bash
cd services
DATABASE_URL="postgresql://...scratch-branch.../neondb?sslmode=require" \
  scripts/restore.sh ./backups/inspira-YYYYMMDD-HHMMSS.dump.gz
```

`restore.sh` interactively prompts before doing anything irreversible.
Set `CONFIRM_RESTORE=yes` only in CI.

After it succeeds, point Fly at the new branch the same way as the PITR
flow above.

### Smoke-testing that backups are restorable

The whole point of backups is they're worthless if they don't restore.
`scripts/test_restore.py` is the recurring proof:

```bash
cd services
python scripts/test_restore.py
```

What it does:

1. Locates the newest `inspira-*.dump[.gz]` under `services/backups/`
   (or `--dump <path>` to point at a specific file).
2. Builds a throwaway scratch SQLite database in a tempdir.
3. Runs `alembic upgrade head` against the scratch DB — proves the
   migration chain still applies cleanly end-to-end.
4. Issues a handful of read-only sample queries against tables that
   every revision must produce (`users`, `v2_projects`, `topics`,
   `shelves`, `project_share_tokens`, `shared_links`,
   `alembic_version`). Any failure aborts with the offending statement.
5. **Optional fidelity mode** — pass `--postgres-url <scratch-url>` (or
   set `TEST_RESTORE_POSTGRES_URL`) and the script also runs the actual
   `pg_restore` of the dump into that database and re-runs the sample
   queries. Use this in CI against a scratch Neon branch — never
   against prod or staging.

Exit codes: `0` pass, `1` arg/file problem, `2` alembic failed,
`3` sample query failed, `4` `pg_restore` failed.

Run this on every dump you ship to cold storage and at least weekly in
CI against a fresh scratch branch.

---

## What's NOT in this runbook (yet)

- **Stripe integration** — NoopBillingProvider is still the stub. When you have Stripe keys, tell me and I'll swap in the real adapter (budget half a day).
- **Transactional email** — Forgot-password emails are queued but the sender isn't wired. Same deal.
- **Error monitoring** — Add Sentry once you have a DSN. `ErrorBoundary.tsx` is pre-wired.
- **Analytics** — You decide (Plausible / PostHog / none).

See `docs/deploy/CHECKLIST.md` (or the table in the main chat) for the full punch-list.
