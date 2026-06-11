"""Pydantic schemas + role enum for the v4 workspace surface.

Role enum values match the ``role`` CHECK constraint in
``workspace_members`` (see alembic 20260504_0002 and the inline
executescript in ``store.py``). The four-role set was chosen over
the engineering-plan working draft's five-role split because the
per-resource verbs (plan vs. review) already live in
``audit_log.action``; collapsing them into a workspace role would
force per-feature scope tables in W5. Industry default for B2B
SaaS (Linear, Notion, GitHub) is also four-role.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class Role(str, Enum):
    """Workspace roles, ordered low-to-high privilege.

    The ``role_at_least`` helper compares actual vs. required role
    using the explicit rank below.
    """

    viewer = "viewer"
    member = "member"
    admin = "admin"
    owner = "owner"


_ROLE_RANK: dict[Role, int] = {
    Role.viewer: 0,
    Role.member: 1,
    Role.admin: 2,
    Role.owner: 3,
}


def role_at_least(actual: Role, minimum: Role) -> bool:
    """Return True iff ``actual`` is privileged enough for ``minimum``."""
    return _ROLE_RANK[actual] >= _ROLE_RANK[minimum]


class Workspace(BaseModel):
    """Full workspace record — exposed by ``GET /api/v2/workspaces/{id}``."""

    workspace_id: str
    slug: str
    name: str
    created_at: str
    billing_owner_user_id: str
    plan_tier: str = "free"
    stripe_customer_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    archived_at: str | None = None


class WorkspaceMember(BaseModel):
    """Membership row — workspace_id + user_id is the PK."""

    workspace_id: str
    user_id: str
    role: Role
    created_at: str
    invited_by: str | None = None


class WorkspaceSummary(BaseModel):
    """Light-weight projection used by ``GET /api/v2/workspaces`` (list-mine).

    Carries the requesting user's role in each workspace so the FE
    can render permission-aware UI without a second round-trip.
    """

    workspace_id: str
    slug: str
    name: str
    plan_tier: str
    role: Role


_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])?$")


class CreateWorkspaceBody(BaseModel):
    """Request body for ``POST /api/v2/workspaces``.

    Slug rules:
    - 3..40 chars
    - Lowercase letters, digits, hyphens
    - Must start and end with alphanumeric (no leading/trailing dash)

    The slug is the workspace's URL-handle and must be globally
    unique. The store layer raises a clean error on collision.
    """

    slug: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _SLUG_PATTERN.match(v):
            raise ValueError(
                "slug must be 3-40 chars of lowercase letters, digits, "
                "or hyphens, starting and ending with alphanumeric"
            )
        # 'personal-' is reserved for the auto-backfilled personal
        # workspaces created by migration 0005.
        if v.startswith("personal-"):
            raise ValueError("slug 'personal-*' is reserved")
        return v


class InviteMemberBody(BaseModel):
    """Request body for ``POST /api/v2/workspaces/{id}/members``.

    W1 ships a stub: the row is recorded with ``status='queued'``
    and the real email-delivery flow lands in W5 F11. The shape is
    forward-compatible — the W5 patch only flips state, not schema.
    """

    email: EmailStr
    role: Role


class WorkspaceUpdateBody(BaseModel):
    """Request body for ``PATCH /api/v2/workspaces/{id}``.

    Both fields are optional, but at least one must be present
    (validated below). Slug rules mirror ``CreateWorkspaceBody`` —
    3..40 chars, lowercase alphanumerics + hyphens, no
    leading/trailing dash, no ``personal-*`` reserved prefix.

    Module-scope (no forward refs) per the FastAPI + future-
    annotations interaction: handler-parameter types referenced in
    a router with ``from __future__ import annotations`` must be
    importable at module load, otherwise FastAPI misclassifies the
    param as a query string and rejects with 422 (or worse, drops
    the WS with 1008 on socket routes).
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=3, max_length=40)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _SLUG_PATTERN.match(v):
            raise ValueError(
                "slug must be 3-40 chars of lowercase letters, digits, "
                "or hyphens, starting and ending with alphanumeric"
            )
        if v.startswith("personal-"):
            raise ValueError("slug 'personal-*' is reserved")
        return v

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "WorkspaceUpdateBody":
        # Mirrors the JSON-API "no-op PATCH" convention: callers must
        # send at least one mutable field so a bare ``PATCH {}`` is a
        # 422 (intent-ambiguous) rather than a 200 with no effect.
        if self.name is None and self.slug is None:
            raise ValueError("at least one of 'name' or 'slug' is required")
        return self
