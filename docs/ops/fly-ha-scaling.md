# Bumping the backend to 2 machines for HA

How to take the Inspira API from a single machine in `iad` to a two-machine
active/active pair on Fly, with no downtime and a clean rollback.

## Prerequisites

Read these before running the command:

1. **Postgres is the source of truth.** Every request path reads and
   writes through `DATABASE_URL` (Neon in prod). The Fly volume mounted
   at `/data` exists only for local SQLite fallback and per-machine
   transcripts — no two-machine consistency guarantees depend on the
   volume, and losing a machine's volume does not lose user data. If you
   are unsure, confirm your deploy is on Postgres before scaling:

   ```bash
   flyctl ssh console -a inspira-backend -C "env | grep DATABASE_URL"
   ```

   The value must start with `postgresql://` or `postgresql+psycopg://`.
   If it says `sqlite:///...` STOP — scaling to 2 machines with SQLite
   will split-brain the data and you will lose writes.

2. **Migration history matches production.** The migration runner is
   manual (`alembic upgrade head` against the unpooled Neon URL —
   see [deploy-runbook.md](deploy-runbook.md)). Before scaling, confirm
   both machines will boot on the same schema head:

   ```bash
   flyctl ssh console -a inspira-backend -C "alembic current"
   ```

   Should print the same revision ID as `head` in
   `services/alembic/versions/`.

3. **Session cookies survive machine switches.** Inspira uses
   `itsdangerous` signed cookies, not server-side session state, so a
   request being routed to a different machine mid-session is fine as
   long as every machine has the same `SESSION_SECRET`. This is already
   true by construction — `flyctl secrets` replicates across machines.

## The command

```bash
flyctl scale count 2 -a inspira-backend
```

Fly provisions a second machine in `iad` (the primary region) using the
same image as the existing machine. The new machine boots, passes the
`/api/health` check, and starts receiving traffic from the edge. This
takes about 30-60 seconds.

If you want to prefer a specific region for the second machine (the
default is `primary_region = iad` from `fly.toml`):

```bash
flyctl scale count 2 --region iad -a inspira-backend
```

For a multi-region fan-out you can add a second region:

```bash
flyctl scale count 2 --max-per-region 1 --region iad,sjc -a inspira-backend
```

Multi-region requires you to think about Postgres read-replicas; don't
do that until you have steady traffic.

## Verification

```bash
flyctl status -a inspira-backend
```

You should see two rows under `Machines`, both with `STATE = started`
and `CHECKS = 1 passed`.

Tail logs on the combined stream:

```bash
flyctl logs -a inspira-backend
```

The log stream tags each line with a machine ID. You should see both
machine IDs appearing under normal traffic — requests being distributed
across the pair. Look specifically for:

- `INFO planning_studio.api Inspira service starting on 0.0.0.0:4174` from
  each machine ID exactly once.
- Alternating `INFO uvicorn.access ... /api/...` lines tagged with
  different machine IDs.

Smoke the health endpoint a handful of times — Fly's edge does
connection-level load balancing, so curl from your laptop should hit
both machines:

```bash
for i in $(seq 1 10); do
  curl -s -o /dev/null -D - https://api.tryinspira.com/api/health | grep -i fly-machine-id
done
```

You should see at least two distinct `Fly-Machine-Id` headers in the
output of ten requests.

## Rollback

Take the service back to a single machine:

```bash
flyctl scale count 1 -a inspira-backend
```

Fly picks one machine to keep and destroys the other. Both machines
were stateless (Postgres is the data layer), so the one that's
destroyed loses nothing important — `/data` transcripts on that machine
are gone, but those are best-effort and redundant with the DB.

## Cost impact

At launch pricing, a `shared-cpu-1x` / 512MB machine in `iad` runs
about $1.94/mo when always-on, so the second machine costs roughly
**+$2/mo**. The Fly volume you attached to the second machine is
another $0.15/mo/GB (the default is 1GB, so $0.15).

Bumping to 2 machines also changes the cost shape: auto-stop no longer
saves you meaningful money because the machine that gets auto-stopped
just hands its load to the other. If you want true cost parity with a
single-machine deploy, keep `auto_stop_machines = "stop"` and set
`min_machines_running = 1` — one machine always on, the second woken on
demand. That's a middle ground between HA and bill size.

## Gotchas

1. **The Fly volume is per-machine.** Each machine gets its own
   `inspira_data` volume. This is fine because Postgres is the real
   state store, but it means anything you write to `/data` on machine A
   will NOT be visible to machine B. Do not regress into caching
   anything user-visible on the local filesystem — a user's second
   request will often land on the other machine and see a cache miss.

2. **Session transcripts fragment across machines.** The
   `PLANNING_STUDIO_STORAGE_ROOT=/data` path holds per-session markdown
   transcripts written synchronously during a turn. These are not
   replicated between machines. If you ever need to read back a
   transcript, you may need to check both machines. Long-term fix:
   move transcripts to object storage (S3 / R2). Not a scaling blocker
   — the transcripts are secondary to the DB-resident `qna_turns`.

3. **Scheduled cleanup jobs run independently.** See
   [cleanup-jobs.md](cleanup-jobs.md). The cleanup machine is its own
   process group and is not affected by `scale count`.

4. **Graceful shutdown.** Fly sends SIGTERM when a machine is being
   drained. Uvicorn handles SIGTERM cleanly — it drains in-flight
   requests before exiting. There is nothing Inspira-specific to tune
   here; it works by default.
