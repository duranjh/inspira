# Operations Runbook

**Audience:** whoever is on duty (currently: the founder, solo).
**Scope:** day-to-day operations of Inspira, not incident response. For incidents, see `docs/ops/incident-response.md`.
**Last updated:** 2026-04-20

This runbook assumes the current topology: a FastAPI backend running under uvicorn, a managed Postgres database, a static-hosted React frontend, Sentry for error monitoring, and OpenAI and Anthropic for AI. Update it as the architecture evolves.

---

## 1. Topology at a glance

```
          +----------------------+        +-----------------------+
Users --> |  app.tryinspira.com  |        |  tryinspira.com       |
          |  (React + React Flow)|        |  (marketing)          |
          +----------+-----------+        +-----------------------+
                     |
                     | HTTPS (JSON)
                     v
          +----------------------+
          | planning_studio_svc  |
          | FastAPI + uvicorn    |
          +----------+-----------+
                     |
      +--------------+--------------+--------------+
      |              |              |              |
      v              v              v              v
  Managed         OpenAI       Anthropic         Sentry
  Postgres        API          API               (errors)
```

Internal identifiers:

- Repo root: `<repo-root>` (wherever you cloned the repository)
- Backend package: `planning_studio_service`
- Frontend app: `app/`
- Entry points: `python -m planning_studio_service`, `npm --prefix app run dev`

---

## 2. Health endpoints

| Endpoint | Purpose | Expected response |
| --- | --- | --- |
| `GET /healthz` | Liveness. Answers without touching external dependencies. | `200 OK` with `{"status": "ok"}` |
| `GET /readyz` | Readiness. Checks database and critical dependencies. | `200 OK` with `{"status": "ok", "dependencies": {...}}`. On a failed dep, returns `503` and names the failure. |
| `GET /version` | Deployed git SHA and build timestamp. | `200 OK` with `{"sha": "...", "built_at": "..."}` |

