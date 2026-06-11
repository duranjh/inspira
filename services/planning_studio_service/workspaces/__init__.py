"""Workspace primitives for the v4 B2B pivot.

The workspace is the v4 unit of isolation, billing, and member
management. Every endpoint shipped from W1 onward carries the
``current_workspace_member(role_min)`` FastAPI dependency that
enforces membership and role scope.

Module layout:

- ``models``       — Pydantic v2 schemas + the ``Role`` enum and
                     ``role_at_least`` helper.
- ``helpers``      — id / slug generation utilities.
- ``store``        — free functions that take a
                     ``PlanningStudioStore`` first arg (matches the
                     extension pattern from ``realtime.py``).
- ``dependencies`` — the FastAPI ``current_workspace_member``
                     dependency factory (added in slice B3).
- ``router``       — the ``/api/v2/workspaces`` APIRouter (B3).

The five backing tables are defined in ``store.py``'s
``_initialize_users_schema`` inline executescript and mirrored in
alembic migrations ``20260504_0001..0005``.
"""
from .models import (
    CreateWorkspaceBody,
    InviteMemberBody,
    Role,
    Workspace,
    WorkspaceMember,
    WorkspaceSummary,
    role_at_least,
)

__all__ = [
    "CreateWorkspaceBody",
    "InviteMemberBody",
    "Role",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceSummary",
    "role_at_least",
]
