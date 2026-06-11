"""FastAPI router for the W2 κ exports surface.

Mounts under ``/api/v2/projects/{project_id}/export/*``. Two
endpoints, one per provider:

- ``POST /export/linear``  — parent issue + sub-issue per topic.
- ``POST /export/github``  — single issue with task-list body.

Auth: workspace-member-or-above. Project access: the requester
must own the v2_project (matches the rest of the v2 project
routes — see api.py ``_require_owned_project``). Every error
returns a JSON body with ``code`` so the frontend can branch on
the failure mode without parsing prose.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import _resolve_frontend_base_url
from ..connectors.linear.client import (
    LinearAuthError,
    LinearRateLimited,
    LinearTransient,
)
from ..connectors.github.client import (
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)
from ..workspaces.models import Role, WorkspaceMember
from .builders import build_issue_body
from .github_send import GitHubAppNotConfigured, send_to_github
from .linear_send import (
    ConnectorNotConfigured,
    DestinationNotConfigured,
    send_to_linear,
)
from .pr_verification import fetch_pr_verification
from .scaffold_to_pr import ScaffoldNotReady, send_scaffold_as_pr

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


logger = logging.getLogger(__name__)


_CurrentUserCallable = Callable[..., dict[str, Any]]
_CurrentWorkspaceMemberFactory = Callable[..., Callable[..., WorkspaceMember]]


class ExportProjectBody(BaseModel):
    """Body for POST /api/v2/projects/{id}/export/{provider}.

    All four fields default to "user-friendly defaults": full
    body, source feedback included, P1 priority label applied. The
    modal toggles only override these — there's no required field.
    """

    include_canvas_link: bool = True
    include_source_feedback: bool = True
    apply_priority_label: bool = True
    priority_label: Literal["P0", "P1", "P2"] = "P1"


def make_exports_router(
    store: "PlanningStudioStore",
    current_user: _CurrentUserCallable,
    current_workspace_member: _CurrentWorkspaceMemberFactory,
) -> APIRouter:
    """Build the exports router with closed-over deps."""
    router = APIRouter(
        prefix="/api/v2/projects",
        tags=["exports"],
    )

    def _require_owned_project(
        project_id: str, *, user_id: str
    ) -> dict[str, Any]:
        """Return the project dict; raise 404 on absent OR cross-user.

        Matches the IDOR-safe pattern used elsewhere in api.py: a
        404 (not 403) for cross-user access so callers cannot
        enumerate project ids.
        """
        if not store.verify_project_ownership(
            project_id=project_id, user_id=user_id
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": "project_not_found"},
            )
        # verify_project_ownership has already confirmed presence;
        # _get_v2_project gives us the row for downstream metadata.
        project = store._get_v2_project(project_id)
        if project is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "project_not_found"},
            )
        return project

    def _canvas_url(request: Request, project_id: str) -> str:
        base = _resolve_frontend_base_url(request).rstrip("/")
        return f"{base}/p/{project_id}"

    @router.post("/{project_id}/export/linear")
    async def export_to_linear_route(
        project_id: str,
        request: Request,
        body: ExportProjectBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user_id=member.user_id)
        try:
            issue_body = build_issue_body(
                store,
                project_id=project_id,
                include_canvas_link=body.include_canvas_link,
                include_source_feedback=body.include_source_feedback,
                apply_priority_label=body.apply_priority_label,
                priority_label=body.priority_label,
                canvas_url=_canvas_url(request, project_id),
            )
        except LookupError:
            raise HTTPException(
                status_code=404,
                detail={"error": "project_not_found"},
            )
        try:
            result = await send_to_linear(
                store,
                workspace_id=member.workspace_id,
                body=issue_body,
            )
        except ConnectorNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "connector_not_configured",
                    "provider": "linear",
                },
            )
        except DestinationNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "destination_not_configured",
                    "provider": "linear",
                    "hint": (
                        "Configure default destination via PUT "
                        "/api/v2/connectors/linear/destination"
                    ),
                },
            )
        except LinearAuthError:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_unauthorized",
                    "provider": "linear",
                },
            )
        except LinearRateLimited:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_rate_limited",
                    "provider": "linear",
                },
            )
        except LinearTransient as exc:
            logger.warning("linear export transient: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_transient",
                    "provider": "linear",
                },
            )
        return {
            "ok": True,
            "provider": "linear",
            **result,
        }

    @router.post("/{project_id}/export/github")
    async def export_to_github_route(
        project_id: str,
        request: Request,
        body: ExportProjectBody,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        _require_owned_project(project_id, user_id=member.user_id)
        try:
            issue_body = build_issue_body(
                store,
                project_id=project_id,
                include_canvas_link=body.include_canvas_link,
                include_source_feedback=body.include_source_feedback,
                apply_priority_label=body.apply_priority_label,
                priority_label=body.priority_label,
                canvas_url=_canvas_url(request, project_id),
            )
        except LookupError:
            raise HTTPException(
                status_code=404,
                detail={"error": "project_not_found"},
            )
        try:
            result = await send_to_github(
                store,
                workspace_id=member.workspace_id,
                body=issue_body,
            )
        except ConnectorNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "connector_not_configured",
                    "provider": "github",
                },
            )
        except DestinationNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "destination_not_configured",
                    "provider": "github",
                    "hint": (
                        "Configure default destination via PUT "
                        "/api/v2/connectors/github/destination"
                    ),
                },
            )
        except GitHubAppNotConfigured:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "github_app_not_configured",
                    "provider": "github",
                },
            )
        except GitHubUnauthorized:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_unauthorized",
                    "provider": "github",
                },
            )
        except GitHubRateLimited:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_rate_limited",
                    "provider": "github",
                },
            )
        except GitHubTransient as exc:
            logger.warning("github export transient: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_transient",
                    "provider": "github",
                },
            )
        return {
            "ok": True,
            "provider": "github",
            **result,
        }

    @router.post("/{project_id}/export/github-pr")
    async def export_scaffold_as_pr_route(
        project_id: str,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Push the project's generated scaffold to GitHub as a PR.

        Distinct from /export/github (which files an Issue) — this one
        creates a branch, commits each scaffold file, and opens a Pull
        Request. Only meaningful when the project's artifact has a
        non-empty scaffold; returns 409 ``scaffold_not_ready`` otherwise
        so the FE can show a "Generate the code first" hint.
        """
        proj = _require_owned_project(project_id, user_id=member.user_id)
        title = (
            f"Inspira: {proj.get('title') or project_id}"
        )[:120]
        # Lightweight body — partner can edit on GitHub before merge.
        body_text = (
            f"Generated by Inspira from project `{project_id}`.\n\n"
            f"This PR contains the scaffold files Inspira drafted "
            f"based on the project canvas. Review the diff, ask "
            f"Inspira to refine via the chat sidebar in the artifact "
            f"viewer, or merge as-is.\n\n"
            f"---\n"
            f"_Powered by [Inspira](https://github.com/duranjh/inspira)._"
        )
        try:
            result = await send_scaffold_as_pr(
                store,
                workspace_id=member.workspace_id,
                user_id=member.user_id,
                project_id=project_id,
                pr_title=title,
                pr_body=body_text,
            )
        except ConnectorNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "connector_not_configured",
                    "provider": "github",
                },
            )
        except DestinationNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "destination_not_configured",
                    "provider": "github",
                },
            )
        except GitHubAppNotConfigured:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "github_app_not_configured",
                    "provider": "github",
                },
            )
        except ScaffoldNotReady as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "scaffold_not_ready",
                    "reason": str(exc) or "scaffold_not_generated",
                },
            )
        except GitHubUnauthorized:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_unauthorized",
                    "provider": "github",
                },
            )
        except GitHubRateLimited:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_rate_limited",
                    "provider": "github",
                },
            )
        except GitHubTransient as exc:
            logger.warning("github PR export transient: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_transient",
                    "provider": "github",
                },
            )
        return {"ok": True, "provider": "github", **result}

    @router.get("/{project_id}/pr-verification")
    async def pr_verification_route(
        project_id: str,
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.member)
        ),
    ) -> dict[str, Any]:
        """Poll the GitHub Actions / check_runs status for the project's PR.

        Founder direction (2026-05-04): "once the PR's actually in
        GitHub, Inspira should verify the change actually landed and is
        working as expected." v0 reports what the partner's existing
        CI says — pass / fail / pending / no_ci_configured. v1 (deferred)
        spins up a sandboxed runner that re-clones the merged branch
        and runs tests directly.
        """
        _require_owned_project(project_id, user_id=member.user_id)
        try:
            return await fetch_pr_verification(
                store,
                workspace_id=member.workspace_id,
                project_id=project_id,
            )
        except ConnectorNotConfigured:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "connector_not_configured",
                    "provider": "github",
                },
            )
        except GitHubAppNotConfigured:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "github_app_not_configured",
                    "provider": "github",
                },
            )
        except GitHubUnauthorized:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_unauthorized",
                    "provider": "github",
                },
            )
        except GitHubRateLimited:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_rate_limited",
                    "provider": "github",
                },
            )
        except GitHubTransient as exc:
            logger.warning("pr_verification transient: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "upstream_transient",
                    "provider": "github",
                },
            )

    # ``current_user`` is included in the factory signature for
    # symmetry with the connectors / orchestrator routers, but the
    # exports endpoints route through ``current_workspace_member``
    # which already resolves the session-scoped user. Reference it
    # to silence unused-arg lints.
    _ = current_user

    return router
