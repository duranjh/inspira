"""Workspace store helpers — free functions over ``PlanningStudioStore``.

Mirrors the extension pattern used by ``realtime.py``: free
functions that take ``store: PlanningStudioStore`` as the first
arg, sharing the underlying ``_connect()`` context manager. The
functions are thin SQL wrappers that return the Pydantic models
defined in ``models.py``.

All SQL uses parameter binding (no f-strings into SQL).
"""
from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from .helpers import short_uid
from .models import Role, Workspace, WorkspaceMember, WorkspaceSummary

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


class WorkspaceSlugExists(Exception):
    """Raised when ``create_workspace`` hits the UNIQUE slug constraint.

    Routes catch this to return a 409 instead of letting the raw
    integrity error bubble up as a 500.
    """


class LastOwnerError(Exception):
    """Raised when an operation would leave a workspace with zero owners.

    Currently fires from ``update_member_role`` (demoting the last
    owner) and ``remove_member`` (removing the last owner). The W4
    membership UI gates these client-side, but the server enforces
    too — the store is the source of truth.
    """


def _now(store: "PlanningStudioStore") -> str:
    """ISO-8601 UTC timestamp matching the rest of the store."""
    from ..store import now_timestamp

    return now_timestamp()


def _is_unique_violation(exc: Exception) -> bool:
    """Detect "UNIQUE constraint" across SQLite and Postgres without
    pinning a specific exception class.

    SQLite raises ``IntegrityError`` whose ``str`` contains
    ``UNIQUE constraint failed``. Postgres / psycopg raises
    ``UniqueViolation`` (subclass of ``IntegrityError``); SQLAlchemy
    wraps it as ``IntegrityError`` whose orig contains a ``pgcode``
    of ``"23505"``. The store doesn't use SQLAlchemy ORM; its raw
    cursor surfaces psycopg's exception directly.
    """
    msg = str(exc).lower()
    if "unique" in msg:
        return True
    pgcode = getattr(exc, "pgcode", None)
    if pgcode == "23505":
        return True
    return False


# ---------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------


def create_workspace(
    store: "PlanningStudioStore",
    *,
    owner_user_id: str,
    slug: str,
    name: str,
    plan_tier: str = "free",
) -> Workspace:
    """Create a workspace and add the creator as ``owner``.

    Both rows insert in the same transaction so a UNIQUE-slug
    failure rolls back cleanly without leaving an orphaned member
    row. Raises ``WorkspaceSlugExists`` on slug collision; the
    route layer translates to HTTP 409.
    """
    workspace_id = short_uid("ws-", 10)
    now = _now(store)

    try:
        with store._connect() as connection:
            connection.execute(
                """
                INSERT INTO workspaces (
                    workspace_id, slug, name, created_at,
                    billing_owner_user_id, plan_tier, settings_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    slug,
                    name,
                    now,
                    owner_user_id,
                    plan_tier,
                    "{}",
                ),
            )
            connection.execute(
                """
                INSERT INTO workspace_members (
                    workspace_id, user_id, role, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (workspace_id, owner_user_id, Role.owner.value, now),
            )
            connection.commit()
    except sqlite3.IntegrityError as exc:
        if _is_unique_violation(exc):
            raise WorkspaceSlugExists(slug) from exc
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_unique_violation(exc):
            raise WorkspaceSlugExists(slug) from exc
        raise

    return Workspace(
        workspace_id=workspace_id,
        slug=slug,
        name=name,
        created_at=now,
        billing_owner_user_id=owner_user_id,
        plan_tier=plan_tier,
        stripe_customer_id=None,
        settings={},
        archived_at=None,
    )


def get_workspace(
    store: "PlanningStudioStore", workspace_id: str
) -> Workspace | None:
    """Fetch a workspace by id; return None when absent."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT
                workspace_id, slug, name, created_at,
                billing_owner_user_id, plan_tier, stripe_customer_id,
                settings_json, archived_at
            FROM workspaces
            WHERE workspace_id = ?
            """,
            (workspace_id,),
        ).fetchone()
    if row is None:
        return None
    return Workspace(
        workspace_id=row[0],
        slug=row[1],
        name=row[2],
        created_at=row[3],
        billing_owner_user_id=row[4],
        plan_tier=row[5],
        stripe_customer_id=row[6],
        settings=json.loads(row[7] or "{}"),
        archived_at=row[8],
    )


