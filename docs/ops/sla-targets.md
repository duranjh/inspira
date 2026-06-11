# Internal SLA Targets

**Audience:** operator on duty.
**Status:** internal-only. NOT a public commitment.
**Last updated:** 2026-04-20

This document is the north star for "how good is good enough" today.
It sets internal targets we use to triage alerts, prioritize fixes,
and decide what counts as an "incident" worth posting on
`status.tryinspira.com`. Public-facing language about availability
does not live here — when we're ready to publish an external SLA, we
will draft it separately and have counsel review it.

---

## 1. Targets

| Metric | Target | Measurement | Notes |
| --- | --- | --- | --- |
| Availability (`/api/health` 200) | **99.5%** over a rolling 30-day window | External uptime checks (BetterStack), sampled every 1 min | Translates to ~3h 36m of allowed downtime per month. |
| LLM failure rate | **<1%** over a rolling 24-hour window | MetricsCollector `llm_calls.success_rate_last_24h`; >= 99% success | A "failure" is any call that raises, times out at 60s, or returns a non-recoverable parse error. |
| P95 kickoff latency | **<20s** | MetricsCollector `llm_latency_ms.p95` filtered to the kickoff route | Measured at the LLM call level, not the end-to-end HTTP request. Rollout of a smaller fallback model is the lever when this slips. |
| P95 topic_turn latency | **<10s** | MetricsCollector `llm_latency_ms.p95` filtered to the topic_turn route | The per-turn interactive loop. Users tolerate much less latency here than at kickoff. |
| P99 API response time (non-LLM routes) | **<1s** | MetricsCollector `request_latency_ms.p99` excluding LLM-bearing routes | Database, project CRUD, session lookups. |
| 5xx error rate | **<0.5%** over a rolling 24h window | MetricsCollector `requests.error_rate_last_24h` | Excludes 429s (rate limiting) and 4xx client errors. |

These are **ambitions**, not contracts. In the solo-operator phase, a
single bad deploy can blow the monthly budget inside an hour. That's
fine — the target's job is to inform priorities, not to produce
public hand-wringing.

---

## 2. Measurement

### 2.1 Availability

We measure availability externally, not from the backend. BetterStack
probes `/api/health` every minute from at least one region. A minute
is "down" if two consecutive probes fail. Monthly availability =
`(minutes_up / total_minutes)`.

This deliberately ignores planned maintenance windows shorter than 5
minutes — a rolling restart to pick up a secret rotation shouldn't
count against the target.

Planned maintenance longer than 5 minutes:

- Announce on `status.tryinspira.com` at least 24h in advance.
- Tag it "scheduled_maintenance" in the incidents JSON.
- Excluded from the monthly availability calculation.

### 2.2 Latency

Latency targets are measured at the LLM-call level by
`MetricsCollector.record_llm_call(...)`. They specifically exclude the
HTTP overhead (parsing the request body, auth lookup, writing to the
database) — that framing is a ceiling on LLM-call time, not on the
full end-to-end request. If the LLM returns in 9s but the full
request takes 11s, we look for DB-or-network overhead separately.

"P95" over the 24h window means: sort all samples, the 95th
percentile is the answer. The collector estimates this from
exponentially-bucketed histograms, which **over-estimates by up to
one bucket** (see `_percentile_from_buckets` in
`services/planning_studio_service/metrics/collector.py`). Good
enough for internal dashboards — call this out when a future
external SLA gets drafted.

### 2.3 LLM failure rate

A "call" is every invocation of the planner adapter that leaves the
process boundary (one OpenAI HTTP request, one Anthropic HTTP
request, etc.). A "failure" is:

- HTTP non-2xx from the provider.
- Timeout at the configured adapter timeout.
- A response we couldn't parse into the expected tool-call shape, AND
  the adapter's graceful-repair path also failed (see
  `agents/openai_adapter.py`).

Partial successes (e.g. a repaired response that lost one suggestion)
count as successes — the user got a usable answer.

### 2.4 5xx error rate

Measured via `MetricsCollector.record_request(...)`. Only 5xx counts
— 429 rate-limit responses and 4xx client errors are excluded. A 5xx
that occurred because a user's session cookie decoded to an invalid
user ID (a known class of transient failure on secret rotation)
still counts; we fix the cause rather than excluding the symptom.

---

## 3. Grace periods

Burning the monthly budget in small chunks is normal. Burning it in
one shot is worth a post-mortem.

- **First 15 minutes of any outage**: no public status-page update
  required if recovery is clearly imminent. Use the incident log
  inside `docs/ops/incidents/` from minute 1, but hold the public
  post until the 15-minute mark or until we know the outage will
  last longer, whichever comes first. Rationale: transient
  self-healing outages (DB failover, cold start after deploy) are
  overwhelmingly the common case, and status-page noise erodes
  signal.
- **First 72h after a major version bump** (React Flow major, FastAPI
  major, Postgres major): regressions are expected. We still page on
  them, but we don't count minor latency regressions against the
  P95 target for those 72h. Anything worse than that goes into a
  "new-version baseline" note.
- **Grace for OpenAI / Anthropic provider outages**: our target for
  LLM failure rate is "internal" — if the upstream provider is down,
  we don't flagellate ourselves over the number. We still post the
  user-visible symptom on the status page and expose a fallback path
  (Anthropic ↔ OpenAI) where the prompt shape allows it. Provider-
  caused failures are tracked separately so we can decide when the
  fallback investment is worth it.

---

## 4. What counts as an "incident" for the status page

A **SEV-1 or SEV-2 incident** (per `docs/ops/incident-response.md`
§1) MUST be posted on the status page, regardless of whether anyone
reports it externally.

A **SEV-3** is posted on the status page only when **user-visible
symptoms last longer than 30 minutes**. Shorter SEV-3s are logged
internally but stay off the public page; a trickle of tiny 3-minute
blips on the public board erodes signal without helping anyone.

A **SEV-4** is never posted publicly.

Non-incidents that still feel important:

- **Sustained slow LLM responses** (P95 drifts above 20s for >1h) =
  post as a `degraded` status for the Planner component, even
  though nothing is strictly "down."
- **Email delivery backlog** (>15 minutes of queued outbound mail) =
  post as `degraded` for Email delivery. Users trying to sign up or
  reset a password are effectively blocked.

---

## 5. What does NOT count as downtime

- A user whose session was invalidated by a secret rotation (see
  runbook §5.2). They get a "please sign in again" screen, not a
  500. The UX is graceful.
- Rate-limited requests (429). Working as intended.
- Requests rejected for violating input caps (400) — e.g. an 8 MB
  `user_idea` body.
- A user over their daily token budget (429 from `_require_token_budget`).
- Scheduled maintenance shorter than 5 minutes.

---

## 6. Review cadence

- Monthly: review the previous month's availability, LLM failure
  rate, and P95 latency against the targets. If we missed a target,
  write a one-paragraph note explaining why (top causes, not just
  "Sentry was noisy"). File under `docs/ops/monthly-review/`.
- Quarterly: review the target numbers themselves. As the product
  grows, the bar moves up (99.5% → 99.9%), not down. Update this
  file.
- After every SEV-1: re-read the "grace periods" and "what counts as
  an incident" sections. If something felt off during the incident,
  fix the doc before memory fades.

This doc is the source of truth for what "healthy" means
internally. When it gets out of date the on-call makes worse
decisions — keep it current.
