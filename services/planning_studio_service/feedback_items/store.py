"""Free-function store helpers for feedback_items (W2 F4 + F5).

Workspace-scoped at the schema layer (every helper takes
``workspace_id`` as a keyword arg). Idempotency via the
``UNIQUE (workspace_id, content_hash)`` constraint:
``upsert_item`` re-runs as a no-op when the same content hits
twice.

``content_hash_for`` is the canonical hashing rule. Linear /
GitHub / etc. include the source-side ID so two issues with
identical content stay distinct; CSV imports include
``received_at`` so two imports of the same row dedupe.

W2 F5: ``upsert_item`` runs the rule-based classifier
(``feedback_items.classify``) on insert and stores the
result in ``type_hint``. Partners' source-supplied hint values
are preserved; only empty hints get filled by the classifier.
The status is flipped from ``queued`` → ``classified`` on
insert so the F6+ inbox can render directly without waiting on
an async worker tick.

Architectural note — when to migrate to async queue + worker
-------------------------------------------------------------

The current architecture is *sync classify on insert*. Every
import row gets classified synchronously inside ``upsert_item``
(via the rule-based fallback) or pre-classified at the import-
endpoint layer (when LLM mode is on, see ``llm_classify``).

This pattern works as long as **(import_size × per-item latency)
< request-timeout**. Concrete trigger points to migrate to a
queue + worker pattern:

1. **Import volume crosses 5000+ rows per batch.** Currently
   capped at 5000 by ``MAX_CSV_IMPORT_ROWS`` in the router; if
   partners need 50K+ row imports, the request times out before
   completion. Move to: import endpoint inserts ``status='queued'``
   rows, returns 202; a worker picks queued rows in batches.

2. **LLM classifier latency × import size > 30s.** Today: 200
   rows × ~40ms/item batch latency = ~8s. If we shift to a slower
   model (Sonnet for high-stakes review, ~3s/call) or per-item
   embedding calls, the same 200-row import would block ~600s.

3. **Partner volume crosses ~50 active workspaces.** The Linear /
   GitHub sync scheduler is already sequential per cycle; adding
   inline LLM classify on each sync compounds.

When any of those triggers fire, the queue + worker shape:
  - Reuse the existing ``status='queued'`` schema field (already
    reserved).
  - New ``feedback_items_classify_queue`` Postgres table or just
    SELECT ... WHERE status='queued' LIMIT N FOR UPDATE SKIP LOCKED.
  - Worker as another lifespan-spawned asyncio task (mirror of
    ``sync_scheduler``).
  - Inbox UI shows a "classifying…" badge for queued items;
    partner sees them appear classified once the worker catches up.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any

from .classify import classify
from .models import FeedbackItem, FeedbackItemCount, FeedbackItemStatus

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


def _now(store: "PlanningStudioStore") -> str:
    """Mirror of the rest-of-store ISO-8601 UTC stamp."""
    from ..store import now_timestamp

    return now_timestamp()


def content_hash_for(
    *,
    source: str,
    external_id: str | None,
    title: str,
    body: str,
    received_at: str | None,
) -> str:
    """SHA-256 of the canonical content string for a feedback item.

    Drives the idempotency UNIQUE constraint. Sources that have a
    server-side ID (Linear, GitHub, etc.) should pass it as
    ``external_id``; CSV imports leave it None and the hash falls
    back to (title, body, received_at).
    """
    if external_id:
        canon = f"{source}::{external_id}"
    else:
        canon = f"{source}::{title}::{body}::{received_at or ''}"
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def upsert_item(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    source: str,
    external_id: str | None,
    title: str,
    body: str = "",
    author: str | None = None,
    author_email: str | None = None,
    received_at: str | None = None,
    type_hint: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Insert or no-op-update a feedback item.

    Returns ``(item_id, inserted)``. ``inserted`` is True when this
    call created a new row, False when the row already existed
    (same workspace_id + content_hash).

    The "no-op-update" path leaves the existing row's status alone
    — a re-import of the same content shouldn't reset
    classification work that's already happened.
    """
    title_clean = title.strip()
    if not title_clean:
        raise ValueError("title is required")
    body_clean = body or ""
    chash = content_hash_for(
        source=source,
        external_id=external_id,
        title=title_clean,
        body=body_clean,
        received_at=received_at,
    )
    now = _now(store)
    payload_json = json.dumps(raw_payload, default=str) if raw_payload else None

    with store._connect() as connection:
        # Cheap pre-check: if a row already exists, return its id
        # without performing the write. This avoids the SQLite
        # case where ON CONFLICT DO NOTHING swallows the row but
        # the lastrowid is unreliable for return.
        existing = connection.execute(
            """
            SELECT item_id
            FROM feedback_items
            WHERE workspace_id = ? AND content_hash = ?
            """,
            (workspace_id, chash),
        ).fetchone()
        if existing is not None:
            return (existing[0], False)

        # F5 sync-classify: fill empty hints with the rule-based
        # classifier; preserve partner-supplied values.
        final_hint = type_hint
        if not (final_hint and final_hint.strip()):
            final_hint = classify(title=title_clean, body=body_clean, hint=None)

        item_id = f"fi-{secrets.token_hex(6)}"
        connection.execute(
            """
            INSERT INTO feedback_items (
                item_id, workspace_id, source, external_id,
                content_hash, title, body, author, author_email,
                received_at, ingested_at, type_hint, raw_payload_json,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'classified')
            ON CONFLICT (workspace_id, content_hash) DO NOTHING
            """,
            (
                item_id,
                workspace_id,
                source,
                external_id,
                chash,
                title_clean,
                body_clean,
                author,
                author_email,
                received_at,
                now,
                final_hint,
                payload_json,
            ),
        )
        connection.commit()
    return (item_id, True)


