"""Atomic merge of two duplicate topics.

Public surface:
    merge_topics(store, *, user_id, project_id, keep_id, drop_id)
        -> dict with merged_turns, merged_decisions,
                    rerouted_relationships, dropped_self_edges

All mutations run inside a single ``with connection:`` transaction so the
operation is all-or-nothing.  The caller (api.py route) is responsible for
firing the ``inspira:decisions-changed`` and ``inspira:topics-changed``
events on the *frontend*; this module only touches the database.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import PlanningStudioStore


def merge_topics(
    store: "PlanningStudioStore",
    *,
    user_id: str,
    project_id: str,
    keep_id: str,
    drop_id: str,
) -> dict[str, Any]:
    """Merge *drop_id* into *keep_id* within *project_id*.

    Steps (all inside one transaction):
    1. Validate both topics belong to project_id and are owned by user_id.
    2. Re-parent qna_turns from drop → keep (preserve order_index).
    3. Re-parent decisions from drop → keep.
    4. Re-point relationships: replace drop_id refs with keep_id, drop
       any that would become self-edges (keep→keep) or that duplicate an
       already-existing relationship.
    5. Soft-delete the drop topic (sets deleted_at; does NOT cascade-delete
       relationships because we've already migrated them in step 4).

    Returns a summary dict for the API response.

    Raises:
        ValueError — self-merge, cross-project, or cross-user violation.
    """
    if keep_id == drop_id:
        raise ValueError("keep_id and drop_id must be different topics")

    # Ownership + project membership checks happen via store helpers so we
    # never bypass the tenancy fence.
    keep_topic = store.get_topic_with_ownership(keep_id, user_id=user_id)
    drop_topic = store.get_topic_with_ownership(drop_id, user_id=user_id)

    if keep_topic is None:
        raise ValueError(f"Topic {keep_id!r} not found or not owned by user")
    if drop_topic is None:
        raise ValueError(f"Topic {drop_id!r} not found or not owned by user")

    if keep_topic["project_id"] != project_id:
        raise ValueError(
            f"Topic {keep_id!r} belongs to project {keep_topic['project_id']!r}, "
            f"not {project_id!r}"
        )
    if drop_topic["project_id"] != project_id:
        raise ValueError(
            f"Topic {drop_id!r} belongs to project {drop_topic['project_id']!r}, "
            f"not {project_id!r}"
        )
    if keep_topic["project_id"] != drop_topic["project_id"]:
        raise ValueError("Topics belong to different projects")

    # Re-use the store's internal _connect() so we share the same DB
    # connection (SQLite locally, Postgres in prod) and can wrap
    # everything in one transaction.
    from .store import now_timestamp
    _now_iso = now_timestamp()
    with store._connect() as conn:  # noqa: SLF001
        # ------------------------------------------------------------------
        # 1. Re-parent Q&A turns
        # ------------------------------------------------------------------
        # We must offset the incoming turns' order_index so they append
        # *after* the turns already on keep_id.
        row = conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) FROM qna_turns WHERE topic_id = ?",
            (keep_id,),
        ).fetchone()
        keep_max_order: int = row[0]

        # Fetch drop's turns in order so we can assign new order_index values.
        drop_turns = conn.execute(
            "SELECT turn_id, order_index FROM qna_turns WHERE topic_id = ? ORDER BY order_index",
            (drop_id,),
        ).fetchall()

        merged_turns = 0
        for turn_row in drop_turns:
            new_order = keep_max_order + 1 + turn_row[1]
            conn.execute(
                "UPDATE qna_turns SET topic_id = ?, order_index = ? WHERE turn_id = ?",
                (keep_id, new_order, turn_row[0]),
            )
            merged_turns += 1

        # ------------------------------------------------------------------
        # 2. Re-parent decisions
        # ------------------------------------------------------------------
        cursor = conn.execute(
            "UPDATE decisions SET topic_id = ? WHERE topic_id = ? AND retracted_at IS NULL",
            (keep_id, drop_id),
        )
        merged_decisions = cursor.rowcount
        # Also update any retracted decisions so the drop topic is fully drained.
        conn.execute(
            "UPDATE decisions SET topic_id = ? WHERE topic_id = ?",
            (keep_id, drop_id),
        )

        # ------------------------------------------------------------------
        # 3. Re-point relationships
        # ------------------------------------------------------------------
        # Collect all live relationships touching drop_id.
        rels = conn.execute(
            """
            SELECT relationship_id, source_topic_id, target_topic_id
            FROM relationships
            WHERE (source_topic_id = ? OR target_topic_id = ?)
              AND deleted_at IS NULL
            """,
            (drop_id, drop_id),
        ).fetchall()

        rerouted = 0
        dropped_self = 0

        for rel in rels:
            rel_id = rel[0]
            new_src = keep_id if rel[1] == drop_id else rel[1]
            new_tgt = keep_id if rel[2] == drop_id else rel[2]

            # Self-edge after remap → drop.
            if new_src == new_tgt:
                conn.execute(
                    "UPDATE relationships SET deleted_at = ? WHERE relationship_id = ?",
                    (_now_iso, rel_id),
                )
                dropped_self += 1
                continue

            # Would duplicate an existing live relationship → drop.
            existing = conn.execute(
                """
                SELECT 1 FROM relationships
                WHERE project_id = ?
                  AND source_topic_id = ?
                  AND target_topic_id = ?
                  AND relationship_id != ?
                  AND deleted_at IS NULL
                """,
                (project_id, new_src, new_tgt, rel_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE relationships SET deleted_at = ? WHERE relationship_id = ?",
                    (_now_iso, rel_id),
                )
                dropped_self += 1
                continue

            # Safe to re-point.
            conn.execute(
                "UPDATE relationships SET source_topic_id = ?, target_topic_id = ? WHERE relationship_id = ?",
                (new_src, new_tgt, rel_id),
            )
            rerouted += 1

        # ------------------------------------------------------------------
        # 4. Soft-delete the drop topic WITHOUT cascading relationship
        #    soft-deletes (we handled them above).
        # ------------------------------------------------------------------
        conn.execute(
            "UPDATE topics SET deleted_at = ?, updated_at = ? "
            "WHERE topic_id = ? AND deleted_at IS NULL",
            (_now_iso, _now_iso, drop_id),
        )

        # Transaction commits on __exit__ of the context manager.

    return {
        "merged_turns": merged_turns,
        "merged_decisions": merged_decisions,
        "rerouted_relationships": rerouted,
        "dropped_self_edges": dropped_self,
    }
