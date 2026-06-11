# Database backups (Neon)

Inspira runs on Neon Postgres. Backups are primarily handled by Neon's
built-in point-in-time recovery (PITR); this document covers the cadence,
how to take an on-demand manual snapshot, and the restore procedure for
both rehearsal (to a recovery branch) and the real thing (cutting over).

See also: [backup-restore.md](backup-restore.md) for the underlying
`pg_dump` / `pg_restore` commands — that doc is provider-agnostic. This
doc is the Neon-specific playbook.

## Cadence

Neon captures continuous WAL and retains it according to your plan:

- **Free plan:** 7 days of PITR.
- **Scale plan:** 30 days of PITR.

Inspira pre-launch should be on **Scale, 30 days retained**. After 90
days of steady-state traffic, drop to **Scale, 7 days retained** to save
~$15/mo — anything older than a week should come from your own off-site
dumps (see below). Adjust on your Neon project dashboard under *Branches
→ Retention*.

No cron is required for the retention window itself — Neon does it.

## Off-site dumps

PITR keeps you safe against ACCIDENTAL deletes and schema mistakes, but
not against Neon losing your account, billing lapses, or region-wide
outages. For that, take nightly `pg_dump` snapshots to S3:

```bash
# Example: cron or GitHub Actions job
pg_dump --format=custom --no-owner --no-acl "$DATABASE_URL_UNPOOLED" \
  | gzip -9 \
  > "inspira-$(date -u +%Y%m%d-%H%M%S).dump.gz"
aws s3 cp inspira-*.dump.gz s3://inspira-backups/prod/
```

**Retention recommendation:** 30 days rolling on S3 for pre-launch (so
you can restore a week-old user who realised they deleted something
important), 7 days once steady-state.

## Manual snapshot (Neon CLI)

Before a risky migration or a destructive admin action, take a point-in-
time branch so you have a targeted restore-point you can name:

```bash
# Install once: npm install -g neonctl
neonctl auth

neonctl branches create \
  --project-id <your-project-id> \
  --parent main \
  --name pre-migration-$(date -u +%Y%m%d-%H%M%S)
```

This creates a Neon branch pinned to the current WAL position. Neon
bills branches at a small per-GB rate; delete the branch after the
migration lands successfully with:

```bash
neonctl branches delete --project-id <project-id> <branch-id>
```

## Rehearsal restore (recommended before every real restore)

Never restore straight into `main`. Always rehearse on a branch first.

1. Create a recovery branch pinned to the time you want to restore to:

   ```bash
   neonctl branches create \
     --project-id <project-id> \
     --parent main \
     --name recovery-rehearsal \
     --parent-timestamp "2026-04-21T13:05:00Z"
   ```

   `--parent-timestamp` is the wall-clock moment you want the branch to
   point at. Neon replays WAL to that point; the branch is read/write
   once it's ready (usually 30-60 seconds).

2. Grab the branch's connection string:

   ```bash
   neonctl connection-string recovery-rehearsal --project-id <project-id>
   ```

3. Smoke-test against the branch URL:

   ```bash
   DATABASE_URL="<branch-connection-string>" python -c "
   from planning_studio_service.store import PlanningStudioStore
   from planning_studio_service.config import load_config
   s = PlanningStudioStore(load_config())
   with s._connect() as c:
       n = c.execute('SELECT COUNT(*) AS n FROM v2_projects WHERE deleted_at IS NULL').fetchone()['n']
       print(f'{n} live projects at restore point')
   "
   ```

4. If the rehearsal looks good, you have two options:

   - **Cut over to the branch.** Update `DATABASE_URL` on Fly to the
     branch's connection string, then deploy. This is fastest (no data
     copy) but leaves `main` behind as a dead branch until you clean
     it up.
   - **Restore into `main`.** Dump the recovery branch and restore into
     `main`. More traditional, more downtime. Only necessary if
     external systems reference `main` by name.

## Full restore procedure (production cutover)

For a real outage where you need to point Inspira at a restored branch:

1. Identify the target timestamp. Prefer the last moment known-good
   *before* the problem occurred. If uncertain, pick a timestamp 5
   minutes before the earliest symptom.

2. Create the recovery branch per the rehearsal section above, named
   `recovery-prod-<date>`.

3. Rehearse against the branch URL for ~5 minutes with basic
   read-heavy smoke tests. Confirm `alembic current` on the branch
   matches the schema head your backend code expects.

4. Flip `DATABASE_URL` on Fly to the branch's connection string:

   ```bash
   flyctl secrets set -a inspira-backend \
     DATABASE_URL="<branch-pooled-connection-string>" \
     DATABASE_URL_UNPOOLED="<branch-unpooled-connection-string>"
   ```

   `flyctl secrets set` rolls the running machines so every live
   process sees the new URL within ~60 seconds.

5. Verify from a client:

   ```bash
   curl -s https://api.tryinspira.com/api/health | jq
   ```

6. Within 7 days, promote the recovery branch to `main` via Neon's
   dashboard ("Promote branch" → replaces `main`'s WAL stream with the
   branch's). Until you promote, the old `main` is still billable.

### Downtime estimate

- Detect-to-decision: human-limited, typically 5-15 minutes.
- Branch creation: 30-60 seconds.
- Smoke test: 5 minutes.
- `flyctl secrets set` roll: 60 seconds.
- Neon branch promotion (async, can happen later): ~1 minute.

**Total from decision-to-cutover:** ~10 minutes. The actual user-visible
downtime is just the rolled machines' restart window — about 60 seconds
if you do it serially (one at a time); instantaneous if you have two
machines (see [fly-ha-scaling.md](fly-ha-scaling.md)).

## What `DATABASE_URL` should look like

Pooled connection string for normal traffic (the `-pooler` suffix):

```
postgresql://inspira:<password>@ep-xxx-yyy-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
```

Unpooled connection string for migrations (`alembic upgrade head`):

```
postgresql://inspira:<password>@ep-xxx-yyy.us-east-2.aws.neon.tech/neondb?sslmode=require
```

On Fly, keep both:

- `DATABASE_URL` — the pooled one, used at runtime.
- `DATABASE_URL_UNPOOLED` — the unpooled one, used for manual migrations.

The store supports both via the same code path; the distinction matters
only for migrations (pooled connections can't hold the advisory lock
alembic needs).

## Testing a restore on a recovery branch

A quick sanity check you can run in CI (or before a scary migration):

```bash
# 1. Create a throwaway branch from right now.
neonctl branches create \
  --project-id <project-id> \
  --parent main \
  --name ci-restore-test-$(date +%s)

# 2. Run alembic against it.
DATABASE_URL=<branch-url> alembic current

# 3. Run the full pytest suite (hits Postgres if DATABASE_URL is PG).
DATABASE_URL=<branch-url> pytest services/tests/

# 4. Destroy the branch.
neonctl branches delete ci-restore-test-...
```
