"""Tests for the example project seeding module.

Covers:
1. Unknown slug raises ValueError
2. Instantiate creates project + all expected topics / decisions / turns
3. Metadata has is_example: true
4. Cross-user isolation (created under the passed user_id only)
5. All 6 seeds validate structure invariants (≥5 topics, decisions/turns)
"""
from __future__ import annotations

import os
import tempfile

import pytest

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.config import load_config
from planning_studio_service.example_projects import (
    EXAMPLE_PROJECTS,
    ExampleProjectBody,
    instantiate_example_project,
)
from planning_studio_service.store import PlanningStudioStore

ensure_loaded()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def store():
    """Fresh isolated store backed by a temp directory."""
    with tempfile.TemporaryDirectory(
        prefix="inspira-example-test-", ignore_cleanup_errors=True,
    ) as tmp:
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = tmp
        yield PlanningStudioStore(load_config())


def _make_user(store: PlanningStudioStore, email: str = "alice@example.com") -> str:
    user = store.create_user(email=email, display_name="Alice", password_hash="x")
    return user["user_id"]


# ---------------------------------------------------------------------------
# 1. Unknown slug raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_slug_raises(store):
    user_id = _make_user(store)
    with pytest.raises(ValueError, match="unknown_example"):
        instantiate_example_project(store, user_id=user_id, slug="not_a_real_slug")


# ---------------------------------------------------------------------------
# 2. Instantiate creates project + all expected topics / decisions / turns
# ---------------------------------------------------------------------------


def test_instantiate_novel_creates_topics_decisions_turns(store):
    user_id = _make_user(store)
    project = instantiate_example_project(store, user_id=user_id, slug="novel")

    project_id = project["project_id"]

    # All 6 topics should exist
    topics = store.list_topics(project_id=project_id)
    assert len(topics) == 6

    # Check that at least one topic has decisions
    all_decisions = store.list_decisions(project_id=project_id)
    assert len(all_decisions) >= 6, "Expected several decisions across all topics"

    # Check that at least one topic has Q&A turns
    any_turns = False
    for topic in topics:
        turns = store.list_qna_turns(topic_id=topic["topic_id"])
        if turns:
            any_turns = True
            # Planner turn must come first
            assert turns[0]["role"] == "planner"
            # User answer should follow
            if len(turns) >= 2:
                assert turns[1]["role"] == "user"
    assert any_turns, "Expected at least one topic to have Q&A turns"


# ---------------------------------------------------------------------------
# 3. Metadata has is_example: true and example_slug
# ---------------------------------------------------------------------------


def test_metadata_is_example(store):
    user_id = _make_user(store)
    project = instantiate_example_project(store, user_id=user_id, slug="startup")

    meta = project.get("metadata") or {}
    assert meta.get("is_example") is True, "Expected is_example=True in metadata"
    assert meta.get("example_slug") == "startup"


# ---------------------------------------------------------------------------
# 4. Cross-user isolation
# ---------------------------------------------------------------------------


def test_cross_user_isolation(store):
    user_a = _make_user(store, email="alice@example.com")
    user_b = _make_user(store, email="bob@example.com")

    project_a = instantiate_example_project(store, user_id=user_a, slug="event")
    project_b = instantiate_example_project(store, user_id=user_b, slug="event")

    assert project_a["user_id"] == user_a
    assert project_b["user_id"] == user_b
    assert project_a["project_id"] != project_b["project_id"]

    # Listing topics for project_a should not return project_b's topics
    topics_a = store.list_topics(project_id=project_a["project_id"])
    topics_b = store.list_topics(project_id=project_b["project_id"])
    ids_a = {t["topic_id"] for t in topics_a}
    ids_b = {t["topic_id"] for t in topics_b}
    assert ids_a.isdisjoint(ids_b), "Topics leaked across users"


# ---------------------------------------------------------------------------
# 5. All 6 seeds validate structure invariants
# ---------------------------------------------------------------------------


def test_all_seeds_validate_structure():
    """Each seed must have ≥5 topics, each topic ≥1 decision, ≥3 topics with a turn."""
    assert len(EXAMPLE_PROJECTS) == 6, "Expected exactly 6 example project seeds"

    for seed in EXAMPLE_PROJECTS:
        assert len(seed.topics) >= 5, (
            f"Seed '{seed.slug}' has only {len(seed.topics)} topics; need ≥5"
        )

        for topic in seed.topics:
            assert len(topic.decisions) >= 1, (
                f"Topic '{topic.title}' in seed '{seed.slug}' has no decisions"
            )

        topics_with_turns = [t for t in seed.topics if t.turns]
        assert len(topics_with_turns) >= 3, (
            f"Seed '{seed.slug}' has only {len(topics_with_turns)} topics with Q&A turns; need ≥3"
        )


# ---------------------------------------------------------------------------
# 6. ExampleProjectBody Pydantic model parses correctly
# ---------------------------------------------------------------------------


def test_example_project_body_model():
    body = ExampleProjectBody(slug="career")
    assert body.slug == "career"


# ---------------------------------------------------------------------------
# 7. Each seed instantiation produces the right title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", [s.slug for s in EXAMPLE_PROJECTS])
def test_each_slug_creates_correctly_titled_project(store, slug):
    user_id = _make_user(store)
    project = instantiate_example_project(store, user_id=user_id, slug=slug)

    seed = next(s for s in EXAMPLE_PROJECTS if s.slug == slug)
    assert project["title"] == seed.display_name
    assert project["user_id"] == user_id
