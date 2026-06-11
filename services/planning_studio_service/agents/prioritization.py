"""F6 — ROI prioritization agent (W3).

Reads a workspace's clustered feedback (from
``feedback_items.cluster.list_clusters_with_distribution``) plus
optional repo metadata, asks the frontier model to rank them by ROI,
and persists the ranking on a ``prioritization_runs`` row.

**Side effect:** when a cluster has no ``theme`` label (the W2 cluster
pipeline doesn't auto-label), this module backfills a short title
into ``feedback_clusters.theme`` from the LLM output. Future readers
should not assume this is a pure scoring function — the theme write
is intentional and lets the inbox show meaningful cluster cards
without a separate W3.5 backfill job.

LLM
---

GPT-5-mini via OpenAI (gpt-5 was tested live but exceeds 30s for
even 15 clusters; gpt-5-mini delivers coherent rationales in
~24s for the same workspace size). Provider rule: non-code-gen
LLM features always use OpenAI regardless of tier.
Falls back to a deterministic heuristic ranker when:

- ``OPENAI_API_KEY`` is missing (dev / test path), OR
- the LLM call errors / times out / returns malformed JSON.

The fallback ranking exists so the surface still produces a
meaningful prioritization when API access is briefly unavailable;
the heuristic is documented inline.

Output schema
-------------

The output JSON has shape::

    {
        "themes": [
            {
                "cluster_id": str,
                "rank": int,           # 1-based, no ties
                "score": float,        # 0.0 - 100.0
                "rationale": str,      # 1-2 sentences
                "suggested_theme_label": str,  # short, used to backfill cluster.theme
                "provenance": {
                    "item_count": int,
                    "category_counts": {bug, feature, complaint, praise, question, noise},
                    "most_recent_ingested_at": str | None,
                    "sample_item_ids": [str],
                },
            },
            ...
        ],
        "model": str,           # "gpt-5-mini" | "heuristic-fallback"
        "input_cluster_count": int,
    }
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from ..feedback_items.cluster import list_clusters_with_distribution
from ..orchestrator_store import (
    complete_prioritization_run,
    create_prioritization_run,
)

if TYPE_CHECKING:
    from ..store import PlanningStudioStore

logger = logging.getLogger(__name__)


PRIORITIZATION_MODEL = "gpt-5-mini"
# 90s gives headroom for the typical workspace (10-30 clusters at
# ~24s for 15 clusters on gpt-5-mini). Workspaces with 100+ clusters
# may still hit the heuristic fallback — see issue #119 for the
# pre-filter scaling fix that addresses very large workspaces.
DEFAULT_TIMEOUT_S = 90.0

SYSTEM_PROMPT = """You are an ROI-vs-effort prioritization analyst for a B2B product team.

You receive a list of CLUSTERS of similar customer feedback items. Each cluster has:
- a size (item_count): how many feedback items joined this cluster
- a category_counts breakdown (bug, feature, complaint, praise, question, noise)
- a most_recent_ingested_at timestamp (or null) showing recency
- an optional theme label

Your job is to rank these clusters from HIGHEST to LOWEST priority for the
product team to address. Priority is BENEFIT divided by EFFORT — partners want
the highest-impact, lowest-cost work pulled to the top of the queue.

BENEFIT heuristics (in priority order):

1. Bugs and complaints with high item_count drive higher benefit than scattered
   questions or feature wishes — broken things lose customers.
2. Recency matters: a cluster receiving items in the last 7 days is hotter than
   one whose newest item is 90+ days old.
