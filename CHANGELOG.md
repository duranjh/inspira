# Changelog

All notable changes to Inspira (repo: `planning-studio`) are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). No prior version tags exist; the version numbers below are retroactive milestones chosen to reflect meaningful feature breakpoints. Dates are the last commit date included in each release.

---

## [1.0.0] — 2026-06-10 — Initial public release

Inspira is now open source under the MIT license. The hosted service has been
discontinued; this repository is the complete, self-hostable product.

### Added
- MIT `LICENSE`; contribution terms updated in `CONTRIBUTING.md`.
- `INSPIRA_FRONTEND_URL` and `INSPIRA_ADMIN_EMAIL` environment variables so
  share links, transactional emails, and the admin metrics gate work on any
  deployment (previously hardcoded to the original hosted domain).

### Changed
- README rewritten for self-hosters: requirements, quick start, env-var
  reference, production guidance.
- Security reporting moved to GitHub Private Vulnerability Reporting.
- Feedback theme-extraction endpoint rate-limited (10/min per user) and
  token-budget gated.
- Marketing copy passes for brand/voice coherence; static hero inlined in
  `app/index.html` for sub-500ms first paint (was 2.4-3s blank root).

### Removed
- Legacy v1 routes (`/api/projects` GET, `/api/sessions` GET+POST,
  `/api/artifacts` GET+POST) — superseded by v2; the old store methods did
  not scope by `user_id`.
- Internal-only docs and founder-machine tooling — not relevant to the
  public project.

### Fixed
- Project-card click → canvas navigation on the projects list page.

---

## [0.4.0] — 2026-04-21

"Deploy-readiness, hardening, and polish." Tightened the app for multi-tenant use and the first external preview: FastAPI entry point, auth scaffolding, database migrations, toasts, error boundaries, a circuit breaker on AI calls, and a first security audit. Added file and URL/paste sources for kickoffs, keyboard shortcuts, and skeletons while requests are in flight.

### Added
- FastAPI-based service boundary, auth scaffolding, responsive layout, dark mode, and export flows as the foundation for deploy-ready operation.
- Multi-tenancy hardening: per-user data isolation, Alembic migrations, URL/paste sources, toast notifications, and a global error boundary.
- Circuit breaker in front of AI provider calls to fail fast during provider outages.
- Kickoff file attachments and rotating kickoff chip suggestions in the Inspira canvas.
- Skeletons and keyboard shortcuts in the canvas UI.

### Changed
- Packaged the backend and frontend for deployment: build assets, host configuration, and release scripts.

### Fixed
- Scrubbed a legacy traceback leak path in `app.py` surfaced by the H4 audit item.

### Security
- First pass of the security audit: removed a traceback disclosure, reviewed request-scoping for multi-tenant isolation, and hardened the auth scaffolding.

---

## [0.3.0] — 2026-04-20

"Canvas POC." The first interactive Inspira canvas built on React Flow, talking to a live backend. Topic detail Q&A, auto-generated decisions, a canvas composer, and a zoom-to-detail morph.

### Added
- React Flow canvas wired to live `kickoff`, `topic_turn`, and `list` HTTP endpoints.
- Topic detail panel with a Q&A thread, auto-extracted decisions, and a canvas-side composer.
- Zoom-morph animation that expands a topic card into its detail view.
- Canvas edits: drag-persist, edge create/update/delete, and dagre-based automatic layout.
- Spacing tuning for the canvas grid.

### Changed
- `topic_turn` mode in the planning interviewer agent: a focused interview inside an individual topic, distinct from the broader kickoff.

---

## [0.2.0] — 2026-04-20

"Backend + agent foundation." Wired up the OpenAI adapter, the v2 HTTP endpoints, and the agent prompts and schemas that drive kickoff and topic interviews. Added `.env` auto-loading for local development and established Inspira v2 as the product direction.

### Added
- OpenAI adapter for the `planning_interviewer` agent, starting with kickoff mode.
- v2 HTTP endpoints: `kickoff`, `topic_turn`, and a project `list` endpoint.
- Agent specs and scaffolding for v2 backend, including prompts and response schemas.
- Auto-load of `.env` for local development via python-dotenv.
- "Modern" theme scaffolding for the standalone topic HTML view.

### Changed
- Established Inspira as the public product direction; repo, service, and folder names stay as `planning-studio` / `planning_studio_service` internally.

### Fixed
- Adapter reasoning-budget and `reasoning_effort` handling; graceful repair when the model returns malformed tool output.
- GPT-5 compatibility shims: temperature handling and tool_call retry on transient failures.

---

## [0.1.0] — 2026-04-17

"Scaffolding." Initial repository, Tauri desktop shell, service skeleton, CI/CD wiring, and the first clean UI baseline. This is the earliest runnable state of the project before any production features landed.

### Added
- Initial Planning Studio product repo scaffold.
- Tauri desktop shell, backend services skeleton, and CI/CD configuration.
- Polished operator workflow for the initial version of the app.

### Changed
- Reset the app UI to a blank baseline in preparation for the v2 canvas work.

---

[1.0.0]: #100--2026-06-10--initial-public-release
[0.4.0]: #040--2026-04-21
[0.3.0]: #030--2026-04-20
[0.2.0]: #020--2026-04-20
[0.1.0]: #010--2026-04-17
