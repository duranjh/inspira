# Backup and restore (Postgres)

This document covers the backup and restore procedures for Inspira's
production Postgres database. It is the canonical operator reference — if
you are paged, start here.

## What is backed up

Everything in the database referenced by `DATABASE_URL`:

- `users` and auth metadata
- `v2_projects`, `topics`, `relationships`, `qna_turns`, `decisions`
- Supporting tables (`consistency_flags`, `summary_versions`,
  `approval_actions`, `audit_log`, etc.)
- The `alembic_version` row so a restore lands on a known schema head

The dump uses `pg_dump --format=custom --no-owner --no-acl`. The custom
format is required for `pg_restore --clean`; `--no-owner` / `--no-acl`
keep the dump portable across managed Postgres providers (Neon, Supabase,
Fly PG) where the default role name differs.

What is **not** in the dump:

- Blob storage, uploaded attachments, Sentry breadcrumbs, and any
  derived artifacts written outside Postgres. If you move those onto
  object storage later, extend this doc with a parallel procedure.
- Secrets. `DATABASE_URL`, `OPENAI_API_KEY`, and cookie secrets live in
  your deploy host's secret store (Fly / Vercel / Railway / etc.) and
  are restored by redeploying, not by `pg_restore`.

## When to back up

**Daily minimum.** Production runs a scheduled backup at 03:00 UTC via
the deploy host's cron or equivalent. A successful run writes a dated
object to the off-site bucket (see below).

**Before a risky migration.** Any alembic revision that drops columns,
renames tables, or rewrites data must be preceded by an on-demand
backup. The PR description should include a line like
`Backup: s3://inspira-backups/prod/inspira-20260421-140522.dump.gz`.

**Before a major deploy.** The deploy pipeline can chain
`scripts/backup.sh` as a pre-deploy step. Keep the output path in the
deploy log so a rollback has a known-good dump to point at.

## Where backups live

Backups are stored on **separate infrastructure from the database**.
This is non-negotiable: if the Postgres instance is compromised or the
cloud account is locked, the backup must still be reachable.

Recommended target: an S3-compatible object store in a different
account/region from the DB. Current recommendation:

- **Bucket:** `s3://inspira-backups/` (or Cloudflare R2 equivalent)
- **Region:** different from the DB region (e.g. DB in `us-east-1`,
  backups in `eu-west-1`).
- **Access:** IAM role with write-only + listing; restore workflow
  assumes a separate read role. Never use the same credentials for
  write-backup and read-restore.
- **Encryption:** server-side encryption enabled, plus an additional
  layer via `gpg --symmetric` if the data is sensitive enough to
  warrant it.

## Retention

**30 days rolling.** Daily dumps older than 30 days are lifecycled out
by the bucket policy. Keep monthly dumps for 12 months if you need
compliance retention — set a prefix like `monthly/` and skip the
lifecycle rule there.

Do not rely on the Postgres provider's own backup (Neon PITR, etc.) as
your only line of defense — a provider-side outage is exactly when you
need an off-site copy.

## Restore testing cadence

**Monthly.** On the first Monday of each month, run a drill:

1. Pick the most recent dump from the bucket.
2. Spin up a throwaway Postgres (locally or on a cheap provider).
3. Run `scripts/restore.sh path/to/dump.dump.gz` against the scratch DB.
4. Open `psql` and sanity-check row counts on `users`, `v2_projects`,
   `topics`, `qna_turns`. Diff against a known-good snapshot.
5. Record the test outcome in the runbook (`docs/ops/runbook.md`).

If the drill fails, treat it as a P1: a broken backup is a broken
product. Triage before the next production write window.

## Exact commands

### Taking a backup

```sh
# From repo root, with DATABASE_URL pointing at prod:
DATABASE_URL="postgresql+psycopg://USER:PASS@HOST:5432/inspira" \
  services/scripts/backup.sh ./backups

# Prints the absolute path of the dump on stdout, e.g.:
# /Users/you/inspira/backups/inspira-20260421-140522.dump.gz
```

The script:

- Accepts SQLAlchemy-style `postgresql+psycopg://...` URLs and
  normalises them for `pg_dump`.
