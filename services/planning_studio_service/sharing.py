"""Read-only share-link business logic for Inspira projects.

Thin service layer that wraps ``PlanningStudioStore`` share-link methods.
All store calls are synchronous (SQLite); the public API is simple enough
that the FastAPI route handlers call these directly.

Tokens
------
``secrets.token_urlsafe(24)`` returns a 32-character URL-safe base64 string
(24 raw bytes × 4/3 ≈ 32 chars after base64 encoding with no padding).
Tokens are stored in plain text — they are capability credentials, not
passwords; hashing them would make revocation more complex without a
meaningful security benefit (the link itself is the secret).

Idempotency
-----------
``create_share_token`` revokes any existing live link before inserting a
new one (the store layer handles this atomically). This means calling it
twice in a row gives you two different tokens; only the second is active.
The API layer (``POST /api/v2/projects/{id}/share``) always calls this path
so the user always gets a fresh, working link.
"""
from __future__ import annotations

import os
import secrets
from typing import Any


def _base_url() -> str:
    """Base URL share links target.

    Honors ``INSPIRA_APP_BASE_URL`` / ``INSPIRA_FRONTEND_URL`` — the same
    keys ``auth._resolve_frontend_base_url`` reads for reset / welcome
    links — and falls back to the local dev frontend.
    """
    for key in ("INSPIRA_APP_BASE_URL", "INSPIRA_FRONTEND_URL"):
        explicit = os.environ.get(key, "").strip()
        if explicit:
            return explicit.rstrip("/")
    return "http://localhost:5173"


def create_share_token(
    store: Any,
    *,
    user_id: str,
    project_id: str,
) -> dict[str, str]:
    """Mint a new share token for *project_id*, owned by *user_id*.

    Revokes any existing live token first.  Returns ``{token, url}`` where
    ``url`` is the full canonical share URL.

    Raises :exc:`PermissionError` when *user_id* does not own the project.
    """
    if not store.verify_project_ownership(
        project_id=project_id, user_id=user_id,
    ):
        raise PermissionError("project_not_found")

    row = store.create_share_link(project_id=project_id, user_id=user_id)
    token: str = row["token"]
    return {"token": token, "url": f"{_base_url()}/shared/{token}"}


def revoke_share_token(
    store: Any,
    *,
    user_id: str,
    project_id: str,
) -> bool:
    """Revoke the active token for *project_id*.

    Returns ``True`` when a live token was found and revoked, ``False``
    when no live token exists or when *user_id* doesn't own the project.
    """
    return store.revoke_share_link(project_id=project_id, user_id=user_id)


def resolve_share_token(
    store: Any,
    *,
    token: str,
) -> dict[str, str] | None:
    """Look up *token* and return ``{project_id, owner_user_id}`` or None.

    Returns ``None`` for unknown tokens, revoked tokens, or tokens whose
    project has been soft-deleted.  Never raises.
    """
    try:
        row = store.get_share_link_by_token(token)
    except Exception:  # noqa: BLE001
        return None

    if row is None:
        return None
    if row.get("revoked_at") is not None:
        return None

    # Cross-check: the project must still exist (not soft-deleted).
    project_id: str = row["project_id"]
    try:
        project = store._get_v2_project(project_id)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None

    if project is None:
        return None

    return {
        "project_id": project_id,
        "owner_user_id": row["created_by_user_id"],
    }
