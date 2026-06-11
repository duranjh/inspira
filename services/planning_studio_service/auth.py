"""Authentication + session layer for Inspira.

Approach:
- Password auth with argon2 hashing (no plaintext ever stored).
- Signed cookie sessions via itsdangerous — no session table needed for the v1.
  Each cookie carries the user_id + issued-at; the signing key detects
  tampering and the TTL rejects stale cookies.
- Google OAuth is scaffolded as a second provider — the route exists but
  raises NotImplementedError when the ``GOOGLE_OAUTH_*`` env vars are absent.
  Wiring is a small follow-up once the user creates a Google Cloud project.

Fallthrough behavior for the transitional period:
- The existing single-tenant UI used a hardcoded DEFAULT_PROJECT_ID. Until
  every user in a deployment has logged in, the ``current_user`` dependency
  resolves to a bootstrap "system" user owning legacy rows. New rows from
  authenticated users get scoped properly; legacy rows stay visible to the
  system user until explicitly re-assigned.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from .store import PlanningStudioStore


# RFC 6761 reserves these TLDs as non-routable for documentation / testing /
# loopback use. The ``email-validator`` package that backs ``EmailStr`` only
# rejects a subset (it accepts any syntactically valid address), so spam,
# fake signups and QA regressions regularly slip through with addresses like
# ``qa@planning.example``. Blocking them at the schema layer returns a 422
# that the frontend already renders as a validation error — no other
# behavior change is required on the client.
_RESERVED_DOC_TLDS = frozenset({"example", "invalid", "test", "localhost"})


def _domain_has_reserved_tld(email: str) -> bool:
    """Return True when ``email``'s domain is one of RFC 6761's reserved TLDs.

    Matches both the ``@example`` loopback shape and the more common
    ``@something.example`` / ``@foo.test`` subdomain shape.
    """
    domain = email.rsplit("@", 1)[-1].lower().strip().rstrip(".")
    if not domain:
        return False
    # Exact-match (e.g. ``@localhost``) OR final-label match (e.g.
    # ``@foo.example``). We split on dot and check the last segment.
    if domain in _RESERVED_DOC_TLDS:
        return True
    last_label = domain.rsplit(".", 1)[-1]
    return last_label in _RESERVED_DOC_TLDS

logger = logging.getLogger("planning_studio.auth")


# ---------------------------------------------------------------------------
# Log redaction helpers
# ---------------------------------------------------------------------------
#
# Audit follow-up: keep PII out of the log stream. These helpers exist so
# any future ``logger.X(..., user)`` style call has a one-liner to reach
# for instead of inlining the field pick. The mail/sender module already
# carries its own ``_redact_email`` for the to-address case; we re-export
# it here so auth call sites don't need to reach into the mail package.
from .mail.sender import _redact_email as _redact_email  # noqa: E402,F401 — re-export


def _redact_user(user: Any) -> dict[str, Any]:
    """Return the log-safe projection of a user row.

    Only ``user_id`` survives — never ``email``, never ``password_hash``,
    never anything else. ``user_id`` is a server-side opaque identifier
    so it's safe to log; everything else on the row is PII or worse.

    Tolerates ``None`` and non-dict inputs so a caller does not need to
    branch before logging.
    """
    if not isinstance(user, dict):
        return {"user_id": None}
    return {"user_id": user.get("user_id")}


SESSION_COOKIE_NAME = "inspira_session"
# 30 days; the cookie renews on each authenticated request via middleware
# (see ``current_user_dependency`` below).
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600


# Memoized per process. Rotating the secret requires a process restart
# (which invalidates every live session anyway — that's the expected
# blast radius of a rotation). Avoiding the per-call env read keeps the
# login hot path tight.
#
# Thread-safety: ``URLSafeTimedSerializer`` is stateless config so two
# transient instances are functionally equivalent, but the lock removes
# the theoretical race and makes the singleton-cache contract explicit.
_session_serializer_cached: URLSafeTimedSerializer | None = None
_session_serializer_lock = threading.Lock()


def _session_serializer() -> URLSafeTimedSerializer:
    global _session_serializer_cached
    if _session_serializer_cached is not None:
        return _session_serializer_cached
    with _session_serializer_lock:
        # Re-check inside the lock — another thread may have populated.
        if _session_serializer_cached is not None:
            return _session_serializer_cached
        secret = os.environ.get("INSPIRA_SESSION_SECRET", "").strip()
        if not secret:
            # Dev-only fallback. Production MUST set this; Dockerfile deploy
            # docs call it out. If the key changes, all existing sessions
            # invalidate — that's the expected blast radius of a rotation.
            secret = "inspira-dev-only-change-me"
            logger.warning(
                "INSPIRA_SESSION_SECRET not set; using dev fallback. "
                "Production deploys MUST set this env var to a random 32+ byte value.",
            )
        _session_serializer_cached = URLSafeTimedSerializer(
            secret, salt="inspira-session",
        )
    return _session_serializer_cached


# Short-lived ticket serializer used for WebSocket handshake auth.
# The session cookie is httpOnly so JS can't read it, and SameSite=Lax
# isn't reliably sent on cross-origin (cross-subdomain) WS upgrades.
# Instead: client fetches a signed ticket over HTTPS (cookie flows
# normally on the fetch), includes it as ?auth=<ticket> on the WS URL,
# and the realtime handler accepts it as a session-equivalent.
# TTL is short (90s) so a leaked ticket has limited blast radius.
_WS_TICKET_MAX_AGE_SECONDS = 90


def _ws_ticket_serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("INSPIRA_SESSION_SECRET", "").strip()
    if not secret:
        secret = "inspira-dev-only-change-me"
    return URLSafeTimedSerializer(secret, salt="inspira-ws-ticket")


def mint_ws_ticket(user_id: str) -> str:
    """Mint a signed ticket valid for ~90 seconds. Contains just the
    user_id — the realtime handler resolves the user fresh on accept.
    """
    return _ws_ticket_serializer().dumps({"uid": user_id})


def resolve_ws_ticket(ticket: str) -> str | None:
    """Verify a WS ticket and return the user_id, or None if invalid /
    expired / tampered. Fail-closed on any error."""
    try:
        payload = _ws_ticket_serializer().loads(
            ticket, max_age=_WS_TICKET_MAX_AGE_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return None
    uid = payload.get("uid") if isinstance(payload, dict) else None
    return uid if isinstance(uid, str) and uid else None


# Argon2 parameters, tuned for a small deploy (Fly shared-cpu-1x / 256MB)
# without becoming the bottleneck on login concurrency (audit M4).
# - time_cost=3 iterations — OWASP-recommended minimum.
# - memory_cost=65536 KiB (64 MiB) — OWASP-recommended minimum; comfortable
#   for small instances, expensive enough to resist GPU cracking.
# - parallelism=2 — two threads per hash; argon2-cffi can use more but we
#   keep it low on shared-cpu tiers.
# Rotate UP on bigger instances; argon2-cffi re-hashes transparently when
# the stored hash's cost is below the current parameters (see
# `PasswordHasher.check_needs_rehash`).
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 65536
_ARGON2_PARALLELISM = 2


# Memoized per process. Hashes carry their own params so even after
# bumping the cost knobs, ``check_needs_rehash`` can detect old hashes
# at login time and the rotated hasher writes new hashes correctly.
#
# Thread-safety: PasswordHasher is stateless config (the cost params
# are immutable once constructed), so a transient duplicate is
# functionally equivalent — but the lock keeps the singleton-cache
# contract clean and silences the obvious code-review concern.
_password_hasher_cached: Any = None
_password_hasher_lock = threading.Lock()


def _password_hasher():
    """Factory — imports lazily so non-auth tests skip argon2-cffi cost.
    Memoized after first call so login doesn't pay the import + class
    construction overhead per request.
    """
    global _password_hasher_cached
    if _password_hasher_cached is not None:
        return _password_hasher_cached
    with _password_hasher_lock:
        # Re-check inside the lock — another thread may have populated.
        if _password_hasher_cached is not None:
            return _password_hasher_cached
        from argon2 import PasswordHasher

        _password_hasher_cached = PasswordHasher(
            time_cost=_ARGON2_TIME_COST,
            memory_cost=_ARGON2_MEMORY_COST,
            parallelism=_ARGON2_PARALLELISM,
        )
    return _password_hasher_cached


def _verify_password(password: str, hashed: str) -> bool:
    """Constant-time-ish password verification.

    Argon2-cffi's ``PasswordHasher.verify(hash, password)`` argument order
    is ``(hash, password)`` per the upstream docs — the call below is
    correct. Future contributors: do not "fix" this to ``verify(password,
    hash)``; that swap silently turns every login into a mismatch.
    """
    from argon2.exceptions import VerifyMismatchError

    try:
        _password_hasher().verify(hashed, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception as exc:  # noqa: BLE001
        # InvalidHash, malformed hash from a corrupted row, etc. Treat
        # as a failed verification but log so on-call sees the data
        # corruption signal.
        logger.warning("argon2 verify raised: %s", exc)
        return False


def _hash_password(password: str) -> str:
    return _password_hasher().hash(password)


SYSTEM_USER_ID = "user-system"
SYSTEM_USER_EMAIL = "system@inspira.local"

# Anonymous visitors get a per-session user row so their projects are
# genuinely theirs (not shared with every other guest) while still
# presenting as ``is_system=True`` to the frontend — the UI contract
# "not yet signed in" covers both the legacy shared system user and
# modern anonymous sessions. User_ids use the ``user-anon-`` prefix so
# we can identify them for the anonymous-to-account transfer flow.
ANON_USER_ID_PREFIX = "user-anon-"
ANON_USER_EMAIL_DOMAIN = "anon.inspira.local"


def _is_anon_user_id(user_id: str) -> bool:
    return bool(user_id) and user_id.startswith(ANON_USER_ID_PREFIX)


def _ensure_system_user(store: PlanningStudioStore) -> dict[str, Any]:
    """Idempotently ensure the fallback ``system`` user exists.

    Called lazily on first auth check. Owns all pre-migration rows so the
    legacy single-tenant UI keeps working until users sign in.
    """
    user = store.get_user_by_id(SYSTEM_USER_ID)
    if user is not None:
        return user
    return store.create_user(
        user_id=SYSTEM_USER_ID,
        email=SYSTEM_USER_EMAIL,
        password_hash=None,
        display_name="System",
    )


def _create_anon_user(store: PlanningStudioStore) -> dict[str, Any]:
    """Mint a fresh anonymous user row.

    Each anonymous visitor gets their own ``user-anon-<hex>`` row so their
    projects are scoped to them, not commingled with the shared system
    user. When they later sign up we move every row with this user_id
    over to their new account (see ``/api/v2/auth/transfer-anonymous-projects``).
    """
    import uuid as _uuid

    anon_id = f"{ANON_USER_ID_PREFIX}{_uuid.uuid4().hex[:12]}"
    # The email column is UNIQUE NOT NULL; we fabricate a per-id address
    # in a reserved domain so two anon users never collide. Nothing ever
    # emails it — the domain is intentionally non-routable.
    anon_email = f"{anon_id}@{ANON_USER_EMAIL_DOMAIN}"
    return store.create_user(
        user_id=anon_id,
        email=anon_email,
        password_hash=None,
        display_name="Guest",
    )


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = ""
    # Terms-of-service acceptance. Required on the happy path — a missing
    # or false value returns 400 with ``{"error": "terms_required"}``. We
    # default to False so older clients that forget the field surface the
    # same clear error, not a silent acceptance.
    terms_accepted: bool = False

    @field_validator("email")
    @classmethod
    def _reject_reserved_tlds(cls, value: str) -> str:
        # EmailStr already rejects anything the ``email-validator`` lib
        # considers malformed; this extra pass catches RFC 6761 reserved
        # TLDs (``.example``, ``.invalid``, ``.test``, ``localhost``) which
        # are syntactically valid but never resolve to a real mailbox.
        if _domain_has_reserved_tld(value):
            raise ValueError(
                "email domain uses a reserved TLD and cannot receive mail",
            )
        return value


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    # The raw token from the emailed URL. Stored as the SHA-256 hash in
    # the DB; we compare hashes inside store.consume_password_reset_token.
    token: str = Field(min_length=1, max_length=256)
    # Same minimum the signup path uses -- keep the rule uniform so a user
    # can't set a password at reset that signup wouldn't have accepted.
    new_password: str = Field(min_length=8, max_length=256)

    @model_validator(mode="before")
    @classmethod
    def _normalise_password_alias(cls, data: Any) -> Any:
        """Accept the legacy ``password`` field name in addition to ``new_password``.

        Older clients POSTed ``{token, password}`` on this endpoint while
        every other auth endpoint uses ``password``. The field was
        rebranded to ``new_password`` to make the reset intent explicit,
        but older cached frontend bundles and third-party scripts still
        send the short form. Accept both so we stop emitting 422s at
        clients we cannot redeploy.

        The ``new_password`` value always wins if both are present — no
        need to second-guess a client that sent the canonical name.
        TODO(remove-alias): drop once fly logs show zero ``password``-only
        calls for a full week.
        """
        if not isinstance(data, dict):
            return data
        if "new_password" in data and data.get("new_password") not in (None, ""):
            return data
        alias = data.get("password")
        if alias is not None:
            # Only copy, never swap in place — keeps callers' dicts intact.
            data = dict(data)
            data["new_password"] = alias
            data.pop("password", None)
        return data


class AuthedUser(BaseModel):
    user_id: str
    email: str
    display_name: str
    is_system: bool
    # The workspace the frontend should treat as the user's "home" — set
    # once the user creates / joins a workspace. None for anon users and
    # for signed-in users who haven't gone through workspace setup yet.
    # The post-login Kanban Workspace Home keys off this field; absent it
    # the UI falls back to the legacy ProjectsListPage.
    default_workspace_id: str | None = None


class TransferAnonymousProjectsBody(BaseModel):
    # ``user-anon-<hex>`` id the caller claims to own. Must match the
    # ``previous_anon_user_id`` stamped on the caller's current session
    # cookie at signup/login — otherwise we refuse, so a signed-in user
    # cannot claim anyone's anonymous projects by guessing the id.
    anonymous_user_id: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter()




# Optional escape hatch for cross-site preview deployments (e.g. one fresh
# Cloudflare Pages subdomain per PR build): when a request Origin matches the
# operator-supplied ``INSPIRA_PREVIEW_ORIGIN_REGEX``, the session cookie is
# minted with ``SameSite=None`` so cross-site auth round-trips work. Unset
# (the default) the carve-out is disabled and every origin gets SameSite=Lax.
# Only set this to a pattern whose entire match space you control.
def _preview_origin_pattern() -> re.Pattern[str] | None:
    raw = os.environ.get("INSPIRA_PREVIEW_ORIGIN_REGEX", "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw)
    except re.error:
        return None


def _samesite_for_origin(request: Request | None) -> str:
    """Pick the SameSite attribute based on the request Origin.

    Returns ``"none"`` for origins matching the operator-supplied
    ``INSPIRA_PREVIEW_ORIGIN_REGEX`` (cross-site preview deployments that
    require cross-site cookies) and ``"lax"`` for everything else. A
    missing Origin (typed from a tool, etc.) falls back to ``"lax"`` so prod
    callers keep their stricter default.
    """
    if request is None:
        return "lax"
    pattern = _preview_origin_pattern()
    origin = (request.headers.get("origin") or "").strip()
    if origin and pattern is not None and pattern.match(origin):
        return "none"
    return "lax"


def _set_session_cookie(
    response: Response,
    user_id: str,
    *,
    previous_anon_user_id: str | None = None,
    request: Request | None = None,
) -> None:
    """Set the signed session cookie on ``response``.

    ``previous_anon_user_id`` is stashed on the signed payload when we
    rotate from an anonymous session to a real signed-in user. The
    transfer endpoint reads it to authorise moving the anon user's
    projects to the new account — without this, a signed-in user
    could claim any anonymous id they happen to know.

    ``request`` (Option A, #143): when provided, the SameSite attribute is
    chosen based on the request Origin — CF Pages preview origins get
    ``SameSite=None`` so cross-site auth works in PR previews; everything
    else stays on ``SameSite=Lax``. Pass ``None`` (the default) to preserve
    the historical Lax behavior — used by tests and any callers that don't
    have a request handy.
    """
    serializer = _session_serializer()
    payload: dict[str, Any] = {"user_id": user_id, "iat": int(time.time())}
    if previous_anon_user_id and _is_anon_user_id(previous_anon_user_id):
        payload["previous_anon_user_id"] = previous_anon_user_id
    token = serializer.dumps(payload)
    # Secure=True requires HTTPS. Default behavior is now safe-by-default:
    # production environments get Secure unconditionally; dev/test default
    # to insecure so localhost over plain HTTP still works without env
    # plumbing. INSPIRA_COOKIE_SECURE explicitly overrides either way
    # (e.g. setting to "false" in a prod-ish staging that runs over HTTP
    # behind a private tunnel; or setting to "true" against a local
    # HTTPS dev proxy). SameSite=Lax stays the default — Strict would
    # break the marketing-to-app login hop.
    _env_name = os.environ.get("ENVIRONMENT", "development").lower()
    _secure_default = "true" if _env_name == "production" else "false"
    secure = os.environ.get(
        "INSPIRA_COOKIE_SECURE", _secure_default,
    ).lower() == "true"
    samesite = _samesite_for_origin(request)
    # SameSite=None requires Secure=True per the browser spec. If a caller
    # explicitly set INSPIRA_COOKIE_SECURE=false in a dev env, the browser
    # will reject the SameSite=None cookie outright — fall back to Lax so the
    # cookie at least lands locally. Production has Secure=True by default so
    # this branch is only relevant on a HTTP dev tunnel from a preview origin
    # (vanishingly rare).
    if samesite == "none" and not secure:
        samesite = "lax"
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def _peek_session_user_id(session_cookie: str | None) -> str | None:
    """Best-effort extraction of the user_id from a session cookie.

    Used by signup/login to pick up the caller's prior anonymous id so
    we can stash it on the new session and later authorise the
    anonymous-to-account transfer. Returns ``None`` on any parse / sig
    failure — the feature is purely additive so a missing prior id just
    means the user has no anon projects to transfer.
    """
    if not session_cookie:
        return None
    serializer = _session_serializer()
    try:
        payload = serializer.loads(session_cookie, max_age=SESSION_MAX_AGE_SECONDS)
    except Exception:  # noqa: BLE001 — any failure → no prior id, soft path
        return None
    user_id = str(payload.get("user_id", "")).strip()
    return user_id or None


def _peek_session_previous_anon_user_id(session_cookie: str | None) -> str | None:
    """Read the ``previous_anon_user_id`` stamp from the signed session.

    Only non-null on sessions minted by a signup/login that happened on
    top of an anonymous session. The value authorises the transfer
    endpoint — without it we'd have no way to verify that a caller
    claiming a given anon id actually owned it.
    """
    if not session_cookie:
        return None
    serializer = _session_serializer()
    try:
        payload = serializer.loads(session_cookie, max_age=SESSION_MAX_AGE_SECONDS)
    except Exception:  # noqa: BLE001
        return None
    raw = payload.get("previous_anon_user_id")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    return raw if raw and _is_anon_user_id(raw) else None


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _queue_welcome_email(
    *, to_email: str, display_name: str, app_url: str,
) -> None:
    """Render + dispatch the welcome template via the mail module.

    Mirrors ``_queue_password_reset_email``: best-effort, swallows any
    provider failure because the user already got their 201 for the
    signup and we do not want a mail outage to block account creation.
    """
    try:
        from .mail import get_email_sender

        sender = get_email_sender()
        sender.send(
            to_email=to_email,
            template_id="welcome",
            context={
                "display_name": display_name or to_email.split("@", 1)[0],
                "app_url": app_url,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- best effort, see docstring
        logger.warning("welcome email dispatch failed: %s", exc)


def _queue_verify_email(
    *, to_email: str, display_name: str, verify_url: str,
) -> None:
    """Render + dispatch the email-confirmation note. Best-effort."""
    try:
        from .mail import get_email_sender

        sender = get_email_sender()
        sender.send(
            to_email=to_email,
            template_id="verify_email",
            context={
                "display_name": display_name or to_email.split("@", 1)[0],
                "verify_url": verify_url,
                "expires_in_human": "24 hours",
            },
        )
    except Exception as exc:  # noqa: BLE001 -- best effort, see docstring
        logger.warning("verify_email dispatch failed: %s", exc)


def _queue_password_changed_email(
    *, to_email: str, display_name: str, reset_link: str,
) -> None:
    """Render + dispatch the "your password was changed" receipt.

    Sent AFTER a successful reset so the owner has a paper trail. If
    the change wasn't them, the note includes a fresh reset link they
    can use to regain control.
    """
    try:
        from .mail import get_email_sender

        sender = get_email_sender()
        sender.send(
            to_email=to_email,
            template_id="password_changed",
            context={
                "display_name": display_name or to_email.split("@", 1)[0],
                "reset_link": reset_link,
                # Portable format — e.g. "April 23, 2026 at 17:43 UTC".
                # Strips the leading zero from the day for readability.
                "changed_at_human": time.strftime(
                    "%B %d, %Y at %H:%M UTC", time.gmtime(),
                ).replace(" 0", " "),
            },
        )
    except Exception as exc:  # noqa: BLE001 -- best effort, see docstring
        logger.warning("password_changed dispatch failed: %s", exc)


def _is_unique_email_violation(exc: BaseException) -> bool:
    """Recognise the "email already exists" error from either DB backend.

    The ``users.email`` column carries a UNIQUE constraint, so two parallel
    signups for the same address — both passing the pre-insert
    ``get_user_by_email`` check because the first one hasn't committed yet —
    race into a unique-violation on the loser's INSERT. psycopg raises
    :class:`psycopg.errors.UniqueViolation`; sqlite3 raises
    :class:`sqlite3.IntegrityError` with the message
    ``UNIQUE constraint failed: users.email``.

    This helper collapses both onto a bool so the signup route can map the
    race to the same 409 the non-racing path returns, instead of leaking a
    500 through the global handler.
    """
    # psycopg UniqueViolation — lazy-import so sqlite-only test envs don't
    # need psycopg installed.
    try:
        from psycopg import errors as _pg_errors  # type: ignore[import-not-found]

        if isinstance(exc, _pg_errors.UniqueViolation):
            return True
    except Exception:  # noqa: BLE001 -- psycopg absent is fine
        pass
    # sqlite3 IntegrityError — we do not want to catch every IntegrityError
    # (could be a different NOT NULL or CHECK violation), only the specific
    # UNIQUE-on-users.email case. Match on the exception string.
    import sqlite3 as _sqlite3

    if isinstance(exc, _sqlite3.IntegrityError):
        message = str(exc).lower()
        if "unique constraint failed" in message and "users.email" in message:
            return True
        # Some sqlite builds phrase it as just "column email is not unique".
        if "email is not unique" in message:
            return True
    return False


@router.post("/signup", status_code=201)
def signup_route(
    request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
    body: SignupBody, response: Response,
    background_tasks: BackgroundTasks,
    inspira_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> AuthedUser:
    # Terms acceptance gate — the frontend gates the submit button on the
    # checkbox, but a script-driven signup that bypasses the UI must still
    # see a clean 400 rather than silently creating an unaccepted account.
    if not body.terms_accepted:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "terms_required",
                "message": (
                    "You must accept the Terms and Privacy Policy to create "
                    "an account."
                ),
            },
        )
    email = body.email.lower().strip()
    existing = store.get_user_by_email(email)
    if existing is not None:
        raise HTTPException(status_code=409, detail={"error": "email_in_use"})
    try:
        # Stamp the acceptance timestamp on the row so audits can prove
        # which version of Terms each user agreed to (by created_at time).
        accepted_at = datetime.now(timezone.utc)
        user = store.create_user(
            email=email,
            password_hash=_hash_password(body.password),
            display_name=body.display_name.strip() or email.split("@")[0],
            terms_accepted_at=accepted_at,
        )
    except Exception as exc:  # noqa: BLE001 -- intentionally broad, see helper
        # Two parallel signups with the same email can both pass the
        # ``get_user_by_email`` check above because the first hasn't
        # committed yet. The loser's INSERT then hits the UNIQUE
        # constraint. Map that race onto the same 409 the serial
        # duplicate path returns rather than letting it surface as a 500.
        if _is_unique_email_violation(exc):
            raise HTTPException(
                status_code=409, detail={"error": "email_in_use"},
            ) from exc
        raise
    # Stash the prior anonymous id on the new session so the frontend
    # can call /api/v2/auth/transfer-anonymous-projects to move any
    # canvases they built as a guest onto their new account.
    prior = _peek_session_user_id(inspira_session)
    _set_session_cookie(
        response,
        user["user_id"],
        previous_anon_user_id=prior if prior and _is_anon_user_id(prior) else None,
        request=request,
    )
    # Send the welcome note in the background so signup latency is the
    # hash + DB write, not the mail provider's roundtrip.
    background_tasks.add_task(
        _queue_welcome_email,
        to_email=user["email"],
        display_name=user["display_name"] or "",
        app_url=_resolve_frontend_base_url(request),
    )
    # Email-verification: mint a random token, store its sha256 hash on
    # the user row, and mail the raw token inside a /email-confirm link.
    # Best-effort — a mail outage or a missing column on an older DB
    # (retrofit may be in flight) must NOT fail the 201 signup.
    try:
        import hashlib
        import secrets

        raw_token = secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        try:
            store.set_email_verification_token(user["user_id"], token_hash)
        except Exception as exc:  # noqa: BLE001 — best-effort persist
            logger.warning("verify_email token persist failed: %s", exc)
        frontend_base = _resolve_frontend_base_url(request)
        verify_url = f"{frontend_base}/email-confirm?token={raw_token}"
        background_tasks.add_task(
            _queue_verify_email,
            to_email=user["email"],
            display_name=user["display_name"] or "",
            verify_url=verify_url,
        )
    except Exception as exc:  # noqa: BLE001 — never fail signup on mail
        logger.warning("verify_email wiring failed: %s", exc)
    return AuthedUser(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"],
        is_system=False,
    )


# Pre-computed dummy argon2 hash used to equalize login response times
# when the email doesn't exist (audit M3 — email enumeration defense).
# Generated once at module import. The value isn't secret — it's a hash of
# a throwaway password. We verify against it so the attacker sees the
# same ~argon2-verify latency regardless of whether the email is in the DB.
_DUMMY_HASH: str | None = None


def _get_dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hash_password("inspira-constant-time-dummy")
    return _DUMMY_HASH


def _queue_new_signin_email(
    *, to_email: str, display_name: str,
    device_label: str, location_label: str,
    signed_in_at_human: str, two_factor_link: str,
) -> None:
    """Best-effort ``new_signin`` email — fires when a device fingerprint
    we have not seen before signs into an account."""
    try:
        from .mail import get_email_sender

        sender = get_email_sender()
        sender.send(
            to_email=to_email,
            template_id="new_signin",
            context={
                "display_name": display_name or to_email.split("@", 1)[0],
                "device_label": device_label,
                "location_label": location_label,
                "signed_in_at_human": signed_in_at_human,
                "two_factor_link": two_factor_link,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- best effort
        logger.warning("new_signin dispatch failed: %s", exc)


def _device_fingerprint(request: Request) -> str:
    """sha256 of ``(client_ip, user_agent)`` — stable identifier for the
    "first-seen device" gate. Not cryptographically binding; the point is
    to remember devices the user has already confirmed."""
    import hashlib as _hashlib

    ua = request.headers.get("user-agent") or ""
    # starlette exposes request.client.host; fall back to "unknown" when
    # the connection is unusual (e.g. tests that bypass the socket layer).
    ip = getattr(getattr(request, "client", None), "host", "") or ""
    raw = f"{ip}|{ua}"
    return _hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _device_label_from_user_agent(request: Request) -> str:
    """Short human label for the new-signin email. Quiet heuristic — the
    exact UA string is ugly, so we pick the browser and OS."""
    ua = (request.headers.get("user-agent") or "").lower()
    if "firefox" in ua:
        browser = "Firefox"
    elif "edg/" in ua:
        browser = "Edge"
    elif "chrome" in ua:
        browser = "Chrome"
    elif "safari" in ua:
        browser = "Safari"
    else:
        browser = "a browser"
    if "mac os x" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "windows" in ua:
        os_name = "Windows"
    elif "linux" in ua:
        os_name = "Linux"
    elif "iphone" in ua:
        os_name = "iPhone"
    elif "android" in ua:
        os_name = "Android"
    else:
        os_name = "an unknown device"
    return f"{browser} on {os_name}"


@router.post("/login")
def login_route(
    request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
    body: LoginBody, response: Response,
    background_tasks: BackgroundTasks,
    inspira_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> AuthedUser:
    """Log in.

    Email-enumeration defense (audit M3): when the email doesn't exist OR
    has no password hash (OAuth-only account), we STILL run argon2 verify
    against a pre-computed dummy hash so the response latency looks the
    same as a real wrong-password case. The response body is always the
    same ``invalid_credentials`` payload — the attacker can't distinguish
    missing-user, wrong-password, or OAuth-only by timing or content.
    """
    email = body.email.lower().strip()
    user = store.get_user_by_email(email)

    # Always verify against SOMETHING so the path is constant-time enough
    # to not leak account existence.
    valid_hash = (user or {}).get("password_hash") or _get_dummy_hash()
    matches = _verify_password(body.password, valid_hash)

    if user is None or not user.get("password_hash") or not matches:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials"},
        )

    # Argon2 rehash-on-login: if the stored hash was made with weaker
    # parameters than current (e.g. we bumped time_cost or memory_cost
    # since signup), transparently re-hash with the current params and
    # persist. Failure to rehash is non-fatal — the login still succeeds
    # against the legacy hash; ops gets a WARN to investigate later.
    try:
        hasher = _password_hasher()
        stored_hash = user["password_hash"]
        if hasher.check_needs_rehash(stored_hash):
            new_hash = hasher.hash(body.password)
            updated = store.update_user_password(user["user_id"], new_hash)
            if not updated:
                logger.warning(
                    "argon2 rehash persist returned False for user %s",
                    user["user_id"],
                )
    except Exception as exc:  # noqa: BLE001 — never block login on rehash
        logger.warning(
            "argon2 rehash on login failed for user %s: %s",
            user.get("user_id"), exc,
        )

    prior = _peek_session_user_id(inspira_session)
    _set_session_cookie(
        response,
        user["user_id"],
        previous_anon_user_id=prior if prior and _is_anon_user_id(prior) else None,
        request=request,
    )
    # New-device email — fire the new_signin note only when the
    # sha256(ip, user-agent) pair hasn't been seen for this user before.
    # `observe_device_fingerprint` is a single UPDATE that appends-or-noops
    # and returns True iff the hash was freshly added — so we never
    # double-send, and a corrupt column / missing migration silently
    # returns False rather than blocking login.
    try:
        fp = _device_fingerprint(request)
        newly_seen = store.observe_device_fingerprint(user["user_id"], fp)
        if newly_seen:
            frontend_base = _resolve_frontend_base_url(request)
            background_tasks.add_task(
                _queue_new_signin_email,
                to_email=user["email"],
                display_name=user.get("display_name") or "",
                device_label=_device_label_from_user_agent(request),
                # Location lookup is a follow-up (would need a GeoIP
                # dep). For now, we admit we don't know — honest beats
                # fake geolocation.
                location_label="an unknown location",
                signed_in_at_human=time.strftime(
                    "%B %d, %Y at %H:%M UTC", time.gmtime(),
                ).replace(" 0", " "),
                two_factor_link=f"{frontend_base}/account?section=security",
            )
    except Exception as exc:  # noqa: BLE001 — mail outage never blocks login
        logger.warning("new_signin wiring failed: %s", exc)
    return AuthedUser(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"] or "",
        is_system=False,
    )


@router.post("/logout")
def logout_route(response: Response) -> dict[str, bool]:
    _clear_session_cookie(response)
    return {"logged_out": True}


# ---------------------------------------------------------------------------
# Email verification — consume token / resend link
# ---------------------------------------------------------------------------
#
# Signup mints a random token, stores its sha256 on the user row, and
# emails the raw token inside a ``/email-confirm?token=...`` link.
#
# The consume endpoint hashes the incoming token, flips
# ``email_verified_at``, and clears the stored hash so the link can't be
# replayed. The resend endpoint rate-limits per user (in-memory, best-
# effort — slowapi's global per-IP limiter still applies on top) so a
# compromised link can't be spammed.

# Per-user throttle for verify-email resends. Keyed on user_id → last
# send unix-ts. In-memory; a process restart forgets state, which is the
# safe direction (we'd rather let a user re-send after a deploy than
# silently lock them out).
#
# Multi-machine note: this dict is per-process so two Fly machines each
# enforce the throttle independently. A user round-robined across both
# can resend twice as often. Acceptable for an email-resend rate limit
# at this scale; if it becomes an abuse vector, move the cursor into
# Postgres.
#
# Concurrency: ``_verify_resend_lock`` serializes the read-then-write
# pair below so two simultaneous requests can't both pass the throttle
# check on a stale read. The lock is held for microseconds.
_VERIFY_RESEND_MIN_INTERVAL_S = 5 * 60
_verify_resend_last: dict[str, float] = {}
_verify_resend_lock = threading.Lock()


class VerifyEmailResendBody(BaseModel):
    """Empty body — route reads the authed user from the cookie."""
    model_config = {"extra": "forbid"}


@router.post("/verify-email/{token}")
def verify_email_consume_route(
    token: str,
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> dict[str, bool]:
    """Consume a verification token and stamp ``email_verified_at``.

    The endpoint is intentionally public — the raw token carries its
    own authorization. On success we clear the stored hash so a replay
    (email forwarded, link reused) is a 400 not a silent re-flip.
    """
    import hashlib

    stripped = (token or "").strip()
    if not stripped:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_or_expired_token"},
        )
    token_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    try:
        user_id = store.consume_email_verification_token(token_hash)
    except Exception as exc:  # noqa: BLE001 — DB blip shouldn't 500 on verify
        logger.warning("verify_email consume failed: %s", exc)
        raise HTTPException(
            status_code=400, detail={"error": "invalid_or_expired_token"},
        ) from exc
    if user_id is None:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_or_expired_token"},
        )
    return {"ok": True}


@router.post("/verify-email/resend", status_code=204)
def verify_email_resend_route(
    request: Request,  # noqa: ARG001 — for slowapi + frontend-base lookup
    background_tasks: BackgroundTasks,
    inspira_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> Response:
    """Re-send the verify_email link to the currently signed-in user.

    Throttled to one send per ``_VERIFY_RESEND_MIN_INTERVAL_S`` seconds
    per user. A throttled request returns 429; otherwise 204 regardless
    of whether the user is already verified, so the endpoint doesn't
    leak verification state.
    """
    import hashlib
    import secrets

    user_id = _peek_session_user_id(inspira_session)
    if not user_id or _is_anon_user_id(user_id):
        raise HTTPException(
            status_code=401, detail={"error": "not_authenticated"},
        )
    user = store.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=401, detail={"error": "not_authenticated"},
        )
    # Atomic check-and-claim: hold the lock through both the read AND
    # the write so two concurrent resend POSTs from the same user can't
    # both pass the throttle on a stale read. Lock is microsecond-held.
    now = time.time()
    with _verify_resend_lock:
        last = _verify_resend_last.get(user_id)
        if last is not None and (now - last) < _VERIFY_RESEND_MIN_INTERVAL_S:
            retry_after = int(_VERIFY_RESEND_MIN_INTERVAL_S - (now - last))
            raise HTTPException(
                status_code=429,
                detail={"error": "resend_throttled"},
                headers={"Retry-After": str(max(retry_after, 1))},
            )
        # Reserve the slot now so the racing request loses on its check.
        _verify_resend_last[user_id] = now
    raw_token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    try:
        store.set_email_verification_token(user_id, token_hash)
    except Exception as exc:  # noqa: BLE001 — never 500 on a resend
        logger.warning("verify_email resend persist failed: %s", exc)
    frontend_base = _resolve_frontend_base_url(request)
    verify_url = f"{frontend_base}/email-confirm?token={raw_token}"
    background_tasks.add_task(
        _queue_verify_email,
        to_email=user["email"],
        display_name=user.get("display_name") or "",
        verify_url=verify_url,
    )
    # Throttle slot is already reserved up in the lock block above.
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Password reset -- forgot / reset routes
# ---------------------------------------------------------------------------
#
# Two-step flow:
#   1. POST /api/auth/forgot-password {email}
#      -> ALWAYS returns 200 with a generic message (audit M3 extension:
#         no enumeration via status code, body, or timing). If the email
#         maps to a real user, a reset token is minted and queued for
#         delivery by the existing mail.EmailSender.
#   2. POST /api/auth/reset-password {token, new_password}
#      -> Validates the token, updates the user's password hash,
#         invalidates the token + every other live reset for the user.
#
# Per-IP rate limiting is the global slowapi middleware (see api.py).
# Per-email rate limiting piggybacks on the store: ``create_password_reset_token``
# enforces PASSWORD_RESET_MAX_ACTIVE_TOKENS by invalidating the oldest.

# Human-readable rendering of the token TTL. The email template needs a
# short phrase like "1 hour" so we derive it from the store's TTL rather
# than hard-coding copy that could drift.
def _token_ttl_human() -> str:
    seconds = PlanningStudioStore.PASSWORD_RESET_TOKEN_TTL_SECONDS
    hours = seconds // 3600
    if hours >= 1:
        return "1 hour" if hours == 1 else f"{hours} hours"
    minutes = max(1, seconds // 60)
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _resolve_frontend_base_url(request: Request) -> str:
    """Pick the base URL the reset / welcome link should target.

    Priority:
      1. ``INSPIRA_APP_BASE_URL`` env var -- production override. Spec
         name; preferred over the older alias below.
      2. ``INSPIRA_FRONTEND_URL`` env var -- legacy alias kept so
         existing Fly configs do not need to change all at once.
      3. The request's ``Origin`` header -- whatever the SPA actually
         pointed its browser at. Works for local dev (http://localhost:5173)
         and LAN (http://10.0.0.219:4175) without configuration.
      4. ``http://localhost:5173`` fallback so misconfigured local boots
         still produce a URL rather than a blank one.

    Trailing slashes are stripped so callers can safely append
    ``/reset-password?token=...``.
    """
    for key in ("INSPIRA_APP_BASE_URL", "INSPIRA_FRONTEND_URL"):
        explicit = os.environ.get(key, "").strip()
        if explicit:
            return explicit.rstrip("/")
    origin = request.headers.get("origin", "").strip()
    if origin:
        return origin.rstrip("/")
    return "http://localhost:5173"


def _generic_reset_response() -> dict[str, Any]:
    """The response body every /forgot-password call returns.

    Never varies. Must not hint at whether the email exists -- that's
    the enumeration defense the whole route is built around.
    """
    return {
        "ok": True,
        "message": (
            "If an account exists for that address we sent a link. "
            "Check your email."
        ),
    }


def _queue_password_reset_email(
    *, to_email: str, display_name: str, reset_link: str,
) -> None:
    """Render + dispatch the password_reset template via the mail module.

    Isolated as a module-level function so we can hand it to FastAPI's
    BackgroundTasks (which expects a plain callable) without capturing
    the whole route closure. Swallows mail-send errors because the user
    already got a 200 -- raising here would leak (via logs spiking) that
    the email existed, and the request context is gone anyway.

    We emit two observability lines at INFO:
      - a ``dispatched`` line BEFORE the send so fly logs always show
        that the background task fired even if the provider hangs or
        the process is killed mid-send,
      - a ``sent`` line after the provider returns so we can tell apart
        "never called" from "provider rejected".
    """
    try:
        from .mail import get_email_sender

        sender = get_email_sender()
        provider_name = type(sender).__name__
        logger.info(
            "queue_password_reset_email: dispatched provider=%s recipient=%s",
            provider_name,
            _redact_email(to_email),
        )
        sender.send(
            to_email=to_email,
            template_id="password_reset",
            context={
                "display_name": display_name or to_email.split("@", 1)[0],
                "reset_link": reset_link,
                "expires_in_human": _token_ttl_human(),
            },
        )
        logger.info(
            "queue_password_reset_email: sent provider=%s recipient=%s",
            provider_name,
            _redact_email(to_email),
        )
    except Exception as exc:  # noqa: BLE001 -- best effort, see docstring
        logger.warning("password_reset email dispatch failed: %s", exc)


@router.post("/forgot-password")
def forgot_password_route(
    body: ForgotPasswordBody,
    request: Request,
    background_tasks: BackgroundTasks,
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> dict[str, Any]:
    """Kick off a password reset by emailing a signed link.

    Always returns 200 + the same generic body. Whether the email exists
    is never leaked in the response. Token minting and email dispatch
    happen only when the email resolves to a real user with a password
    hash (OAuth-only accounts have nothing to reset).
    """
    email = body.email.lower().strip()
    user = store.get_user_by_email(email)
    if user is not None and user.get("password_hash"):
        raw_token = store.create_password_reset_token(user["user_id"])
        frontend_base = _resolve_frontend_base_url(request)
        # Prefer the spec URL (/reset-password?token=) — the SPA also
        # accepts the legacy /?reset_token= shape, but this is the one
        # we document going forward and the one every new client routes.
        reset_link = f"{frontend_base}/reset-password?token={raw_token}"
        # Enqueue the send as a BackgroundTask so the HTTP response
        # returns quickly regardless of provider latency.
        background_tasks.add_task(
            _queue_password_reset_email,
            to_email=user["email"],
            display_name=(user.get("display_name") or ""),
            reset_link=reset_link,
        )
    return _generic_reset_response()


@router.post("/reset-password")
def reset_password_route(
    request: Request,  # noqa: ARG001 — required for per-route slowapi rate limiting
    body: ResetPasswordBody,
    response: Response,  # noqa: ARG001 -- reserved for session rotation follow-up
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> dict[str, bool]:
    """Consume a reset token and rotate the user's password hash.

    On success we invalidate every other live token for the user so a
    sibling link sitting in another email client no longer works.

    SESSION-WIDE LOGOUT FOLLOW-UP: our session cookies are signed with
    INSPIRA_SESSION_SECRET; we cannot invalidate a specific cookie
    without rotating the secret (which logs out every user globally).
    The intended v2 solution is a per-user ``session_nonce`` mixed into
    the signed payload so we can bump it per user on reset. For v1 we
    accept that existing sessions survive the reset -- documented here
    as a known follow-up.
    """
    user_id = store.consume_password_reset_token(body.token.strip())
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_or_expired_token"},
        )
    user = store.get_user_by_id(user_id)
    if user is None:
        # The token pointed at a user that has since been removed. Be
        # generic -- no point leaking "your account was deleted" here.
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_or_expired_token"},
        )
    new_hash = _hash_password(body.new_password)
    store.update_user_password(user_id, new_hash)
    store.invalidate_user_password_reset_tokens(user_id)
    # Confirmation receipt — so the owner has a paper trail every time
    # their password rotates. Includes a fresh reset link for the rare
    # case where the change wasn't them.
    try:
        frontend_base = _resolve_frontend_base_url(request)
        raw_token = store.create_password_reset_token(user_id)
        reset_link = f"{frontend_base}/reset-password?token={raw_token}"
        # BackgroundTasks isn't available on this route today, so dispatch
        # in-process. The helper swallows provider errors — a mail outage
        # must never turn a successful reset into an error response.
        _queue_password_changed_email(
            to_email=user["email"],
            display_name=user.get("display_name") or "",
            reset_link=reset_link,
        )
    except Exception as exc:  # noqa: BLE001 — receipt is non-critical
        logger.warning("password_changed receipt dispatch failed: %s", exc)
    return {"ok": True}


@router.get("/ws-ticket")
def ws_ticket_route(
    response: Response,  # noqa: ARG001 — kept for shape parity with other auth routes
    inspira_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> dict[str, str]:
    """Return a ~90-second WS ticket scoped to the current user.

    The session cookie is httpOnly (JS can't read it) and SameSite=Lax
    isn't reliably forwarded on cross-subdomain WS upgrades, so the
    frontend can't just let the browser attach the cookie to the WS
    handshake. This route swaps the cookie for a short-lived signed
    ticket that the WS handler accepts via `?auth=<ticket>`.

    No anon fallback: if the caller has no valid session, return an
    empty ticket so the client can decide not to open the WS (no real
    collab benefit for anon users anyway).
    """
    if not inspira_session:
        return {"ticket": ""}
    user, _minted = _resolve_user_with_anon(store, inspira_session)
    uid = str(user.get("user_id") or "").strip()
    if not uid:
        return {"ticket": ""}
    return {"ticket": mint_ws_ticket(uid)}


@router.get("/me")
def me_route(
    request: Request,
    response: Response,
    inspira_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    store: PlanningStudioStore = Depends(lambda: _default_store()),
) -> AuthedUser:
    user, minted_anon = _resolve_user_with_anon(store, inspira_session)
    if minted_anon:
        # First-contact anonymous visitor: persist the new anon id in
        # their session cookie so their projects and Q&A survive a
        # reload without needing to sign in.
        _set_session_cookie(response, user["user_id"], request=request)
    from .workspaces.store import get_user_default_workspace_id  # noqa: PLC0415
    return AuthedUser(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user.get("display_name") or "",
        # ``is_system`` is the "not yet signed in" flag the frontend
        # relies on — both the shared legacy system user AND per-session
        # anonymous users present as system to the UI, which gates
        # signed-in-only affordances (projects grid, account settings)
        # without breaking the canvas experience for anon users.
        is_system=(
            user["user_id"] == SYSTEM_USER_ID or _is_anon_user_id(user["user_id"])
        ),
        default_workspace_id=get_user_default_workspace_id(store, user["user_id"]),
    )


# ---------------------------------------------------------------------------
# Dependency factory
# ---------------------------------------------------------------------------

# The FastAPI router routes get a store via ``Depends(lambda: _default_store())``.
# That lambda is bound when ``create_app`` stashes the store on the module
# (below). Keeps the router import-time-safe while allowing tests to swap it.
_default_store_holder: dict[str, PlanningStudioStore | None] = {"store": None}


def _default_store() -> PlanningStudioStore:
    store = _default_store_holder["store"]
    if store is None:
        raise RuntimeError(
            "auth store not configured — call current_user_dependency(store) "
            "from create_app to bind it",
        )
    return store


def current_user_dependency(store: PlanningStudioStore):
    """Bind the auth module's store and return a Depends-compatible callable.

    The returned dependency MAY mint a per-session anonymous user on
    first contact — it takes ``Response`` as a FastAPI parameter so it
    can set the session cookie when one is missing or invalid. The
    anonymous user is a real row in the users table with a
    ``user-anon-<hex>`` id, so their canvas data is scoped to them.
    """
    _default_store_holder["store"] = store

    def _resolver(
        request: Request,
        response: Response,
        inspira_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    ) -> dict[str, Any]:
        user, minted_anon = _resolve_user_with_anon(store, inspira_session)
        if minted_anon:
            _set_session_cookie(response, user["user_id"], request=request)
        return user

    return _resolver


def _resolve_user(
    store: PlanningStudioStore,
    session_cookie: str | None,
) -> dict[str, Any]:
    """Convert a session cookie into a user dict, or the system fallback.

    Used by routes that don't need the anonymous-user-minting side effect
    (read-only probes like ``/api/auth/me`` that already go through a
    Cookie-only path). Callers that need to mint-on-miss should use
    ``_resolve_user_with_anon`` instead.
    """
    user, _minted = _resolve_user_with_anon(store, session_cookie)
    return user


def _resolve_user_with_anon(
    store: PlanningStudioStore,
    session_cookie: str | None,
) -> tuple[dict[str, Any], bool]:
    """Resolve the session to a user, minting an anon row on first contact.

    Returns ``(user, minted_anon)``. ``minted_anon=True`` means the caller
    should set the session cookie on the response so the same anonymous
    user carries across requests — otherwise every page view would mint
    a fresh anon user and their canvas would evaporate on reload.
    """
    if not session_cookie:
        anon = _create_anon_user(store)
        return anon, True
    serializer = _session_serializer()
    try:
        payload = serializer.loads(session_cookie, max_age=SESSION_MAX_AGE_SECONDS)
    except SignatureExpired:
        anon = _create_anon_user(store)
        return anon, True
    except BadSignature:
        anon = _create_anon_user(store)
        return anon, True
    except Exception:  # noqa: BLE001
        anon = _create_anon_user(store)
        return anon, True
    user_id = str(payload.get("user_id", "")).strip()
    if not user_id:
        anon = _create_anon_user(store)
        return anon, True
    user = store.get_user_by_id(user_id)
    if user is None:
        # Cookie pointed at a user that's been deleted — mint a fresh
        # anon. Could also fall back to the system user, but the anon
        # path is what new visitors get; reuse it for consistency.
        anon = _create_anon_user(store)
        return anon, True
    return user, False


# ---------------------------------------------------------------------------
# Google OAuth placeholder — wiring deferred until creds are provisioned.
# ---------------------------------------------------------------------------


@router.get("/google/start")
def google_start_route() -> dict[str, Any]:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(
            status_code=501,
            detail={
                "error": "google_oauth_not_configured",
                "hint": "Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REDIRECT_URI.",
            },
        )
    # Stub — real implementation will return an authorization URL after the
    # user provides Google Cloud creds. Left as NotImplementedError so the
    # frontend can feature-detect via 501 status.
    raise HTTPException(status_code=501, detail={"error": "google_oauth_unimplemented"})


@router.get("/google/callback")
def google_callback_route() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail={"error": "google_oauth_unimplemented"})


__all__ = [
    "router",
    "current_user_dependency",
    "SYSTEM_USER_ID",
    "SYSTEM_USER_EMAIL",
    "SESSION_COOKIE_NAME",
    "ANON_USER_ID_PREFIX",
    "TransferAnonymousProjectsBody",
    "_is_anon_user_id",
    "_peek_session_user_id",
    "_peek_session_previous_anon_user_id",
    "_redact_user",
    "_redact_email",
]

# Silence unused-import lint when email validator isn't installed; pydantic
# only reads EmailStr if the optional email-validator package is present.
_ = json
