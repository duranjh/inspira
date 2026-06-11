"""LLM-driven cluster merge pass.

Embeddings catch paraphrases ("login broken" / "can't sign in"). What
they miss are different surface descriptions of the same root issue
("page crashed" / "white screen" / "doesn't load") — the words don't
overlap but the underlying bug is identical.

This module asks GPT to read cluster summaries and merge clusters
that describe the same root issue. Output is fed back through the
``feedback_items`` table by repointing ``cluster_id`` and deleting the
now-empty clusters.

Cost / safety
-------------

- One GPT call per workspace, batched over up to ~80 clusters at a
  time (typical small-team workspace).
- Falls back to no-op (returns 0 merged) on any error so a flaky
  OpenAI call never blocks an import.
- Idempotent: running it twice on the same workspace produces no
  change once the first pass converges.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


logger = logging.getLogger(__name__)


DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEOUT_S = 30.0
MAX_CLUSTERS_PER_CALL = 80
MAX_SAMPLE_TITLES = 3


def _system_prompt() -> str:
    return (
        "You are a triage assistant for a customer-feedback inbox. You "
        "look at clusters of feedback items and decide which clusters "
        "describe the SAME ROOT ISSUE — even if the surface words "
        "differ.\n"
        "\n"
        "EXAMPLES of clusters that describe the same root issue:\n"
        "- 'page crashed' / 'white screen on load' / 'page doesn't "
        "load' — same bug, different symptoms.\n"
        "- 'export to PDF is broken' / 'can't download as PDF' / "
        "'PDF export fails' — same feature, different phrasings.\n"
        "- 'add dark mode' / 'support night mode' / 'theme toggle' — "
        "same feature request.\n"
        "\n"
        "DO NOT merge clusters that share a topic but describe "
        "different concrete issues. 'Login is slow' and 'login fails "
        "on Safari' are RELATED but DISTINCT issues — keep them "
        "separate.\n"
        "\n"
        "Output strict JSON with this shape:\n"
        '  {"merges": [{"primary": "cl-...", "absorb": ["cl-...", '
        '"cl-..."]}]}\n'
        "Each entry says 'cluster X absorbs clusters Y and Z'. Omit "
        "clusters that have no merge partner. Use the cluster_id "
        "values from the input verbatim."
    )


def _user_prompt(clusters: list[dict[str, Any]]) -> str:
    lines = [
        "Here are the clusters in this workspace. Each line is a "
        "cluster_id followed by up to "
        f"{MAX_SAMPLE_TITLES} representative item titles. Decide which "
        "clusters describe the same root issue.",
        "",
    ]
    for c in clusters:
        sample = " | ".join(c.get("sample_titles", [])[:MAX_SAMPLE_TITLES])
        lines.append(f"{c['cluster_id']}: {sample}")
    return "\n".join(lines)


def _load_clusters_with_samples(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Pull every cluster + a few representative item titles. The
    sample drives the LLM judgment without ballooning tokens."""
    with store._connect() as connection:
        rows = connection.execute(
            """
            SELECT cluster_id, theme, item_count
            FROM feedback_clusters
            WHERE workspace_id = ?
            ORDER BY item_count DESC, updated_at DESC
            """,
            (workspace_id,),
        ).fetchall()
        clusters: list[dict[str, Any]] = []
        for cid, theme, item_count in rows:
            samples = connection.execute(
                """
                SELECT title
                FROM feedback_items
                WHERE workspace_id = ? AND cluster_id = ?
                ORDER BY ingested_at DESC
                LIMIT ?
                """,
                (workspace_id, cid, MAX_SAMPLE_TITLES),
            ).fetchall()
            clusters.append({
                "cluster_id": cid,
                "theme": theme,
                "item_count": int(item_count),
                "sample_titles": [s[0] for s in samples if s[0]],
            })
    return clusters


