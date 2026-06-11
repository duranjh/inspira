# Agent Guide

Orientation order for AI coding agents working in this repo:

1. `README.md` — what the product is, how to run it, env vars.
2. `docs/architecture/overview.md` — system map and end-to-end data flow.
3. `docs/dev/` — local setup, debugging, code style.
4. `CONTRIBUTING.md` — branching, commit style, PR expectations.

## Naming

- The public product name is **Inspira**; internal identifiers keep the
  original `planning-studio` codename (`planning_studio_service`, folder
  names, package names). Don't rename them.

## Layout

- `app/` — React 19 + Vite + TypeScript frontend. Check `app/package.json`
  before adding dependencies. Desktop wrapper lives in `app/src-tauri/`.
- `services/` — FastAPI backend (`planning_studio_service`), Alembic
  migrations, pytest suite in `services/tests/`.

## Verification

- Backend: `cd services && pytest -x`
- Frontend: `cd app && npx tsc --noEmit && npm test && npm run build`
- Run both before claiming a change works; CI runs them on every push.
