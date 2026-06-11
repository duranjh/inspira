# Scheduled cleanup jobs

Three idempotent sweeps keep Inspira's Postgres from accumulating stale
state. They live in `services/planning_studio_service/cleanup_jobs.py` and
are designed to run out-of-process on a Fly scheduled machine, not inside
the API's lifespan — cron-style scheduling avoids the "what if the task
never fires" failure mode an `asyncio.create_task` would have when the
primary machine sleeps.

## What the jobs do

| Job                              | Target table             | Action           | Default threshold |
|----------------------------------|--------------------------|------------------|-------------------|
| `prune_expired_sessions`         | `password_reset_tokens`  | `DELETE`         | `expires_at <= now` |
| `prune_abandoned_anonymous_accounts` | (no-op)              | Logs skip        | n/a               |
| `prune_stale_share_tokens`       | `shared_links`           | `UPDATE revoked_at` | `last_viewed_at IS NULL AND created_at <= now - 90 days` |

`password_reset_tokens` is the only table in the schema with a real
`expires_at` column, which is why it's what the "expired sessions" sweep
targets; auth session cookies are stateless (itsdangerous) and therefore
nothing to prune.

The anonymous-accounts job is currently a documented no-op because
`is_system` is a single shared user (`user-system`) rather than one row
per anonymous visitor. If/when true anonymous signup lands, fill in the
body of `prune_abandoned_anonymous_accounts` in `cleanup_jobs.py`.

Share tokens are **revoked** (not deleted) so `view_count` history
survives the sweep. Already-revoked rows are not re-touched.

## Running locally

```bash
cd services
python -m planning_studio_service.cleanup_jobs
```

Counts go to stdout as a single JSON line:

```json
{"sessions": 0, "anon_accounts": 0, "share_tokens": 0}
```

Human-readable INFO logs go to stderr. Exit code is `0` when every job
ran to completion (even if zero rows were touched) and `1` when any job
raised — the failing job reports `-1` in the JSON for alerting.

## Running on Fly (production)

The job runs as a scheduled Fly machine that uses the same image as the
API. There are two choices:

### Option A: `fly machine run --schedule` (recommended)

A one-off machine provisioned with the built image, triggered by Fly's
scheduler. No changes to `fly.toml` are needed because the scheduled
machine is orthogonal to the `app` process group.

```bash
flyctl machine run registry.fly.io/inspira-backend:deployment-<sha> \
  --app inspira-backend \
  --region iad \
  --schedule daily \
  --restart no \
  --entrypoint "" \
  --command "python -m planning_studio_service.cleanup_jobs"
```

Replace `deployment-<sha>` with the image tag of your most recent deploy
(check `flyctl releases --app inspira-backend`). Fly accepts `hourly`,
`daily`, `weekly`, `monthly` for `--schedule`. Pick `daily` for this.

Re-run the command after every deploy that changes `cleanup_jobs.py` or
its dependencies so the scheduled machine picks up the new code — Fly
does not automatically update the schedule's image tag.

### Option B: Fly Machines REST API

If you'd rather not re-run the CLI after every deploy, write a thin
GitHub Actions job that hits the Machines API and sets the schedule's
image to `latest`. Not worth it until we're deploying multiple times a
week; the `--schedule daily` command is fine for launch.

## Verification

```bash
flyctl machine list --app inspira-backend
```

Look for a machine whose `PROCESS GROUP` is empty (scheduled machines
aren't part of the main process group) and whose `STATE` is `stopped` —
that's the cron-like machine between invocations. The machine starts,
runs the CLI, prints the JSON counts to its logs, exits zero, and is
torn down by Fly.

To inspect the most recent run:

```bash
flyctl logs --app inspira-backend | grep cleanup_jobs
```

You should see three INFO lines per run, one per job, each with a
`removed=` / `revoked=` count.

## Rollback / kill-switch

To disable the schedule while keeping the machine definition:

```bash
flyctl machine update <machine-id> --schedule none
```

To tear it down entirely:

```bash
flyctl machine destroy <machine-id>
```

The job is fully idempotent and safe to re-run, so there is no data-level
rollback to worry about. A bad deploy that corrupts the CLI only costs
you a cron cycle.

## Runbook snippets for on-call

- **"Nightly cleanup went red"** → `flyctl logs --app inspira-backend | grep cleanup_jobs`. The failing job prints a traceback via `logger.exception`. Check which key in the JSON is `-1`; that's the job that raised.
- **"Share-tokens page shows revoked links that should still be live"** → a user viewed the link but `last_viewed_at` wasn't recorded (best-effort `touch_share_link` swallows errors). Cross-check `view_count > 0` on the revoked row; if so, the sweep over-reached and the default threshold should be bumped from 90 to 180.