- Defaults to `./backups/` if you pass no arg.
- Timestamps with `date -u +"%Y%m%d-%H%M%S"` so output files sort
  lexicographically and never collide.
- Exits non-zero if `pg_dump` fails — wrap it in cron with proper
  alerting so a silent failure doesn't sit unnoticed.

If you're running it from cron or a CI deploy step, pipe stdout to
your log aggregator so the final dump path is searchable:

```sh
services/scripts/backup.sh /tmp/inspira-backups \
  | tee -a /var/log/inspira-backup.log \
  | aws s3 cp - "s3://inspira-backups/prod/$(date -u +%Y%m%d)/" \
      --recursive --exclude "*" --include "*.dump.gz"
```

(That one-liner is sketchy; in practice use a small wrapper that
uploads the file at the printed path rather than piping stdin.)

### Restoring from a backup

**First, decide where.** Never restore straight into prod without
testing in staging first, unless you are in an incident and the prod
DB is already corrupt.

```sh
# 1. Download the dump
aws s3 cp s3://inspira-backups/prod/20260421/inspira-20260421-140522.dump.gz .

# 2. Restore to STAGING first
DATABASE_URL="postgresql+psycopg://USER:PASS@STAGING_HOST:5432/inspira_staging" \
  services/scripts/restore.sh inspira-20260421-140522.dump.gz

# 3. Smoke-test. Open the staging UI, run a couple of queries.

# 4. If staging looks good, restore to PROD. Script prompts Y/n.
DATABASE_URL="postgresql+psycopg://USER:PASS@PROD_HOST:5432/inspira" \
  services/scripts/restore.sh inspira-20260421-140522.dump.gz
```

The restore script:

- Prompts `continue? [y/N]` before doing anything destructive. Set
  `CONFIRM_RESTORE=yes` to skip the prompt in scripted drills — never
  in a prod shell.
- Uses `pg_restore --clean --if-exists --no-owner --no-acl` so a
  restore into a non-empty DB overwrites every object and no permission
  errors bubble up from drops.
- Handles gzipped or raw `.dump` input; decompresses into a temp file
  that is cleaned up on exit.

## Testing a restore to staging before prod

The most common reason a restore fails at 3am is that the staging DB
was on a different Postgres major version than prod. Keep them in lock
step: if prod is 16.2, staging is 16.2. The `postgres:16-alpine` tag in
CI and compose is the authoritative version reference.

A safe staging-restore procedure:

1. **Snapshot staging first** (`scripts/backup.sh ./staging-backups`)
   so you can revert if the restore surfaces a bug.
2. **Drop and recreate the staging DB** via your provider's console, or
   let `restore.sh`'s `--clean` flag do it in place.
3. **Run `scripts/migrate.sh`** after the restore to ensure the
   `alembic_version` row matches head. A mid-migration backup (taken
   while a deploy was running) can land at a stale revision — this
   catches it.
4. **Smoke-test the app against staging.** Log in, open a project,
   create a topic, confirm autosave.
5. **Diff row counts** against prod using a read-only query. They
   should match the moment the dump was taken.

## Handling a corrupt or truncated dump

`pg_restore` fails loudly on truncation. If you see
`pg_restore: error: could not read from input file`, the dump is
incomplete — do not retry; go back to the previous day's dump. Raise
an incident and root-cause why the bad dump was produced: usually an
out-of-disk on the backup host, or a network interruption mid-upload.

## Related docs

- [`docs/ops/postgres-setup.md`](postgres-setup.md) — first-time DB
  provisioning.
- [`docs/ops/runbook.md`](runbook.md) — on-call overview.
- [`docs/ops/incident-response.md`](incident-response.md) — severity
  classification and comms templates.

## Script permissions note

The three scripts (`backup.sh`, `restore.sh`, `migrate.sh`) are checked
in with the executable bit set (`100755`). If you clone onto a
filesystem that strips the bit (some Windows setups, some archives),
restore it with:

```sh
chmod +x services/scripts/backup.sh services/scripts/restore.sh services/scripts/migrate.sh
```

The repo's `.gitattributes` pins `*.sh` to LF line endings so the
scripts run on Linux even when edited from Windows.
