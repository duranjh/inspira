# Architecture Overview

High-level system diagram, tech stack, and end-to-end data flow for Inspira
(repo codename: planning-studio). This document captures the current
implementation; items marked "planned" are not live yet.

## System Diagram

```
+--------------------------------------------------------------------------+
|                          Browser (Vite/React 19)                         |
|                                                                          |
|   app/src/App.tsx -> app/src/features/inspira/InspiraApp.tsx             |
|         |                                                                |
|         +-- ProjectCanvas (React Flow + dagre auto-layout)               |
|         +-- TopicDetail (zoom-morph overlay)                             |
|         +-- KickoffForm (idea + attachments)                             |
|         +-- ToastProvider, ErrorBoundary (global)                        |
|                                                                          |
|   HTTP calls go through app/src/features/inspira/api.ts                  |
|   (fetch + credentials:'include' so the session cookie rides along)      |
+-----------------------------+--------------------------------------------+
                              | JSON over HTTPS
                              | CORS open in dev, locked via
                              | INSPIRA_ALLOWED_ORIGINS in prod
                              v
+--------------------------------------------------------------------------+
|               FastAPI backend (uvicorn ASGI, Python 3.12)                |
|                                                                          |
|   Entrypoint: services/planning_studio_service/__main__.py               |
|   App factory: services/planning_studio_service/api.py:create_app        |
|                                                                          |
|  +---------+   +------------+   +-------------------+   +-------------+  |
|  | CORS    |-->| SlowAPI    |-->| session cookie    |-->| route       |  |
|  | middlew.|   | rate limit |   | resolver (auth.py)|   | handlers    |  |
|  +---------+   +------------+   +-------------------+   +-------------+  |
|                                                              |           |
|      +-------------------------------------------------------+           |
|      |                                                                   |
|      v                                                                   |
|  +-------------------------+      +----------------------------+         |
|  | ownership gate          |      | per-user daily             |         |
|  | _require_owned_project  |      | token budget gate          |         |
|  | _require_owned_topic    |      | _require_token_budget      |         |
|  | _require_owned_decision |      +----------------------------+         |
|  +-------------------------+                                             |
|      |                                                                   |
|      v                                                                   |
|  +-------------------------+      +----------------------------+         |
|  | PlanningStudioStore     |<---->| OpenAI adapter             |         |
|  | (SQLite; Postgres when  |      | (services/.../agents/      |         |
|  | DATABASE_URL is set)    |      |  openai_adapter.py)        |         |
|  +-------------------------+      +----------------------------+         |
|      |                                   |                               |
|      v                                   v                               |
|  local/data/planning-studio.sqlite    OpenAI Chat Completions            |
|  (topics, relationships, decisions,    gpt-5-mini, strict tool calls      |
|   qna_turns, v2_projects, users,       + pybreaker circuit breaker        |
|   user_usage, audit_log, etc.)         + transient-retry backoff          |
|                                        + Claude fallback adapter          |
|                                                                          |
+--------------------------------------------------------------------------+
```

## Tech Stack

### Frontend (`app/`)

- Vite 7 + React 19 (strict mode, functional components only).
- TypeScript 5.9, `strict: true`, bundler resolution.
- React Flow 11 for the topic canvas; dagre for auto-layout.
- `html2pdf.js` for export. `pdfjs-dist` (lazy-loaded) for extraction of
  user-attached PDFs.
- No Redux, Zustand, or SWR. State lives in local React hooks and flows
  through props. Global side channels use `window.dispatchEvent` +
  `CustomEvent` (see `app/src/features/inspira/InspiraApp.tsx:426`).
- PWA scaffolding is installed (`vite-plugin-pwa`) but service worker
  registration gates on `import.meta.env.PROD` and is currently a no-op
  in dev. Source at `app/src/pwa/` (planned rollout).
- Tauri 2.10 desktop wrapper at `app/src-tauri/` (optional; not part of
  the hosted deploy).

### Backend (`services/`)

- Python 3.11+ (CI uses 3.12).
- FastAPI 0.115 on uvicorn, ASGI. Legacy `BaseHTTPServer` entrypoint at
  `services/planning_studio_service/app.py` is kept for backward
  compatibility and opt-in via `python -m planning_studio_service --legacy`.
- SQLite for dev and first-production; Postgres supported via
  `DATABASE_URL` (psycopg binary is pinned in `pyproject.toml`).
- Alembic owns schema migrations going forward; see
  `services/alembic/versions/20260421_0001_baseline.py`. During the
  transition, `store.py`'s `_initialize*` methods still bootstrap schema
  on first boot — both paths use `CREATE TABLE IF NOT EXISTS` so they are
  idempotent against each other.
- Pydantic 2 for request bodies. Response bodies stay as plain dicts for
  now so the client sees the same loose shapes the legacy stdlib handler
  emitted.
- `itsdangerous` for signed session cookies; `argon2-cffi` for password
  hashing.
- `slowapi` for per-IP rate limits; `pybreaker` for the OpenAI circuit
  breaker.
- `openai>=1.54` (primary planner provider); `anthropic>=0.39` (Claude
  fallback adapter — wired, activation path in `claude_adapter.py` but
  not yet invoked from `openai_adapter.py`; see the llm-pipeline doc).
- Sentry (optional; gated by `SENTRY_DSN`).

### Dev tools and infra

- npm for frontend installs (lockfile checked in).
- pip + setuptools for backend installs (`pip install -e services/[dev]`).
- Dockerfiles for both services; `docker-compose.yml` wires them together
  against a host-mounted `./local/data` volume.