def list_items(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    source: str | None = None,
    status: FeedbackItemStatus | None = None,
    cluster_id: str | None = None,
    archived: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FeedbackItem]:
    """Paginated read for the inbox / connector tile listings.

    ``cluster_id`` filter (W2 F5+ embeddings) lets the cluster
    drawer fetch only the members of one cluster.

    ``archived`` filter drives the inbox New / Archive tabs:
      - True  → cluster_id IS NOT NULL (AI has merged it into an
                issue / sifted through it)
      - False → cluster_id IS NULL (still raw, untouched)
      - None  → no constraint (legacy behaviour).
    """
    sql = (
        "SELECT item_id, workspace_id, source, external_id, content_hash, "
        "title, body, author, author_email, received_at, ingested_at, "
        "type_hint, status, cluster_id FROM feedback_items WHERE workspace_id = ?"
    )
    args: list[Any] = [workspace_id]
    if source is not None:
        sql += " AND source = ?"
        args.append(source)
    if status is not None:
        sql += " AND status = ?"
        args.append(status)
    if cluster_id is not None:
        sql += " AND cluster_id = ?"
        args.append(cluster_id)
    if archived is True:
        sql += " AND cluster_id IS NOT NULL"
    elif archived is False:
        sql += " AND cluster_id IS NULL"
    sql += " ORDER BY ingested_at DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    with store._connect() as connection:
        rows = connection.execute(sql, args).fetchall()
    return [
        FeedbackItem(
            item_id=r[0],
            workspace_id=r[1],
            source=r[2],
            external_id=r[3],
            content_hash=r[4],
            title=r[5],
            body=r[6],
            author=r[7],
            author_email=r[8],
            received_at=r[9],
            ingested_at=r[10],
            type_hint=r[11],
            status=r[12],
            cluster_id=r[13] if len(r) > 13 else None,
        )
        for r in rows
    ]


