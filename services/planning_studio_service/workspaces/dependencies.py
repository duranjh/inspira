"""FastAPI dependencies for the v4 workspace surface.

Two factories:

- ``make_current_workspace_member(store, current_user)`` returns a
  factory ``current_workspace_member(role_min: Role)`` whose value
  is a FastAPI dependency. The dependency resolves the workspace
  id from path / header / user-default in that order, fetches the
  user's membership, enforces ``role_min``, and stamps
  ``request.state.workspace_id`` so the W4 audit-shim swap can
  read it without touching every endpoint.

- ``make_optional_workspace_member(store, current_user)`` returns
  the same dependency but resolves to ``None`` instead of 403/400
  when no workspace is in scope. Used by routes that optionally
  scope (e.g. listing your own workspaces).

Both factories take ``store`` and ``current_user`` because the
``_current_user`` callable is built inside ``create_app()`` as a
closure over the request-scoped store. We can't import a module-
level dependency without re-creating that closure.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from fastapi import Depends, Header, HTTPException, Request

from .models import Role, WorkspaceMember, role_at_least
from .store import get_member, get_user_default_workspace_id

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


_CurrentUserCallable = Callable[..., dict[str, Any]]


def make_current_workspace_member(
    store: "PlanningStudioStore",
    current_user: _CurrentUserCallable,
) -> Callable[..., Callable[..., WorkspaceMember]]:
    """Factory for the workspace-member dependency.

    Usage in api.py:

        current_workspace_member = make_current_workspace_member(
            _store, _current_user,
        )
        @app.get("/api/v2/connectors")
        def list_connectors(
            member: WorkspaceMember = Depends(
                current_workspace_member(Role.viewer)
            ),
        ): ...

    The returned factory takes a minimum required role; the
    inner dependency 403s if the membership is missing or the
    role is below ``role_min``.
    """

    def current_workspace_member(
        role_min: Role = Role.viewer,
    ) -> Callable[..., WorkspaceMember]:
        def _dep(
            request: Request,
            user: dict[str, Any] = Depends(current_user),
            x_workspace_id: str | None = Header(
                default=None,
                alias="X-Workspace-Id",
                description=(
                    "Workspace context for this request. Falls back "
                    "to the user's default_workspace_id when omitted "
                    "and the route has no {workspace_id} path param."
                ),
            ),
        ) -> WorkspaceMember:
            # Resolution order — path param wins over header, header
            # wins over user-default. Path param comes from
            # request.path_params because Path() in a sub-dependency
            # only binds to the route operation, not to the dep tree.
            path_ws_id = request.path_params.get("workspace_id")
            user_id = user.get("user_id") or ""
            default_ws_id = (
                user.get("default_workspace_id")
                or get_user_default_workspace_id(store, user_id)
            )
            ws_id = path_ws_id or x_workspace_id or default_ws_id
            if not ws_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "workspace_id_required",
                        "message": (
                            "Pass an X-Workspace-Id header or use a "
                            "route with {workspace_id}, or create a "
                            "workspace first."
                        ),
                    },
                )
            member = get_member(
                store, workspace_id=ws_id, user_id=user_id
            )
            if member is None:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "workspace_access_denied",
                        "message": (
                            "You are not a member of this workspace."
                        ),
                    },
                )
            if not role_at_least(member.role, role_min):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "workspace_role_insufficient",
                        "required": role_min.value,
                        "actual": member.role.value,
                    },
                )
            # Stamp workspace context on request.state so the W4
            # audit-shim swap (replacing the "ws-default" literal at
            # store.py:5624) can read it without touching every
            # endpoint signature.
            request.state.workspace_id = ws_id
            request.state.workspace_role = member.role
            return member

        return _dep

    return current_workspace_member
