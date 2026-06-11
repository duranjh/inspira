"""Unit tests for the scheduled cleanup jobs.

Exercises each of the three prune functions against a minimal fixture DB
(no HTTP layer, no planner adapter) and asserts:
- Correct count returned.
- Correct rows removed / revoked.
- Idempotency: a second run returns 0 because nothing is left to do.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from planning_studio_service import cleanup_jobs
from planning_studio_service.config import load_config
from planning_studio_service.store import PlanningStudioStore, now_timestamp


def _iso_offset(seconds: int) -> str:
    """Return an ISO-8601 UTC timestamp offset from now by ``seconds``."""
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="seconds")


def _iso_days_ago(days: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat(timespec="seconds")


class CleanupJobsFixtureMixin:
    """Builds a fresh isolated store on every test."""

    def setUp(self) -> None:  # type: ignore[override]
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="inspira-cleanup-test-", ignore_cleanup_errors=True,
        )
        os.environ["PLANNING_STUDIO_STORAGE_ROOT"] = self.temp_dir.name
        self.config = load_config()
        self.store = PlanningStudioStore(self.config)

    def tearDown(self) -> None:  # type: ignore[override]
        os.environ.pop("PLANNING_STUDIO_STORAGE_ROOT", None)
        self.temp_dir.cleanup()


class PruneExpiredSessionsTests(CleanupJobsFixtureMixin, unittest.TestCase):
    """``prune_expired_sessions`` operates on ``password_reset_tokens``."""

    def _make_user(self, user_id: str = "u-1") -> str:
        # The password_reset_tokens FK to users isn't declared in the SQLite
        # schema (the column references user_id purely by convention), so
        # we can insert directly without going through create_user. But
        # we do so anyway — it keeps the test faithful to a real path.
        user = self.store.create_user(
            user_id=user_id,
            email=f"{user_id}@example.com",
            password_hash=None,
            display_name="",
        )
        return user["user_id"]

    def _insert_token(
        self,
        *,
        token_hash: str,
        user_id: str,
        requested_at: str,
        expires_at: str,
        used_at: str | None = None,
    ) -> None:
        with self.store._connect() as conn:  # noqa: SLF001 — test-only
            conn.execute(
                """
                INSERT INTO password_reset_tokens
                    (token_hash, user_id, requested_at, expires_at, used_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, requested_at, expires_at, used_at),
            )
            conn.commit()

    def _count_tokens(self) -> int:
        with self.store._connect() as conn:  # noqa: SLF001 — test-only
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM password_reset_tokens"
            ).fetchone()
        return int(row["n"])

    def test_removes_only_expired_rows(self) -> None:
        user_id = self._make_user()
        now_iso = now_timestamp()

        # Two expired (one consumed, one not) + two still-live.
        self._insert_token(
            token_hash=hashlib.sha256(b"exp-1").hexdigest(),
            user_id=user_id,
            requested_at=_iso_offset(-7200),
            expires_at=_iso_offset(-3600),  # 1h ago
        )
        self._insert_token(
            token_hash=hashlib.sha256(b"exp-2").hexdigest(),
            user_id=user_id,
            requested_at=_iso_offset(-7200),
            expires_at=_iso_offset(-1),  # 1s ago
            used_at=now_iso,
        )
        self._insert_token(
            token_hash=hashlib.sha256(b"live-1").hexdigest(),
            user_id=user_id,
            requested_at=now_iso,
            expires_at=_iso_offset(3600),  # 1h from now
        )
        self._insert_token(
            token_hash=hashlib.sha256(b"live-2").hexdigest(),
            user_id=user_id,
            requested_at=now_iso,
            expires_at=_iso_offset(86400),  # 24h from now
        )

        self.assertEqual(self._count_tokens(), 4)
        removed = cleanup_jobs.prune_expired_sessions(self.store)
        self.assertEqual(removed, 2)
        self.assertEqual(self._count_tokens(), 2)

        # Only live tokens survive.
        with self.store._connect() as conn:  # noqa: SLF001 — test-only
            rows = conn.execute(
                "SELECT token_hash FROM password_reset_tokens ORDER BY token_hash"
            ).fetchall()
        surviving = {r["token_hash"] for r in rows}
        self.assertIn(hashlib.sha256(b"live-1").hexdigest(), surviving)
        self.assertIn(hashlib.sha256(b"live-2").hexdigest(), surviving)

    def test_idempotent_second_call_returns_zero(self) -> None:
        user_id = self._make_user()
        self._insert_token(
            token_hash=hashlib.sha256(b"only").hexdigest(),
            user_id=user_id,
            requested_at=_iso_offset(-7200),
            expires_at=_iso_offset(-3600),
        )
        self.assertEqual(cleanup_jobs.prune_expired_sessions(self.store), 1)
        self.assertEqual(cleanup_jobs.prune_expired_sessions(self.store), 0)

    def test_empty_table_returns_zero(self) -> None:
        self.assertEqual(cleanup_jobs.prune_expired_sessions(self.store), 0)