3. Praise clusters are LOW benefit for action (they validate, not signal work).
4. Question clusters are MEDIUM-LOW benefit unless their item_count is very high
   (then they're a docs/onboarding gap).
5. Mixed clusters (broadly distributed) are usually less actionable than focused
   ones (one category dominant) — focus signals a clear problem.

EFFORT heuristics — estimate both:

- effort_loc_estimate: rough lines of code Inspira would touch to ship the fix
  (small bug = 10–50, medium feature = 100–400, large rewrite = 800+).
- effort_hours_estimate: rough engineer-hours including review and tests
  (small = 1–4h, medium = 8–20h, large = 40h+).
- A "missing dark mode toggle" is small. "Rewrite the search engine" is large.
- When unsure, lean SMALL — Inspira's scaffold-and-iterate loop favors quick
  wins. Cap your estimates at 1500 LOC and 80h.

PRIORITY formula:

  priority = benefit / max(1, effort_loc_estimate/100)

A 30-LOC bug fix that 200 partners hit beats a 1200-LOC feature that 8
partners want. Surface the quick wins first. Spread your final 0-100 scores
so ranks are visually distinct.

For each cluster, return:
- rank (1-based, all distinct, 1 = highest priority)
- score 0-100 (higher = more important — already factors in effort)
- effort_loc_estimate (integer LOC)
- effort_hours_estimate (integer hours)
- rationale: ONE OR TWO sentences explaining the rank in product terms,
  including the ROI-vs-effort tradeoff you saw.
- suggested_theme_label: a short 2-5 word theme title (e.g., "Crashes on iOS startup",
  "Slack export missing"). If the cluster already has a good theme, reuse it.

Output STRICT JSON matching the provided schema. No prose before or after.
"""


def _build_user_prompt(clusters: list[dict[str, Any]]) -> str:
    """Render the per-run prompt body. The system prompt above is fixed."""
    lines = [
        "Rank these clusters by ROI. Output JSON only.",
        "",
        "CLUSTERS:",
    ]
    for c in clusters:
        cats = c["category_counts"]
        cat_str = ", ".join(
            f"{k}={v}" for k, v in cats.items() if v > 0
        ) or "none"
        theme = c.get("theme") or "(no theme yet)"
        recent = c.get("most_recent_ingested_at") or "(unknown)"
        lines.append(
            f'- cluster_id="{c["cluster_id"]}" theme="{theme}" '
            f"item_count={c['item_count']} categories=[{cat_str}] "
            f'most_recent={recent}'
        )
    lines.append("")
    lines.append(
        'Output: {"themes": [{"cluster_id":..., "rank":..., '
        '"score":..., "effort_loc_estimate":..., '
        '"effort_hours_estimate":..., "rationale":..., '
        '"suggested_theme_label":...}, ...]}'
    )
    return "\n".join(lines)


def _is_openai_available() -> bool:
    """Env gate: real OpenAI API key present.

    This module never prevents F6 from running — when False, we
    fall back to the heuristic ranker. The caller doesn't need to
    branch on this; ``rank_clusters`` does.
    """
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _heuristic_rank(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic fallback: cluster ROI = weighted category score.

    Weights chosen to roughly match the LLM rubric:
    - bug × 3.0
    - complaint × 2.0
    - feature × 1.5
    - question × 0.7
    - noise × 0.0
    - praise × 0.0

    Plus a small recency bonus for clusters with a recent
    ``most_recent_ingested_at`` — but kept small because the
    fallback only fires when the LLM is unavailable, and we don't
    want to fake nuance the heuristic doesn't actually have.

    Output is the SAME shape as the LLM ranker (rank, score,
    rationale, suggested_theme_label) so callers don't need to
    branch.
    """
    weights = {
        "bug": 3.0, "complaint": 2.0, "feature": 1.5,
        "question": 0.7, "praise": 0.0, "noise": 0.0,
    }
    # Per-category baseline LOC + hours for the fallback's effort
    # estimate. The LLM path produces sharper estimates; this is the
    # "no-LLM" floor so the FE still gets actionable effort signals.
    effort_baseline = {
        "bug": (40, 3),         # small repro+fix
        "complaint": (60, 4),
        "feature": (220, 12),   # medium feature
        "question": (10, 1),    # docs nudge
        "praise": (0, 0),
        "noise": (0, 0),
    }
    scored: list[tuple[float, dict[str, Any], int, int]] = []
    for c in clusters:
        cats = c["category_counts"]
        weighted_count = sum(weights[k] * cats[k] for k in weights)
        # Benefit on a 0-100 scale (cap at 95).
        benefit = min(95.0, 5.0 + weighted_count * 1.5)
        # Dominant category drives the effort estimate; if mixed,
        # blend by category share. Cap LOC at 1500, hours at 80.
        total_items = max(1, c["item_count"])
        loc = 0.0
        hours = 0.0
        for k, n in cats.items():
            if n <= 0:
                continue
            base_loc, base_hours = effort_baseline[k]
            share = n / total_items
            loc += base_loc * share
            hours += base_hours * share
        loc_int = max(5, min(1500, int(round(loc)) or 30))
        hours_int = max(1, min(80, int(round(hours)) or 2))
        # Final priority = benefit / max(1, effort_loc/100). A 30-LOC
        # cluster with benefit=50 scores ~50; a 600-LOC cluster with
        # benefit=50 scores ~8.3.
        priority = benefit / max(1.0, loc_int / 100.0)
        scored.append((priority, c, loc_int, hours_int))
    # Sort by adjusted priority descending; stable on insertion order
    # for ties.
    scored.sort(key=lambda x: -x[0])
    out: list[dict[str, Any]] = []
    for rank, (priority, c, loc_int, hours_int) in enumerate(scored, start=1):
        cats = c["category_counts"]
        # Pick the dominant category for the rationale string.
        dom_cat, dom_count = max(cats.items(), key=lambda x: x[1])
        if dom_count == 0:
            rationale = (
                f"Heuristic fallback: cluster of {c['item_count']} "
                f"items; no clear category signal. ~{loc_int} LOC / "
                f"{hours_int}h estimated."
            )
        else:
            rationale = (
                f"Heuristic fallback: {dom_count} {dom_cat} signal(s) "
                f"in a cluster of {c['item_count']}. "
                f"~{loc_int} LOC / {hours_int}h estimated."
            )
        out.append(
            {
                "cluster_id": c["cluster_id"],
                "rank": rank,
                "score": round(min(100.0, priority), 1),
                "effort_loc_estimate": loc_int,
                "effort_hours_estimate": hours_int,
                "rationale": rationale,
                "suggested_theme_label": c.get("theme") or f"Cluster {rank}",
            }
        )
    return out


def _llm_rank(clusters: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Call GPT-5-mini to rank clusters. Returns None on any failure.

    The caller (``rank_clusters``) catches the None and falls back to the
    heuristic. We keep this function strictly responsible for the LLM path
    so the fallback site is one place.

    ``max_completion_tokens=16384`` + ``reasoning_effort="low"``: gpt-5
    family models count internal reasoning tokens against the completion
    budget. With the default reasoning effort, 50+ cluster rankings
    consume the full budget on reasoning and emit empty content. Low
    effort + 16K budget keeps output complete (live-verified at 55
    clusters, ~29s, full rationales). See issue #119 for the deeper
    cluster-count scaling fix (pre-filter to top-30 before LLM).
    """
    try:
        from openai import OpenAI  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai SDK unavailable for F6: %s", exc)
        return None
    user_prompt = _build_user_prompt(clusters)
    try:
        client = OpenAI(max_retries=0)
        response = client.chat.completions.create(
            model=PRIORITIZATION_MODEL,
            max_completion_tokens=16384,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            reasoning_effort="low",
            timeout=DEFAULT_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("F6 prioritization LLM call failed: %s", exc)
        return None
    try:
        raw = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("F6 LLM returned unexpected response shape: %s", exc)
        return None
    if not raw:
        logger.warning("F6 LLM returned empty content")
        return None
    # Defense-in-depth: strip Markdown fences. ``response_format=json_object``
    # should never emit them, but cheap to handle if it does.
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("F6 LLM returned non-JSON: %s", exc)
        return None
    themes = parsed.get("themes")
    if not isinstance(themes, list) or not themes:
        logger.warning("F6 LLM JSON missing 'themes' list")
        return None
    # Light validation: every theme entry needs cluster_id + rank.
    valid_cluster_ids = {c["cluster_id"] for c in clusters}
    cleaned: list[dict[str, Any]] = []
    seen_ranks: set[int] = set()
    for entry in themes:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("cluster_id")
        if cid not in valid_cluster_ids:
            continue
        try:
            rank = int(entry.get("rank") or 0)
        except (TypeError, ValueError):
            continue
        if rank <= 0 or rank in seen_ranks:
            continue
        seen_ranks.add(rank)
        try:
            score = float(entry.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        cleaned.append(
            {
                "cluster_id": cid,
                "rank": rank,
                "score": max(0.0, min(100.0, score)),
                "rationale": str(entry.get("rationale") or "").strip()
                or "(no rationale)",
                "suggested_theme_label": str(
                    entry.get("suggested_theme_label") or ""
                ).strip(),
            }
        )
    if len(cleaned) < len(clusters):
        # The LLM dropped or duplicated some entries. Patch with heuristic
        # entries for the missing cluster_ids so the output is complete.
        seen_cids = {e["cluster_id"] for e in cleaned}
        missing = [c for c in clusters if c["cluster_id"] not in seen_cids]
        if missing:
            fallback_extras = _heuristic_rank(missing)
            next_rank = max(seen_ranks, default=0) + 1
            for entry in fallback_extras:
                entry["rank"] = next_rank
                next_rank += 1
                cleaned.append(entry)
    cleaned.sort(key=lambda e: e["rank"])
    return cleaned


def _backfill_cluster_themes(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    ranked: list[dict[str, Any]],
    clusters_by_id: dict[str, dict[str, Any]],
) -> None:
    """Persist suggested theme labels to clusters that don't have one yet.

    Side effect of F6 — see module docstring. Idempotent: running F6
    twice never overwrites a theme that's already set (whether from a
    prior F6 run or from a partner-supplied label later). Empty
    suggestions are skipped silently.
    """
    now = _now(store)
    with store._connect() as connection:
        for entry in ranked:
            cid = entry["cluster_id"]
            existing_theme = clusters_by_id.get(cid, {}).get("theme")
            if existing_theme:
                continue
            label = entry.get("suggested_theme_label", "").strip()
            if not label:
                continue
            connection.execute(
                """
                UPDATE feedback_clusters
                SET theme = ?, updated_at = ?
                WHERE cluster_id = ? AND workspace_id = ? AND theme IS NULL
                """,
                (label, now, cid, workspace_id),
            )
        connection.commit()


def _now(store: "PlanningStudioStore") -> str:
    from ..store import now_timestamp

    return now_timestamp()


def rank_clusters(
    clusters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Rank a list of clusters by ROI. Returns ``(ranked_themes, model_used)``.

    Tries the LLM first when configured; falls back to the deterministic
    heuristic ranker on any failure. Always returns ``len(clusters)``
    entries with distinct 1-based ranks.

    Pure-ish: no DB writes here. Theme backfill happens in ``run`` after
    persistence.
    """
    if not clusters:
        return ([], "no-clusters")
    if _is_openai_available():
        ranked = _llm_rank(clusters)
        if ranked is not None and len(ranked) == len(clusters):
            return (ranked, PRIORITIZATION_MODEL)
        logger.info("F6 falling back to heuristic ranker")
    return (_heuristic_rank(clusters), "heuristic-fallback")


def run(
    store: "PlanningStudioStore",
    *,
    workspace_id: str,
    triggered_by: str,
) -> str:
    """End-to-end F6 invocation.

    Steps:
    1. Read clusters with distribution from the feedback layer.
    2. Insert ``prioritization_runs`` row with status='running'.
    3. Rank the clusters (LLM or heuristic).
    4. Backfill ``feedback_clusters.theme`` for clusters that are
       missing one.
    5. Mark the run completed with the ranked output as ``output_json``.

    Returns the new ``run_id``. Synchronous (called from a FastAPI
    BackgroundTasks queue, which already runs sync work in a worker
    thread). On any unexpected error the run is marked status='error'
    with the exception text.
    """
    clusters = list_clusters_with_distribution(
        store, workspace_id=workspace_id
    )
    input_snapshot = {
        "cluster_ids": [c["cluster_id"] for c in clusters],
        "cluster_count": len(clusters),
    }
    run_id = create_prioritization_run(
        store,
        workspace_id=workspace_id,
        triggered_by=triggered_by,
        input_snapshot=input_snapshot,
    )
    try:
        ranked, model_used = rank_clusters(clusters)
        clusters_by_id = {c["cluster_id"]: c for c in clusters}
        # Attach provenance so consumers (orchestrator) don't have to re-join.
        for entry in ranked:
            cluster = clusters_by_id.get(entry["cluster_id"], {})
            entry["provenance"] = {
                "item_count": cluster.get("item_count", 0),
                "category_counts": cluster.get("category_counts", {}),
                "most_recent_ingested_at": cluster.get(
                    "most_recent_ingested_at"
                ),
                "sample_item_ids": cluster.get("sample_item_ids", []),
            }
        _backfill_cluster_themes(
            store,
            workspace_id=workspace_id,
            ranked=ranked,
            clusters_by_id=clusters_by_id,
        )
        complete_prioritization_run(
            store,
            workspace_id=workspace_id,
            run_id=run_id,
            output={
                "themes": ranked,
                "model": model_used,
                "input_cluster_count": len(clusters),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("F6 prioritization run failed (run_id=%s)", run_id)
        try:
            complete_prioritization_run(
                store,
                workspace_id=workspace_id,
                run_id=run_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "F6 also failed to mark run errored (run_id=%s)", run_id
            )
    return run_id