def slug_exists(store: "PlanningStudioStore", slug: str) -> bool:
    """True iff a workspace already owns this slug."""
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM workspaces WHERE slug = ? LIMIT 1",
            (slug,),
        ).fetchone()
    return row is not None


def update_workspace(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    name: str | None = None,
    slug: str | None = None,
) -> Workspace | None:
    """Update a workspace's mutable fields (currently ``name`` + ``slug``).

    Both args are optional; a None value leaves the corresponding column
    untouched. Returns the updated ``Workspace`` (re-fetched after the
    UPDATE), or ``None`` if the row doesn't exist.

    Raises ``WorkspaceSlugExists`` when the requested slug collides
    with another workspace; the route translates that to HTTP 409. SQL
    parameter binding throughout — no string interpolation.
    """
    if name is None and slug is None:
        return get_workspace(store, workspace_id)

    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        args.append(name)
    if slug is not None:
        sets.append("slug = ?")
        args.append(slug)
    args.append(workspace_id)

    try:
        with store._connect() as connection:
            cursor = connection.execute(
                f"UPDATE workspaces SET {', '.join(sets)} "
                "WHERE workspace_id = ?",
                tuple(args),
            )
            if cursor.rowcount == 0:
                return None
            connection.commit()
    except sqlite3.IntegrityError as exc:
        if slug is not None and _is_unique_violation(exc):
            raise WorkspaceSlugExists(slug) from exc
        raise
    except Exception as exc:  # noqa: BLE001
        if slug is not None and _is_unique_violation(exc):
            raise WorkspaceSlugExists(slug) from exc
        raise

    return get_workspace(store, workspace_id)


class LastActiveWorkspaceError(Exception):
    """Raised when a user tries to archive their only active workspace.

    Surfaced as a 409 by the route. The check exists so the partner
    doesn't get stranded with no Inspira to log into — they must
    create a replacement first OR keep this one.
    """


class WorkspaceNotFoundError(Exception):
    """Raised when archive_workspace can't find the workspace, the
    actor isn't a member, or the workspace was already archived. The
    route surfaces as 404 (cross-workspace cases conflated for
    security so we don't leak existence)."""


def archive_workspace(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """Soft-delete a workspace by stamping ``archived_at = NOW()``.

    Founder direction 2026-05-05: partners need a self-serve way to
    delete a workspace. Schema already carries ``archived_at`` and
    ``list_workspaces_for_user`` already filters by it, so a soft-
    delete is consistent with the rest of the system + zero risk of
    cascading data loss. The underlying feedback / projects / etc.
    rows stay intact and recoverable by support if needed.

    Guards:
    - Actor must be a member with role=owner of the workspace.
    - Cannot archive the user's last active workspace (they'd have
      nowhere to log into). The route surfaces 409 + a friendly
      message; the FE blocks the Delete button when there's only
      one workspace.

    Returns the archived workspace summary so the FE can stamp a
    toast or remove the row optimistically.
    """
    member = get_member(
        store, workspace_id=workspace_id, user_id=actor_user_id,
    )
    if member is None or member.role != Role.owner:
        raise WorkspaceNotFoundError(workspace_id)
    workspace = get_workspace(store, workspace_id)
    if workspace is None:
        raise WorkspaceNotFoundError(workspace_id)
    # Last-workspace guard. Counts only active (non-archived)
    # workspaces the user belongs to; the workspace being archived
    # is still active at this point so the comparison is "<=1" =
    # "this one is the only one left."
    active = list_workspaces_for_user(store, actor_user_id)
    if len(active) <= 1:
        raise LastActiveWorkspaceError(workspace_id)
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            "UPDATE workspaces SET archived_at = ? "
            "WHERE workspace_id = ? AND archived_at IS NULL",
            (now, workspace_id),
        )
        connection.commit()
    append_workspace_audit_event(
        store,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        category="workspace",
        action="workspace.archived",
        before={
            "name": workspace.name,
            "slug": workspace.slug,
            "plan_tier": workspace.plan_tier,
        },
        after={"archived_at": now},
    )
    return {
        "workspace_id": workspace_id,
        "slug": workspace.slug,
        "name": workspace.name,
        "archived_at": now,
    }


