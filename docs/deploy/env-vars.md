# Environment Variables

Complete list of environment variables the Inspira backend and frontend
read, with types, defaults, and production requirements.

Sources:

- `services/planning_studio_service/config.py`
- `services/planning_studio_service/api.py`
- `services/planning_studio_service/auth.py`
- `services/planning_studio_service/agents/openai_adapter.py`
- `services/planning_studio_service/agents/claude_adapter.py`
- `services/planning_studio_service/agents/suggestions.py`
- `services/planning_studio_service/__main__.py`
- `app/vite.config.ts` + `app/src/features/inspira/api.ts`

Legend:

- **Required in prod** — backend refuses to boot without a valid value
  when `ENVIRONMENT=production` (see `_assert_production_safe`,
  `api.py:199`).
- **Optional** — safe default; override for specific deployments.
- **Dev-only** — ignore in production.

## Required in production

| Variable | Type | Example | Required in prod | Read at | Purpose |
|---|---|---|---|---|---|
| `OPENAI_API_KEY` | string | `sk-proj-...` | **yes** | `openai_adapter.py:760`, `suggestions.py:239` | OpenAI API key for the primary planner adapter. |
| `INSPIRA_SESSION_SECRET` | string (48+ bytes) | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | **yes** | `auth.py:43` (read in `_session_serializer`) | Signs session cookies. Must NOT be the literal `"inspira-dev-only-change-me"`. |
| `INSPIRA_ALLOWED_ORIGINS` | comma-separated URLs | `https://tryinspira.com,https://www.tryinspira.com` | **yes** | `api.py:283` | Browser origins allowed by CORS. Wildcard `*` is permitted in dev but forbidden in prod by the startup guard. |
| `INSPIRA_COOKIE_SECURE` | `"true"` or `"false"` | `true` | **yes (must be `"true"`)** | `auth.py:151` (`_set_session_cookie`) | Gates the `Secure` cookie attribute. Production guard refuses any value other than `"true"`. |
| `ENVIRONMENT` | `"production"` / `"staging"` / `"development"` | `production` | conditional | `api.py:189, 208` | Activates the `_assert_production_safe` guard. |

## Service runtime

| Variable | Type | Default | Required in prod | Read at | Purpose |
|---|---|---|---|---|---|
| `PLANNING_STUDIO_HOST` | string | `127.0.0.1` (out-of-container) / `0.0.0.0` (Docker default) | no | `config.py:48`, `__main__.py:50` | Bind address for uvicorn. |
| `PLANNING_STUDIO_PORT` | int | `4174` | no | `config.py:49`, `__main__.py:51` | Bind port. |
| `PLANNING_STUDIO_STORAGE_ROOT` | path | `<repo>/local/data` (dev); `/data` (Docker) | no | `config.py:47` | Directory for the SQLite file and transcripts. |
| `DATABASE_URL` | SQLAlchemy URL | empty (falls back to SQLite) | no | `config.py:39` | Points at Postgres in production, e.g. `postgresql+psycopg://user:pass@host:5432/inspira`. |
| `LOG_LEVEL` | `debug` / `info` / `warning` / `error` | `info` | no | `__main__.py:58` | uvicorn log verbosity. |
| `UVICORN_RELOAD` | `"true"` / `"false"` | `"false"` | no | `__main__.py:59` | Dev auto-reload. Set `"true"` for fast-iteration; never in prod. |

## Auth / security

| Variable | Type | Default | Required in prod | Read at | Purpose |
|---|---|---|---|---|---|
| `INSPIRA_RATE_LIMIT` | slowapi rate string | `120/minute` | no | `api.py:311` | Per-IP default rate limit. Also accepts `"60/minute"`, `"10/second"`, etc. |
| `INSPIRA_USER_DAILY_TOKEN_BUDGET` | int | `200000` | no | `api.py:47` | Combined prompt+completion tokens per UTC day per user. Non-positive value disables the gate. |
| `INSPIRA_VOICE_DAILY_OUTPUT_SECONDS_BUDGET` | int | `1800` | no | `voice.py:daily_output_seconds_budget` | Per-user ceiling on cumulative Realtime output-seconds per UTC day. Checked before minting an ephemeral token on `POST /api/v2/voice/realtime/session`. Default 1800s = 30 minutes of synthesized voice. Non-positive value disables the gate. Belt-and-braces against a compromised ephemeral token — the token itself is ~60s, but a user who has already burned the daily cap shouldn't be able to mint another session. |
| `GOOGLE_OAUTH_CLIENT_ID` | string | unset | no | `auth.py:332` (`google_start_route`) | **Planned**. When set, the `/api/auth/google/start` route activates; unset returns 501. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | string | unset | no | — | **Planned**. Paired with client id. |
| `GOOGLE_OAUTH_REDIRECT_URI` | URL | unset | no | — | **Planned**. OAuth callback. |

