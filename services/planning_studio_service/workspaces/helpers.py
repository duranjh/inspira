"""ID + slug helpers for workspaces.

Kept tiny on purpose — these are the same primitives migration
0005 (``workspace_backfill``) uses for the personal-workspace
backfill, mirrored here so the runtime path produces matching IDs.
"""
from __future__ import annotations

import re
import secrets


def short_uid(prefix: str = "ws-", n: int = 10) -> str:
    """Generate ``<prefix><n hex chars>``.

    n=10 gives 40 bits of entropy — adequate for ~1M workspaces
    before birthday-paradox collision risk hits 1%. The PK
    collision case still raises cleanly via SQLite's UNIQUE error.
    """
    if n <= 0 or n % 2 != 0:
        # token_hex returns full bytes; for an odd n we'd be one
        # char short. Round up and slice for safety.
        return f"{prefix}{secrets.token_hex((n + 1) // 2)[:n]}"
    return f"{prefix}{secrets.token_hex(n // 2)}"


_SLUG_INVALID = re.compile(r"[^a-z0-9-]+")
_MULTI_DASH = re.compile(r"-+")


def slugify(name: str) -> str:
    """Loose slugification suitable for personal-workspace defaults.

    Lowercases, strips non-alphanumeric (replacing with hyphens),
    collapses runs of hyphens, trims leading/trailing hyphens, and
    truncates to 40 chars. Returns ``"workspace"`` if the result
    would otherwise be empty (e.g. ``slugify("***")``).
    """
    s = name.lower().strip()
    s = _SLUG_INVALID.sub("-", s)
    s = _MULTI_DASH.sub("-", s).strip("-")
    return s[:40] or "workspace"


def make_personal_slug(user_id: str) -> str:
    """Personal-workspace slug: ``personal-<8 chars after 'user-'>``.

    Matches what migration 0005 emits for backfilled accounts. Used
    by the store helper that auto-creates a personal workspace for
    a fresh signup if the W1 first-run flow doesn't.
    """
    clean = user_id.replace("user-", "", 1)
    return f"personal-{clean[:8]}"
