# Postgres setup (first-time provisioning)

This walkthrough takes you from zero to "Inspira is running against a
managed Postgres" in about 15 minutes. It assumes Neon's free tier as
the default because it has the lowest friction for a single-developer
deploy, but the steps transfer cleanly to Supabase and Fly PG — only
the signup URLs change.

## 1. Sign up for Neon

1. Go to <https://console.neon.tech> and sign in with GitHub.
2. **Create a project.** Use region closest to your deploy host
   (`us-east-2` if you're deploying to Fly `iad`; `us-west-2` if
   you're on Vercel US-West; `eu-central-1` if you're in Europe). The
   DB should be in the same region as the app for sub-5ms latency.
3. **Project name:** `inspira-prod`. This becomes part of the hostname.
4. **Postgres version:** 16. This matches `postgres:16-alpine` used in
   CI and docker-compose — staying on the same major version avoids
   surprises when dumps travel between environments.
5. **Database name:** `inspira`.

Neon creates a default branch named `main` with a primary compute
endpoint. That's your production DB.

## 2. Grab the connection string

From the Neon dashboard → project → **Connection Details**:

- **Connection string (Direct):** copy the URI. It looks like:

  ```
  postgresql://inspira_owner:AbC123@ep-winter-moon-12345.us-east-2.aws.neon.tech/inspira?sslmode=require
  ```

- **Important:** Neon's UI shows both a "pooled" and a "direct"
  connection URL. Use the **direct** one for migrations
  (`scripts/migrate.sh`) — `alembic` opens long transactions and the
  pooler (pgBouncer in transaction mode) can drop them mid-DDL.
  Use the pooled URL in your app's runtime `DATABASE_URL` so Inspira
  benefits from connection reuse.

SQLAlchemy/psycopg expect the driver prefix; the app's URL form is:

```
postgresql+psycopg://inspira_owner:AbC123@ep-winter-moon-12345.us-east-2.aws.neon.tech/inspira?sslmode=require
```

If you paste the Neon URL without the `+psycopg` driver hint, the app
still works (SQLAlchemy picks the default driver), but `psycopg` is
pinned in `pyproject.toml` so specifying it explicitly is the safe
form.

## 3. Store the URL as a deploy secret

The URL is a secret. Never commit it. Set it on your deploy host:

- **Fly:** `fly secrets set DATABASE_URL="postgresql+psycopg://..."`
- **Railway:** Variables tab → add `DATABASE_URL`.
- **Vercel / Netlify** (for SSR adapters): same pattern.
- **Local dev:** add it to `.env` (which is in `.gitignore`). For
  day-to-day dev you probably want to stay on SQLite — leave
  `DATABASE_URL` unset and the app falls back to
  `sqlite:///local/data/planning-studio.sqlite`.

Also set the URL in your local shell **only when you intentionally
want to run against prod Postgres** (e.g. during the initial migration
below). Unset it afterward so you don't accidentally run tests against
prod.

## 4. Run the initial migration from your dev machine

```sh
export DATABASE_URL="postgresql+psycopg://inspira_owner:AbC123@ep-winter-moon-12345.us-east-2.aws.neon.tech/inspira?sslmode=require"
cd /path/to/planning-studio
./services/scripts/migrate.sh
```

The script:

