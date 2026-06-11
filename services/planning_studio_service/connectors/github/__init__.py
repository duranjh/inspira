"""GitHub App connector — OAuth + installation tokens + sync.

Module split per W2 watch point #2 (App JWT vs installation token
distinction):

- ``app_jwt`` ONLY signs RS256 App JWTs (10-min max TTL, used to
  authenticate AS the app — never for actual repo API calls).
- ``client`` ONLY uses installation tokens (1-hour TTL, used for
  every actual repo / issue / commit read). Never accepts an App
  JWT directly.
- ``oauth`` handles state-token CSRF mint/consume + the user-OAuth
  code exchange. State token is itsdangerous-signed at salt
  ``inspira-gh-oauth-state`` (distinct from the existing
  ``inspira-ws-ticket`` salt at auth.py:148) AND bound to the
  initiating user_id+workspace_id.
- ``sync`` calls into ``client`` to fetch repo metadata and
  ``connectors.store.upsert_repo_snapshot`` to persist (idempotent
  via composite PK).

Webhooks are out of scope for W2 — they ship in W4 F9. No
webhook endpoint is surfaced from this module.
"""
from .app_jwt import GitHubAppConfig, app_jwt, installation_access_token
from .client import (
    GitHubClient,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)
from .oauth import (
    GitHubOAuthConfig,
    OAuthStateError,
    build_install_url,
    consume_state_token,
    exchange_user_code,
    issue_state_token,
)

__all__ = [
    "GitHubAppConfig",
    "GitHubClient",
    "GitHubError",
    "GitHubNotFound",
    "GitHubOAuthConfig",
    "GitHubRateLimited",
    "GitHubTransient",
    "GitHubUnauthorized",
    "OAuthStateError",
    "app_jwt",
    "build_install_url",
    "consume_state_token",
    "exchange_user_code",
    "installation_access_token",
    "issue_state_token",
]
