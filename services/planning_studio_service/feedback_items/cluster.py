"""Embedding-based cluster assignment + persistence (W2 F5+).

For each item with a fresh embedding, find the nearest existing
cluster within the workspace and join it (cosine ≥ threshold), or
create a new single-item cluster. The cluster's ``centroid_json``
is the running average of member embeddings; on each new
member the centroid shifts toward the new item.

Threshold: ``DEFAULT_SIMILARITY_THRESHOLD = 0.65``. Lowered from
0.78 (founder direction 2026-05-04) so semantic paraphrases of the
same root issue ("login broken" / "can't sign in" / "auth fails")
collapse into one cluster instead of three. Embeddings catch
paraphrases at 0.65–0.75; tighter thresholds shatter what should
be one issue into many. The follow-up LLM merge pass
(``merge_clusters_via_llm``) handles cases that don't fall under
embedding-similarity at all (e.g., "page crashed" vs "white
screen" — same bug, different surface words).

Workspace-scoped throughout. The store helpers all take
``workspace_id`` as a keyword and apply it to every WHERE clause.
No cross-tenant cluster joining.

Cost
----

Cluster assignment is O(N_existing_clusters) per item, with each
comparison being a 1536-dim dot product (≈ 1 ms in pure Python,
faster with numpy). For the partner-demo target of ~50 clusters
× 200 items per import = 10K dot products ≈ 0.01s. No further
optimisation needed until partners have 500+ clusters in a
single workspace; at that point switch to an ANN index (e.g.,
hnsw or pgvector).
"""
from __future__ import annotations

import json
import logging
import math
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..agents.tiers import ModelTier, kanban_card_cap_for_tier
from .embedding import EMBEDDING_DIMS

if TYPE_CHECKING:
    from ..store import PlanningStudioStore

logger = logging.getLogger(__name__)


DEFAULT_SIMILARITY_THRESHOLD = 0.65


# ----------------------------------------------------------------------
# Ranking weights (#172 — per-tier Kanban auto-promote cap).
# Score for a cluster = item_count * recency_weight * severity_weight.
# Weights mirror the v4 partner-journey priority order: bugs come first,
# then complaints, then features, then everything else.
# ----------------------------------------------------------------------

_SEVERITY_WEIGHTS: dict[str, float] = {
    "bug": 1.0,
    "complaint": 0.7,
    "feature": 0.5,
}


def _recency_weight(
    received_at_iso: str | None,
    *,
    now: datetime | None = None,
) -> float:
    """Recency multiplier for the cluster-ranking score (#172).

    1.0 within 7d, 0.7 within 30d, 0.5 within 90d, 0.3 otherwise (or on
    missing/unparseable timestamp). ``now`` is injectable for tests so
    we don't need to monkeypatch ``datetime.now``.
    """
    if not received_at_iso:
        return 0.3
    try:
        ts = datetime.fromisoformat(received_at_iso)
    except ValueError:
        return 0.3
    current = now or datetime.now(timezone.utc)
    # Tolerate naive timestamps (legacy rows) by treating them as UTC.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = (current - ts).total_seconds() / 86_400
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.7
    if age_days <= 90:
        return 0.5
    return 0.3


def _severity_weight(category: str | None) -> float:
    """Severity multiplier for the cluster-ranking score (#172).

    Categories come from ``feedback_items.type_hint`` (see
    ``feedback_items.classify.ALLOWED_HINT_VALUES``). Anything outside
    {bug, complaint, feature} (praise / question / noise / general /
    NULL) collapses to 0.3.
    """
    if not category:
        return 0.3
    return _SEVERITY_WEIGHTS.get(category.lower(), 0.3)


# ----------------------------------------------------------------------
# Title-normalisation fallback (no embeddings required).
# ----------------------------------------------------------------------

import re as _re


_STOPWORDS = {
    "the", "a", "an", "is", "are", "to", "of", "for", "and", "or",
    "in", "on", "at", "with", "as", "by", "from", "this", "that",
    "i", "we", "our", "you", "my",
}


def normalize_title(title: str) -> str:
    """Normalise a feedback title for cheap duplicate detection.

    Lowercases, strips punctuation, collapses whitespace, drops
    common English stopwords, and sorts the remaining tokens. The
    sort makes "can't undo after sorting" and "after sorting can't
    undo" hash to the same key. Returned as a single space-joined
    string; an empty string indicates "no signal" so callers should
    NOT cluster on it.

    This is intentionally low-precision — the point is to get from
    "200 individual cards" to "20 grouped issues" without an OpenAI
    key. Embeddings (when enabled) re-cluster more precisely.
    """
    if not title:
        return ""
    lower = title.lower()
    stripped = _re.sub(r"[^a-z0-9\s]", " ", lower)
    tokens = [
        t for t in stripped.split()
        if t and t not in _STOPWORDS and len(t) > 1
    ]
    if not tokens:
        return ""
    tokens.sort()
    return " ".join(tokens)