## LLM providers

| Variable | Type | Default | Required in prod | Read at | Purpose |
|---|---|---|---|---|---|
| `OPENAI_API_KEY` | string | unset | yes (see above) | `openai_adapter.py:760` | Primary planner provider. |
| `ANTHROPIC_API_KEY` | string | unset | no | `claude_adapter.py:110` | **Planned** — Claude fallback adapter requires this at construction. Not yet invoked automatically. |
| `ANTHROPIC_MODEL` | string | `claude-sonnet-4-6` | no | `claude_adapter.py:118` | Override the Claude model. |
| `INSPIRA_SUGGESTIONS_MODEL` | string | `gpt-5-mini` | no | `suggestions.py:32` | Override the model used by the AI project suggestions call. |
| `INSPIRA_TEST_MODEL` | string | `gpt-5-mini` | no (dev only) | `tests/test_openai_adapter.py:631` | Override the model used by the live integration tests. |

## Observability

| Variable | Type | Default | Required in prod | Read at | Purpose |
|---|---|---|---|---|---|
| `SENTRY_DSN` | Sentry DSN URL | unset | no (recommended) | `api.py:178` | When set, wraps the FastAPI app with Sentry. |
| `SENTRY_TRACES_SAMPLE_RATE` | float `0.0`–`1.0` | `0.1` | no | `api.py:187` | Performance sampling for Sentry. |

## Frontend (build-time)

The frontend has a single env var, read by Vite at **build time** and
inlined into the output bundle. Changing the value after build has no
effect — rebuild the image.

| Variable | Type | Default | Required in prod | Read at | Purpose |
|---|---|---|---|---|---|
| `VITE_INSPIRA_API_URL` | URL | `http://localhost:4174` | **yes** | `app/src/features/inspira/api.ts:9`; `app/Dockerfile` `ARG` | Backend URL the built bundle calls. Set via `docker build --build-arg VITE_INSPIRA_API_URL=...` or in your deploy platform's build env. |

## Dev conveniences

These are read outside the production path. Document them here so
contributors know the knobs:

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `PLANNING_STUDIO_STORAGE_ROOT` | path | `<repo>/local/data` | Used by tests (`tests/_helpers.py:60`) to redirect the SQLite file to a tempdir. |

## Loading `.env` in dev

`services/planning_studio_service/_env_bootstrap.py:ensure_loaded` walks
up from the current working directory and loads the nearest `.env` via
`python-dotenv`. Existing `os.environ` values win over `.env` — setting
a variable in your shell overrides the file. Call is idempotent.

`ensure_loaded()` is called from:

- `__main__.py:29` (service entrypoint).
- `tests/_helpers.py:45` (unit tests).

Library modules never load `.env` on their own — the caller owns
bootstrap.

## `.env.example`

Canonical template at the repo root: `.env.example`. Copy to `.env` and
fill in the values you need for local work:

```
cp .env.example .env
# edit .env, then run the backend / tests
```

The checked-in example covers the minimum for dev (OpenAI key +
optional model override + optional storage-root / host / port
overrides). Production secrets (`INSPIRA_SESSION_SECRET`,
`INSPIRA_ALLOWED_ORIGINS`, `DATABASE_URL`, `SENTRY_DSN`) are
deliberately omitted so nobody accidentally ships the example.

## Production safety check

At boot, `_assert_production_safe` (`api.py:199`) raises a
`RuntimeError` with a multi-line explanation when `ENVIRONMENT` equals
`"production"` (case-insensitive) and any of the following holds:

- `INSPIRA_SESSION_SECRET` is empty or equals the dev fallback.
- `INSPIRA_ALLOWED_ORIGINS` is empty.
- `INSPIRA_COOKIE_SECURE` is not `"true"`.
- `OPENAI_API_KEY` is empty.

The loud crash is intentional — a quiet compromise is far worse than a
boot failure that points you at the missing secret.
