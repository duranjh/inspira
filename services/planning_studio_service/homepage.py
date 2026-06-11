"""Homepage AI suggestions — infer 3 new project ideas from a user's existing work.

Pure stateless function; the API layer owns caching if/when it lands.

Privacy contract: only project titles and topic titles are sent to the
model. No Q&A turn bodies, no decisions, no attached source content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import PlanningStudioStore

# Top topic titles included per project in the prompt context.
_MAX_TOPICS_PER_PROJECT = 3

# Minimum project count before suggestions are generated.
_MIN_PROJECTS = 2


def generate_suggestions(
    store: "PlanningStudioStore",
    *,
    user_id: str,
    adapter: Any,
    locale: str | None = None,
) -> list[str]:
    """Return up to 3 suggested new project ideas for the user.

    Returns an empty list when:
    - the user has fewer than ``_MIN_PROJECTS`` projects, or
    - the LLM call fails for any reason (non-critical feature).

    Args:
        store: The live store instance — used to read project + topic titles.
        user_id: Authenticated user identifier; never mixes in other users' data.
        adapter: An object that implements ``generate_homepage_suggestions``.
        locale: BCP-47 primary subtag (e.g. "es", "fr"). ``None`` → English.

    Returns:
        List of 3 suggestion strings, each 60–120 chars. May be fewer if
        the adapter returns a malformed payload; never raises.
    """
    all_projects = store.list_v2_projects(user_id=user_id)
    # Example projects are onboarding scaffolding, not real work the user
    # started. Excluding them from the count means a user with 1 real
    # project + 1 example correctly falls below the suggestions threshold
    # instead of triggering "you might also want to start…" on content
    # they didn't even author.
    projects = [p for p in all_projects if not _is_example_project(p)]
    if len(projects) < _MIN_PROJECTS:
        return []

    context = _build_context(store, projects)
    try:
        return adapter.generate_homepage_suggestions(context, locale)
    except Exception:  # noqa: BLE001
        # Non-critical feature — any adapter failure is a silent no-op.
        return []


def _is_example_project(project: dict[str, Any]) -> bool:
    """True when the project row is an onboarding example (not real user work).

    Examples are tagged with ``metadata.is_example = True`` at creation
    time by :mod:`planning_studio_service.example_projects`. The metadata
    column is stored as JSON text, so we tolerate dict / json-string /
    missing shapes.
    """
    meta = project.get("metadata")
    if meta is None:
        return False
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except (ValueError, TypeError):
            return False
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("is_example"))


def _build_context(
    store: "PlanningStudioStore",
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the context dict passed to the adapter.

    Shape::

        {
          "projects": [
            {"title": "...", "topics": ["...", "...", "..."]},
            ...
          ]
        }

    Topic titles are limited to ``_MAX_TOPICS_PER_PROJECT`` per project
    so the prompt stays within ~200 tokens.
    """
    result: list[dict[str, Any]] = []
    for project in projects:
        pid = project["project_id"]
        title = project.get("title") or "Untitled project"
        topics = store.list_topics(project_id=pid)
        topic_titles = [
            t["title"]
            for t in topics
            if t.get("title") and t.get("deleted_at") is None
        ][:_MAX_TOPICS_PER_PROJECT]
        result.append({"title": title, "topics": topic_titles})
    return {"projects": result}