def list_workspaces_for_user(
    store: "PlanningStudioStore", user_id: str
) -> list[WorkspaceSummary]:
    """Return active workspaces the user is a member of.

    Excludes archived workspaces (``archived_at IS NOT NULL``).
    Ordered most-recently-created first so newly-joined workspaces
    show up at the top of the switcher.
    """
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT
                w.workspace_id, w.slug, w.name, w.plan_tier, m.role
            FROM workspaces w
            JOIN workspace_members m
              ON m.workspace_id = w.workspace_id
            WHERE m.user_id = ?
              AND w.archived_at IS NULL
            ORDER BY w.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        WorkspaceSummary(
            workspace_id=r[0],
            slug=r[1],
            name=r[2],
            plan_tier=r[3],
            role=Role(r[4]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------


def add_member(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    user_id: str,
    role: Role,
    invited_by: str | None = None,
) -> WorkspaceMember:
    """Add a membership. Idempotent on (workspace_id, user_id) PK.

    If the row already exists, returns the existing record without
    upserting the role. Use ``update_member_role`` to change roles.
    """
    existing = get_member(
        store, workspace_id=workspace_id, user_id=user_id
    )
    if existing is not None:
        return existing
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO workspace_members (
                workspace_id, user_id, role, created_at, invited_by
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                user_id,
                role.value,
                now,
                invited_by,
            ),
        )
        connection.commit()
    return WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        created_at=now,
        invited_by=invited_by,
    )


def get_member(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    user_id: str,
) -> WorkspaceMember | None:
    """Single membership lookup. Used by ``current_workspace_member``."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT
                workspace_id, user_id, role, created_at, invited_by
            FROM workspace_members
            WHERE workspace_id = ? AND user_id = ?
            """,
            (workspace_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return WorkspaceMember(
        workspace_id=row[0],
        user_id=row[1],
        role=Role(row[2]),
        created_at=row[3],
        invited_by=row[4],
    )


def list_members(
    store: "PlanningStudioStore", workspace_id: str
) -> list[WorkspaceMember]:
    """All memberships for a workspace, oldest-first."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT
                workspace_id, user_id, role, created_at, invited_by
            FROM workspace_members
            WHERE workspace_id = ?
            ORDER BY created_at ASC
            """,
            (workspace_id,),
        ).fetchall()
    return [
        WorkspaceMember(
            workspace_id=r[0],
            user_id=r[1],
            role=Role(r[2]),
            created_at=r[3],
            invited_by=r[4],
        )
        for r in rows
    ]


def _count_owners(
    store: "PlanningStudioStore", workspace_id: str
) -> int:
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) FROM workspace_members
            WHERE workspace_id = ? AND role = ?
            """,
            (workspace_id, Role.owner.value),
        ).fetchone()
    return int(row[0]) if row else 0


def update_member_role(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    user_id: str,
    role: Role,
) -> WorkspaceMember | None:
    """Change a member's role; raise on last-owner-demotion.

    Returns None if no such membership exists. Raises
    ``LastOwnerError`` if demoting the last owner — every workspace
    must have at least one owner at all times.
    """
    current = get_member(
        store, workspace_id=workspace_id, user_id=user_id
    )
    if current is None:
        return None
    if (
        current.role == Role.owner
        and role != Role.owner
        and _count_owners(store, workspace_id) <= 1
    ):
        raise LastOwnerError(
            f"workspace {workspace_id} would have zero owners"
        )
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE workspace_members
            SET role = ?
            WHERE workspace_id = ? AND user_id = ?
            """,
            (role.value, workspace_id, user_id),
        )
        connection.commit()
    return get_member(
        store, workspace_id=workspace_id, user_id=user_id
    )


