"""Add v2_artifact_comments table for inline IDE-style comments on generated code.

Revision ID: 20260513_0001
Revises: 20260512_0001
Create Date: 2026-05-13

Context
-------

Wave F.4 (#147 / design brief Direction A) ships per-line comment chips on the
artifact-viewer's generated scaffold. Partners reviewing the AI's code can flag
a specific line, pick a category (question / concern / suggest_fix), thread
replies, and resolve — without context-switching to the right-rail chat panel.

Anchoring
---------

Comments anchor to ``(file_path, line_number, line_content_hash)``. The hash
is a SHA-256 over the line's raw UTF-8 bytes, truncated to 16 hex chars —
short enough to compare quickly on the FE, long enough that drift past line
N isn't accidentally confused for a content match. The FE recomputes the
current line's hash on render; mismatch → "stale" outline UI. We never
auto-migrate or delete a stale comment (product decision: respect user
intent — if the line moved, the user decides what to do).

Threading
---------

Single-level v1. ``parent_comment_id`` always points to a top-level comment;
replies-to-replies still carry the same parent. FE renders replies as a flat
list under the parent. If we need real n-level threads later, the column is
already wired with the right CASCADE shape.

Idempotency
-----------

``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS`` mirror the
pattern from ``20260603_0001_decision_versions.py`` — works identically on
SQLite and Postgres because the schema is TEXT/INTEGER only.

Downgrade
---------

Drops indexes → table. Comment data is partner-authored and not regenerable,
so a real-world downgrade is destructive — but CI's reversibility smoke
requires a clean walk, so we drop cleanly.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260513_0001"
down_revision: Union[str, Sequence[str], None] = "20260512_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS v2_artifact_comments (
            comment_id         TEXT PRIMARY KEY,
            project_id         TEXT NOT NULL
                               REFERENCES v2_projects(project_id)
                               ON DELETE CASCADE,
            file_path          TEXT NOT NULL,
            line_number        INTEGER NOT NULL,
            line_content_hash  TEXT NOT NULL,
            category           TEXT NOT NULL
                               CHECK (category IN
                                 ('question','concern','suggest_fix')),
            body               TEXT NOT NULL,
            author_user_id     TEXT NOT NULL
                               REFERENCES users(user_id),
            parent_comment_id  TEXT
                               REFERENCES v2_artifact_comments(comment_id)
                               ON DELETE CASCADE,
            resolved_at        TEXT,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_v2_artifact_comments_project "
        "ON v2_artifact_comments(project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_v2_artifact_comments_file "
        "ON v2_artifact_comments(project_id, file_path)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_v2_artifact_comments_file")
    op.execute("DROP INDEX IF EXISTS idx_v2_artifact_comments_project")
    op.execute("DROP TABLE IF EXISTS v2_artifact_comments")