def assign_or_create_title_cluster(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    item_id: str,
    title: str,
) -> tuple[str, bool] | None:
    """Title-only fallback when embeddings aren't available.

    Looks for an existing item in the workspace whose title
    normalises to the same key. If found and that item already
    belongs to a cluster, joins it. If found but the existing item
    has no cluster, mints a fresh cluster and assigns BOTH items
    to it. If no match, returns ``None`` (caller leaves the item
    un-clustered).

    Returns ``(cluster_id, was_new_cluster)`` or ``None``.
    """
    norm = normalize_title(title)
    if not norm:
        return None
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT item_id, title, cluster_id
            FROM feedback_items
            WHERE workspace_id = ?
              AND item_id != ?
            """,
            (workspace_id, item_id),
        ).fetchall()
        match: tuple[str, str, str | None] | None = None
        for r in rows:
            if normalize_title(r[1] or "") == norm:
                match = (r[0], r[1] or "", r[2])
                break
        if match is None:
            return None
        other_item_id, _other_title, other_cluster_id = match
        now = _now(store)
        if other_cluster_id:
            connection.execute(
                """
                UPDATE feedback_items
                SET cluster_id = ?
                WHERE item_id = ? AND workspace_id = ?
                """,
                (other_cluster_id, item_id, workspace_id),
            )
            connection.execute(
                """
                UPDATE feedback_clusters
                SET item_count = item_count + 1, updated_at = ?
                WHERE cluster_id = ? AND workspace_id = ?
                """,
                (now, other_cluster_id, workspace_id),
            )
            connection.commit()
            return (other_cluster_id, False)
        cluster_id = f"cl-{secrets.token_hex(6)}"
        connection.execute(
            """
            INSERT INTO feedback_clusters (
                cluster_id, workspace_id, centroid_json, theme,
                item_count, created_at, updated_at
            )
            VALUES (?, ?, ?, NULL, 2, ?, ?)
            """,
            (cluster_id, workspace_id, "[]", now, now),
        )
        connection.execute(
            """
            UPDATE feedback_items
            SET cluster_id = ?
            WHERE item_id IN (?, ?) AND workspace_id = ?
            """,
            (cluster_id, item_id, other_item_id, workspace_id),
        )
        connection.commit()
        return (cluster_id, True)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Returns 0.0 for length-mismatched or zero-norm inputs (so a
    new cluster gets created instead of a math error).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _now(store: "PlanningStudioStore") -> str:
    from ..store import now_timestamp

    return now_timestamp()


def _running_average(
    old_centroid: list[float],
    old_count: int,
    new_vector: list[float],
) -> list[float]:
    """Update the centroid as a true running average: c' = c + (v - c)/(n+1)."""
    new_count = old_count + 1
    out = [
        old + (new - old) / new_count
        for old, new in zip(old_centroid, new_vector)
    ]
    return out