def remove_member(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    user_id: str,
) -> bool:
    """Remove a membership; raise on last-owner removal.

    Returns True if a row was removed. Raises ``LastOwnerError`` if
    removing the last owner.
    """
    current = get_member(
        store, workspace_id=workspace_id, user_id=user_id
    )
    if current is None:
        return False
    if (
        current.role == Role.owner
        and _count_owners(store, workspace_id) <= 1
    ):
        raise LastOwnerError(
            f"workspace {workspace_id} would have zero owners"
        )
    with store._connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM workspace_members
            WHERE workspace_id = ? AND user_id = ?
            """,
            (workspace_id, user_id),
        )
        connection.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------
# Default workspace (used by current_workspace_member fallback)
# ---------------------------------------------------------------------


def get_user_default_workspace_id(
    store: "PlanningStudioStore", user_id: str
) -> str | None:
    """Read the user's default workspace id (may be NULL).

    Returns None for anon users (excluded from backfill) and for
    authenticated users who haven't created a workspace yet.
    """
    with store._connect() as connection:
        row = connection.execute(
            "SELECT default_workspace_id FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return row[0] if row[0] else None


def set_user_default_workspace_id(
    store: "PlanningStudioStore",
    *,
    user_id: str,
    workspace_id: str,
) -> None:
    """Update the user's default workspace.

    Called from ``create_workspace`` when the user has no default
    yet (first workspace becomes default). Idempotent: re-setting
    to the same id is a no-op write.
    """
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE users
            SET default_workspace_id = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (workspace_id, _now(store), user_id),
        )
        connection.commit()


def create_workspace_for_signup(
    store: "PlanningStudioStore",
    *,
    user_id: str,
    display_name: str,
) -> Workspace:
    """Convenience: create a personal workspace for a fresh signup
    and set it as the user's default.

    Used from the W1 onboarding flow when a brand-new user lands on
    `/connectors` without any workspaces. Slug follows the same
    ``personal-<user_id[:8]>`` shape that migration 0005 backfills.
    """
    from .helpers import make_personal_slug

    slug = make_personal_slug(user_id)
    safe_name = (display_name or "My").strip() or "My"
    name = (
        f"{safe_name}' workspace"
        if safe_name.endswith("s")
        else f"{safe_name}'s workspace"
    )
    ws = create_workspace(
        store,
        owner_user_id=user_id,
        slug=slug,
        name=name,
    )
    if get_user_default_workspace_id(store, user_id) is None:
        set_user_default_workspace_id(
            store, user_id=user_id, workspace_id=ws.workspace_id
        )
    return ws


# ---------------------------------------------------------------------
# Audit-log helpers (the W4 shim seam)
# ---------------------------------------------------------------------


def append_workspace_audit_event(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    actor_user_id: str,
    category: str,
    action: str,
    project_id: str | None = None,
    subject_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Workspace-aware variant of ``store.append_user_audit_event``.

    Routes that have already migrated to ``current_workspace_member``
    call this directly, passing the workspace_id from
    ``request.state.workspace_id``. The unmigrated routes keep
    calling ``append_user_audit_event`` (which still hardcodes
    ``"ws-default"``) until W4 swaps the literal at
    ``store.py:5624`` for ``request.state.workspace_id``.
    """
    try:
        store.append_audit_event(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            category=category,
            action=action,
            project_id=project_id,
            subject_id=subject_id,
            before=before,
            after=after,
        )
    except Exception:  # noqa: BLE001
        # Audit-log failures must never break the main flow; mirror
        # the catch-and-warn pattern in append_user_audit_event.
        import logging

        logging.getLogger(__name__).warning(
            "append_workspace_audit_event failed (%s/%s)",
            category,
            action,
            exc_info=True,
        )