- GitHub Actions CI at `.github/workflows/ci.yml` runs backend unit tests,
  frontend typecheck + build, and both Docker image builds on every push
  to `main` and every PR. No production deploy workflow ships in this repo.

## Data Flow: User Click to UI Update

End-to-end walkthrough of the "user submits a kickoff idea" flow, which
exercises the longest path in the system. Line-level references are to
the current `main` as of April 2026.

1. **User types in `KickoffForm`** and submits.
   - `app/src/features/inspira/KickoffForm.tsx` fires its `onSubmit`
     callback with `(idea, attachments)`.
   - `InspiraApp.tsx:149` (`handleKickoff`) takes over: it mints a project
     title from the first line of the idea, then calls
     `api.createV2Project(title)` to allocate a new `project_id` on the
     backend (`POST /api/v2/projects`). Then it calls
     `api.kickoff(projectId, idea, attachments)` (`POST
     /api/v2/projects/{id}/kickoff`).

2. **Fetch layer attaches credentials.**
   - `app/src/features/inspira/api.ts:117` wraps every call through
     `postJson` / `getJson`, which set `credentials: "include"` so the
     `inspira_session` cookie rides along.

3. **FastAPI middleware stack runs.**
   - CORS middleware (`api.py:290`) permits the request.
   - SlowAPI per-IP rate limiter (`api.py:309`) applies the default
     `120/minute` (overridable via `INSPIRA_RATE_LIMIT`).
   - The `current_user` dependency (`auth.py:239`) reads the
     `inspira_session` cookie, verifies its `itsdangerous` signature, and
     resolves to a real user or falls back to `user-system`.

4. **Route handler runs (`api.py:528` `v2_kickoff`).**
   - `_require_token_budget(user)` (`api.py:408`) checks today's user
     token spend against `INSPIRA_USER_DAILY_TOKEN_BUDGET` (default
     200,000). Over budget returns HTTP 429 with `Retry-After`.
   - `_store.ensure_project(project_id=..., user_id=...)` (`store.py:493`)
     confirms ownership and creates/upserts the project row.

5. **Adapter call.**
   - `_require_adapter()` (`api.py:255`) lazily constructs an
     `OpenAIPlanningInterviewer` if none was injected at app startup.
   - `adapter.kickoff(user_idea=..., attached_sources=...)`
     (`openai_adapter.py:116`):
     - Composes the prompt: `BASE_SYSTEM_PROMPT + "\n\n" + KICKOFF_MODE_PROMPT`
       (`prompts.py:16` + `prompts.py:57`).
     - Formats the user message with sources (`_format_kickoff_user_message`).
     - Calls OpenAI Chat Completions with `tool_choice` forced to the
       `kickoff_response` tool, `strict: true`, wrapped in a pybreaker +
       transient-retry harness (`_breakered_create`).
     - On `_EmptyToolCallResponse` (no tool call returned), retries once
       (`_call_with_toolcall_retry`).
     - Runs `_sanitize_kickoff_response`: enforces topic count 5-10,
       drops relationships that reference unknown titles, auto-connects
       orphan topics (`openai_adapter.py:635`).

6. **Persistence.**
   - Handler loops over returned topics, calls `_store.create_topic` per
     topic (`store.py:827`).
   - Loops over returned relationships, looks up the freshly-persisted
     topic IDs by title, calls `_store.create_relationship`
     (`store.py:939`).
   - Calls `_record_llm_usage` (`api.py:443`) to attribute today's token
     spend to the user.

7. **Response.**
   - Returns a JSON envelope: `{kickoff, topics, relationships}`.

8. **Client reconciles UI.**
   - `InspiraApp.tsx:195` computes a dagre layout over the new topic set
     (`layout.ts:40` `computeTopicLayout`), persists each new position via
     `api.updateTopic` fire-and-forget, and transitions to
     `phase: "canvas"`.
   - `ProjectCanvas` renders React Flow nodes for each topic and edges
     for each relationship. The user sees the map.

The second dominant flow (a topic Q&A turn) follows the same shape
against `POST /api/v2/topics/{id}/turn` — see
`llm-pipeline.md` for the planner loop and
`api/reference.md` for the payloads.

## Where Things Live

- `services/planning_studio_service/api.py` — FastAPI routes + middleware.
- `services/planning_studio_service/auth.py` — signup/login/me, cookie
  sessions, `current_user` dependency.
- `services/planning_studio_service/store.py` — SQLite bootstrap, every
  CRUD method, per-user token usage, suggestions cache.
- `services/planning_studio_service/config.py` — env-driven `ServiceConfig`.
- `services/planning_studio_service/agents/` — LLM adapters + prompts +
  schemas.
- `services/alembic/versions/` — schema migrations (baseline only for now).
- `app/src/features/inspira/` — canvas, detail view, kickoff form, API
  wrapper, layout, file extraction.
- `app/src/components/` — app-wide pieces: ToastProvider, ErrorBoundary,
  ShortcutHelpOverlay, AuthPanel, Skeletons, dialog primitives.
- `docs/` — this directory: `architecture/`, `api/`, `deploy/`,
  `deployment/`, `dev/`, `integrations/`, `legal/`, `ops/`, `specs/`,
  and `status-page/` subtrees.

## Current Deploy Shape

- Default dev layout runs the backend on `http://127.0.0.1:4174` and the
  Vite dev server on `http://127.0.0.1:4175`.
- Docker Compose brings both up with the frontend on `localhost:8080`
  pointing at `http://localhost:4174` for the API (build-time
  `VITE_INSPIRA_API_URL` arg).
- Hosted deploys (Fly.io / Railway) are planned; see `docs/deploy/playbook.md`.
