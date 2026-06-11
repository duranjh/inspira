"""Shelves — user-owned named containers for grouping related projects.

Shelves are how users cluster their projects — "the novel and its research,"
"the startup and all the side experiments." A project belongs to at most one
shelf; a project whose ``shelf_id`` is NULL is on the implicit "Unfiled"
shelf (never materialised). Deleting a shelf un-shelves its member projects;
projects are never deleted as a side effect of shelf deletion.

This module contains the thin wrappers the FastAPI router calls into. Each
function here:
  * validates the input (name length, trim);
  * delegates storage to ``PlanningStudioStore``;
  * returns plain dicts so the router layer stays format-free.

The ownership story mirrors ``v2_projects``: every mutation takes a
``user_id`` and the store refuses to read/write cross-user rows. Cross-user
attempts surface as ``None`` from the store helpers; the router maps that
to 404 so object IDs remain un-enumerable.
"""
from __future__ import annotations

from typing import Any

from .store import PlanningStudioStore


# Hard caps on the free-text shelf name. 80 is generous for "The novel and
# its research" while bounded enough that a malicious user can't stash
# megabytes in a title.
MAX_SHELF_NAME_CHARS = 80


class ShelfValidationError(ValueError):
    """Raised when a shelf name is empty / too long / otherwise invalid.

    The router catches this and returns 400 with the message as a detail.
    """


def _validate_name(name: str) -> str:
    """Return the trimmed shelf name, or raise ``ShelfValidationError``."""
    trimmed = (name or "").strip()
    if not trimmed:
        raise ShelfValidationError("shelf name cannot be empty")
    if len(trimmed) > MAX_SHELF_NAME_CHARS:
        raise ShelfValidationError(
            f"shelf name must be {MAX_SHELF_NAME_CHARS} characters or fewer",
        )
    return trimmed


def list_shelves(store: PlanningStudioStore, *, user_id: str) -> list[dict[str, Any]]:
    """Return the user's active shelves, ordered by sort_order then name.

    Each entry carries a ``project_count`` derived via JOIN — the router
    passes that straight through to the frontend so the shelf header can
    render "N projects" without a second round-trip.
    """
    return store.list_shelves(user_id=user_id)


def create_shelf(
    store: PlanningStudioStore,
    *,
    user_id: str,
    name: str,
) -> dict[str, Any]:
    """Create a new shelf for the user. Raises ``ShelfValidationError`` on
    a bad name.
    """
    clean = _validate_name(name)
    return store.create_shelf(user_id=user_id, name=clean)


def rename_shelf(
    store: PlanningStudioStore,
    *,
    shelf_id: str,
    user_id: str,
    name: str,
) -> dict[str, Any] | None:
    """Rename a shelf. Returns None when the shelf is absent / not owned.

    Raises ``ShelfValidationError`` if the name doesn't pass the same
    non-empty + length check that applies to creation.
    """
    clean = _validate_name(name)
    return store.update_shelf(shelf_id=shelf_id, user_id=user_id, name=clean)


def reorder_shelf(
    store: PlanningStudioStore,
    *,
    shelf_id: str,
    user_id: str,
    sort_order: int,
) -> dict[str, Any] | None:
    """Move the shelf to a new sort position. Thin pass-through helper so
    the router doesn't need to touch the store module directly for this
    one corner case.
    """
    return store.update_shelf(
        shelf_id=shelf_id, user_id=user_id, sort_order=int(sort_order),
    )


def delete_shelf(
    store: PlanningStudioStore,
    *,
    shelf_id: str,
    user_id: str,
) -> bool:
    """Soft-delete a shelf and un-shelve its member projects.

    Returns True on success, False when absent / not owned. Member projects
    are never deleted — they drop onto the "Unfiled" shelf (shelf_id=NULL).
    """
    return store.delete_shelf(shelf_id=shelf_id, user_id=user_id)


def move_project_to_shelf(
    store: PlanningStudioStore,
    *,
    project_id: str,
    user_id: str,
    shelf_id: str | None,
) -> dict[str, Any] | None:
    """Move ``project_id`` to ``shelf_id`` (or to Unfiled when None).

    The store verifies ownership on BOTH objects. Any cross-user attempt
    returns None, which the router surfaces as 404.
    """
    normalized = shelf_id if shelf_id else None
    return store.move_project_to_shelf(
        project_id=project_id, user_id=user_id, shelf_id=normalized,
    )
