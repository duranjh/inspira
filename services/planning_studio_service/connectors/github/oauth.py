"""GitHub OAuth state token + user-code exchange + redirect URLs.

W2 watch point #1 (state-parameter CSRF): state tokens are HMAC-
signed via ``itsdangerous.URLSafeTimedSerializer`` AND bound to
the initiating user_id + workspace_id. The salt
``inspira-gh-oauth-state`` is distinct from the existing
``inspira-ws-ticket`` salt at ``auth.py:148`` and the
``inspira-session`` salt at ``auth.py:128``.

The CSRF gate is in two parts:
1. itsdangerous signature verifies the state wasn't forged.
2. The callback handler MUST compare ``payload['u']`` to the
   request's ``current_user['user_id']``. If a different user
   clicks the callback link with someone else's state token, the
   comparison fails and the workspace doesn't get linked to the
   wrong account.

State token TTL is 600s — long enough for a real user to click
through the GitHub install screens, short enough that a leaked
token can't be replayed days later.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from itsdangerous import (
    BadSignature,
    SignatureExpired,
    URLSafeTimedSerializer,
)


# Salt distinct from the two existing serializers in auth.py:
# - "inspira-session"   → session cookies (auth.py:128)
# - "inspira-ws-ticket" → realtime WebSocket tickets (auth.py:148)
# Distinct salt prevents cross-context replay even if the same
# session secret is shared.
_OAUTH_STATE_SALT = "inspira-gh-oauth-state"
_OAUTH_STATE_TTL_S = 600


class OAuthStateError(Exception):
    """Raised when state-token verification fails.

    Subclasses tell the route handler which redirect-error reason
    to emit so the FE can render the right copy.
    """


class OAuthStateInvalidSignature(OAuthStateError):
    """Token signature didn't verify. Forged or corrupted."""


class OAuthStateExpired(OAuthStateError):
    """Token signature verified but the TTL elapsed."""


class OAuthStateUserMismatch(OAuthStateError):
    """Token signature verified but the bound user_id doesn't
    match the current session. The CSRF gate."""


@dataclass(frozen=True)
class GitHubOAuthConfig:
    """User-OAuth config (NOT the App-level id/key pair).

    - ``client_id`` / ``client_secret``: the App's OAuth client
      credentials. Used to exchange a ``code`` for a user access
      token in the OAuth callback.
    - ``session_secret``: shared secret for itsdangerous signing.
      Reuses ``INSPIRA_SESSION_SECRET`` so we don't introduce
      another rotation surface.
    """

    client_id: str
    client_secret: str
    session_secret: str


def _serializer(session_secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        session_secret, salt=_OAUTH_STATE_SALT
    )


_REDIRECT_TO_ALLOWLIST = ("/connectors", "/onboarding")


def _validate_redirect_to(redirect_to: str | None) -> str | None:
    """Validate a caller-supplied post-OAuth redirect path.

    Returns the path verbatim when it's in the allowlist, ``None``
    otherwise (caller treats ``None`` as "fall back to /connectors").

    Open-redirect defense: the path must start with one of the
    known FE paths and must not contain a scheme or host. We're
    defense-in-depth here — the path is also signed inside the
    state token, but allowlisting at issue time prevents a
    misconfigured FE from minting open-redirect tokens that an
    attacker could later replay.
    """
    if not redirect_to:
        return None
    # Reject any absolute URL or scheme-relative URL — no `http://`,
    # `https://`, `//`, or `\\` allowed.
    if (
        redirect_to.startswith("http://")
        or redirect_to.startswith("https://")
        or redirect_to.startswith("//")
        or redirect_to.startswith("\\")
    ):
        return None
    # Must start with one of the allowlisted prefixes (with `?` or
    # path-end as the next char so `/onboardingfoo` doesn't sneak by).
    for prefix in _REDIRECT_TO_ALLOWLIST:
        if redirect_to == prefix:
            return redirect_to
        if redirect_to.startswith(prefix + "?"):
            return redirect_to
        if redirect_to.startswith(prefix + "/"):
            return redirect_to
    return None


def issue_state_token(
    *,
    user_id: str,
    workspace_id: str,
    session_secret: str,
    redirect_to: str | None = None,
) -> str:
    """Mint a state token bound to (user_id, workspace_id).

    Payload: ``{u, w, n}`` where ``n`` is a 12-byte random nonce
    so two concurrent OAuth starts by the same user produce
    different state tokens (defends against a session-fixation
    attacker who knows the user_id + workspace_id).

    When ``redirect_to`` is supplied AND passes the allowlist
    check, it's bound into the payload as ``r``. The callback
    handler uses this to send the browser back to the originating
    surface (e.g. ``/onboarding?step=2`` for the wizard) rather
    than the default ``/connectors``. Open-redirect defense:
    allowlisted prefixes only — see ``_validate_redirect_to``.
    """
    nonce = secrets.token_urlsafe(12)
    payload: dict[str, Any] = {"u": user_id, "w": workspace_id, "n": nonce}
    validated = _validate_redirect_to(redirect_to)
    if validated is not None:
        payload["r"] = validated
    return _serializer(session_secret).dumps(payload)


