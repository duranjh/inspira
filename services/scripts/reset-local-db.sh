#!/usr/bin/env bash
# Drop + recreate a local Inspira dev DB, then re-run alembic. Use between test runs
# to start clean. Default target is inspira_wave34_main; pass a session slug to target
# another DB (e.g. `./reset-local-db.sh alpha`).
set -euo pipefail

SESSION_SLUG="${1:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PG_PREFIX="/opt/homebrew/opt/postgresql@16"
PG_PORT=5432
DB_NAME="inspira_wave34_${SESSION_SLUG}"

cd "$SERVICES_DIR"

echo "[reset] Dropping + recreating $DB_NAME..."
"$PG_PREFIX/bin/psql" -h 127.0.0.1 -p "$PG_PORT" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DB_NAME;
CREATE DATABASE $DB_NAME OWNER inspira;
SQL

# Activate venv + load env so alembic can read DATABASE_URL
# shellcheck disable=SC1091
source .venv/bin/activate
set -a
# shellcheck disable=SC1091
source .env.local
set +a

# Override env if the slug doesn't match what's in .env.local
export DATABASE_URL="postgresql://inspira:inspira@127.0.0.1:$PG_PORT/$DB_NAME"
export INSPIRA_DATABASE_DIRECT_URL="$DATABASE_URL"

echo "[reset] Running alembic upgrade head on $DB_NAME..."
alembic upgrade head >/dev/null
echo "[reset] $DB_NAME is reset + migrated."
