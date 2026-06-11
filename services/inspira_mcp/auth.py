"""Bearer Personal Access Token resolver for the MCP + OpenAPI Actions surface.

Flow:

1. Claude.ai (MCP) or a ChatGPT Custom GPT (OpenAI Actions) sends a request
   with ``Authorization: Bearer inspira_pat_<hex>``.
2. ``resolve_bearer_token`` strips the ``Bearer `` prefix, normalises the
   token, and computes a SHA-256 hash of the raw value.
3. We look up the hash in ``user_access_tokens``.
4. On match, we bump ``last_used_at`` as a best-effort update and return
   the resolved ``user_id``. On miss (or if the row is revoked) we raise
   ``AuthError``.

Why hash lookup instead of plaintext storage:

- A DB dump therefore cannot be replayed as live PATs. The only way to
  mint a token is the explicit issue flow.
- SHA-256 is fast enough to evaluate on every request without argon2
  cost. PATs are long random strings (32 bytes / 256 bits of entropy) so
  offline brute-force is not meaningfully cheaper than guessing the raw
  token.

Resilience guarantee:

If the ``user_access_tokens`` table does not exist yet (e.g. migrations
have not run), the lookup here gracefully catches the "no such table"
error (both backends) and returns a 401, keeping the main service
bootable.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Any

logger = logging.getLogger("inspira_mcp.auth")


TOKEN_PREFIX = "inspira_pat_"


class AuthError(Exception):
    """Raised when a bearer token is missing, malformed, or unknown.

    Both surfaces map this to a 401 with a small, non-leaking detail
    payload (``{"error": "invalid_token"}``) so an attacker who probes
    with garbage tokens gets no information about whether their token
    prefix / length was close to valid.
    """

    def __init__(self, reason: str = "invalid_token") -> None:
        super().__init__(reason)
        self.reason = reason


def _normalise_header(raw: str | None) -> str | None:
    """Return the token value inside an ``Authorization`` header, or None.

    Accepts both ``Bearer <tok>`` and raw ``<tok>`` forms so a
    mis-configured client still authenticates (at the cost of some
    lenience). The raw-form is never documented but we've seen it in
    real clients so rejecting it would be user-hostile.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        value = value.split(" ", 1)[1].strip()
    return value or None


def _hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw PAT.

    Matches the scheme used by the token-issue endpoint:
    ``token_hash = sha256(token).hexdigest()``. The raw token is never
    written anywhere on our side — only this hex digest is stored in
    the DB. A DB dump therefore cannot be replayed as live tokens.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_bearer_token(store: Any, authorization_header: str | None) -> str:
    """Return the user_id owning this PAT, or raise AuthError.

    - ``store`` is a ``PlanningStudioStore``. We use ``store._connect``
      directly because the PAT lookup is orthogonal to every other
      store method and the table does not have a dedicated store
      helper.
    - ``authorization_header`` is the raw HTTP header value. Both
      FastAPI and the MCP SDK pass it through verbatim.

    Side effect: on a successful match we bump ``last_used_at`` on the
    token row. Failures never write.
    """
    token = _normalise_header(authorization_header)
    if not token:
        raise AuthError("missing_token")
    # Belt-and-suspenders: PATs we mint start with a well-known prefix.
    # Tokens that don't match are rejected before we bother the DB. This
    # both speeds up the hot path on obviously-bad input (e.g. an empty
    # or "null" string) and shrinks the attack surface.
    if not token.startswith(TOKEN_PREFIX):
        raise AuthError("invalid_token")
    token_hash = _hash_token(token)
    try:
        user_id = _lookup_token(store, token_hash)
    except _MissingTable:
        # The user_access_tokens table doesn't exist yet (migrations
        # haven't run). Treat every request as
        # unauthenticated so the rest of the service stays usable — do
        # NOT fall back to the anonymous-session flow, which would
        # silently create data under the wrong identity.
        logger.warning(
            "user_access_tokens table missing — MCP/Actions auth returning 401"
        )
        raise AuthError("tokens_table_missing")
    if user_id is None:
        raise AuthError("invalid_token")
    _touch_last_used(store, token_hash)
    return user_id


class _MissingTable(Exception):
    """Internal marker: swapped for a 401 in resolve_bearer_token."""


def _lookup_token(store: Any, token_hash: str) -> str | None:
    """Hit the DB. Return user_id, or None when the row is missing/revoked.

    Raises ``_MissingTable`` when the ``user_access_tokens`` table does
    not exist. Both SQLite and Postgres produce a distinct error for
    "relation not found"; we catch both and promote to the sentinel
    exception so the caller can convert to a 401.
    """
    try:
        with store._connect() as connection:
            row = connection.execute(
                """
                SELECT user_id, revoked_at
                FROM user_access_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "no such table" in message:
            raise _MissingTable() from exc
        raise
    except Exception as exc:  # noqa: BLE001 — psycopg raises its own type
        message = str(exc).lower()
        if "relation" in message and "does not exist" in message:
            raise _MissingTable() from exc
        if "undefinedtable" in type(exc).__name__.lower():
            raise _MissingTable() from exc
        raise
    if row is None:
        return None
    record = dict(row)
    if record.get("revoked_at"):
        return None
    user_id = record.get("user_id")
    return str(user_id) if user_id else None


def _touch_last_used(store: Any, token_hash: str) -> None:
    """Best-effort ``last_used_at`` bump. Failures are swallowed.

    An analytics / staleness signal should NEVER break the hot path.
    If the column doesn't exist (e.g. a drifted schema) or the write
    fails for any other reason, we log at debug level and move on —
    the user still gets authenticated, which is what matters.
    """
    from planning_studio_service.store import now_timestamp  # noqa: PLC0415

    try:
        with store._connect() as connection:
            connection.execute(
                "UPDATE user_access_tokens SET last_used_at = ? WHERE token_hash = ?",
                (now_timestamp(), token_hash),
            )
            connection.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("last_used_at bump failed (token_hash=%s): %s", token_hash, exc)