- Verifies `DATABASE_URL` is set.
- Prints the current alembic revision before the upgrade (empty on a
  fresh DB — the `alembic_version` table doesn't exist yet).
- Runs `alembic -c services/alembic.ini upgrade head`.
- Prints the resulting revision (`20260421_0001`).

You should see output like:

```
migrate.sh: using config .../services/alembic.ini
migrate.sh: current revision (before) = <none>
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 20260421_0001, baseline
migrate.sh: current revision (after) = 20260421_0001 (head)
migrate.sh: upgrade complete
```

## 5. Verify tables exist via psql

```sh
psql "$DATABASE_URL" -c '\dt'
```

Expected output (at minimum):

```
             List of relations
 Schema |       Name         | Type  |     Owner
--------+--------------------+-------+----------------
 public | alembic_version    | table | inspira_owner
 public | approval_actions   | table | inspira_owner
 public | artifacts          | table | inspira_owner
 public | audit_log          | table | inspira_owner
 public | consistency_flags  | table | inspira_owner
 public | context_sources    | table | inspira_owner
 public | decisions          | table | inspira_owner
 public | open_questions     | table | inspira_owner
 public | projects           | table | inspira_owner
 public | qna_turns          | table | inspira_owner
 public | relationships      | table | inspira_owner
 public | risks_assumptions  | table | inspira_owner
 public | schema_version     | table | inspira_owner
 public | sessions           | table | inspira_owner
 public | source_references  | table | inspira_owner
 public | summary_versions   | table | inspira_owner
 public | topics             | table | inspira_owner
 public | users              | table | inspira_owner
 public | v2_projects        | table | inspira_owner
```

Quick sanity on the alembic version pin:

```sh
psql "$DATABASE_URL" -c 'SELECT version_num FROM alembic_version;'
#  version_num
# -------------
#  20260421_0001
```

## 6. First application deploy

Now the DB is ready. Trigger a deploy:

- **Fly:** `fly deploy` from `services/`. The healthcheck
  (`/api/health`) returns 200 once the app is connected.
- **Docker / compose in production:** `docker compose up -d --build`
  with `DATABASE_URL` pre-set in the environment. The backend container
  does not run migrations on boot by design — schema changes are an
  explicit operator step. Run `scripts/migrate.sh` from a one-shot
  container or a local shell pointed at prod.

**Smoke test after deploy:**

1. `curl https://your.inspira.host/api/health` → `{"ok": true}`.
2. Sign up a test account through the UI. Confirm a row lands in
   `users`.
3. Create a project. Confirm `v2_projects` has a row. Create a topic;
   confirm `topics` + `qna_turns`.

If any of those fail, check the app logs — a missing table or column
usually means the app image is ahead of the schema. Run
`scripts/migrate.sh` again or roll the deploy back.

## 7. Neon free-tier limits and when to upgrade

Neon's free tier (as of writing) gives you:

- **0.5 GB storage** — plenty for Inspira's first few hundred users.
  Each project averages < 1 MB including all transcripts.
- **191 compute hours/month** — effectively unlimited for a hobby
  deploy because compute auto-suspends after 5 min idle.
- **Point-in-time restore: 7 days** on free tier. That's a nice safety
  net on top of the daily pg_dump backups covered in
  [`backup-restore.md`](backup-restore.md) — do not rely on it as your
  only backup strategy.
- **No read replicas, no autoscaling.**

**Upgrade to Launch (~$19/mo) when you hit any of:**

- Storage approaches 0.4 GB (upgrade before you hit the cap, not
  after — writes start failing once you're over).
- You need more than 7 days of PITR.
- You're hitting compute-hour caps, meaning the instance is actually
  staying warm all month. That's a signal you have real traffic.
- You want connection pooling that's more predictable than the free
  tier's shared pooler.

Migrating from free to Launch is a click in the Neon dashboard — no
DB URL change required, no downtime.

## 8. Alternative providers

If you'd rather not use Neon:

- **Supabase** — same pattern, get the URI from Project Settings →
  Database → Connection string. Use the "Transaction pooler" URL at
  port 6543 for runtime, direct URL at 5432 for migrations.
- **Fly PG** — `fly postgres create`; the URL is attached to the app
  as `DATABASE_URL` automatically. Same `scripts/migrate.sh` call
  works. Fly PG is not a managed service in the Neon sense — you own
  the Postgres and the backups. Pair with the backup script in this
  repo from day one.
- **Railway / Render** — same pattern: get a URL, set it as a secret,
  run `scripts/migrate.sh`.

All of these support `pg_dump` / `pg_restore` so the backup/restore
playbook in [`backup-restore.md`](backup-restore.md) applies
unchanged.

## Related docs

- [`docs/ops/backup-restore.md`](backup-restore.md) — daily ops.
- [`docs/ops/runbook.md`](runbook.md) — on-call reference.
- [`services/alembic/README.md`](../../services/alembic/README.md) —
  migration authoring guide.
