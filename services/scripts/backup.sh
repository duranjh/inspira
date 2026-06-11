#!/usr/bin/env bash
# Usage: scripts/backup.sh [output_dir]
#
# Dumps the current DATABASE_URL to a timestamped .dump.gz file using
# pg_dump --format=custom. The custom format is what pg_restore expects and
# compresses better than plain SQL; we still wrap it in gzip so the artifact
# is trivially shippable to object storage (S3/R2/etc).
#
# Args:
#   $1  Output directory. Defaults to ./backups/ (created if missing).
#
# Env:
#   DATABASE_URL  Required. postgresql+psycopg:// is normalized to postgres://
#                 for the pg_* binaries.
#
# Exit codes:
#   0  success (prints the absolute path of the dump on stdout)
#   1  DATABASE_URL missing
#   2  pg_dump failed
#   3  output directory not writable
#
# Works on Linux and macOS. Uses `date -u` with a POSIX-portable format
# string, avoiding GNU-only flags like `--iso-8601=seconds`.

set -euo pipefail

out_dir="${1:-./backups}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "backup.sh: DATABASE_URL is not set" >&2
  exit 1
fi

# SQLAlchemy-style driver prefixes (postgresql+psycopg://) are not understood
# by libpq; strip the driver hint so pg_dump sees a clean postgres:// URL.
pg_url="${DATABASE_URL/postgresql+psycopg:\/\//postgresql:\/\/}"
pg_url="${pg_url/postgresql+psycopg2:\/\//postgresql:\/\/}"

mkdir -p "$out_dir" || {
  echo "backup.sh: cannot create output dir $out_dir" >&2
  exit 3
}

if [[ ! -w "$out_dir" ]]; then
  echo "backup.sh: output dir $out_dir is not writable" >&2
  exit 3
fi

timestamp="$(date -u +"%Y%m%d-%H%M%S")"
dump_path="$out_dir/inspira-${timestamp}.dump"
gz_path="${dump_path}.gz"

echo "backup.sh: dumping DATABASE_URL to $gz_path" >&2

if ! pg_dump --format=custom --no-owner --no-acl --file="$dump_path" "$pg_url"; then
  echo "backup.sh: pg_dump failed" >&2
  rm -f "$dump_path"
  exit 2
fi

# gzip the custom-format dump. Custom format is already compressed internally
# but gzip shrinks the outer envelope another ~10-20% and makes the file
# mime-sniffable as application/gzip.
gzip -f "$dump_path"

# Absolute path for scripting callers.
abs_path="$(cd "$(dirname "$gz_path")" && pwd)/$(basename "$gz_path")"
echo "$abs_path"
