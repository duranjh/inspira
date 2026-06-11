# Inspira

Inspira is an open-source, AI-powered planning canvas that turns customer
feedback into ready-to-build features. Drop in raw feedback — support exports,
App Store reviews, sales-call notes — and Inspira reads through everything,
groups the recurring themes, and generates one canvas per theme so engineering
picks up the work pre-thought-through.

It is domain-agnostic by design: the same canvas works for founders, PMs,
researchers, novelists, teachers — anyone turning a pile of raw input into a
structured body of work.

> **Project status:** Inspira began life as a hosted SaaS. The hosted service
> has been **discontinued** — this repository *is* the product now. It is a
> complete, working MVP released under the MIT license for anyone to run,
> study, fork, or build on. It is not under active development; issues and
> PRs are welcome but responses may be slow.
>
> Internal code identifiers keep the original `planning-studio` codename for
> historical reasons; anything user-facing says "Inspira."

## What's in this repo

| Path | What lives there |
|---|---|
| `app/` | React 19 + Vite + TypeScript frontend. The whole product UI — canvas, topic drawers, kickoff, projects list, account settings, dialogs, command palette, auth, error screens, onboarding, PWA shell. |
| `app/src-tauri/` | Optional Tauri 2 desktop shell (macOS / Windows / Linux). |
| `services/planning_studio_service/` | Python FastAPI backend. Auth, projects, topics, relationships, decisions, Q&A, LLM adapters (OpenAI + Claude fallback), per-user token budgets, rate limits, session cookies. |
| `services/alembic/` | SQL migrations. Default SQLite, Postgres via `DATABASE_URL`. |
| `services/tests/` | 98+ tests (pytest/unittest): store, FastAPI routes, auth flow, cross-user ownership, adapter repair paths. |
| `docs/` | Architecture, API reference, dev setup, ops runbooks, deploy playbooks, legal drafts. |
| `docker-compose.yml` | Two-service local stack. |
| `.github/workflows/` | CI (backend tests + frontend typecheck/build + Docker builds), E2E (Playwright, fully mocked — no API keys needed), desktop builds. |

## Requirements

- **Python 3.11+** and **Node 20+** (or just Docker).
- **An OpenAI API key.** The planning agent is LLM-powered; the app will not
  do useful work without `OPENAI_API_KEY`. An Anthropic key is optional
  (Claude fallback). There is no offline / air-gapped mode.

## Quick start

### Docker Compose (easiest)

```sh
cp .env.example .env   # fill in OPENAI_API_KEY at minimum
docker compose up --build
```

Frontend at http://localhost:8080, backend at http://localhost:4174.
SQLite data persists in `./local/data` (bind-mounted from the host).

### Local dev (without Docker)

```sh
# Terminal 1 — backend
cd services
pip install -e .
python -m planning_studio_service

# Terminal 2 — frontend
cd app
npm ci
npm run dev
```

Backend: http://localhost:4174 — Frontend: http://localhost:4175.

The frontend defaults to `http://127.0.0.1:4174` for API calls; override via
`VITE_INSPIRA_API_URL` at build time (it is baked into the bundle).

## Environment variables

The key variables the app reads are documented in `.env.example` with inline
comments and `REPLACE_ME` markers for required secrets. The key ones:

| Variable | Required in prod | Purpose |
|---|---|---|
| `ENVIRONMENT` | Yes | Set to `production` to enable startup assertions |
| `INSPIRA_SESSION_SECRET` | Yes | 48+ byte random string — signs session cookies |
| `OPENAI_API_KEY` | Yes | Primary LLM provider |
| `INSPIRA_ALLOWED_ORIGINS` | Yes | CORS allowlist (comma-separated) |
| `INSPIRA_COOKIE_SECURE` | Yes | Set `true` to require HTTPS for cookies |
| `INSPIRA_APP_BASE_URL` | Yes | Public URL of your frontend — used in share links and transactional emails (`INSPIRA_FRONTEND_URL` is a legacy alias) |
| `INSPIRA_ADMIN_EMAIL` | No | Account email granted access to `/api/admin/metrics` |
| `DATABASE_URL` | For Postgres | Default is SQLite; set to `postgresql+psycopg://...` |
| `SENTRY_DSN_BACKEND` | No | Error tracking (optional; `SENTRY_DSN` accepted as legacy alias) |
| `ANTHROPIC_API_KEY` | No | Claude fallback provider |
| `STRIPE_SECRET_KEY` | No | Activates Stripe billing when set — **without it the app runs with billing disabled (free tier for everyone)** |

