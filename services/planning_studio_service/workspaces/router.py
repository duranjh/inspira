"""FastAPI router for the v4 workspace surface (W1).

Four endpoints:

- ``POST /api/v2/workspaces``                          create
- ``GET  /api/v2/workspaces``                          list-mine
- ``GET  /api/v2/workspaces/{workspace_id}``           read (members + role)
- ``POST /api/v2/workspaces/{workspace_id}/members``   invite (W1 stub)

The router is a factory because the dependencies (``_current_user``
and the ``current_workspace_member`` it returns) are closures over
the request-scoped store built inside ``create_app()``. No
module-level singletons.

Invite stub semantics
---------------------

W1 ships the invite endpoint as a stub: if the email already
matches an existing user, they're added as a member directly
(useful for the partner demo flow). If the email is unknown, the
endpoint returns 202 ``status='queued'`` without persisting — the
real email flow + ``pending_invitations`` table land in W5 F11.
The shape is forward-compatible: the W5 patch will populate the
table during the existing 202 response, no client-side change.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from .models import (
    CreateWorkspaceBody,
    InviteMemberBody,
    Role,
    WorkspaceMember,
    WorkspaceUpdateBody,
)
from .store import (
    LastActiveWorkspaceError,
    LastOwnerError,
    WorkspaceNotFoundError,
    WorkspaceSlugExists,
    add_member,
    append_workspace_audit_event,
    archive_workspace,
    create_workspace,
    get_member,
    get_user_default_workspace_id,
    get_workspace,
    list_members,
    list_workspaces_for_user,
    set_user_default_workspace_id,
    update_workspace,
)

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


_CurrentUserCallable = Callable[..., dict[str, Any]]
_CurrentWorkspaceMemberFactory = Callable[..., Callable[..., WorkspaceMember]]


def make_workspaces_router(
    store: "PlanningStudioStore",
    current_user: _CurrentUserCallable,
    current_workspace_member: _CurrentWorkspaceMemberFactory,
) -> APIRouter:
    """Build the ``/api/v2/workspaces`` router with closed-over deps."""
    router = APIRouter(prefix="/api/v2/workspaces", tags=["workspaces"])

    def _require_authed(user: dict[str, Any]) -> str:
        """Bounce anon / system users with 401."""
        if user.get("is_system"):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "auth_required",
                    "message": "Sign in to manage workspaces.",
                },
            )
        user_id = user.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail={"error": "auth_required"},
            )
        return user_id

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
    )
    def create_workspace_route(
        body: CreateWorkspaceBody,
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        """Create a workspace, add the creator as ``owner``, set as
        default if the user doesn't have one yet."""
        user_id = _require_authed(user)
        try:
            ws = create_workspace(
                store,
                owner_user_id=user_id,
                slug=body.slug,
                name=body.name,
            )
        except WorkspaceSlugExists as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "workspace_slug_taken",
                    "slug": body.slug,
                },
            ) from exc

        # Promote to default if the user has no default workspace yet.
        # This is a convenience: the first workspace becomes the
        # implicit context for all X-Workspace-Id-less requests.
        if not get_user_default_workspace_id(store, user_id):
            set_user_default_workspace_id(
                store, user_id=user_id, workspace_id=ws.workspace_id
            )

        append_workspace_audit_event(
            store,
            workspace_id=ws.workspace_id,
            actor_user_id=user_id,
            category="workspace",
            action="created",
            after={"slug": ws.slug, "name": ws.name},
        )

        payload = ws.model_dump(mode="json")
        payload["role"] = Role.owner.value  # the creator is always owner
        return {"workspace": payload}

    @router.get("")
    def list_workspaces_route(
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        """List the active workspaces this user belongs to."""
        if user.get("is_system"):
            # Anon users have no workspaces — return empty list rather
            # than 401 so the frontend's first-run flow (anon → list →
            # empty → "create your workspace" CTA) doesn't have to
            # special-case auth state.
            return {"workspaces": []}
        user_id = user.get("user_id") or ""
        rows = list_workspaces_for_user(store, user_id)
        return {
            "workspaces": [r.model_dump(mode="json") for r in rows],
        }

    @router.get("/{workspace_id}")
    def get_workspace_route(
        workspace_id: str,  # noqa: ARG001 — used by the dependency via path_params
        member: WorkspaceMember = Depends(
            current_workspace_member(Role.viewer)
        ),
    ) -> dict[str, Any]:
        """Workspace detail — members + the requester's role.

        The path-param ``workspace_id`` flows through
        ``request.path_params`` to ``current_workspace_member``;
        this handler reads ``member.workspace_id`` from the
        validated dependency rather than re-validating the param.
        """
        ws = get_workspace(store, member.workspace_id)
        if ws is None:
            # Membership exists but workspace was archived/deleted in
            # the same request window. 404 rather than 500.
            raise HTTPException(status_code=404)
        members = list_members(store, member.workspace_id)
        return {
            "workspace": ws.model_dump(mode="json"),
            "members": [m.model_dump(mode="json") for m in members],
            "your_role": member.role.value,
        }

    @router.post(
        "/{workspace_id}/members",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def invite_member_route(
        workspace_id: str,  # noqa: ARG001 — used by the dependency
        body: InviteMemberBody,
        actor: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        """Invite a user by email (W1 stub).

        - If the email matches an existing user, they're added as a
          member immediately (idempotent on PK).
        - If the email is unknown, return 202 'queued' without
          persisting; the W5 email flow + ``pending_invitations``
          table take over here.
        """
        email = body.email.lower().strip()
        existing = store.get_user_by_email(email)

        if existing is not None:
            target_user_id = existing["user_id"]
            already = get_member(
                store,
                workspace_id=actor.workspace_id,
                user_id=target_user_id,
            )
            if already is not None:
                return {
                    "invitation": {
                        "email": email,
                        "role": already.role.value,
                        "status": "already_member",
                    }
                }
            try:
                added = add_member(
                    store,
                    workspace_id=actor.workspace_id,
                    user_id=target_user_id,
                    role=body.role,
                    invited_by=actor.user_id,
                )
            except LastOwnerError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "last_owner_change_blocked"},
                ) from exc

            append_workspace_audit_event(
                store,
                workspace_id=actor.workspace_id,
                actor_user_id=actor.user_id,
                category="workspace",
                action="member.added",
                subject_id=target_user_id,
                after={"role": added.role.value, "email": email},
            )
            return {
                "invitation": {
                    "email": email,
                    "role": added.role.value,
                    "status": "added",
                }
            }

        # W1 stub for unknown emails: record the intent in the audit
        # log so the W5 email-flow can replay invites that were
        # logged-but-not-delivered. No pending_invitations table yet.
        append_workspace_audit_event(
            store,
            workspace_id=actor.workspace_id,
            actor_user_id=actor.user_id,
            category="workspace",
            action="member.invite_queued",
            after={"email": email, "role": body.role.value},
        )
        return {
            "invitation": {
                "email": email,
                "role": body.role.value,
                "status": "queued",
                "note": (
                    "Email delivery ships W5; the audit log records "
                    "the intent so W5 can backfill."
                ),
            }
        }

    @router.patch("/{workspace_id}")
    def update_workspace_route(
        workspace_id: str,  # noqa: ARG001 — used via dependency path_params
        body: WorkspaceUpdateBody,
        actor: WorkspaceMember = Depends(
            current_workspace_member(Role.admin)
        ),
    ) -> dict[str, Any]:
        """Update a workspace's name and/or slug.

        Admin or owner only (``Role.admin`` via ``role_at_least``).
        The body validator already enforces "at least one of name or
        slug"; the route layer handles the slug-collision 409 + the
        404 race when the workspace is archived mid-request.

        Audit-log records before/after so the W4 partner audit page
        can show the rename history.
        """
        existing = get_workspace(store, actor.workspace_id)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found"},
            )
        try:
            updated = update_workspace(
                store,
                workspace_id=actor.workspace_id,
                name=body.name,
                slug=body.slug,
            )
        except WorkspaceSlugExists as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "workspace_slug_taken",
                    "slug": body.slug,
                },
            ) from exc
        if updated is None:
            # Race: archived between the existence-check above and the
            # UPDATE. 404 rather than 500.
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found"},
            )

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        if body.name is not None and body.name != existing.name:
            before["name"] = existing.name
            after["name"] = updated.name
        if body.slug is not None and body.slug != existing.slug:
            before["slug"] = existing.slug
            after["slug"] = updated.slug
        if after:
            append_workspace_audit_event(
                store,
                workspace_id=actor.workspace_id,
                actor_user_id=actor.user_id,
                category="workspace",
                action="updated",
                before=before,
                after=after,
            )

        return {"workspace": updated.model_dump(mode="json")}

    @router.delete("/{workspace_id}")
    def delete_workspace_route(
        workspace_id: str,
        actor: WorkspaceMember = Depends(
            current_workspace_member(Role.owner)
        ),
    ) -> dict[str, Any]:
        """Soft-delete a workspace (sets ``archived_at = NOW()``).

        Founder direction 2026-05-05: partners need a self-serve way
        to delete a workspace. Owner-only. The store's
        ``archive_workspace`` enforces the last-active-workspace guard
        — surfaced as 409 here so the FE can suggest creating a
        replacement first. The actual rows underneath
        (feedback_items / projects / etc.) stay intact and recoverable
        by support; only the workspace itself is hidden from the
        user's list. The FE confirmation dialog requires the partner
        to type "delete" before this fires, so the destructive blast
        radius is gated behind explicit intent.
        """
        if actor.workspace_id != workspace_id:
            # Defense in depth — current_workspace_member should
            # already 404 on cross-workspace, but guard anyway.
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found"},
            )
        try:
            summary = archive_workspace(
                store,
                workspace_id=workspace_id,
                actor_user_id=actor.user_id,
            )
        except LastActiveWorkspaceError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "last_active_workspace",
                    "message": (
                        "This is your only workspace. Create another "
                        "one before deleting it so you don't get "
                        "locked out."
                    ),
                },
            ) from exc
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found"},
            ) from exc
        return {"workspace": summary}

    return router
