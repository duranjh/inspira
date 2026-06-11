"""Server-side Markdown export for a v2 canvas.

Ported from ``app/src/features/inspira/export.ts::projectToMarkdown`` so the
MCP ``export_markdown`` tool and the ``/api/v2/mcp/export_markdown`` OpenAPI
route can serve the same string the browser would produce — without round-
tripping through the client.

Design parity with the TypeScript source:

- Section ordering is identical: ``# title`` → italic cover line →
  per-topic ``## title icon`` blocks (each with ``### Decisions`` and
  optional ``### Q&A``) → trailing ``## Relationships`` list.
- Topics render in ``order_index`` ascending (primary) and ``created_at``
  secondary, matching the store's ``list_topics`` ordering.
- Every user-supplied string passes through ``_escape_markdown`` so a
  rogue backtick, asterisk, or bracket in a title can't silently reflow
  downstream content. Heading prefixes (``## ``, ``### ``) are NOT
  escaped — they're template text, not user content.
- Q&A turns group planner/user pairs the same way the TS source does:
  each turn renders in its order, alternating ``**Planner:**`` and
  ``**You:**`` markers. The blockquote "why this matters" shows only
  on planner turns that have the field populated.

The function is pure: no DB calls, no network, no clocks. The caller
(the ``export_markdown`` handler) assembles the raw store output and
hands it in.
"""
from __future__ import annotations

import re
from typing import Any


# Matches the TS ICON_GLYPH_MAP in export.ts. Keep in sync when either
# side changes — the two glyph tables diverging would be confusing but
# not correctness-breaking (both sides fall back to the neutral bullet).
_ICON_GLYPH_MAP: dict[str, str] = {
    "lightbulb": "\u25CB",
    "feather": "\u270E",
    "book": "\u25A1",
    "compass": "\u27D0",
    "map-pin": "\u25C9",
    "clock": "\u25D0",
    "flag": "\u2690",
    "heart": "\u2665",
    "chart": "\u25A6",
    "megaphone": "\u23F5",
    "camera": "\u25C7",
    "leaf": "\u273F",
}


def _icon_glyph(name: str | None) -> str:
    if not name:
        return "\u2022"
    return _ICON_GLYPH_MAP.get(name, "\u2022")


# Characters Markdown treats as formatting. We escape them verbatim so
# user-supplied prose can't inject markup into the rendered output.
# Matches the TS ``escapeMarkdown`` regex: \`*_{}[]()#+-!|>
_MD_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()#+\-!|>])")


def _escape_markdown(s: str | None) -> str:
    if not s:
        return ""
    return _MD_ESCAPE_RE.sub(r"\\\1", s)


def project_to_markdown(
    *,
    project_title: str,
    topics: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    decisions_by_topic_id: dict[str, list[dict[str, Any]]],
    turns_by_topic_id: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Render the whole project as a single Markdown string.

    Arguments mirror the TS signature one-for-one; shapes match the
    ``store`` return values so the caller hands the dicts in verbatim
    without any pre-flattening.

    Output always ends with a trailing newline so downstream paste /
    file-write lands cleanly. Deterministic — no dates, no clocks.
    """
    parts: list[str] = []

    clean_title = (project_title or "").strip() or "Untitled project"
    # H1 project title: per the TS spec, the title text is NOT escaped
    # so users see exactly the title they set. All other interpolated
    # strings below go through _escape_markdown.
    parts.append(f"# {clean_title}")
    parts.append("")

    ordered_topics = sorted(
        topics,
        key=lambda t: (int(t.get("order_index", 0) or 0), str(t.get("created_at", ""))),
    )

    # Cover line summary: N topics · M decisions.
    decision_count = 0
    for topic in ordered_topics:
        decisions = decisions_by_topic_id.get(topic["topic_id"], [])
        decision_count += len(decisions)
    topic_label = "topic" if len(ordered_topics) == 1 else "topics"
    decision_label = "decision" if decision_count == 1 else "decisions"
    parts.append(
        f"_{len(ordered_topics)} {topic_label} \u00B7 {decision_count} {decision_label}_"
    )
    parts.append("")

    for topic in ordered_topics:
        glyph = _icon_glyph(topic.get("icon"))
        title_line = f"## {_escape_markdown(topic.get('title', ''))} {glyph}".rstrip()
        parts.append(title_line)
        parts.append("")

        why_raw = ""
        metadata = topic.get("metadata") or {}
        raw_why = metadata.get("why_this_topic")
        if isinstance(raw_why, str):
            why_raw = raw_why.strip()
        if why_raw:
            parts.append(f"_{_escape_markdown(why_raw)}_")
            parts.append("")

        decisions = decisions_by_topic_id.get(topic["topic_id"], [])
        parts.append("### Decisions")
        if not decisions:
            parts.append("")
            parts.append("_No decisions captured yet._")
        else:
            for decision in decisions:
                parts.append(f"- {_escape_markdown(decision.get('statement', ''))}")
                rationale = decision.get("rationale")
                if isinstance(rationale, str) and rationale.strip():
                    parts.append(f"  _{_escape_markdown(rationale.strip())}_")
        parts.append("")

        turns = (turns_by_topic_id or {}).get(topic["topic_id"])
        if turns:
            parts.append("### Q&A")
            # Store returns turns in order_index ascending; re-sort defensively
            # in case the caller passed an out-of-order list.
            ordered_turns = sorted(turns, key=lambda t: int(t.get("order_index", 0) or 0))
            for turn in ordered_turns:
                parts.append("")
                role = turn.get("role")
                body = turn.get("body", "")
                if role == "planner":
                    parts.append(f"**Planner:** {_escape_markdown(body)}")
                    why_matters = turn.get("why_this_matters")
                    if isinstance(why_matters, str) and why_matters.strip():
                        parts.append(
                            f"> _{_escape_markdown(why_matters.strip())}_"
                        )
                else:
                    parts.append(f"**You:** {_escape_markdown(body)}")
            parts.append("")

        parts.append("---")
        parts.append("")

    if relationships:
        title_by_id: dict[str, str] = {
            t["topic_id"]: t.get("title", "") for t in topics
        }
        parts.append("## Relationships")
        parts.append("")
        for rel in relationships:
            src_id = rel.get("source_topic_id", "")
            tgt_id = rel.get("target_topic_id", "")
            src = title_by_id.get(src_id, "?")
            tgt = title_by_id.get(tgt_id, "?")
            raw_label = rel.get("label")
            label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else "-"
            parts.append(
                f"- **{_escape_markdown(src)}** \u2192 **{_escape_markdown(tgt)}**: {_escape_markdown(label)}"
            )
        parts.append("")

    return "\n".join(parts)
