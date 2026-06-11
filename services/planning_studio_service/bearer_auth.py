"""Bearer-token authentication for Inspira Personal Access Tokens (PATs).

The v2 API's primary auth is a signed session cookie (see ``auth.py``).
External automations -- Zapier workflows, the Inspira MCP server, a
user's own CLI script -- can't carry a browser cookie, so they
authenticate via an HTTP header:

    Authorization: Bearer inspira_pat_<32 hex>

A PAT grants the owner's full read+write access (v1 ships with empty
``scopes_json`` which resolves to full access; scope-narrowing is a
follow-up that lives on the ``user_access_tokens`` row so it doesn't
need another migration).

Design notes
------------

* **Fallthrough, not override.** ``try_resolve_bearer_user`` returns the
  user dict when a valid PAT is present, else ``None``.  The caller
  (the existing ``_current_user`` dependency in ``api.py``) falls
  through to the cookie path when ``None`` comes back, so a request
  carrying both a bearer and a cookie always goes through the PAT path
  first -- an externally-triggered call should not accidentally pick
  up session state from a logged-in browser tab sharing the same
  origin.

* **Malformed header is a soft miss.** A garbled ``Authorization`` value
  (empty after ``Bearer ``, missing prefix, wrong scheme) resolves to
  ``None`` rather than raising.  The final 401 is owned by the route's
  own auth check so a client accidentally forwarding a stale header
  still gets a clean ``401 unauthorized`` instead of a 500.

* **Rate limiting bucket.** Bearer-authed requests get their OWN bucket
  (100/min per token) separate from cookie sessions (60/min per
  session).  An integration is expected to burst higher than a
  human-driven tab, and penalising PATs into the session bucket would
  also put ordinary browser users behind the more-lenient integration
  bucket -- wrong on both ends.

* **Why not Depends()?** Making this a regular FastAPI dependency means
  every PAT-authed request goes through a separate auth pass, and the
  existing cookie dependency can't decide whether to run.  Instead we
  call ``try_resolve_bearer_user`` INSIDE the cookie dependency and
  short-circuit the cookie read when a PAT authenticates.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from .store import PlanningStudioStore


logger = logging.getLogger("planning_studio.bearer_auth")


_BEARER_SCHEME_PREFIX = "Bearer "


def _extract_bearer_value(header: str | None) -> str | None:
    """Parse the ``Authorization`` header, return the raw bearer value.

    Returns ``None`` for any shape that isn't a well-formed
    ``Bearer <value>`` pair.  Case-insensitive on the scheme word per
    RFC 7235; whitespace after the scheme word is collapsed.
    """
    if not header:
        return None
    stripped = header.strip()
    if not stripped:
        return None
    # Scheme is case-insensitive.  We check the lowercased prefix once
    # and then slice the original so the value keeps its casing.
    lower = stripped.lower()
    if not lower.startswith("bearer "):
        return None
    value = stripped[len(_BEARER_SCHEME_PREFIX):].strip()
    return value or None


def try_resolve_bearer_user(
    request: Request,
    store: PlanningStudioStore,
) -> dict[str, Any] | None:
    """Return the user dict for a valid bearer token, else ``None``.

    Reads ``Authorization`` from the request headers; if it carries an
    Inspira PAT (``inspira_pat_<hex>``) and the token is not revoked,
    returns the owner's user row.  Returns ``None`` for every other
    case -- no header, wrong scheme, unknown token, revoked token --
    so the caller can fall through to the cookie path.

    Stashes the resolved ``token_id`` on ``request.state.bearer_token_id``
    so downstream rate-limit / audit logic can key off of it.  Never
    raises -- logging + ``None`` is the error budget.
    """
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization",
    )
    raw_token = _extract_bearer_value(auth_header)
    if raw_token is None:
        return None
    try:
        user_id = store.resolve_access_token(raw_token)
    except Exception as exc:  # noqa: BLE001 -- store raised, don't 500 on auth
        logger.warning("bearer auth lookup raised: %s", exc)
        return None
    if not user_id:
        return None
    user = store.get_user_by_id(user_id)
    if user is None:
        # Token row references a user that's been deleted.  Treat like
        # a revoked token -- the integration owner's account is gone.
        return None
    # Stash for any downstream middleware / telemetry that wants to key
    # off the authenticating token (rate-limit bucket, audit log, etc.).
    # Use try/except because request.state is Starlette-specific and
    # this module must stay usable with a mock request in tests.
    try:
        request.state.bearer_token_user_id = user_id  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return user


# Rate limits.  Tunable via env at the slowapi wiring site in api.py;
# these constants are the defaults the UI and docs should reference.
#
# ``BEARER_RATE_LIMIT`` -- burst budget for a single PAT.  100/minute
# keeps a Zapier-style poll-every-few-seconds integration comfortable
# (12 polls/min for one trigger, times ~5 triggers, still comfortable)
# while making a runaway loop visible.
#
# ``COOKIE_RATE_LIMIT`` -- per-session budget for browser traffic.
# 60/minute is one call/sec average, which is plenty for the canvas
# (mutation loops are already debounced) and tight enough to flag a
# misbehaving tab.
BEARER_RATE_LIMIT = "100/minute"
COOKIE_RATE_LIMIT = "60/minute"


def bearer_rate_limit_key(request: Request) -> str:
    """slowapi key function -- route a request to its right bucket.

    When ``try_resolve_bearer_user`` stamped ``state.bearer_token_user_id``
    the request is PAT-authed and we bucket by user_id so the 100/min
    limit is per-owner (one user's runaway script doesn't take down
    another user's integration).  Otherwise we bucket by remote-IP the
    same way ``slowapi.util.get_remote_address`` would.
    """
    bearer_user = getattr(request.state, "bearer_token_user_id", None)
    if bearer_user:
        return f"pat:{bearer_user}"
    # Fallback: remote IP.  Matches slowapi's default key_func so a
    # request that failed bearer auth and falls back to the cookie
    # path still respects the global per-IP budget.
    client = request.client
    if client is not None:
        return f"ip:{client.host}"
    return "ip:unknown"


__all__ = [
    "try_resolve_bearer_user",
    "bearer_rate_limit_key",
    "BEARER_RATE_LIMIT",
    "COOKIE_RATE_LIMIT",
]