def count_items(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    source: str | None = None,
) -> FeedbackItemCount:
    """Aggregate counts for the connector tile's "247 items synced" copy."""
    sql_total = (
        "SELECT COUNT(*) FROM feedback_items WHERE workspace_id = ?"
    )
    sql_queued = (
        "SELECT COUNT(*) FROM feedback_items "
        "WHERE workspace_id = ? AND status = 'queued'"
    )
    sql_last = (
        "SELECT MAX(ingested_at) FROM feedback_items "
        "WHERE workspace_id = ?"
    )
    args: list[Any] = [workspace_id]
    if source is not None:
        for_ = " AND source = ?"
        sql_total += for_
        sql_queued += for_
        sql_last += for_
        args.append(source)

    with store._connect() as connection:
        total = int(connection.execute(sql_total, args).fetchone()[0])
        queued = int(connection.execute(sql_queued, args).fetchone()[0])
        last = connection.execute(sql_last, args).fetchone()[0]
    return FeedbackItemCount(
        total=total, queued=queued, last_ingested_at=last
    )


def mark_status(
    store: "PlanningStudioStore",
    *,
    item_id: str,
    workspace_id: str,
    status: FeedbackItemStatus,
) -> bool:
    """Update an item's status. Returns True if a row matched.

    Workspace_id is part of the WHERE clause as a defense-in-depth
    check — even if a stale item_id leaks across workspaces, the
    update is a no-op rather than a silent cross-tenant write.
    """
    with store._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE feedback_items
            SET status = ?
            WHERE item_id = ? AND workspace_id = ?
            """,
            (status, item_id, workspace_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def update_category(
    store: "PlanningStudioStore",
    *,
    item_id: str,
    workspace_id: str,
    category: str,
) -> bool:
    """Manual category override (W2 F6).

    Lets a partner correct the classifier from the inbox UI.
    Workspace-scoped via the WHERE clause (same defense-in-depth
    pattern as ``mark_status``). The classifier never overrides a
    manual edit on subsequent re-imports — content_hash idempotency
    means re-imports return the existing row id, and the existing
    row's ``type_hint`` survives.
    """
    with store._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE feedback_items
            SET type_hint = ?
            WHERE item_id = ? AND workspace_id = ?
            """,
            (category, item_id, workspace_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def bulk_delete_items(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    item_ids: list[str],
) -> int:
    """Delete a batch of feedback_items rows. Workspace-scoped.

    Returns the number of rows actually removed. Item ids that don't
    belong to this workspace (or no longer exist) are silently
    skipped — the workspace_id in the WHERE clause is the
    cross-tenant guard.
    """
    if not item_ids:
        return 0
    placeholders = ",".join("?" * len(item_ids))
    with store._connect() as connection:
        cursor = connection.execute(
            f"""
            DELETE FROM feedback_items
            WHERE workspace_id = ?
              AND item_id IN ({placeholders})
            """,
            (workspace_id, *item_ids),
        )
        connection.commit()
        return cursor.rowcount or 0


def get_item(
    store: "PlanningStudioStore",
    *,
    item_id: str,
    workspace_id: str,
) -> FeedbackItem | None:
    """Fetch a single item by id. Workspace-scoped — returns None
    when the item belongs to a different workspace (vs. raising)
    so callers can convert to a 404."""
    with store._connect() as connection:
        row = connection.execute(
            """
            SELECT item_id, workspace_id, source, external_id, content_hash,
                   title, body, author, author_email, received_at, ingested_at,
                   type_hint, status, cluster_id
            FROM feedback_items
            WHERE item_id = ? AND workspace_id = ?
            """,
            (item_id, workspace_id),
        ).fetchone()
    if row is None:
        return None
    return FeedbackItem(
        item_id=row[0],
        workspace_id=row[1],
        source=row[2],
        external_id=row[3],
        content_hash=row[4],
        title=row[5],
        body=row[6],
        author=row[7],
        author_email=row[8],
        received_at=row[9],
        ingested_at=row[10],
        type_hint=row[11],
        status=row[12],
        cluster_id=row[13] if len(row) > 13 else None,
    )