*(If any of these endpoints are not yet implemented, add them. Liveness + readiness is the minimum needed for a managed host's health-check probes.)*

Hit them via:

```
curl -fsS https://api.tryinspira.com/healthz
curl -fsS https://api.tryinspira.com/readyz
```

Replace the host with the deploy target once finalized.

---

## 3. Logs and observability

### 3.1 Sentry (application errors)

- Dashboard: *[LINK PLACEHOLDER — https://sentry.io/organizations/inspira/issues]*
- Look for: unhandled exceptions, 5xx spikes, latency regressions, AI provider errors.
- When triaging: filter by `release` tag to see if the issue correlates with a deploy.
- Scrub rule: we strip Authorization headers, cookies, and `password` fields before they reach Sentry. Verify periodically that no secret is leaking into event payloads.

### 3.2 uvicorn logs (stdout on the deploy host)

The backend logs to stdout. Stream them from the deploy host:

```
# On a managed container host (example):
<host-cli> logs --service planning-studio --follow

# Or, on a VM with systemd:
journalctl -u planning-studio.service -f

# Or, inside a local Docker container:
docker compose logs -f services
```

Log format is structured JSON when available, free-form otherwise. Look for `request_id` to correlate a single request across lines, and `X-Request-ID` on responses for end-user bug reports.

### 3.3 Frontend console errors

The frontend sends uncaught errors to the same Sentry project under a different DSN. Check the `frontend` project in Sentry.

### 3.4 Database slow-query log

Managed Postgres typically exposes a slow-query log at the host console. Any query slower than 500 ms during normal load should be reviewed. Add an index or rewrite the query.

---

## 4. Database triage queries

All queries below assume the current v2 schema. Update them when the schema changes.

### 4.1 Stuck kickoffs

A kickoff is "stuck" when it has been pending for more than a few minutes.

```sql
SELECT project_id, user_id, created_at, status
FROM v2_projects
WHERE status = 'kickoff_in_progress'
  AND created_at < now() - interval '10 minutes'
ORDER BY created_at;
```

Action options:
- Inspect the matching Sentry events for the project_id.
- If the underlying AI request died, mark the project `status = 'kickoff_failed'` and notify the user so they can retry.
- If many rows appear, suspect a provider outage; switch provider or degrade gracefully.

### 4.2 Orphan topics

Topics whose `project_id` no longer exists, usually a sign of a cascade-delete bug.

```sql
SELECT t.topic_id, t.project_id, t.created_at
FROM topics t
LEFT JOIN v2_projects p ON p.project_id = t.project_id
WHERE p.project_id IS NULL
ORDER BY t.created_at DESC
LIMIT 50;
```

If any rows appear, file a bug — the foreign-key constraint is not being honored. Do not delete the orphans until you understand how they were created.

### 4.3 Soft-deleted rows ready for purge

Projects or accounts past the 30-day grace period that should be hard-deleted.

```sql
SELECT project_id, user_id, deleted_at
FROM v2_projects
WHERE deleted_at IS NOT NULL
  AND deleted_at < now() - interval '30 days'
ORDER BY deleted_at;
```

```sql
SELECT user_id, email, deleted_at
FROM users
WHERE deleted_at IS NOT NULL
  AND deleted_at < now() - interval '30 days'
ORDER BY deleted_at;
```

Run the purge job (see Section 9 and `docs/legal/gdpr-data-subject-procedure.md`). Verify backups will overwrite within 30 days.

### 4.4 Lock waits / hung transactions

```sql
SELECT pid, state, query_start, wait_event_type, wait_event, left(query, 200) AS query
FROM pg_stat_activity
WHERE state != 'idle'
  AND query_start < now() - interval '30 seconds'
ORDER BY query_start;
```

If a hung transaction is blocking progress, terminate it with `pg_terminate_backend(pid)` — but only after you understand what it is doing.

### 4.5 Active connections

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE state = 'active') AS active,
       count(*) FILTER (WHERE state = 'idle') AS idle
FROM pg_stat_activity;
```

Alert threshold: connections > 80% of the pool. Lower idle by tuning the connection pool. A sudden spike usually signals a stuck query loop.

---

## 5. Rotating secrets

### 5.1 Rotating `OPENAI_API_KEY` without downtime

1. In the OpenAI dashboard, create a new API key. Label it with the date.
2. Update the secret in the deploy host's secret manager under the environment variable `OPENAI_API_KEY`.
3. Trigger a rolling restart of the backend. Confirm at least one new instance is up and healthy before taking any instance down.
4. Verify a kickoff completes end-to-end.
5. Wait 15 minutes for any in-flight requests on the old key to drain.
6. In the OpenAI dashboard, revoke the old key.
7. Check Sentry for any "invalid API key" errors during the cutover; if you see one, roll back.

Same procedure for `ANTHROPIC_API_KEY`.

### 5.2 Rotating `INSPIRA_SESSION_SECRET`

**Warning: rotating this secret invalidates every active session. Every signed-in user will be logged out and will need to sign in again.** Plan this outside peak usage hours and announce it in advance.

1. Generate a new secret (256-bit random hex is fine: `openssl rand -hex 32`).
2. Update the secret under the environment variable `INSPIRA_SESSION_SECRET`.
3. Trigger a rolling restart. Confirm health.
4. Verify sign-in flow works. Users will need to re-authenticate.
5. Optional: send a proactive email to active users if the rotation was forced by a security event, along the lines of:

   > We rotated a security credential as a precaution. You may need to sign in to Inspira again. Your projects are safe.

### 5.3 Rotating the database password

1. Create a second user on the managed Postgres with the same privileges as the application user.
2. Set the new `DATABASE_URL` (with the new user's credentials) in the secret manager.
3. Rolling restart of the backend. Confirm health.
4. Drop the old user only after one hour of clean operation on the new one.

### 5.4 Rotating the Sentry DSN

Low risk. Replace the value, redeploy, and confirm new events land in the expected project.

---

## 6. Deploys

### 6.1 Backend deploy

1. Merge to `main`. CI runs tests (see `services/tests/`).
2. Deploy runs automatically (target: managed container host) — the specifics are configured in `.github/workflows` and the host's deploy hook.
3. After deploy: hit `/healthz` and `/readyz`, then run the smoke-test flow (Section 7).
4. Watch Sentry for the first 10 minutes after deploy.

### 6.2 Frontend deploy

1. Merge to `main`. The static host builds and promotes the new artifact.
2. Hard refresh `app.tryinspira.com`. Verify the login screen renders, a kickoff can start, and a topic detail opens.
3. Check the browser console for errors.

### 6.3 Zero-downtime practice

- Keep backend deploys rolling, not blue/green-stop. A two-instance minimum in production, not one.
- Keep database migrations forward-only. Ship additive schema changes first, backfill, then cut over application code in a later deploy.

---

## 7. Smoke-test flow

Run after every deploy and any rollback. This is the minimum proof that the product works end-to-end.

1. Sign in with a dedicated test account.
2. Create a new project from the dashboard.
3. Run a kickoff with a short prompt. Confirm the canvas renders with at least one topic.
4. Click a topic. Ask a question inside the topic. Confirm the Q&A turn returns.
5. Drag a topic. Refresh the page. Confirm the new position persists.
6. Create an edge between two topics. Delete it.
7. Upload a small attachment. Confirm it appears and can be downloaded.
8. Sign out. Sign back in. Confirm the project is still there.

If any step fails, treat it as a SEV-2 and follow `docs/ops/incident-response.md`.

---

## 8. Adding an index during a hot incident

When a query is suddenly slow and an index will fix it, use Alembic's forward-only style with `CREATE INDEX CONCURRENTLY` to avoid a long write lock. Example:

```python
# services/planning_studio_service/migrations/versions/<timestamp>_add_hot_index.py
from alembic import op

revision = "<timestamp>_add_hot_index"
down_revision = "<previous>"

def upgrade():
    # CONCURRENTLY requires running outside a transaction.
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_topics_project_id_created_at "
        "ON topics (project_id, created_at DESC)"
    )

