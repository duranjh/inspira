"""Postgres-path coverage for the alembic 20260518_0002 backfill rule.

Pre-W3 ``v2_projects`` rows are user-authored work that's already
shipping or shipped — they should land in the "Shipping" Kanban
column (state = ``approved``), NOT "Review needed". The migration
backfills via:

    UPDATE v2_projects
    SET project_state = COALESCE(metadata_json::jsonb ->> 'state', 'approved')
    WHERE project_state IS NULL

This test asserts that contract by:

1. Spinning up a synthetic v2_projects table on a real Postgres,
2. Seeding rows with the three canonical metadata shapes (legacy
   user-created without state, orchestrator-stamped pending_review,
   orchestrator-stamped any other state), and
3. Running the migration's UPDATE statement directly to confirm
   each row lands in the right column.

The test is gated on the ``INSPIRA_TEST_POSTGRES_URL`` env var so
local SQLite-only runs skip cleanly. A passing CI requires the env
to be set against a real (ephemeral) Postgres instance.
"""
from __future__ import annotations

import os
import unittest
import uuid


_PG_URL = os.environ.get("INSPIRA_TEST_POSTGRES_URL")


@unittest.skipUnless(
    _PG_URL,
    "INSPIRA_TEST_POSTGRES_URL not set — Postgres-path test skipped",
)
class ProjectStateBackfillPostgresTests(unittest.TestCase):
    """Mirror of the migration's UPDATE on a real Postgres connection.

    Each test gets its own throwaway schema so the tests don't
    collide on the shared DB. The schema is dropped on teardown.
    """

    def setUp(self) -> None:
        # Late import — psycopg is a heavy dep that we don't want to
        # require for SQLite-only test runs (the import itself
        # opens up the libpq dynamic library).
        import psycopg  # noqa: PLC0415
        self.conn = psycopg.connect(_PG_URL)
        self.schema = f"backfill_{uuid.uuid4().hex[:8]}"
        with self.conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {self.schema}")
            cur.execute(f"SET search_path TO {self.schema}")
            # Minimal v2_projects shape — matches the columns the
            # migration's UPDATE touches. We omit the indexes /
            # FKs / soft-delete columns since they're orthogonal to
            # the backfill logic.
            cur.execute(
                """
                CREATE TABLE v2_projects (
                    project_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        self.conn.commit()

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def _seed(self, project_id: str, metadata_json: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SET search_path TO {self.schema}; "
                "INSERT INTO v2_projects "
                "(project_id, user_id, title, metadata_json, "
                " created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (project_id, "u1", "T", metadata_json,
                 "2026-04-01T00:00:00Z", "2026-04-01T00:00:00Z"),
            )
        self.conn.commit()

    def _backfill(self) -> None:
        """Run the exact ALTER + UPDATE sequence the migration runs."""
        with self.conn.cursor() as cur:
            cur.execute(f"SET search_path TO {self.schema}")
            cur.execute(
                "ALTER TABLE v2_projects "
                "ADD COLUMN IF NOT EXISTS project_state TEXT"
            )
            # Mirror migration 20260518_0002's exact UPDATE — the
            # ``replace(metadata_json, '\u0000', '')`` strips JSON-escaped
            # null-byte sequences before the ``::jsonb`` cast. Real
            # production data hit this; without the strip, Postgres raises
            # ``UntranslatableCharacter`` on rows with stray null bytes.
            cur.execute(
                """
                UPDATE v2_projects
                SET project_state = COALESCE(
                    replace(metadata_json, '\\u0000', '')::jsonb ->> 'state',
                    'approved'
                )
                WHERE project_state IS NULL
                """
            )
        self.conn.commit()

    def _state_of(self, project_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SET search_path TO {self.schema}; "
                "SELECT project_state FROM v2_projects WHERE project_id = %s",
                (project_id,),
            )
            row = cur.fetchone()
        assert row is not None, f"row {project_id!r} missing"
        return row[0]

    def test_legacy_row_without_state_metadata_backfills_to_approved(
        self,
    ) -> None:
        """The pre-W3 user-created shape: empty metadata. Lands in Shipping."""
        self._seed("p-legacy", metadata_json="{}")
        self._backfill()
        self.assertEqual(self._state_of("p-legacy"), "approved")

    def test_orchestrator_pending_review_metadata_preserved(self) -> None:
        """Session α's orchestrator stamps state into metadata; preserve it."""
        self._seed(
            "p-pending",
            metadata_json='{"state":"pending_review","autonomous":true}',
        )
        self._backfill()
        self.assertEqual(self._state_of("p-pending"), "pending_review")

    def test_orchestrator_failed_state_metadata_preserved(self) -> None:
        """The orchestrator can also write 'generation_failed' for crashed
        runs — the COALESCE preserves the literal value, even though it's
        not a state-machine target. The migration does not validate
        against ``STATES`` (intentionally — the CHECK constraint runs
        AFTER the backfill, and rows with a non-enum value will be caught
        only on the subsequent enum-CHECK pass)."""
        self._seed(
            "p-failed",
            metadata_json='{"state":"generation_failed"}',
        )
        # Using a permissive CHECK (no enum yet) so the backfill itself
        # lands. In production the next step is ``ADD CHECK`` which
        # would fail this row — surfacing the bug at upgrade time, by
        # design (we don't want to silently drop user data).
        self._backfill()
        self.assertEqual(self._state_of("p-failed"), "generation_failed")

    def test_other_metadata_without_state_backfills_to_approved(
        self,
    ) -> None:
        """Metadata exists but no ``state`` key — same fallback as
        empty metadata."""
        self._seed(
            "p-other",
            metadata_json='{"domain":"software","skill":"backend"}',
        )
        self._backfill()
        self.assertEqual(self._state_of("p-other"), "approved")

    def test_null_byte_in_metadata_does_not_crash_backfill(self) -> None:
        """Regression for the deploy-blocker incident (2026-05-03):
        a real production v2_projects row contained ``\\u0000`` in
        ``metadata_json`` (user-typed stray null byte in an
        ``opening_note``). Postgres ``jsonb`` cannot represent null
        bytes in strings, so the original ``metadata_json::jsonb``
        cast raised ``UntranslatableCharacter`` and three consecutive
        deploys (γ, provider-swap, Promote-and-SSE) failed in a row.

        The fix strips the literal 6-character ``\\u0000`` sequence
        from the TEXT input before the cast. Row's ``metadata_json``
        keeps the null-byte escape (we don't mutate user data); only
        the backfill computation skips it.

        This test pins the fix: a metadata blob with a stray
        ``\\u0000`` in a non-state field should backfill to
        ``approved`` without error (the ``state`` key isn't present;
        COALESCE falls through)."""
        self._seed(
            "p-nullbyte-other",
            metadata_json='{"opening_note":"hello\\u0000world","domain":"x"}',
        )
        # Without the ``replace`` strip in the migration, this raises
        # psycopg.errors.UntranslatableCharacter and the deploy fails.
        self._backfill()
        self.assertEqual(self._state_of("p-nullbyte-other"), "approved")

    def test_null_byte_alongside_state_key_still_preserves_state(
        self,
    ) -> None:
        """The defense-in-depth case: an orchestrator-stamped row where
        the partner-supplied opening_note contains ``\\u0000`` AND the
        orchestrator wrote ``state=pending_review``. Strip the null byte
        from the TEXT input; the ``state`` value comes through cleanly."""
        self._seed(
            "p-nullbyte-pending",
            metadata_json='{"state":"pending_review","note":"a\\u0000b"}',
        )
        self._backfill()
        self.assertEqual(self._state_of("p-nullbyte-pending"), "pending_review")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
