"""GitHub App JWT signing — RS256 with the App's private key.

Two functions here:

- ``app_jwt(...)``: synthesize a short-lived App JWT (TTL 540s
  default; GitHub's hard ceiling is 600s). The JWT authenticates AS
  the app — used ONLY to fetch installations or exchange for an
  installation access token. Not valid for repo API calls.
- ``installation_access_token(...)``: async; mint an App JWT and
  call ``POST /app/installations/{id}/access_tokens`` to exchange
  it for an installation access token (1-hour TTL). The token
  string is what ``GitHubClient`` consumes for actual repo reads.

W2 watch point #2 (App JWT vs installation token): keep these
strictly separate. App JWTs are short-lived and auth-as-the-app;
installation tokens are 1-hour and auth-as-the-installation.
Caching policy: no cache on the JWT (cheap to mint), cache the
installation token in-memory in the ``GitHubClient`` keyed by
installation_id with a small (5-min) safety margin under the
hour cap.

No env reads at this layer — config flows through the
``GitHubAppConfig`` dataclass passed in at call sites. Endpoints
build the config from env vars; tests pass synthetic configs.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


@dataclass(frozen=True)
class GitHubAppConfig:
    """App-level config (NOT the user OAuth client_id/secret pair).

    - ``app_id``: numeric GitHub App ID.
    - ``private_key_pem``: PEM-encoded RSA private key string.
    - ``app_slug``: URL slug for the install URL (e.g.
      ``inspira-planning-studio`` → installs at
      ``https://github.com/apps/inspira-planning-studio``).
    """

    app_id: str
    private_key_pem: str
    app_slug: str


def _b64url_no_pad(raw: bytes) -> str:
    """URL-safe base64 with the trailing ``=`` padding stripped —
    JWT segments are always unpadded."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def app_jwt(
    *,
    app_id: str,
    private_key_pem: str | bytes,
    ttl_seconds: int = 540,
    issued_at: int | None = None,
) -> str:
    """Sign an RS256 App JWT for GitHub App authentication.

    Args:
        app_id: numeric App ID (GitHub returns this as a string in
            its UI; pass as str).
        private_key_pem: PEM-encoded RSA private key. ``str`` or
            ``bytes`` accepted; conversion is internal.
        ttl_seconds: token lifetime in seconds. Default 540 (9 min)
            keeps 60s of headroom under GitHub's 600s ceiling for
            clock drift between our server and GitHub.
        issued_at: override the ``iat`` claim. Defaults to ``now -
            60`` per GitHub's recommendation (defends against minor
            clock skew where their clock is slightly behind ours).

    Returns:
        Signed JWT string (header.payload.signature).
    """
    if isinstance(private_key_pem, str):
        private_key_pem = private_key_pem.encode("utf-8")
    if ttl_seconds > 600:
        # GitHub rejects App JWTs whose exp - iat > 10 min. Cap
        # defensively so a misconfigured caller doesn't silently
        # produce 401s under load.
        raise ValueError(
            "App JWT ttl_seconds must be <= 600 (GitHub's hard ceiling)"
        )

    now = int(time.time())
    iat = issued_at if issued_at is not None else now - 60
    exp = now + ttl_seconds

    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": iat, "exp": exp, "iss": app_id}

    header_b64 = _b64url_no_pad(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    )
    payload_b64 = _b64url_no_pad(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    private_key = serialization.load_pem_private_key(
        private_key_pem, password=None
    )
    signature = private_key.sign(
        signing_input, padding.PKCS1v15(), hashes.SHA256()
    )
    sig_b64 = _b64url_no_pad(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


async def installation_access_token(
    *,
    config: GitHubAppConfig,
    installation_id: str,
    http: httpx.AsyncClient,
) -> tuple[str, datetime]:
    """Exchange an App JWT for an installation access token.

    The returned token authenticates AS the installation — use it
    in ``GitHubClient`` for actual repo / issue / commit reads.
    Token lifetime is exactly 1 hour from issuance per the GitHub
    App spec.

    Args:
        config: App-level config (id + private key + slug).
        installation_id: the GitHub installation_id returned by
            the OAuth callback.
        http: async HTTP client. Tests pass a
            ``httpx.MockTransport``-backed client.

    Returns:
        ``(token_string, expires_at_datetime)``. The expires_at is
        UTC.

    Raises:
        ``httpx.HTTPStatusError`` on non-2xx response from GitHub.
    """
    jwt = app_jwt(
        app_id=config.app_id,
        private_key_pem=config.private_key_pem,
    )
    url = (
        f"https://api.github.com/app/installations/"
        f"{installation_id}/access_tokens"
    )
    response = await http.post(
        url,
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    response.raise_for_status()
    body = response.json()
    expires_at = datetime.fromisoformat(
        body["expires_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    return body["token"], expires_at
