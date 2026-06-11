#!/usr/bin/env bash
# Usage: scripts/restore.sh <backup_file>
#
# Restores a pg_dump custom-format archive (produced by backup.sh) to the
# database pointed at by DATABASE_URL. This is destructive — it drops and
# recreates every object in the target database. The script prompts Y/n
# before doing anything irreversible so you can't lose prod by fat-finger.
#
# Args:
#   $1  Path to a .dump or .dump.gz file produced by scripts/backup.sh.
#
# Env:
#   DATABASE_URL  Required. Target database.
#   CONFIRM_RESTORE=yes  Skip the interactive prompt (for CI / scripted
#                        staging restores). NEVER set this in a prod shell.
#
# Exit codes:
#   0  success
#   1  arguments / env missing
#   2  backup file not readable
#   3  user aborted at prompt
#   4  pg_restore failed
#
# Works on Linux and macOS. Uses /bin/sh-compatible read prompts.

set -euo pipefail

backup_file="${1:-}"

if [[ -z "$backup_file" ]]; then
  echo "restore.sh: usage: restore.sh <backup_file>" >&2
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "restore.sh: DATABASE_URL is not set" >&2
  exit 1
fi

if [[ ! -r "$backup_file" ]]; then
  echo "restore.sh: cannot read $backup_file" >&2
  exit 2
fi

pg_url="${DATABASE_URL/postgresql+psycopg:\/\//postgresql:\/\/}"
pg_url="${pg_url/postgresql+psycopg2:\/\//postgresql:\/\/}"

# Extract a human-readable host:database pair from the URL for the prompt.
# Use Python if available; fall back to a crude awk parse so the prompt is
# still informative in a minimal container.
target_desc="$pg_url"
if command -v python3 >/dev/null 2>&1; then
  target_desc="$(python3 - <<'PY' "$pg_url"
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
host = u.hostname or "?"
port = u.port or "?"
db = (u.path or "/").lstrip("/") or "?"
print(f"{host}:{port}/{db}")
PY
)"
fi

echo "restore.sh: about to RESTORE $backup_file"
echo "restore.sh: target database = $target_desc"
echo "restore.sh: this will DROP and RECREATE every object in the target DB."

if [[ "${CONFIRM_RESTORE:-}" != "yes" ]]; then
  # -r disables backslash interpretation; prompt written to stderr so stdout
  # stays clean for any caller that pipes this script.
  printf 'restore.sh: continue? [y/N] ' >&2
  read -r answer
  case "$answer" in
    y|Y|yes|YES) : ;;
    *)
      echo "restore.sh: aborted by user" >&2
      exit 3
      ;;
  esac
fi

# If the dump is gzipped, decompress into a temp file. pg_restore can't read
# gzip natively for the custom format. Use mktemp in a portable way (macOS
# mktemp needs an explicit template; GNU mktemp accepts one too).
tmp_dump=""
cleanup() {
  if [[ -n "$tmp_dump" && -f "$tmp_dump" ]]; then
    rm -f "$tmp_dump"
  fi
}
trap cleanup EXIT

case "$backup_file" in
  *.gz)
    tmp_dump="$(mktemp -t inspira-restore.XXXXXX)"
    gunzip -c "$backup_file" > "$tmp_dump"
    dump_to_restore="$tmp_dump"
    ;;
  *)
    dump_to_restore="$backup_file"
    ;;
esac

# --clean drops each object before recreating it; --if-exists tolerates
# missing objects so a restore into a fresh DB doesn't error on the drops.
# --no-owner / --no-acl keep the restore neutral across different managed
# Postgres providers (Neon, Supabase, Fly) where the owning role differs.
if ! pg_restore \
    --clean --if-exists --no-owner --no-acl \
    --dbname="$pg_url" \
    "$dump_to_restore"; then
  echo "restore.sh: pg_restore failed" >&2
  exit 4
fi

echo "restore.sh: restore complete"