class PruneAbandonedAnonymousAccountsTests(
    CleanupJobsFixtureMixin, unittest.TestCase,
):
    """The is_system user is shared — the job is a documented no-op."""

    def test_returns_zero_without_touching_users(self) -> None:
        # Materialise the system user the way auth.py does.
        system_user = self.store.create_user(
            user_id="user-system",
            email="system@inspira.local",
            password_hash=None,
            display_name="System",
        )
        self.assertEqual(system_user["user_id"], "user-system")

        result = cleanup_jobs.prune_abandoned_anonymous_accounts(
            self.store, older_than_days=7,
        )
        self.assertEqual(result, 0)

        # System user still there.
        self.assertIsNotNone(self.store.get_user_by_id("user-system"))

    def test_idempotent(self) -> None:
        self.assertEqual(
            cleanup_jobs.prune_abandoned_anonymous_accounts(self.store), 0,
        )
        self.assertEqual(
            cleanup_jobs.prune_abandoned_anonymous_accounts(self.store), 0,
        )


class PruneStaleShareTokensTests(CleanupJobsFixtureMixin, unittest.TestCase):
    """``prune_stale_share_tokens`` revokes never-viewed old tokens."""

    def _insert_share_link(
        self,
        *,
        token: str,
        project_id: str,
        created_at: str,
        last_viewed_at: str | None = None,
        revoked_at: str | None = None,
    ) -> None:
        with self.store._connect() as conn:  # noqa: SLF001 — test-only
            conn.execute(
                """
                INSERT INTO shared_links
                    (token, project_id, created_by_user_id, created_at,
                     revoked_at, last_viewed_at, view_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    token, project_id, "u-owner", created_at,
                    revoked_at, last_viewed_at,
                ),
            )
            conn.commit()

    def _read_link(self, token: str) -> dict[str, object]:
        with self.store._connect() as conn:  # noqa: SLF001 — test-only
            row = conn.execute(
                "SELECT revoked_at, last_viewed_at FROM shared_links WHERE token = ?",
                (token,),
            ).fetchone()
        self.assertIsNotNone(row, f"token {token!r} missing")
        return dict(row)

    def test_revokes_never_viewed_old_tokens_only(self) -> None:
        # Stale + never viewed → revoke.
        self._insert_share_link(
            token="stale-a", project_id="p-1",
            created_at=_iso_days_ago(120),
        )
        self._insert_share_link(
            token="stale-b", project_id="p-2",
            created_at=_iso_days_ago(95),
        )
        # Stale but viewed → keep.
        self._insert_share_link(
            token="viewed", project_id="p-3",
            created_at=_iso_days_ago(200),
            last_viewed_at=_iso_days_ago(10),
        )
        # Recent + never viewed → keep.
        self._insert_share_link(
            token="recent", project_id="p-4",
            created_at=_iso_days_ago(5),
        )
        # Already revoked → untouched (shouldn't count toward revoked total).
        self._insert_share_link(
            token="already-revoked", project_id="p-5",
            created_at=_iso_days_ago(120),
            revoked_at=_iso_days_ago(30),
        )

        revoked = cleanup_jobs.prune_stale_share_tokens(
            self.store, older_than_days=90,
        )
        self.assertEqual(revoked, 2)

        # Stale ones are now revoked.
        self.assertIsNotNone(self._read_link("stale-a")["revoked_at"])
        self.assertIsNotNone(self._read_link("stale-b")["revoked_at"])
        # Viewed stale stays live.
        self.assertIsNone(self._read_link("viewed")["revoked_at"])
        # Recent stays live.
        self.assertIsNone(self._read_link("recent")["revoked_at"])
        # Already-revoked was not re-touched.
        already = self._read_link("already-revoked")
        # revoked_at should still be the original historical value — not now.
        self.assertTrue(str(already["revoked_at"]).startswith(_iso_days_ago(30)[:10]))

    def test_idempotent_second_call_returns_zero(self) -> None:
        self._insert_share_link(
            token="stale", project_id="p-1",
            created_at=_iso_days_ago(120),
        )
        self.assertEqual(
            cleanup_jobs.prune_stale_share_tokens(self.store, older_than_days=90), 1,
        )
        self.assertEqual(
            cleanup_jobs.prune_stale_share_tokens(self.store, older_than_days=90), 0,
        )

    def test_custom_threshold_respected(self) -> None:
        # A 30-day-old token gets spared by the default (90) and swept by 7.
        self._insert_share_link(
            token="mid", project_id="p-1", created_at=_iso_days_ago(30),
        )
        self.assertEqual(
            cleanup_jobs.prune_stale_share_tokens(self.store, older_than_days=90), 0,
        )
        self.assertEqual(
            cleanup_jobs.prune_stale_share_tokens(self.store, older_than_days=7), 1,
        )

    def test_empty_table_returns_zero(self) -> None:
        self.assertEqual(
            cleanup_jobs.prune_stale_share_tokens(self.store), 0,
        )


class RunCleanupTests(CleanupJobsFixtureMixin, unittest.TestCase):
    """End-to-end: run_cleanup returns counts dict + calls all three."""

    def test_returns_dict_with_all_three_keys(self) -> None:
        counts = cleanup_jobs.run_cleanup(self.store)
        self.assertEqual(
            set(counts.keys()), {"sessions", "anon_accounts", "share_tokens"},
        )
        # Nothing in the DB → all zero.
        self.assertEqual(counts["sessions"], 0)
        self.assertEqual(counts["anon_accounts"], 0)
        self.assertEqual(counts["share_tokens"], 0)

    def test_counts_mixed_workload(self) -> None:
        user = self.store.create_user(
            user_id="u-1", email="u-1@example.com",
            password_hash=None, display_name="",
        )
        # One expired token.
        with self.store._connect() as conn:  # noqa: SLF001 — test-only
            conn.execute(
                """
                INSERT INTO password_reset_tokens
                    (token_hash, user_id, requested_at, expires_at, used_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    hashlib.sha256(b"t").hexdigest(), user["user_id"],
                    _iso_offset(-7200), _iso_offset(-3600),
                ),
            )
            conn.execute(
                """
                INSERT INTO shared_links
                    (token, project_id, created_by_user_id, created_at,
                     revoked_at, last_viewed_at, view_count)
                VALUES ('stale', 'p', 'u-1', ?, NULL, NULL, 0)
                """,
                (_iso_days_ago(100),),
            )
            conn.commit()

        counts = cleanup_jobs.run_cleanup(self.store)
        self.assertEqual(counts, {"sessions": 1, "anon_accounts": 0, "share_tokens": 1})

        # Second run is a no-op on the freshly-swept DB.
        counts_2 = cleanup_jobs.run_cleanup(self.store)
        self.assertEqual(counts_2, {"sessions": 0, "anon_accounts": 0, "share_tokens": 0})


if __name__ == "__main__":
    unittest.main()
