#!/usr/bin/env bash
# CI guard — fail if any LAWYER CHECK / LAWYER / OPS CHECK marker sneaks back
# into a public legal document. Everything under docs/legal/*.md renders to a
# user-visible page and must be clean; markers must be replaced with finalized
# text (track pending items outside the repo).
#
# Usage: ./scripts/check_legal_placeholders.sh (from repo root)
# Exits 0 if clean, 1 with a list of offending file:line if not.

set -euo pipefail

# Grep all legal markdown. `|| true` because grep exits 1
# when zero matches, which is the SUCCESS case for us.
offenders=$(grep -nE "LAWYER\s*CHECK|LAWYER\s*/\s*OPS\s*CHECK" \
    docs/legal/*.md 2>/dev/null \
    || true)

if [ -n "$offenders" ]; then
    echo "Legal placeholder leaked into a rendered document:" >&2
    echo "$offenders" >&2
    echo "" >&2
    echo "Replace the inline marker with the finalized text (track" >&2
    echo "pending items outside the repo)." >&2
    exit 1
fi

echo "OK — no legal placeholders in rendered docs."
