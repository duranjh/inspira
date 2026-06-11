# Common production errors — first-response playbook

The five shapes of failure most likely to hit Inspira in production, and
what to do about each in the first 60 seconds. Deeper incident response
lives in [incident-response.md](incident-response.md) — this doc is the
triage card.

## 1. 500 on any endpoint

**Symptom:** User reports a feature broke, or Sentry fires a new error
signature, or `/api/*` returns `{"detail":"Internal Server Error"}`.

**First look — tail the logs:**

```bash
flyctl logs -a inspira-backend
```

Filter to errors:

```bash
flyctl logs -a inspira-backend | grep -E "ERROR|Traceback"
```

The traceback tells you which module raised. Most new 500s fall into
one of the buckets below — jump to that section if the top frame
matches. Otherwise open Sentry and look at the transaction name /
breadcrumbs for the failing request.

**If the traceback is opaque and reproducing is hard:**

```bash
flyctl ssh console -a inspira-backend
# inside the machine:
python -c "from planning_studio_service.api import create_app; print(create_app())"
```

This shakes out config-time errors that the machine's healthcheck
managed to pass. It's surprisingly common for env-var typos to survive
until exactly the right route gets hit.

## 2. `psycopg.errors.UndefinedTable` (or `UndefinedColumn`)

**Symptom:**

```
psycopg.errors.UndefinedTable: relation "<table>" does not exist
```

or

```
psycopg.errors.UndefinedColumn: column "<col>" of relation "<table>" does not exist
```

**Cause:** A migration didn't apply before the code that uses it went
live. Either someone merged a migration-bearing PR without running
`alembic upgrade head` (see [deploy-runbook.md](deploy-runbook.md)), or
you're pointed at the wrong DB.

**Fix:**

```bash
cd services
DATABASE_URL="$DATABASE_URL_UNPOOLED" alembic current
```

If `alembic current` is not equal to `head`, run the upgrade:

```bash
DATABASE_URL="$DATABASE_URL_UNPOOLED" alembic upgrade head
```

Use the UNPOOLED URL. Pooled (`-pooler`) connections can't hold the
advisory lock alembic needs, and the upgrade will hang.

After the upgrade lands, restart the Fly machines so they pick up the
new schema cleanly:

```bash
flyctl machine restart --app inspira-backend
```

## 3. Stale JS bundle / frontend shows old behaviour

**Symptom:** A user reports "I'm not seeing the new feature", or you
see mismatched frontend/backend contract errors (the frontend sends an
old payload shape the new backend rejects).

**Cause hierarchy, in order of likelihood:**

1. **Browser cache.** The user is holding onto a cached `index.html` that
   points at an old bundle hash. Ask them to hard-refresh
   (Ctrl/Cmd+Shift+R). If that fixes it, move on.

2. **Cloudflare Pages edge cache.** The CF edge can serve stale static
   assets for up to 60 seconds after a new deploy. Normally self-heals.
   Force-purge if you're impatient:

   ```
   Cloudflare Dashboard → inspira-app zone → Caching → Configuration
     → Purge everything
   ```

   Do NOT overuse this — it wipes the cache worldwide and increases
   origin load for the next few minutes. One-tap fix, not a habit.

3. **The CF Pages deploy actually failed.** Check the CF Pages
   dashboard: is the latest commit's deployment in green? If it's
   yellow ("Building...") for more than 5 minutes, it's stuck — cancel
   it and retry the deployment.

## 4. OpenAI rate limit (`429` or `insufficient_quota`)

**Symptom:** Logs show

```
openai.RateLimitError: Error code: 429 - {'error': {'message': 'Rate limit reached...'}}
```

or

```
openai.AuthenticationError: Error code: 401 - ... Incorrect API key
```

**First check:** has the key been rotated or exhausted?

1. Log into the OpenAI dashboard → *Usage* → look at the current rate
   limit consumption for the relevant project.
2. Check billing status — a paid org on its free-tier default limits
   can hit 429s under surprisingly light traffic.

**Fixes, in order:**

- If the quota is exhausted, top up billing in the OpenAI dashboard.
  The circuit breaker (`pybreaker` in `openai_adapter.py`) will trip
  after enough 429s and Inspira will briefly fail fast with 503 —
  intentional, so the service doesn't spin on a hopeless retry loop.
  The breaker closes ~30s after a successful call.

- If the key itself is wrong:

  ```bash
  flyctl secrets set -a inspira-backend OPENAI_API_KEY="sk-new-..."
  ```

  Fly rolls the machines; new key is live in ~60s.

- If OpenAI is genuinely down (rare), the Claude fallback adapter
  should be carrying traffic. Confirm:

  ```bash
  flyctl logs -a inspira-backend | grep -i 'fallback\|anthropic'
  ```

  If neither provider is up, Inspira returns a friendly "try again in a
  minute" message — nothing more to do until one of them recovers.

## 5. Session cookie / "you've been signed out" loops

**Symptom:** Users report they sign in, get redirected, and are
immediately signed out again. Or: every API call returns 401 even
though the cookie is set.

**Cause:** `INSPIRA_COOKIE_SECURE` env-var mismatch. In production the
flag must be `"true"` because the cookie needs `Secure` over HTTPS. If
it's `"false"` in production, the cookie is set without `Secure` and
modern browsers reject it on HTTPS origins.

**Check:**

```bash
flyctl ssh console -a inspira-backend -C "env | grep INSPIRA_COOKIE_SECURE"
```

Expected output: `INSPIRA_COOKIE_SECURE=true`.

**Fix:**

```bash
flyctl secrets set -a inspira-backend INSPIRA_COOKIE_SECURE=true
```

Or in `fly.toml` under `[env]` (already set there in the committed
version — but `flyctl secrets set` overrides `[env]` so the override is
the real source of truth).

**Related gotcha:** `SESSION_SECRET` rotation. If you roll the secret,
every existing cookie becomes invalid and every signed-in user is
forcibly signed out. Rotate only during a maintenance window, and
communicate before you do it:

```bash
flyctl secrets set -a inspira-backend SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

## Escalation

If none of the five patterns above matches and the traceback is in
Inspira's own code, reach for:

1. [incident-response.md](incident-response.md) — the structured
   incident playbook (triage, comms, postmortem).
2. [runbook.md](runbook.md) — longer-form operational reference.
3. Sentry → Issues → find the new signature. Nine times in ten the
   stack trace names the exact module to check.

If the traceback names an external dependency (`anthropic`, `psycopg`,
`openai`, `stripe`), check the provider's status page first. Don't
spend 20 minutes debugging Inspira when `status.openai.com` is red.