def _parse_merges(
    text: str,
    valid_ids: set[str],
) -> list[tuple[str, list[str]]]:
    """Parse the GPT response into ``[(primary, [absorb_ids])]`` list.

    Drops entries that reference cluster_ids the model invented (not in
    valid_ids) so a hallucinated id can't repoint real items into a
    nonexistent cluster.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.info("llm_merge: invalid JSON in response — skipping")
        return []
    raw = data.get("merges") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, list[str]]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        primary = entry.get("primary")
        absorb = entry.get("absorb")
        if not isinstance(primary, str) or primary not in valid_ids:
            continue
        if not isinstance(absorb, list):
            continue
        valid_absorb = [
            a for a in absorb
            if isinstance(a, str) and a in valid_ids and a != primary
        ]
        if valid_absorb:
            out.append((primary, valid_absorb))
    return out


def _apply_merges(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    merges: list[tuple[str, list[str]]],
) -> int:
    """Repoint feedback_items.cluster_id from absorbed clusters to
    their primary, bump the primary's item_count, and delete the
    now-empty absorbed cluster rows. Returns the number of clusters
    that got absorbed (i.e., went away)."""
    if not merges:
        return 0
    absorbed_total = 0
    with store._connect() as connection:
        for primary, absorb_ids in merges:
            placeholders = ",".join("?" * len(absorb_ids))
            connection.execute(
                f"""
                UPDATE feedback_items
                SET cluster_id = ?
                WHERE workspace_id = ?
                  AND cluster_id IN ({placeholders})
                """,
                (primary, workspace_id, *absorb_ids),
            )
            connection.execute(
                f"""
                UPDATE feedback_clusters
                SET item_count = (
                    SELECT COUNT(*) FROM feedback_items
                    WHERE workspace_id = ? AND cluster_id = ?
                )
                WHERE cluster_id = ? AND workspace_id = ?
                """,
                (workspace_id, primary, primary, workspace_id),
            )
            connection.execute(
                f"""
                DELETE FROM feedback_clusters
                WHERE workspace_id = ?
                  AND cluster_id IN ({placeholders})
                """,
                (workspace_id, *absorb_ids),
            )
            absorbed_total += len(absorb_ids)
        connection.commit()
    return absorbed_total


def merge_clusters_via_llm(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, int]:
    """Run an LLM-grade merge pass across the workspace's clusters.

    Returns ``{"clusters_before": N, "clusters_absorbed": M,
    "clusters_after": N - M}``.

    Best-effort: any failure (no clusters, OpenAI down, malformed
    response) returns zero-merge stats without raising.
    """
    clusters = _load_clusters_with_samples(
        store, workspace_id=workspace_id
    )
    before = len(clusters)
    if before < 2:
        return {
            "clusters_before": before,
            "clusters_absorbed": 0,
            "clusters_after": before,
        }

    if client is None:
        try:
            from openai import OpenAI  # noqa: PLC0415
            client = OpenAI(max_retries=0)
        except Exception:  # noqa: BLE001
            logger.info("llm_merge: openai SDK unavailable; skipping")
            return {
                "clusters_before": before,
                "clusters_absorbed": 0,
                "clusters_after": before,
            }

    # Cap the batch so we don't blow out context. For very large
    # workspaces (>80 clusters) we'd want a multi-pass fan-out; not
    # needed yet — the cap covers typical partner-team scale.
    batch = clusters[:MAX_CLUSTERS_PER_CALL]
    valid_ids = {c["cluster_id"] for c in batch}

    try:
        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=2048,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(batch)},
            ],
            response_format={"type": "json_object"},
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("llm_merge: API call failed (%s); skipping", exc)
        return {
            "clusters_before": before,
            "clusters_absorbed": 0,
            "clusters_after": before,
        }

    try:
        text = response.choices[0].message.content or ""
    except Exception:  # noqa: BLE001
        return {
            "clusters_before": before,
            "clusters_absorbed": 0,
            "clusters_after": before,
        }

    merges = _parse_merges(text, valid_ids)
    absorbed = _apply_merges(
        store, workspace_id=workspace_id, merges=merges,
    )
    logger.info(
        "llm_merge: workspace=%s before=%d absorbed=%d after=%d",
        workspace_id, before, absorbed, before - absorbed,
    )
    return {
        "clusters_before": before,
        "clusters_absorbed": absorbed,
        "clusters_after": before - absorbed,
    }
