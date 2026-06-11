"""Postgres dialect compatibility test.

Only runs when ``TEST_POSTGRES_URL`` is set in the environment.  Skip
otherwise — the main test suite must not require a live Postgres instance.

To run against a real DB:

    TEST_POSTGRES_URL=postgresql://user:pass@host/dbname pytest services/tests/test_postgres_dialect.py -v

The URL must point at a database that has already been migrated via alembic
(i.e. ``alembic upgrade head`` has been run against it).  A throwaway Neon
branch or a local Postgres with ``createdb inspira_test`` works fine.
"""
from __future__ import annotations

import os
import unittest

import pytest

TEST_PG_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_PG_URL,
    reason="TEST_POSTGRES_URL not set — skipping Postgres dialect tests",
)


@pytest.fixture()
def pg_store():
    """Return a PlanningStudioStore wired to TEST_POSTGRES_URL."""
    os.environ["DATABASE_URL"] = TEST_PG_URL
    from planning_studio_service.config import load_config
    from planning_studio_service.store import PlanningStudioStore

    config = load_config()
    store = PlanningStudioStore(config)
    assert store._is_postgres, "Store should have detected Postgres from DATABASE_URL"
    yield store
    # Cleanup: delete any test rows we wrote so reruns stay idempotent.
    with store._connect() as conn:
        conn.execute("DELETE FROM users WHERE email LIKE %s", ("pgtest_%@example.com",))
        conn.commit()


class TestPostgresBasicCRUD:
    """Basic read/write smoke-tests on the Postgres backend."""

    def test_create_and_get_user(self, pg_store):
        email = "pgtest_create@example.com"
        user = pg_store.create_user(email=email, password_hash="hashed_pw")
        assert user["email"] == email
        fetched = pg_store.get_user_by_email(email)
        assert fetched is not None
        assert fetched["user_id"] == user["user_id"]

    def test_get_user_by_email_missing(self, pg_store):
        result = pg_store.get_user_by_email("pgtest_nobody@example.com")
        assert result is None

    def test_update_user_password(self, pg_store):
        email = "pgtest_pwd@example.com"
        user = pg_store.create_user(email=email, password_hash="old_hash")
        updated = pg_store.update_user_password(user["user_id"], "new_hash")
        assert updated is True
        fetched = pg_store.get_user_by_id(user["user_id"])
        assert fetched is not None
        assert fetched["password_hash"] == "new_hash"

    def test_list_v2_projects_empty(self, pg_store):
        email = "pgtest_proj@example.com"
        user = pg_store.create_user(email=email)
        projects = pg_store.list_v2_projects(user_id=user["user_id"])
        assert isinstance(projects, list)

    def test_create_and_list_v2_project(self, pg_store):
        email = "pgtest_newproj@example.com"
        user = pg_store.create_user(email=email)
        project = pg_store.create_v2_project(user_id=user["user_id"], title="PG Test Project")
        assert project["title"] == "PG Test Project"
        projects = pg_store.list_v2_projects(user_id=user["user_id"])
        assert any(p["project_id"] == project["project_id"] for p in projects)

    def test_delete_v2_project(self, pg_store):
        email = "pgtest_delproj@example.com"
        user = pg_store.create_user(email=email)
        project = pg_store.create_v2_project(user_id=user["user_id"], title="To Delete")
        deleted = pg_store.delete_v2_project(project_id=project["project_id"], user_id=user["user_id"])
        assert deleted is True
        projects = pg_store.list_v2_projects(user_id=user["user_id"])
        assert all(p["project_id"] != project["project_id"] for p in projects)


class TestPostgresDialectCompatibility:
    """Tests that verify dialect-specific translation handles correctly."""

    def test_on_conflict_do_nothing_upsert(self, pg_store):
        """create_user uses ON CONFLICT(user_id) DO NOTHING — calling twice must not error."""
        email = "pgtest_upsert@example.com"
        user = pg_store.create_user(email=email, user_id="pgtest-fixed-uid")
        # Second call with same user_id should be a no-op (DO NOTHING).
        user2 = pg_store.create_user(email=email, user_id="pgtest-fixed-uid")
        # Both return the same user_id.
        assert user["user_id"] == user2["user_id"]

    def test_update_returns_rowcount(self, pg_store):
        """update_user_password returns True (rowcount > 0) on hit, False on miss."""
        hit = pg_store.update_user_password("nonexistent-user-id", "some_hash")
        assert hit is False

    def test_delete_returns_false_on_miss(self, pg_store):
        """delete_v2_project returns False for an unknown project."""
        result = pg_store.delete_v2_project(project_id="no-such-project", user_id="no-such-user")
        assert result is False

    def test_upsert_subscription(self, pg_store):
        """upsert_subscription uses ON CONFLICT … DO UPDATE — idempotent on Postgres."""
        email = "pgtest_sub@example.com"
        user = pg_store.create_user(email=email)
        sub = pg_store.upsert_subscription(user_id=user["user_id"], plan="free", status="active")
        assert sub["plan"] == "free"
        # Upsert again with a different plan.
        sub2 = pg_store.upsert_subscription(user_id=user["user_id"], plan="pro", status="active")
        assert sub2["plan"] == "pro"
