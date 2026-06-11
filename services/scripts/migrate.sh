#!/usr/bin/env bash
# Usage: scripts/migrate.sh
#
# Runs `alembic upgrade head` against the database in DATABASE_URL. Intended
# for use in deploy pipelines (Fly release command, GitHub Actions deploy
# step, a crontab-driven nightly apply, etc). Prints the current revision
# before and after the upgrade so the log line is self-describing.
#
# Env:
#   DATABASE_URL  Required. Must point at a live Postgres (or SQLite for dev).
#   ALEMBIC_CONFIG  Optional. Path to alembic.ini. Defaults to
#                   ../alembic.ini relative to this script so it works from
#                   both services/ and repo-root invocations.
#
# Exit codes:
#   0  upgrade succeeded (current revision is head)
#   1  DATABASE_URL missing
#   2  alembic upgrade failed

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "migrate.sh: DATABASE_URL is not set" >&2
  exit 1
fi

# Resolve alembic.ini relative to this script. `cd` into a subshell so we
# don't mutate the caller's CWD. Works identically on Linux and macOS.
script_dir="$(cd "$(dirname "$0")" && pwd)"
default_ini="$script_dir/../alembic.ini"
alembic_ini="${ALEMBIC_CONFIG:-$default_ini}"

if [[ ! -f "$alembic_ini" ]]; then
  echo "migrate.sh: alembic.ini not found at $alembic_ini" >&2
  echo "migrate.sh: set ALEMBIC_CONFIG to override" >&2
  exit 2
fi

echo "migrate.sh: using config $alembic_ini"

# Current revision (may be empty on a fresh DB — alembic prints nothing).
# We use `|| true` because `alembic current` returns non-zero when the
# alembic_version table does not exist yet, which is expected for a brand
# new DB and should not abort the script.
before_rev="$(alembic -c "$alembic_ini" current 2>/dev/null || true)"
echo "migrate.sh: current revision (before) = ${before_rev:-<none>}"

if ! alembic -c "$alembic_ini" upgrade head; then
  echo "migrate.sh: alembic upgrade head failed" >&2
  exit 2
fi

after_rev="$(alembic -c "$alembic_ini" current 2>/dev/null || true)"
echo "migrate.sh: current revision (after) = ${after_rev:-<none>}"
echo "migrate.sh: upgrade complete"
