"""F7-REVISED — pure-Python conflict detector for sub-agent decisions.

Sub-agents emit decisions tagged with a short ``subject`` (e.g.,
``error_reporting``, ``auth_provider``). When two sub-agents tag a
decision with the same subject but write different statements,
that's a conflict candidate. The orchestrator's moderation pass
(separate, an LLM call) decides what to do — produce a canonical
resolution or accept that they're complementary, not contradictory.

This module is intentionally cheap and deterministic. The
heuristic is "same subject + different statement = candidate";
LLM disambiguation lives in the moderator, not here. That choice
keeps the detector unit-testable and makes the moderator the
single LLM step in the conflict path.

Usage::

    candidates = find_conflict_candidates([
        {"decision_id": "a", "subject": "auth_provider",
         "statement": "Use Stripe Identity", "sub_agent_run_id": "sa-1"},
        {"decision_id": "b", "subject": "auth_provider",
         "statement": "Use Lemon Squeezy", "sub_agent_run_id": "sa-2"},
    ])
    # → [{"decision_a_id": "a", "decision_b_id": "b",
    #     "subject": "auth_provider", "sub_agent_a": "sa-1",
    #     "sub_agent_b": "sa-2"}]
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


# Subjects below this length are too generic to be meaningful conflict
# anchors (e.g., "ui", "x"). The detector skips them entirely.
MIN_SUBJECT_LENGTH = 3

# Subjects that the sub-agent assigned as a fallback (the "_unspecified"
# token from sub_agent.py's sanitizer) are dropped.
RESERVED_SUBJECTS = frozenset({"_unspecified"})


def _normalize_subject(raw: str) -> str:
    """Lower-case + strip whitespace + collapse internal spaces.

    Treat ``"Auth provider"``, ``"auth_provider"``, and ``"AUTH-provider"``
    as the same subject. Concrete normalization rules:

    - lower-case
    - strip leading/trailing whitespace
    - replace runs of whitespace with one underscore
    - replace hyphens with underscores

    Doesn't try to handle synonyms (e.g., ``"sso"`` vs ``"single sign on"``);
    that would need an LLM, which is the moderator's job, not the detector's.
    """
    s = (raw or "").strip().lower()
    if not s:
        return s
    s = s.replace("-", "_")
    # Collapse internal whitespace runs.
    parts = s.split()
    return "_".join(parts) if parts else s


def _normalize_statement(raw: str) -> str:
    """Strict-equality fingerprint for the statement text.

    Two decisions with the SAME normalized statement aren't a conflict —
    they're the same decision said twice (by two sub-agents). Conflicts
    require statement difference.
    """
    return " ".join((raw or "").strip().lower().split())


def find_conflict_candidates(
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find pairs of decisions that share a subject but differ in statement.

    Args:
        decisions: each dict must have keys ``decision_id``, ``subject``,
            ``statement``, and ``sub_agent_run_id``. Optional: anything
            else (passed through to outputs as ``decision_a`` /
            ``decision_b`` if needed).

    Returns:
        A list of pair dicts::

            {
                "decision_a_id": str,
                "decision_b_id": str,
                "subject": str,         # normalized
                "sub_agent_a": str,
                "sub_agent_b": str,
            }

        Pair ordering: ``decision_a_id`` < ``decision_b_id`` lexicographically
        so the orchestrator can dedupe pairs across re-runs of the
        detector. Each pair appears exactly once (no (a, b) AND (b, a)).

    Performance: O(N²) per subject group, but each subject typically has
    2-3 decisions across 3-5 sub-agents, so the total work is ~O(N) in
    practice.
    """
    by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in decisions:
        subject_raw = str(d.get("subject") or "")
        subject = _normalize_subject(subject_raw)
        if not subject:
            continue
        if len(subject) < MIN_SUBJECT_LENGTH:
            continue
        if subject in RESERVED_SUBJECTS:
            continue
        by_subject[subject].append(d)
    out: list[dict[str, Any]] = []
    for subject, group in by_subject.items():
        if len(group) < 2:
            continue
        # Bucket statements that are identical — those aren't conflicts,
        # they're agreement. Conflict requires statement difference.
        seen: dict[str, str] = {}
        for d in group:
            fp = _normalize_statement(str(d.get("statement") or ""))
            seen.setdefault(fp, d.get("decision_id"))
        # If after de-duping by statement we still have >= 2 distinct
        # statements, every pair across distinct statements is a candidate.
        statements = list(group)
        for i in range(len(statements)):
            for j in range(i + 1, len(statements)):
                a, b = statements[i], statements[j]
                a_fp = _normalize_statement(str(a.get("statement") or ""))
                b_fp = _normalize_statement(str(b.get("statement") or ""))
                if a_fp == b_fp:
                    continue
                a_id = str(a.get("decision_id") or "")
                b_id = str(b.get("decision_id") or "")
                # Stable ordering for dedup.
                if a_id > b_id:
                    a, b = b, a
                    a_id, b_id = b_id, a_id
                out.append(
                    {
                        "decision_a_id": a_id,
                        "decision_b_id": b_id,
                        "subject": subject,
                        "sub_agent_a": str(a.get("sub_agent_run_id") or ""),
                        "sub_agent_b": str(b.get("sub_agent_run_id") or ""),
                    }
                )
    return out