def consume_state_token(
    token: str,
    *,
    session_secret: str,
    expected_user_id: str,
    max_age_s: int = _OAUTH_STATE_TTL_S,
) -> dict[str, Any]:
    """Verify + decode a state token. Returns the payload dict.

    Raises one of the typed ``OAuthStateError`` subclasses on
    failure so the route handler can map each to the right redirect
    error reason:

    - ``OAuthStateInvalidSignature`` → ?reason=invalid_state
    - ``OAuthStateExpired``          → ?reason=expired_state
    - ``OAuthStateUserMismatch``     → ?reason=state_user_mismatch

    The ``expected_user_id`` is the current session's resolved
    user_id (from the ``current_user`` dependency). The CSRF gate
    checks the token's bound user matches.
    """
    serializer = _serializer(session_secret)
    try:
        payload = serializer.loads(token, max_age=max_age_s)
    except SignatureExpired as exc:
        raise OAuthStateExpired(str(exc)) from exc
    except BadSignature as exc:
        raise OAuthStateInvalidSignature(str(exc)) from exc

    if not isinstance(payload, dict):
        raise OAuthStateInvalidSignature(
            "state payload is not a dict"
        )
    bound_user = payload.get("u")
    if not bound_user or bound_user != expected_user_id:
        raise OAuthStateUserMismatch(
            f"state token bound to {bound_user!r}, but session is "
            f"{expected_user_id!r}"
        )
    return payload


def build_install_url(
    *,
    app_slug: str,
    state: str,
) -> str:
    """Build the GitHub App install URL.

    Sends the user to ``https://github.com/apps/<slug>/installations/new``
    with the state token in the query. GitHub will present the
    org+repo selection UI, then redirect to the App's configured
    callback URL with ``installation_id`` + ``setup_action=install``
    (and ``code`` if user-OAuth is configured on the App).
    """
    qs = urlencode({"state": state})
    return f"https://github.com/apps/{app_slug}/installations/new?{qs}"


def build_oauth_authorize_url(
    *,
    client_id: str,
    state: str,
    redirect_uri: str,
    scope: str = "",
) -> str:
    """Build the OAuth-App-style authorize URL.

    Used when the GitHub App is configured with "Request user
    authorization during installation" — GitHub serves the
    consent screen, then redirects with ``code``. We don't use this
    in the install path (install URL above does this implicitly),
    but kept for completeness if a future flow needs user-OAuth
    without an install.
    """
    qs = urlencode(
        {
            "client_id": client_id,
            "state": state,
            "redirect_uri": redirect_uri,
            "scope": scope,
        }
    )
    return f"https://github.com/login/oauth/authorize?{qs}"


async def exchange_user_code(
    *,
    code: str,
    config: GitHubOAuthConfig,
    http: httpx.AsyncClient,
) -> dict[str, Any]:
    """Exchange an OAuth ``code`` for a user access token.

    Used in the install callback when the App is configured with
    user-OAuth. The returned access_token authenticates AS THE
    USER (limited to the granted scopes) — used by us only to
    confirm install ownership; we don't make API calls with it.

    Raises ``httpx.HTTPStatusError`` on non-2xx; the caller maps
    to a redirect error reason.
    """
    response = await http.post(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "code": code,
        },
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        # GitHub returns 200 with {error, error_description} on
        # invalid code / used code / expired code. Treat as a 400.
        raise httpx.HTTPStatusError(
            f"github oauth exchange failed: {body.get('error')}",
            request=response.request,
            response=response,
        )
    return body


def load_app_config_from_env() -> tuple[
    "GitHubAppConfig", GitHubOAuthConfig
] | None:
    """Read the GitHub App secrets from env into the two configs.

    Returns ``None`` when ``GITHUB_APP_ID`` is unset — lets
    deployments without GitHub-App secrets boot cleanly (the
    /oauth/start endpoint then returns a "configure GitHub" error
    rather than crashing the whole module).

    Required env vars (all set as Fly secrets):
        GITHUB_APP_ID
        GITHUB_APP_PRIVATE_KEY  (PEM string)
        GITHUB_APP_SLUG         (URL slug, e.g. inspira-planning-studio)
        GITHUB_APP_CLIENT_ID
        GITHUB_APP_CLIENT_SECRET
        INSPIRA_SESSION_SECRET  (already required by auth.py)

    GITHUB_APP_WEBHOOK_SECRET is reserved for W4 F9 — not read here.
    """
    from .app_jwt import GitHubAppConfig

    app_id = os.environ.get("GITHUB_APP_ID")
    if not app_id:
        return None
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    app_slug = os.environ.get("GITHUB_APP_SLUG")
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID")
    client_secret = os.environ.get("GITHUB_APP_CLIENT_SECRET")
    session_secret = os.environ.get("INSPIRA_SESSION_SECRET")
    if not all(
        [private_key, app_slug, client_id, client_secret, session_secret]
    ):
        return None
    return (
        GitHubAppConfig(
            app_id=app_id,
            private_key_pem=private_key,
            app_slug=app_slug,
        ),
        GitHubOAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            session_secret=session_secret,
        ),
    )
