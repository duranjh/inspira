"""Smoke test for the F4/F5/F6 provider swap.

Exercises real OpenAI API end-to-end on:
- F4/F5 classifier: 3 feedback items batched in one call
- F6 prioritization: 55 synthetic clusters (the issue #117 stress case)

Run from the worktree root with the sibling worktree's venv:

    SOFTWARE_DEV="/Users/$USER/Desktop/Software Development"
    SIBLING_VENV="$SOFTWARE_DEV/planning-studio/services/.venv/bin/python"
    cd "$SOFTWARE_DEV/planning-studio-provider-swap"
    "$SIBLING_VENV" services/scripts/smoke_provider_swap.py

Environment: requires OPENAI_API_KEY. The script reads from
``../planning-studio/.env`` if not already in the environment.

Exit code 0 on success, 1 on any assertion or call failure.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# CRITICAL: the sibling worktree's venv has an editable install of
# planning_studio_service pointing at the SIBLING worktree's source.
# That finder lives on sys.meta_path and wins over the cwd-based path
# finder. Without this strip, the smoke imports the sibling's stale
# Anthropic-using code instead of this worktree's swapped code, and
# the auth error you'd see is Anthropic's SDK rejecting the missing
# ANTHROPIC_API_KEY. Strip the finder + force this worktree's services
# dir onto sys.path so imports resolve here.
_THIS_SERVICES = str(Path(__file__).resolve().parent.parent)
sys.meta_path = [
    m for m in sys.meta_path if "EditableFinder" not in type(m).__name__
]
if _THIS_SERVICES not in sys.path:
    sys.path.insert(0, _THIS_SERVICES)


def _load_env_from_sibling() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    sibling = Path(__file__).resolve().parents[2] / ".env"
    if not sibling.exists():
        return
    for line in sibling.read_text().splitlines():
        if line.startswith("OPENAI_API_KEY="):
            os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip()
            break


def smoke_f4_f5() -> None:
    """3 items → batch classify. Asserts the right shape."""
    from planning_studio_service.feedback_items import llm_classify

    items = [
        llm_classify.ItemForClassify(
            title="Login crashes on Safari",
            body="Tried clearing cache, still hangs.",
        ),
        llm_classify.ItemForClassify(
            title="Please add a dark mode",
            body="Easier on the eyes at night.",
        ),
        llm_classify.ItemForClassify(
            title="Love the new redesign!",
            body="",
        ),
    ]
    print("F4/F5 smoke: classifying 3 items via gpt-5-mini ...")
    t0 = time.time()
    cats = llm_classify.classify_batch(items)
    dt = time.time() - t0
    print(f"  result ({dt:.2f}s): {cats}")
    assert len(cats) == 3, f"expected 3 categories, got {len(cats)}: {cats}"
    valid = {"bug", "feature", "complaint", "praise", "question", "noise"}
    for c in cats:
        assert c in valid, f"unexpected category: {c!r}"
    print(f"  OK — categories valid")


def smoke_f6() -> None:
    """55 synthetic clusters → _llm_rank → real LLM rationales (no heuristic)."""
    from planning_studio_service.agents import prioritization

    # Build 55 distinct clusters with varied category mixes so the LLM
    # has something to actually rank on.
    clusters = []
    for i in range(55):
        # Vary the category profile so each cluster looks unique.
        bugs = (i * 3) % 7
        features = (i * 5) % 5
        complaints = (i * 2) % 4
        questions = i % 3
        praise = i % 2
        clusters.append(
            {
                "cluster_id": f"cl-{i:02d}",
                "theme": f"Theme {i:02d}" if i % 3 == 0 else None,
                "item_count": bugs + features + complaints + questions + praise + 1,
                "category_counts": {
                    "bug": bugs,
                    "feature": features,
                    "complaint": complaints,
                    "praise": praise,
                    "question": questions,
                    "noise": 0,
                },
                "most_recent_ingested_at": "2026-05-01T10:00:00Z",
                "sample_item_ids": [],
            }
        )
    print(f"F6 smoke: ranking {len(clusters)} clusters via gpt-5-mini ...")
    # Call rank_clusters once — it internally calls _llm_rank and
    # tags the result with the model used (or "heuristic-fallback").
    # The 55-cluster size pins issue #117's stress case.
    t0 = time.time()
    ranked, model_used = prioritization.rank_clusters(clusters)
    dt = time.time() - t0
    print(f"  result ({dt:.2f}s): {len(ranked)} entries, model={model_used!r}")
    assert model_used == prioritization.PRIORITIZATION_MODEL, (
        f"expected model_used={prioritization.PRIORITIZATION_MODEL!r} "
        f"but got {model_used!r} — LLM call failed and fell back to heuristic. "
        "This is the issue #117 regression scenario."
    )
    assert len(ranked) == 55, (
        f"expected 55 ranked entries, got {len(ranked)}"
    )
    # Spot-check: every entry has a rationale that's NOT the heuristic
    # fallback string. Heuristic fallback rationales start with
    # "Heuristic fallback:".
    heuristic_count = sum(
        1 for r in ranked
        if r["rationale"].startswith("Heuristic fallback")
    )
    if heuristic_count > 0:
        print(
            f"  NOTE: {heuristic_count}/55 entries used heuristic fallback "
            "(LLM dropped some clusters; patched in)"
        )
    # Print a few sample rationales for visual inspection.
    print(f"  Sample LLM rationales:")
    for entry in ranked[:3]:
        print(f"    rank {entry['rank']:>2} cl={entry['cluster_id']} "
              f"score={entry['score']:.1f} : {entry['rationale'][:80]}")
    print(f"  OK — 55 clusters, model={model_used}")


def main() -> int:
    _load_env_from_sibling()
    if not os.environ.get("OPENAI_API_KEY"):
        print("FAIL: OPENAI_API_KEY not set in env or sibling .env", file=sys.stderr)
        return 1
    print(f"Using OPENAI_API_KEY=... (len={len(os.environ['OPENAI_API_KEY'])})\n")
    # Order matters: run F6 first because it's the long call. The
    # F4/F5 call is cheap and rerunable; F6 is the load-bearing one.
    try:
        smoke_f6()
        print()
        smoke_f4_f5()
    except Exception:  # noqa: BLE001
        print("\nSMOKE FAILED:", file=sys.stderr)
        traceback.print_exc()
        return 1
    print("\nSMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
