"""FastAPI router for the W2 comment-cascade surface.

Mounts under ``/api/v2/projects/{project_id}/regenerate-cascade*``:

- ``POST /preview``        → cheap scope check (no LLM); returns banner_state + cost
- ``POST /``               → 202 + cascade_id; schedules BackgroundTask
- ``GET  /{cascade_id}``   → poll endpoint for cascade status + new versions

All routes are workspace + project scoped (member+ role; viewer is
write-blocked on the two POSTs).

Pydantic body classes are defined at module scope so FastAPI's
``from __future__ import annotations`` in api.py resolves them
correctly. Inlining them inside the factory triggers a 422
"body field required" or 500 on /openapi.json.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field, field_validator

from . import cascade_store
from .agents import cascade
from .workspaces.models import Role, WorkspaceMember

if TYPE_CHECKING:
    from .store import PlanningStudioStore


logger = logging.getLogger(__name__)


_CurrentWorkspaceMemberFactory = Callable[..., Callable[..., WorkspaceMember]]
_CurrentUserCallable = Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------
# Body models — module-scope (future-annotations gotcha)
# ---------------------------------------------------------------------


class CommentedDecisionItem(BaseModel):
    decision_id: str = Field(..., min_length=1, max_length=64)
    comment_text: str = Field(..., min_length=1, max_length=2000)


def _reject_duplicate_decision_ids(
    items: list[CommentedDecisionItem],
) -> list[CommentedDecisionItem]:
    """Reject body payloads that include the same decision_id twice.

    Without this guard, two entries for the same decision deterministically
    race on the version_int (one wins, the other UNIQUE-violates and is
    reported as failed). 422 is more correct than letting it through and
    inflating ``failed_count``.
    """
    seen: set[str] = set()
    for item in items:
        if item.decision_id in seen:
            raise ValueError(
                f"duplicate decision_id in commented_decisions: {item.decision_id!r}"
            )
        seen.add(item.decision_id)
    return items


class CascadePreviewBody(BaseModel):
    commented_decisions: list[CommentedDecisionItem] = Field(
        ..., min_length=1, max_length=10,
    )
    scope_mode: str = Field(default="cascade", pattern="^(local|cascade)$")

    @field_validator("commented_decisions")
    @classmethod
    def _dedupe_preview(
        cls, v: list[CommentedDecisionItem],
    ) -> list[CommentedDecisionItem]:
        return _reject_duplicate_decision_ids(v)


class CascadeCommitBody(BaseModel):
    commented_decisions: list[CommentedDecisionItem] = Field(
        ..., min_length=1, max_length=10,
    )
    scope_mode: str = Field(default="cascade", pattern="^(local|cascade)$")
    confirm_scope: str | None = Field(
        default=None, pattern="^(none|narrow|wide)$",
    )

    @field_validator("commented_decisions")
    @classmethod
    def _dedupe_commit(
        cls, v: list[CommentedDecisionItem],
    ) -> list[CommentedDecisionItem]:
        return _reject_duplicate_decision_ids(v)


# ---------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------


def make_cascade_router(
    store: "PlanningStudioStore",
    current_user: _CurrentUserCallable,
    current_workspace_member: _CurrentWorkspaceMemberFactory,
) -> APIRouter:
    """Build the comment-cascade router with closed-over deps."""
    router = APIRouter(
        prefix="/api/v2/projects/{project_id}/regenerate-cascade",
        tags=["cascade"],
    )

    def _ensure_project_in_workspace(
        project_id: str, member: WorkspaceMember,
    ) -> None:
        """404 if the project doesn't belong to the calling workspace member.

        Tenancy check runs after the workspace-membership Depends, so we
        already know the user is in the workspace. Now confirm the
        project belongs to that user.
        """
        if not store.verify_project_ownership(
            project_id=project_id, user_id=member.user_id,
        ):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "project_not_found",
                    "message": project_id,
                },
            )

    # ---------------- preview ----------------

    @router.post("/preview")
    def preview_route(
        project_id: str,
        body: CascadePreviewBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _ensure_project_in_workspace(project_id, member)
        affected_scope = cascade.compute_affected_scope(
            store,
            project_id=project_id,
            commented_decision_ids=[
                c.decision_id for c in body.commented_decisions
            ],
            scope_mode=body.scope_mode,
        )
        cost = cascade.estimate_cost(
            affected_scope=affected_scope,
            commented_decisions=[
                {"decision_id": c.decision_id, "comment_text": c.comment_text}
                for c in body.commented_decisions
            ],
        )
        return {
            "affected_scope": affected_scope,
            **cost,
        }

    # ---------------- commit (202 + cascade_id) ----------------

    @router.post("", status_code=202)
    def commit_route(
        project_id: str,
        body: CascadeCommitBody,
        background_tasks: BackgroundTasks,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _ensure_project_in_workspace(project_id, member)
        if not cascade.is_openai_available():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "cascade_unavailable",
                    "reason": "openai_api_key_not_configured",
                },
            )
        commented = [
            {"decision_id": c.decision_id, "comment_text": c.comment_text}
            for c in body.commented_decisions
        ]
        # Pre-compute scope and persist on the cascade row so the FE poller
        # has it from the first GET (before run_cascade re-computes).
        affected_scope = cascade.compute_affected_scope(
            store,
            project_id=project_id,
            commented_decision_ids=[c["decision_id"] for c in commented],
            scope_mode=body.scope_mode,
        )
        cascade_id = cascade_store.create_cascade_run(
            store,
            workspace_id=member.workspace_id,
            project_id=project_id,
            triggered_by=member.user_id,
            scope_mode=body.scope_mode,
            commented_decisions=commented,
            affected_scope=affected_scope,
        )

        def _run() -> None:
            import asyncio as _asyncio  # noqa: PLC0415
            try:
                _asyncio.run(
                    cascade.run_cascade(
                        store,
                        workspace_id=member.workspace_id,
                        project_id=project_id,
                        cascade_id=cascade_id,
                        user_id=member.user_id,
                        scope_mode=body.scope_mode,
                        commented_decisions=commented,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[cascade-bg] catch-all failure cascade_id=%s: %s",
                    cascade_id, exc,
                )
                try:
                    cascade_store.update_cascade_status(
                        store,
                        workspace_id=member.workspace_id,
                        cascade_id=cascade_id,
                        status="failed",
                        error=f"bg_unhandled: {type(exc).__name__}",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[cascade-bg] mark_failed errored — orphan row cascade_id=%s",
                        cascade_id,
                    )

        background_tasks.add_task(_run)
        return {"cascade_id": cascade_id, "status": "pending"}

    # ---------------- status (poll) ----------------

    @router.get("/{cascade_id}")
    def status_route(
        project_id: str,
        cascade_id: str,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        # Ownership check serves both project and cross-workspace isolation:
        # the cascade is workspace-scoped on the SELECT.
        run = cascade_store.get_cascade_run(
            store,
            workspace_id=member.workspace_id,
            cascade_id=cascade_id,
            project_id=project_id,
        )
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "cascade_run_not_found", "message": cascade_id},
            )
        return run

    return router