def downgrade():
    # Forward-only policy: leave the index in place.
    pass
```

Process:

1. Identify the slow query via `pg_stat_statements` or the slow-query log.
2. Write the migration above with the specific index.
3. Run it against staging first if time allows.
4. Apply in production. `CONCURRENTLY` lets writes continue during the build.
5. Verify the query plan now uses the new index (`EXPLAIN ANALYZE`).
6. Merge the migration PR after the incident, so the fix is captured in source control.

---

## 9. Purging soft-deleted rows

Run on a schedule (weekly) and on demand after an erasure request.

1. Run the queries in Section 4.3 to find candidates.
2. For each user ID flagged, execute the cascade described in Section 9 of `docs/legal/gdpr-data-subject-procedure.md`.
3. Delete attachment blobs from object storage.
4. Delete matching Sentry events via the Sentry data-deletion API.
5. Log the job: user IDs purged, row counts, operator, timestamp. Keep the log for 3 years.

---

## 10. Scaling

### 10.1 Horizontal backend scaling

The backend is stateless apart from the database and third-party APIs. To scale:

1. Increase the replica count in the host's service config.
2. Watch connection-pool usage on the database — see Section 4.5.
3. If connections saturate, raise the pool size cautiously or move to a pool in front of Postgres (pgbouncer on the managed tier, or a built-in pooler).
4. Watch AI provider rate limits. OpenAI and Anthropic have per-minute and per-day caps; if you hit them, request a limit increase or shed load.

### 10.2 Database tier upgrades

Managed Postgres upgrades are usually a single control-plane action that takes the database briefly offline (typically 30-120 seconds).

1. Snapshot the database first.
2. Announce a maintenance window on the status page for anything beyond a minor tier bump.
3. Perform the upgrade.
4. Confirm `/readyz` is green and run the smoke-test flow.

### 10.3 Static frontend scaling

The frontend is static — the host scales it automatically. Watch cache-hit ratios if latency regresses. Bust the cache only when needed; aggressive busting is expensive.

### 10.4 When to add a cache

Do not add a cache until Postgres is the bottleneck and you have exhausted index optimization. A cache in front of project reads is a reasonable first move; a cache in front of AI outputs is dangerous because it can leak across users if the cache key is not scoped per Account.

---

## 11. Backups and restores

Managed Postgres handles backups on the host's default schedule (typically daily full + continuous WAL). Verify:

- backups are being taken;
- retention covers at least 30 days;
- a restore has been rehearsed within the last quarter.

Rehearse a restore:

1. Pick a backup from 24 hours ago.
2. Restore it to a disposable database.
3. Run `SELECT count(*) FROM users` and a few other sanity queries.
4. Drop the disposable database.
5. Note the date of the last rehearsal in the incident-response playbook.

---

## 12. Environment variables

Canonical names and purpose:

| Variable | Purpose | Rotation |
| --- | --- | --- |
| `DATABASE_URL` | Postgres connection string | Section 5.3 |
| `OPENAI_API_KEY` | OpenAI API authentication | Section 5.1 |
| `ANTHROPIC_API_KEY` | Anthropic API authentication | Section 5.1 (same procedure) |
| `INSPIRA_SESSION_SECRET` | Signs session cookies | Section 5.2 — invalidates all sessions |
| `SENTRY_DSN` | Error monitor endpoint | Section 5.4 |
| `FRONTEND_ORIGIN` | Allowed CORS origin | Change and redeploy |
| `LOG_LEVEL` | `INFO` / `DEBUG` | Keep `INFO` in production |

Never commit these to the repository. `.env` is for local development only.

---

## 13. Runbook maintenance

Whenever you make a production change, ask: did this invalidate the runbook? If yes, update it in the same commit. An out-of-date runbook is worse than no runbook.

Do a full read-through of both this file and `docs/ops/incident-response.md` once per quarter.
