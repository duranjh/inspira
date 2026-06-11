"""FastAPI router for Wave F.4 — inline IDE-style comments on generated code.

Mounts under ``/api/v2/projects/{project_id}/artifact/comments``:

- ``POST   /``               (member+) — create a comment, optionally as a
  reply (``parent_comment_id``).
- ``GET    /?include_resolved=`` (member+) — list comments for a project.
- ``PATCH  /{comment_id}``   (member+) — edit body (author-or-admin only)
  and / or toggle resolved state (any workspace member).

Comments are anchored to ``(file_path, line_number, line_content_hash)``
so they survive minor edits to surrounding lines. The hash is SHA-256
over the line's raw UTF-8 bytes truncated to 16 hex chars (see
``PlanningStudioStore._hash_artifact_comment_line``).

Body models live at MODULE scope, not inside ``make_artifact_comments_router``,
because ``api.py`` uses ``from __future__ import annotations``. PEP 563
stringifies every annotation and FastAPI's ``typing.get_type_hints`` resolves
them against the router module's globals — anything defined inside a closure
is invisible and silently misclassified as a query param.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .workspaces.models import Role, WorkspaceMember

if TYPE_CHECKING:
    from .store import PlanningStudioStore


logger = logging.getLogger(__name__)


_CurrentWorkspaceMemberFactory = Callable[..., Callable[..., WorkspaceMember]]


# ---------------------------------------------------------------------
# Body models — module-scope (future-annotations gotcha)
# ---------------------------------------------------------------------


class ArtifactCommentCreateBody(BaseModel):
    """Body for ``POST /api/v2/projects/{project_id}/artifact/comments``."""

    file_path: str = Field(..., min_length=1, max_length=512)
    line_number: int = Field(..., ge=1)
    line_content: str = Field(..., max_length=4000)
    category: str = Field(..., pattern="^(question|concern|suggest_fix)$")
    body: str = Field(..., min_length=1, max_length=4000)
    parent_comment_id: str | None = Field(default=None, max_length=64)


class ArtifactCommentPatchBody(BaseModel):
    """Body for ``PATCH /api/v2/projects/{project_id}/artifact/comments/{id}``."""

    body: str | None = Field(default=None, min_length=1, max_length=4000)
    resolved: bool | None = None


# ---------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------


def make_artifact_comments_router(
    store: "PlanningStudioStore",
    current_workspace_member: _CurrentWorkspaceMemberFactory,
) -> APIRouter:
    """Build the artifact-comments router with closed-over deps."""
    router = APIRouter(
        prefix="/api/v2/projects/{project_id}/artifact/comments",
        tags=["artifact-comments"],
    )

    def _ensure_project_in_workspace(
        project_id: str, member: WorkspaceMember,
    ) -> None:
        """404 if the project doesn't belong to the calling workspace.

        Returns 404 (not 403) on cross-workspace to avoid leaking
        existence across boundaries — same pattern the orchestrator
        + cascade routers use.
        """
        proj = store._get_v2_project(project_id)
        if proj is None or proj.get("workspace_id") != member.workspace_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "project_not_found",
                    "message": project_id,
                },
            )

    @router.post("", status_code=201)
    def create_comment_route(
        project_id: str,
        body: ArtifactCommentCreateBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _ensure_project_in_workspace(project_id, member)
        if body.parent_comment_id is not None:
            parent = store.get_artifact_comment(body.parent_comment_id)
            if parent is None or parent["project_id"] != project_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "parent_comment_not_found",
                        "message": body.parent_comment_id,
                    },
                )
        comment = store.create_artifact_comment(
            project_id=project_id,
            file_path=body.file_path,
            line_number=body.line_number,
            line_content=body.line_content,
            category=body.category,
            body=body.body,
            author_user_id=member.user_id,
            parent_comment_id=body.parent_comment_id,
        )
        return {"comment": comment}

    @router.get("")
    def list_comments_route(
        project_id: str,
        include_resolved: bool = False,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _ensure_project_in_workspace(project_id, member)
        comments = store.list_artifact_comments(
            project_id, include_resolved=include_resolved,
        )
        return {"comments": comments}

    @router.patch("/{comment_id}")
    def update_comment_route(
        project_id: str,
        comment_id: str,
        body: ArtifactCommentPatchBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _ensure_project_in_workspace(project_id, member)
        existing = store.get_artifact_comment(comment_id)
        if existing is None or existing["project_id"] != project_id:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "comment_not_found",
                    "message": comment_id,
                },
            )
        actor_is_admin = member.role in (Role.admin, Role.owner)
        try:
            updated = store.update_artifact_comment(
                comment_id,
                actor_user_id=member.user_id,
                actor_is_admin=actor_is_admin,
                body=body.body,
                resolved=body.resolved,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "comment_edit_forbidden",
                    "message": str(exc),
                },
            ) from exc
        if updated is None:
            # Lost row between get + update; treat as 404.
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "comment_not_found",
                    "message": comment_id,
                },
            )
        return {"comment": updated}

    return router