See `.env.example` for all variables including rate limits, quotas, email
providers, and Stripe price IDs.

## Tests

```sh
# Backend unit + integration tests (install with the dev extra first:
# pip install -e ".[dev]")
cd services
pytest -x

# Frontend typecheck, unit tests, build
cd app
npx tsc --noEmit
npm test
npm run build

# E2E (Playwright, LLM calls mocked — no API key needed)
cd app
npx playwright test
```

CI runs all of these on every push (see `.github/workflows/ci.yml` and
`e2e.yml`). No repository secrets are required for CI.

## Database migrations

Alembic manages schema migrations. SQLite is the default; Postgres is
activated by setting `DATABASE_URL`.

```sh
cd services

# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing models
alembic revision --autogenerate -m "describe the change"
```

In Docker Compose, run migrations before starting the service:
```sh
docker compose run --rm backend alembic upgrade head
```

## Self-hosting in production

- Step-by-step deploy playbook: [`docs/deploy/playbook.md`](docs/deploy/playbook.md)
- Postgres setup: [`docs/ops/postgres-setup.md`](docs/ops/postgres-setup.md)
- Pre-launch security checklist: [`docs/ops/hardening-checklist.md`](docs/ops/hardening-checklist.md)

**Database**: swap SQLite for Postgres by setting `DATABASE_URL`. Postgres is
required for multiple replicas, connection pooling, and concurrent writes.
Run `alembic upgrade head` before deploying new code.

**Session secret rotation**: changing `INSPIRA_SESSION_SECRET` immediately
invalidates all active sessions (users get logged out).

**Scaling**: the backend is stateless except for the SQLite file (dev) or
Postgres connection (prod). In-memory rate-limit counters and daily URL fetch
caps reset on process restart — for multi-replica deploys, move these to
Redis or accept the per-replica ceiling.

**What to monitor**:
- `GET /api/health` — liveness. Returns `{"status": "ok"}` when healthy.
- `GET /api/admin/metrics` — in-process counters (requires the account set in
  `INSPIRA_ADMIN_EMAIL`). Request counts, LLM call counts, error rates.
- Log stream for `planning_studio.client_errors` — React ErrorBoundary POSTs
  here when the frontend catches a render crash.
- Sentry (if `SENTRY_DSN_BACKEND` set) — uncaught backend exceptions + performance.

**HTTPS + cookies**: set `INSPIRA_COOKIE_SECURE=true` and front the service
with a TLS-terminating proxy (nginx, Caddy, or your platform's ingress).
The `Strict-Transport-Security` header is only emitted when
`ENVIRONMENT=production`.

**Stripe webhooks** (only if you enable billing): point
`<backend_url>/api/v2/billing/webhook` at your Stripe webhook endpoint and set
`STRIPE_WEBHOOK_SECRET`. The endpoint verifies the signature before
processing; an invalid signature returns 400.

## Documentation map

- [`docs/architecture/overview.md`](docs/architecture/overview.md) — system map, tech stack, data flow.
- [`docs/architecture/data-model.md`](docs/architecture/data-model.md) — SQL schema.
- [`docs/architecture/auth-flow.md`](docs/architecture/auth-flow.md) — sessions + user_id scoping.
- [`docs/architecture/llm-pipeline.md`](docs/architecture/llm-pipeline.md) — prompts, circuit breaker, fallback.
- [`docs/architecture/frontend-structure.md`](docs/architecture/frontend-structure.md) — component tree, state.
- [`docs/api/`](docs/api/) — HTTP API reference.
- [`docs/dev/`](docs/dev/) — local setup, debugging, code style.
- [`docs/ops/runbook.md`](docs/ops/runbook.md) — day-to-day ops.
- [`docs/ops/incident-response.md`](docs/ops/incident-response.md) — severity matrix + playbook.
- [`docs/deploy/playbook.md`](docs/deploy/playbook.md) — step-by-step deploy.
- [`docs/legal/`](docs/legal/) — ToS + Privacy + DMCA + GDPR **drafts** (never finalized by a lawyer; review before using them for your own deployment).

## Product vision

Inspira is **domain-agnostic** — the same canvas works for novelists, campaign
managers, researchers, founders, teachers, PMs, anyone planning anything.
The aesthetic is **warm editorial**: cream paper, serif display, dotted grid,
sage/gold/rust accents. Explicitly **not** a dashboard, not a workflow tool,
not a linear stepper.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The project is feature-complete as
an MVP and not under active development, but bug fixes and improvements are
welcome.

## License

[MIT](LICENSE).
