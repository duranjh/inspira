#!/usr/bin/env bash
# Boot the local Inspira backend for Mode 3 dev (Postgres + empty).
# Idempotent — safe to re-run; skips steps already done.
#
# Usage:
#   ./services/scripts/dev.sh                  # default DB inspira_wave34_main, port 4174
#   ./services/scripts/dev.sh alpha            # inspira_wave34_alpha on default port 4174
#   ./services/scripts/dev.sh alpha 4175       # inspira_wave34_alpha on port 4175
#
# When multiple checkouts (e.g. git worktrees) run dev.sh concurrently,
# give each one its own DB slug and port to avoid a :4174 collision.
#
# Prereqs:
#   - Homebrew Postgres 16 installed (`brew install postgresql@16`)
#   - .env.local in services/ with DATABASE_URL pointing at the chosen DB
set -euo pipefail

SESSION_SLUG="${1:-main}"
APP_PORT="${2:-4174}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PG_PREFIX="/opt/homebrew/opt/postgresql@16"
PG_PORT=5432
DB_NAME="inspira_wave34_${SESSION_SLUG}"

cd "$SERVICES_DIR"

# 1. Postgres
if ! "$PG_PREFIX/bin/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -q 2>/dev/null; then
  echo "[dev.sh] Starting Postgres 16 via brew services..."
  brew services start postgresql@16
  for _ in $(seq 1 10); do
    "$PG_PREFIX/bin/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -q && break
    sleep 0.5
  done
fi
echo "[dev.sh] Postgres ready on 127.0.0.1:$PG_PORT"

# 2. Ensure role + DB exist
if ! "$PG_PREFIX/bin/psql" -h 127.0.0.1 -p "$PG_PORT" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='inspira'" | grep -q 1; then
  echo "[dev.sh] Creating role inspira..."
  "$PG_PREFIX/bin/psql" -h 127.0.0.1 -p "$PG_PORT" -d postgres -c "CREATE ROLE inspira WITH LOGIN PASSWORD 'inspira' CREATEDB;"
fi
if ! "$PG_PREFIX/bin/psql" -h 127.0.0.1 -p "$PG_PORT" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  echo "[dev.sh] Creating database $DB_NAME..."
  "$PG_PREFIX/bin/psql" -h 127.0.0.1 -p "$PG_PORT" -d postgres -c "CREATE DATABASE $DB_NAME OWNER inspira;"
fi
echo "[dev.sh] DB ready: $DB_NAME"

# 3. Python venv
if [[ ! -d .venv ]]; then
  echo "[dev.sh] Creating Python venv..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -e . >/dev/null
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "[dev.sh] venv activated"

# 4. .env.local must exist (the dev shouldn't commit it; we don't generate here)
if [[ ! -f .env.local ]]; then
  echo "[dev.sh] ERROR: services/.env.local is missing. Create it with DATABASE_URL pointing at $DB_NAME (see services/README.md)." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env.local
set +a

# 5. Alembic
echo "[dev.sh] Running alembic upgrade head..."
alembic upgrade head >/dev/null

# 6. Uvicorn
echo "[dev.sh] Starting FastAPI on 127.0.0.1:${APP_PORT} (DB: ${DB_NAME}) — Ctrl-C to stop"
exec uvicorn planning_studio_service.api:app --host 127.0.0.1 --port "${APP_PORT}" --reload