def list_clusters_for_workspace(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Read all clusters in a workspace (with their centroids)."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT cluster_id, workspace_id, centroid_json, theme,
                   item_count, created_at, updated_at
            FROM feedback_clusters
            WHERE workspace_id = ?
            """,
            (workspace_id,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            centroid = json.loads(r[2])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(centroid, list) or len(centroid) != EMBEDDING_DIMS:
            continue
        out.append(
            {
                "cluster_id": r[0],
                "workspace_id": r[1],
                "centroid": centroid,
                "theme": r[3],
                "item_count": int(r[4]),
                "created_at": r[5],
                "updated_at": r[6],
            }
        )
    return out


def list_clusters_with_distribution(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Cluster summary plus per-category histogram + recency for F6 scoring.

    The W3 prioritization agent (``agents/prioritization.py``) ranks
    clusters by ROI. Useful inputs are: cluster size, the spread across
    feedback categories (e.g., a 30-item cluster that's 25 bugs is a
    different signal than 30 mixed items), and how recently the cluster
    has been growing. This helper joins ``feedback_clusters`` →
    ``feedback_items`` and returns those rolled-up stats.

    Returned shape per cluster::

        {
            "cluster_id": str,
            "theme": str | None,
            "item_count": int,
            "category_counts": {
                "bug": int, "feature": int, "complaint": int,
                "praise": int, "question": int, "noise": int,
            },
            "most_recent_ingested_at": str | None,
            "sample_item_ids": list[str],   # up to 5, newest first
        }

    Workspace-scoped — cross-tenant joins are impossible by query
    construction.
    """
    out: list[dict[str, Any]] = []
    with store._connect() as connection:
        cluster_rows = connection.execute(
            """
            SELECT cluster_id, theme, item_count, created_at, updated_at
            FROM feedback_clusters
            WHERE workspace_id = ?
            ORDER BY updated_at DESC
            """,
            (workspace_id,),
        ).fetchall()
        # Use named row access — destructuring like
        # ``for cid, theme, cnt, ... in cluster_rows`` works on
        # sqlite (Row supports tuple iteration) but on Postgres
        # iterates over the dict KEYS, so ``item_count`` would bind
        # to the literal string "item_count" and ``int(item_count)``
        # raises ValueError.
        for row in cluster_rows:
            cluster_id = row["cluster_id"]
            theme = row["theme"]
            item_count = row["item_count"]
            histogram_rows = connection.execute(
                """
                SELECT COALESCE(type_hint, 'noise') AS category,
                       COUNT(*) AS n
                FROM feedback_items
                WHERE workspace_id = ? AND cluster_id = ?
                GROUP BY COALESCE(type_hint, 'noise')
                """,
                (workspace_id, cluster_id),
            ).fetchall()
            histogram = {
                "bug": 0, "feature": 0, "complaint": 0,
                "praise": 0, "question": 0, "noise": 0,
            }
            for hr in histogram_rows:
                # See note above on why we don't tuple-destructure
                # _PgRow: positional unpacking iterates dict keys on
                # Postgres. The COUNT alias is "n".
                category = hr["category"]
                count = hr["n"]
                if category in histogram:
                    histogram[category] = int(count)
                else:
                    # Unknown category lands in 'noise' — better than
                    # dropping the count silently.
                    histogram["noise"] += int(count)
            recency_row = connection.execute(
                """
                SELECT MAX(ingested_at)
                FROM feedback_items
                WHERE workspace_id = ? AND cluster_id = ?
                """,
                (workspace_id, cluster_id),
            ).fetchone()
            most_recent = recency_row[0] if recency_row else None
            sample_rows = connection.execute(
                """
                SELECT item_id
                FROM feedback_items
                WHERE workspace_id = ? AND cluster_id = ?
                ORDER BY ingested_at DESC
                LIMIT 5
                """,
                (workspace_id, cluster_id),
            ).fetchall()
            out.append(
                {
                    "cluster_id": cluster_id,
                    "theme": theme,
                    "item_count": int(item_count),
                    "category_counts": histogram,
                    "most_recent_ingested_at": most_recent,
                    "sample_item_ids": [r[0] for r in sample_rows],
                }
            )
    return out


def list_clusters_for_inbox(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Cluster summary list for the inbox. Excludes the centroid
    (clients don't need 1536 floats per row)."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT cluster_id, theme, item_count, created_at, updated_at
            FROM feedback_clusters
            WHERE workspace_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    return [
        {
            "cluster_id": r[0],
            "theme": r[1],
            "item_count": int(r[2]),
            "created_at": r[3],
            "updated_at": r[4],
        }
        for r in rows
    ]


def assign_or_create_cluster(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    item_id: str,
    embedding: list[float],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> tuple[str, bool]:
    """Find the nearest existing cluster; join if cos ≥ threshold,
    otherwise create a new cluster.

    Updates the item's ``cluster_id`` and ``embedding_json``.
    Returns ``(cluster_id, was_new_cluster)``.
    """
    if len(embedding) != EMBEDDING_DIMS:
        raise ValueError(
            f"embedding length {len(embedding)} != {EMBEDDING_DIMS}"
        )

    clusters = list_clusters_for_workspace(
        store, workspace_id=workspace_id
    )
    best_cluster: dict[str, Any] | None = None
    best_sim = 0.0
    for c in clusters:
        sim = cosine_similarity(c["centroid"], embedding)
        if sim > best_sim:
            best_sim = sim
            best_cluster = c

    if best_cluster is not None and best_sim >= threshold:
        # Update existing cluster.
        new_centroid = _running_average(
            best_cluster["centroid"],
            best_cluster["item_count"],
            embedding,
        )
        now = _now(store)
        with store._connect() as connection:
            connection.execute(
                """
                UPDATE feedback_clusters
                SET centroid_json = ?,
                    item_count = item_count + 1,
                    updated_at = ?
                WHERE cluster_id = ? AND workspace_id = ?
                """,
                (
                    json.dumps(new_centroid),
                    now,
                    best_cluster["cluster_id"],
                    workspace_id,
                ),
            )
            connection.execute(
                """
                UPDATE feedback_items
                SET cluster_id = ?, embedding_json = ?
                WHERE item_id = ? AND workspace_id = ?
                """,
                (
                    best_cluster["cluster_id"],
                    json.dumps(embedding),
                    item_id,
                    workspace_id,
                ),
            )
            connection.commit()
        return (best_cluster["cluster_id"], False)

    # Create a new single-item cluster.
    cluster_id = f"cl-{secrets.token_hex(6)}"
    now = _now(store)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO feedback_clusters (
                cluster_id, workspace_id, centroid_json, theme,
                item_count, created_at, updated_at
            )
            VALUES (?, ?, ?, NULL, 1, ?, ?)
            """,
            (cluster_id, workspace_id, json.dumps(embedding), now, now),
        )
        connection.execute(
            """
            UPDATE feedback_items
            SET cluster_id = ?, embedding_json = ?
            WHERE item_id = ? AND workspace_id = ?
            """,
            (
                cluster_id,
                json.dumps(embedding),
                item_id,
                workspace_id,
            ),
        )
        connection.commit()
    return (cluster_id, True)


def ensure_v2_projects_for_clusters(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    user_id: str,
    cluster_ids: set[str],
    plan_tier: ModelTier,
    cap: int | None = None,
) -> tuple[int, int]:
    """Auto-promote clusters into v2_projects rows so they show up on the
    workspace Kanban as Drafts immediately after a CSV import.

    Founder direction (2026-05-04): the YC pitch promises "auto-canvas-
    per-theme" — manual Promote-to-Project as the only path didn't match
    the language. This helper creates one v2_projects row per cluster
    that doesn't already have one, with:
      - state=pending_review (= "Draft" in the v4 ApprovalChip UX)
      - title sourced from a representative item's title
      - metadata_json={cluster_id, auto_promoted: true}

    Tier-capped (#172 — 2026-05-12). To prevent the Kanban auto-spawn
    storm we hit on 2026-05-05 (179 cards from one 200-row CSV →
    Enterprise concurrent-subagent cap 100 = crash), at most
    ``KANBAN_CARD_CAPS_BY_PLAN[plan_tier]`` clusters auto-promote per
    call. Candidates that don't make the cut stay in the Inbox archive —
    visible in the feedback-cluster list, manually promotable from
    there. Re-imports re-rank the still-deferred clusters and can
    promote additional ones over time.

    Ranking score = ``item_count × recency_weight × severity_weight``.
    See ``_recency_weight`` / ``_severity_weight`` for the weight tables.
    Higher = more urgent → more likely to promote.

    Args:
        plan_tier: workspace owner's plan tier (mapped via
            ``tiers.kanban_tier_for_plan``). Used to derive the cap when
            ``cap`` is not supplied.
        cap: explicit override, mainly for tests. ``None`` (the normal
            case) falls back to ``kanban_card_cap_for_tier(plan_tier)``.

    Returns:
        ``(promoted, deferred)``. ``promoted`` is the count of NEW
        v2_projects rows created. ``deferred`` is the count of new
        candidate clusters that ranked below the cap and were NOT
        promoted (they stay in Inbox archive). Clusters that already had
        a v2_project on entry are NOT counted in either bucket —
        idempotent re-runs are zero on both numbers.

    No orchestrator runs are spawned — that would burn $1-2 per cluster
    in LLM cost and on a 200-row import means 50+ runs. Canvases stay
    empty until the user opens a project and triggers the orchestrator
    explicitly. The Kanban populates regardless, which is what the
    founder needs for the demo flow.
    """
    if not cluster_ids:
        return (0, 0)
    from ..store import now_timestamp

    is_postgres = getattr(store, "_is_postgres", False)
    placeholder = "%s" if is_postgres else "?"
    cluster_id_extract = (
        "metadata_json::jsonb->>'cluster_id'"
        if is_postgres
        else "json_extract(metadata_json, '$.cluster_id')"
    )

    effective_cap = (
        kanban_card_cap_for_tier(plan_tier) if cap is None else cap
    )

    # ---- Pass 1: filter out already-promoted clusters, gather ranking
    # inputs for the remaining candidates. Keeping the queries per-cluster
    # (instead of one bulk SQL) preserves the dialect-aware ``cluster_id``
    # lookup pattern used everywhere else in this file. Caps are O(10-200),
    # this is not a hot path.
    candidates: list[dict[str, Any]] = []
    now = now_timestamp()
    with store._connect() as connection:
        for cid in cluster_ids:
            existing = connection.execute(
                f"""
                SELECT project_id FROM v2_projects
                WHERE workspace_id = {placeholder}
                  AND {cluster_id_extract} = {placeholder}
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (workspace_id, cid),
            ).fetchone()
            if existing:
                # Idempotent: already promoted — not a candidate, not deferred.
                continue

            rep = connection.execute(
                f"""
                SELECT title FROM feedback_items
                WHERE cluster_id = {placeholder}
                  AND workspace_id = {placeholder}
                ORDER BY received_at {"NULLS LAST" if is_postgres else "IS NULL"},
                         received_at
                LIMIT 1
                """,
                (cid, workspace_id),
            ).fetchone()
            if not rep:
                # Cluster row exists but has no items — shouldn't happen
                # in practice, but skip rather than create a titleless
                # project. Not counted as deferred (no real cluster to defer).
                continue
            rep_title = rep["title"] if isinstance(rep, dict) else rep[0]
            title = (rep_title or f"Cluster {cid[-6:]}").strip()[:200]

            # Dominant category — drives the severity weight AND the
            # Kanban card's BUG/FEATURE/etc chip. Falls back to "general"
            # when F4/F5 hasn't tagged any items in this cluster yet.
            cat_rows = connection.execute(
                f"""
                SELECT COALESCE(NULLIF(LOWER(type_hint), ''), 'general') AS cat,
                       COUNT(*) AS n
                FROM feedback_items
                WHERE cluster_id = {placeholder}
                  AND workspace_id = {placeholder}
                GROUP BY 1
                ORDER BY n DESC
                LIMIT 1
                """,
                (cid, workspace_id),
            ).fetchone()
            if cat_rows:
                dominant_category = (
                    cat_rows["cat"] if isinstance(cat_rows, dict)
                    else cat_rows[0]
                ) or "general"
            else:
                dominant_category = "general"

            # Item count + most-recent received_at — one query, two aggregates.
            agg_row = connection.execute(
                f"""
                SELECT COUNT(*) AS n, MAX(received_at) AS max_received
                FROM feedback_items
                WHERE cluster_id = {placeholder}
                  AND workspace_id = {placeholder}
                """,
                (cid, workspace_id),
            ).fetchone()
            if agg_row is None:
                item_count = 1
                max_received_at = None
            elif isinstance(agg_row, dict):
                item_count = int(agg_row.get("n") or 1)
                max_received_at = agg_row.get("max_received")
            else:
                item_count = int(agg_row[0] or 1)
                max_received_at = agg_row[1]

            score = (
                item_count
                * _recency_weight(max_received_at)
                * _severity_weight(dominant_category)
            )
            candidates.append(
                {
                    "cluster_id": cid,
                    "title": title,
                    "dominant_category": dominant_category,
                    "item_count": item_count,
                    "score": score,
                },
            )

        # ---- Pass 2: rank candidates, promote only the top-N up to the cap.
        # Sort key: primary = score (desc); secondary = cluster_id (asc) so
        # the test suite gets deterministic tie-breaks. Secondary tie-break
        # in production rarely matters — scores typically differ — but the
        # ranking test mixes equal-score features and needs a stable order.
        candidates.sort(key=lambda c: (-c["score"], c["cluster_id"]))
        to_promote = candidates[:effective_cap]
        deferred = len(candidates) - len(to_promote)

        promoted = 0
        for cand in to_promote:
            project_id = f"project-{secrets.token_hex(6)}"
            metadata = json.dumps(
                {
                    "cluster_id": cand["cluster_id"],
                    "auto_promoted": True,
                    "dominant_category": cand["dominant_category"],
                    "feedback_count": int(cand["item_count"] or 1),
                },
            )
            connection.execute(
                f"""
                INSERT INTO v2_projects
                  (project_id, user_id, workspace_id, title, metadata_json,
                   created_at, updated_at, project_state)
                VALUES ({placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder})
                """,
                (
                    project_id,
                    user_id,
                    workspace_id,
                    cand["title"],
                    metadata,
                    now,
                    now,
                    "pending_review",
                ),
            )
            promoted += 1
        connection.commit()
    return (promoted, deferred)
