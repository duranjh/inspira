"""One-shot script: call kickoff with a sample idea and pretty-print the result.

Handy for eyeballing the actual output quality. Not part of the test suite.

    cd services
    python scripts/try_kickoff.py

Pass a custom idea on stdin to try your own:

    echo "A research project on octopus sleep patterns" | python scripts/try_kickoff.py
"""

from __future__ import annotations

import json
import sys

# Windows PowerShell defaults stdout/stderr to cp1252, which can't render
# curly quotes / non-breaking hyphens / em dashes that GPT-5 uses liberally.
# Force UTF-8 for this script so we don't crash mid-print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from planning_studio_service._env_bootstrap import ensure_loaded
from planning_studio_service.agents import OpenAIPlanningInterviewer


DEFAULT_IDEA = (
    "I'm planning a small outdoor wine festival on a Saturday in early October. "
    "Local vineyards, 500–800 guests, families welcome, on the riverside park. "
    "Budget around $50k all-in. Never run one of these before."
)


def main() -> int:
    ensure_loaded()

    if not sys.stdin.isatty():
        idea = sys.stdin.read().strip() or DEFAULT_IDEA
    else:
        idea = DEFAULT_IDEA

    print(f"\n=== KICKOFF INPUT ===\n{idea}\n")
    adapter = OpenAIPlanningInterviewer()
    result = adapter.kickoff(user_idea=idea)

    print(f"=== DOMAIN ===\n{result['domain']} ({result['domain_confidence']} confidence)\n")
    print(f"=== OPENING CARD ===\n{result['opening_card']['body']}\n")
    print("=== TOPICS ===")
    for i, t in enumerate(result["topics"], 1):
        print(f"  {i}. {t['title']} [{t['icon']}]")
        print(f"     {t['why_this_topic']}")
    print()
    if result["relationships"]:
        print("=== RELATIONSHIPS ===")
        for r in result["relationships"]:
            lbl = f" ({r['label']})" if r["label"] else ""
            print(f"  {r['from_topic_title']} → {r['to_topic_title']}{lbl}")
        print()
    print(f"=== SUGGESTED FIRST TOPIC ===\n{result['suggested_first_topic']}\n")
    if result.get("clarifying_question_if_too_vague"):
        print(f"=== CLARIFYING QUESTION ===\n{result['clarifying_question_if_too_vague']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
