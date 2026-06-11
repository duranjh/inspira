# Monitoring

**Audience:** whoever is on duty.
**Goal:** catch production problems before users report them.
**Companion docs:** `docs/ops/runbook.md` (daily ops),
`docs/ops/incident-response.md` (on-call playbook),
`docs/ops/sla-targets.md` (internal targets),
`docs/status-page/README.md` (public status page).

This document describes **what** to monitor, **where** the signal comes
from, and **which thresholds** should page or warn. Specific provider
setup (Sentry, BetterStack, future DataDog) is in section 4 onward.

---

## 1. What to monitor

Grouped by blast radius — top of the list kills the product, bottom is
housekeeping.

| Signal | Source | Notes |
| --- | --- | --- |
| **Backend uptime** (`/api/health` 200 OK) | External pinger (BetterStack) | First line of "is it up." 1-minute interval. |
| **Readiness** (`/readyz` 200 OK) | External pinger | Database reachable + critical deps healthy. 1-minute interval. |
| **5xx error rate** | MetricsCollector `snapshot().requests.error_rate_last_1h` + Sentry issue rate | Page on >1% over 5 min. |
| **LLM latency (P95)** | MetricsCollector `llm_latency_ms.p95` | Warn on >30s over 10 min. Users give up well before this. |
| **LLM failure rate** | MetricsCollector `llm_calls.success_rate_last_24h` | Warn on <99% success sustained >1h. |
| **Token-budget burn** | MetricsCollector `token_budget_utilization.fraction_over_90pct` | Warn on >10% of users over 90%. |
| **Database connection pool** | `pg_stat_activity` (see runbook §4.5) | Alert on >80% pool utilization. |
| **Cookie decode errors** | Logged in `auth.py` (`BadSignature` / `SignatureExpired`) | Count per minute; spike signals key rotation issue or attack. |
| **Email delivery failures** | Provider webhook + internal counter | Bounce rate >2% rolling daily = warn. |
| **Disk / memory / CPU** | Host metrics (cloud provider's default) | Host autoscale should catch; watch for leaks. |

The three numbers that matter most operationally are **`/api/health`
up**, **5xx rate <1%**, and **LLM P95 <30s**. Everything else is a
secondary signal.

---

## 2. Source of truth: the in-process MetricsCollector

The backend runs a single-process in-memory metrics collector at
`services/planning_studio_service/metrics/collector.py`. It exposes:

- Per-minute counts: requests, 5xx/4xx responses, LLM calls, LLM
  failures.
- Histograms: request duration (ms), LLM latency (ms).
- Rolling 1h / 24h windows derived at query time.
- Per-user token-budget utilization band counts.

Consumed by `GET /api/admin/metrics` (soft-admin gated on the account
email set via `INSPIRA_ADMIN_EMAIL`; disabled when unset. Real RBAC
was planned with audit P3). Call it from the operator machine:

```
curl -fsS --cookie "inspira_session=..." https://<your-backend>/api/admin/metrics | jq
```

### 2.1 Shape

```json
{
  "window": { "minute_buckets": 1440, "max_minute_buckets": 1440, "snapshot_minute_utc": 29064480 },
  "requests": {
    "last_1h": 1182,
    "last_24h": 26104,
    "errors_5xx_last_1h": 3,
    "errors_5xx_last_24h": 42,
    "errors_4xx_last_24h": 118,
    "error_rate_last_1h": 0.0025,
    "error_rate_last_24h": 0.0016
  },
  "llm_calls": { "last_24h": 4212, "failures_last_24h": 18, "success_rate_last_24h": 0.9957 },
  "request_latency_ms": { "p50": 100, "p95": 500, "p99": 2500 },
  "llm_latency_ms":     { "p50": 2500, "p95": 10000, "p99": 30000 },
  "token_budget_utilization": {
    "tracked_users": 214,
    "bands": { "0-25": 180, "25-50": 20, "50-75": 10, "75-90": 3, "90-100": 1, "over_100": 0 },
    "fraction_over_90pct": 0.0047
  }
}
```

The JSON is the wire format. A scheduled job (or a future Prometheus
scraper) can poll it, persist to a time-series store, and chart it.

### 2.2 Prometheus-exposition-compatible future

The collector is deliberately structured so a Prometheus-format endpoint
is a **swap, not a rewrite**:

- Counters (`requests_total`, `llm_calls_total`, `requests_5xx`, etc.)
  map 1:1 to Prometheus counters.
- Histograms (`request_histogram`, `llm_histogram`) already use
  explicit buckets; exposing them in the `_bucket{le="..."}` format
  is a mechanical translation.
- The per-minute buckets can be discarded once a real tsdb takes over
  — Prometheus handles the time axis.

Do not add the Prometheus dependency until we actually run a
Prometheus server. Today, JSON via the admin endpoint is enough.

---

## 3. Alerting thresholds

| Signal | Warn threshold | Page threshold | Window |
| --- | --- | --- | --- |
| `/api/health` 200 | n/a | 2 consecutive failures | 1-min probe |
| `/readyz` 200 | 1 failure | 2 consecutive failures | 1-min probe |
| 5xx rate | >0.5% | >1% | 5 min |
| LLM P95 latency | >20s | >30s | 10 min |
| LLM success rate | <99.5% | <99% | 1 h |
| Token budget >90% | >10% of users | >25% of users | 1 h snapshot |
| DB connection pool | >70% utilization | >80% utilization | 1 min |
| Cookie decode errors | >50/min | >500/min | 1 min |
| Disk free | <30% | <10% | 1 h |
| Memory free | <25% | <10% | 5 min |

"Page" routes to the on-call phone (SEV-1/SEV-2 triage — see
`docs/ops/incident-response.md`). "Warn" routes to email + Slack. An
alert that fires more than twice a week without a real issue gets
re-tuned; alert fatigue is a SEV-4 waiting to happen.

---

## 4. Sentry

Sentry owns application-layer errors — stack traces, unhandled
exceptions, assertion failures. It's the first place to look when a
user reports "something broke."

### 4.1 Project setup

- One Sentry project, two DSNs: `backend` and `frontend`.
- Back-end release tag = git SHA of the deploy (wire in
  `.github/workflows` on the deploy step).
- Front-end release tag = the same SHA so issues correlate across
  halves of the stack.

### 4.2 DSN env vars

- Backend: `SENTRY_DSN` (read in `_maybe_init_sentry`).
  Optional: `SENTRY_TRACES_SAMPLE_RATE` (default `0.1`). No-op when
  `SENTRY_DSN` is empty — keeps local dev quiet.
- Frontend: `VITE_SENTRY_DSN`, injected at build time (not runtime;
  burns into the bundle).
- Rotation: see `docs/ops/runbook.md` Section 5.4. Low-risk rotation.

### 4.3 Scrubbing

Audit-hardened (see `api.py` and `_maybe_init_sentry`):

- `send_default_pii=False` — don't send IP, user-agent by default.
- Authorization headers, session cookies, and `password` fields are
  stripped before events leave the process.
- Verify the scrub periodically: pick a recent event in Sentry, check
  the headers + body — no bearer tokens, no session cookie, no raw
  password attempt.

### 4.4 Which errors auto-page

Sentry alert rules, in order of severity:

1. **SEV-1 auto-page** — any `error` event with the `critical` tag:
   `auth`, `db_migration`, `data_corruption`. Fires immediately.
2. **SEV-2 auto-page** — 10+ new events of the same issue within 5
   minutes. Signals a mass regression.
3. **Warn (Slack only)** — first occurrence of any new issue. Good
   hygiene: eyeballs land on new failure modes fast, but it doesn't
   wake anyone up.

Resolve issues via the Sentry UI, linked into the commit that fixes
them so the release tag lights up green.

---

## 5. BetterStack (uptime + status page)

Free tier today — one monitor, 1-minute interval, email + SMS
alerting. Sufficient for a pre-traffic product. Upgrade when we pass
about 500 DAU.

### 5.1 Uptime checks

| Monitor | URL | Interval | Expected |
| --- | --- | --- | --- |
| Backend liveness | `https://api.tryinspira.com/api/health` | 1 min | 200 + JSON `{service, status, generated_at}` |
| Backend readiness | `https://api.tryinspira.com/readyz` | 1 min | 200 or a specific 503 shape |
| Frontend up | `https://app.tryinspira.com/` | 5 min | 200 + some HTML keyword like "Inspira" |
| Marketing up | `https://tryinspira.com/` | 5 min | 200 |

Keyword match on the HTML check protects against a "200-but-blank"
regression — the static host returning the fallback page with a clean
200.

### 5.2 Status page integration

Two options, in increasing order of effort:

1. **BetterStack's hosted status page** — enable, point at the
   monitors above, publish at `statusbetter.tryinspira.com`. Quick
   win, ugly defaults (doesn't match our editorial palette). Useful
   as a redundant signal.
2. **Our own page** at `status.tryinspira.com` — the one in
   `docs/status-page/`. Feed it from a cron that reads BetterStack's
   incidents API and writes `incidents.json`. More work, matches the
   brand. Plan to run both in parallel for a few months, then turn
   off the BetterStack-hosted one.

### 5.3 Paging

- On-call email: `on-call@tryinspira.com` (alias to be configured).
- On-call SMS: the operator's mobile.
- Escalation: none yet — solo operator. Add a 15-minute escalation
  when a second operator joins.

---

## 6. DataDog (future)

Not deployed today. The plan for when traffic justifies paid APM:

- **Host metrics** — CPU, memory, disk, network. DataDog agent on the
  backend host.
- **APM** — per-route latency histograms, trace sampling. The
  existing `MetricsCollector` data would become a secondary, in-proc
  view; DataDog becomes the primary cross-instance aggregator.
- **Log pipeline** — ship stdout JSON logs to DataDog Logs. Filter,
  alert on patterns. Stop manually tailing `journalctl`.
- **Synthetic checks** — simulate the smoke-test flow every 15
  minutes from multiple regions. Kickoff + topic_turn round-trip.
  Catches regional outages the 1-min pinger misses.

Estimated cost at 1k active users / day: ~$30/month starter tier.
Revisit when we hit that scale.

---

## 7. Logs

Structured JSON to stdout. `uvicorn` default format, augmented with
`request_id` from the access log middleware. See runbook §3.2.

Keep these field names stable so the future log-shipping config
doesn't have to rewrite:

| Field | Example | Origin |
| --- | --- | --- |
| `request_id` | `"a1b2c3d4e5f6"` | set by middleware per request, echoed in response `X-Request-ID` header |
| `user_id` | `"user-abc123"` | attached by auth dependency |
| `route` | `"/api/v2/projects/{project_id}/kickoff"` | FastAPI route template, not the concrete path |
| `status_code` | `201` | access log |
| `duration_ms` | `1840.7` | access log |
| `planner_provider` | `"openai"` | LLM code paths only |
| `planner_model` | `"gpt-4o-mini"` | LLM code paths only |

Sensitive values (`password`, session cookie, bearer token) must NEVER
appear in logs. The Sentry scrub list is the minimum; the log format
should strip the same keys.

---

## 8. Runbook cross-references

- `docs/ops/runbook.md` — how to triage health-check and DB signals
  day-to-day.
- `docs/ops/incident-response.md` — severity levels, communication
  templates, post-mortem template.
- `docs/ops/sla-targets.md` — internal targets these alerts enforce.
- `docs/status-page/README.md` — how the public status page consumes
  the signals described here.

Update this file alongside `runbook.md` once a quarter, or any time we
add a new monitor or an alert threshold changes.
