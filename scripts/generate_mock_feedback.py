"""Generate a 200-row mock feedback CSV for ingestion testing.

Output: docs/qa/mock-feedback-200.csv

Schema (header row):
    received_at,source,author,author_email,type_hint,title,body

Distribution (200 total):
    60 bugs, 50 feature requests, 30 complaints, 20 praise,
    20 questions, 20 random / off-topic noise

Templates use varied product surfaces, components, and authors so
clustering behaviour has signal to find. Authors are obviously
fictional ("Sample Author N", "Anonymous", varied first names) —
no real-customer attribution per the capability-vs-usage rule.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "qa" / "mock-feedback-200.csv"
)

random.seed(20260502)

SOURCES = [
    "support-email",
    "in-app-feedback",
    "user-interview",
    "twitter",
    "discord",
    "intercom-chat",
    "csv-import",
    "app-store",
    "play-store",
    "linear-issue",
]

# Authors: ~60% have a name, ~30% anonymous, ~10% role-flavored
FIRST_NAMES = [
    "Avery", "Blake", "Cameron", "Drew", "Emery",
    "Finley", "Gray", "Harper", "Indigo", "Jules",
    "Kennedy", "Logan", "Morgan", "Noa", "Oakley",
    "Parker", "Quinn", "Reese", "Sasha", "Tatum",
    "Umar", "Val", "Wren", "Xan", "Yael", "Zion",
]
LAST_INITIAL = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

COMPONENTS = [
    "canvas",
    "topic detail",
    "kickoff form",
    "share dialog",
    "export to PDF",
    "comment threads",
    "search",
    "settings page",
    "billing screen",
    "members panel",
    "onboarding wizard",
    "kanban board",
    "feedback inbox",
    "decision summary",
    "artifact viewer",
    "sub-agent loaders",
    "workspace switcher",
    "GitHub connector",
    "Linear connector",
    "CSV import",
]

ACTIONS = [
    "clicking",
    "tapping",
    "uploading",
    "dragging",
    "exporting",
    "renaming",
    "saving",
    "submitting",
    "switching workspaces",
    "deleting",
    "duplicating",
    "filtering",
    "sorting",
    "logging in",
]

DEVICES = [
    "Chrome on Mac",
    "Chrome on Windows",
    "Firefox on Linux",
    "Safari on iPad",
    "Safari on iPhone",
    "Edge on Windows",
    "Arc on Mac",
    "Brave on Mac",
]

# ─── Bug templates ──────────────────────────────────────────────────
BUG_TEMPLATES = [
    "{component} crashes when {action}",
    "{component} freezes after {action} on {device}",
    "Lost my work when {action} in {component}",
    "{component} shows blank screen on {device}",
    "{component} won't load — spinner forever",
    "{component} throws an error after {action}",
    "Typing in the {component} input causes a flash of unstyled text",
    "{component} layout breaks at narrow viewports on {device}",
    "Keyboard focus jumps unexpectedly in {component}",
    "{component} doesn't save my changes after {action}",
    "Can't undo after {action} in {component}",
    "{component} renders the wrong color in dark mode on {device}",
    "Drag-drop in {component} drops items on the wrong row",
    "{component} duplicates rows when {action}",
    "Toast in {component} doesn't dismiss",
    "Login session expires mid-{action} and I lose state",
]

BUG_BODIES = [
    "Reproducible every time. Tried clearing cache, no change.",
    "Happens about 1 in 5 attempts. Hard to reliably reproduce.",
    "Started after the update last week. Worked fine before.",
    "Console logs show a 500 from the API. Screenshot attached.",
    "I'm a paying customer and this is blocking my workflow today.",
    "Coworker on a different team confirmed they see it too.",
    "Tried Chrome, Safari, and Firefox — all reproduce.",
    "Only happens on mobile, desktop is fine.",
    "Network panel shows the request hangs without responding.",
    "Refresh fixes it temporarily but it comes back after a few clicks.",
]

# ─── Feature request templates ──────────────────────────────────────
FEATURE_TEMPLATES = [
    "Can we get bulk {action} in the {component}?",
    "Please add keyboard shortcuts for {component}",
    "{component} should support markdown",
    "Add export to CSV from the {component}",
    "Allow renaming via double-click in the {component}",
    "{component} could use a search bar",
    "Filters in the {component} would be huge",
    "Group items by tag in the {component}",
    "Dark mode for {component} please",
    "{component} should remember my last view",
    "Slack notifications when something changes in the {component}",
    "Add a 'duplicate' option in the {component} context menu",
    "Color-coding by status in {component}",
    "Sticky header in the {component} when scrolling",
    "Mobile support for {component}",
    "Public read-only links for {component}",
    "{component} should have an undo button",
    "Allow comments on items in the {component}",
    "Merge similar items in the {component} automatically",
    "{component} should support drag-and-drop reorder",
]

FEATURE_BODIES = [
    "We currently work around this by exporting to a spreadsheet. Painful.",
    "This would save me ~30 minutes per week.",
    "Notion has this and it's the one thing I miss.",
    "Linear's version is great — could you do something similar?",
    "Even a basic MVP version would be huge.",
    "I'd happily upgrade for this.",
    "Whole team is asking — please prioritize.",
    "Open to whatever shape you think is right.",
    "Doesn't have to be perfect. Just something workable.",
    "Pairs really naturally with the existing flow.",
]

# ─── Complaint templates ────────────────────────────────────────────
COMPLAINT_TEMPLATES = [
    "{component} is way too slow",
    "{component} is confusing — I can't find {action}",
    "Pricing is too expensive for what I get",
    "{component} feels overcomplicated",
    "Hate the new {component} layout, the old one was fine",
    "{component} feels janky on mobile",
    "Too many clicks to {action} in the {component}",
    "{component} loads slowly even on fast wifi",
    "The {component} font is hard to read on {device}",
    "{component} has zero docs, I have to guess",
    "Onboarding for {component} is rough — no idea what to do first",
    "Notifications from {component} are way too noisy",
]

COMPLAINT_BODIES = [
    "Honest feedback — keep it up otherwise but this is rough.",
    "Switched away from a competitor for this and now regret it.",
    "Worth fixing before you raise prices.",
    "Not a dealbreaker but it's a daily papercut.",
    "I really want to like this product. Please fix.",
    "Please consider going back to the previous version.",
]

# ─── Praise templates ───────────────────────────────────────────────
PRAISE_TEMPLATES = [
    "Love the {component}!",
    "{component} is the best in any tool I've used",
    "Just discovered the {component} — game changer",
    "The way {component} handles {action} is *chef's kiss*",
    "Whoever designed the {component} deserves a raise",
    "{component} is so much better than [redacted competitor]",
    "Buttery-smooth {component} on {device}",
    "Loving how {component} just works",
    "Recommended your tool to my team because of the {component}",
    "Honestly the {component} alone is worth the subscription",
]

PRAISE_BODIES = [
    "Thanks for shipping this!",
    "Nothing else to add — just wanted to share.",
    "Keep going!",
    "This is what I've been wanting forever.",
    "First time I've ever sent feedback to a product. That's how good this is.",
    "Take my money.",
]

# ─── Question templates ─────────────────────────────────────────────
QUESTION_TEMPLATES = [
    "How do I {action} in the {component}?",
    "Can the {component} integrate with our existing setup?",
    "Is there a way to bulk-{action} in {component}?",
    "When will {component} support {device}?",
    "Does the {component} work offline?",
    "How do permissions work in the {component}?",
    "Is the {component} included in the free plan?",
    "Can I use the {component} programmatically (API)?",
    "Where do I find {component} settings?",
    "Is there an export option for {component}?",
]

QUESTION_BODIES = [
    "Couldn't find this in the docs. Sorry if I'm missing something obvious.",
    "Tried searching the help center, no luck.",
    "Asked on Discord, no replies yet.",
    "Pretty sure this is doable, just can't figure out how.",
    "Coworker said it's possible but I don't see the option.",
]

# ─── Random / noise ─────────────────────────────────────────────────
NOISE_TEMPLATES = [
    "?",
    "test",
    "ignore",
    "...",
    "nvm",
    "oops wrong form",
    "delete this",
    "just checking",
    "yo",
    "hey",
    "wtf",
    "lol",
    "great",
    "ok",
    "👍",
    "thanks",
    "no",
    "maybe",
    "tbd",
    "idk",
]


def fake_author() -> tuple[str, str]:
    r = random.random()
    if r < 0.3:
        return ("", "")  # Anonymous
    if r < 0.4:
        roles = ["Customer Support Lead", "PM at Acme", "Eng at Beta",
                 "Designer at Gamma", "Founder", "Product Marketing"]
        return (random.choice(roles), "")
    first = random.choice(FIRST_NAMES)
    li = random.choice(LAST_INITIAL)
    name = f"{first} {li}."
    handle = f"{first.lower()}.{li.lower()}@example.com"
    if random.random() < 0.4:
        handle = ""  # Sometimes no email
    return (name, handle)


def random_timestamp(days_back: int = 60) -> str:
    """ISO-8601 UTC timestamp within the last `days_back` days."""
    delta = timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    when = datetime(2026, 5, 1, tzinfo=timezone.utc) - delta
    return when.isoformat(timespec="seconds")


def fill_template(template: str) -> str:
    return template.format(
        component=random.choice(COMPONENTS),
        action=random.choice(ACTIONS),
        device=random.choice(DEVICES),
    )


def make_row(category: str) -> dict[str, str]:
    if category == "bug":
        title = fill_template(random.choice(BUG_TEMPLATES))
        body = random.choice(BUG_BODIES)
    elif category == "feature":
        title = fill_template(random.choice(FEATURE_TEMPLATES))
        body = random.choice(FEATURE_BODIES)
    elif category == "complaint":
        title = fill_template(random.choice(COMPLAINT_TEMPLATES))
        body = random.choice(COMPLAINT_BODIES)
    elif category == "praise":
        title = fill_template(random.choice(PRAISE_TEMPLATES))
        body = random.choice(PRAISE_BODIES)
    elif category == "question":
        title = fill_template(random.choice(QUESTION_TEMPLATES))
        body = random.choice(QUESTION_BODIES)
    else:  # noise
        title = random.choice(NOISE_TEMPLATES)
        body = ""
    name, email = fake_author()
    return {
        "received_at": random_timestamp(),
        "source": random.choice(SOURCES),
        "author": name,
        "author_email": email,
        "type_hint": category,
        "title": title,
        "body": body,
    }


def main() -> None:
    plan = (
        [("bug", 60),
         ("feature", 50),
         ("complaint", 30),
         ("praise", 20),
         ("question", 20),
         ("noise", 20)]
    )
    rows: list[dict[str, str]] = []
    for category, n in plan:
        for _ in range(n):
            rows.append(make_row(category))
    random.shuffle(rows)
    rows.sort(key=lambda r: r["received_at"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ["received_at", "source", "author", "author_email",
              "type_hint", "title", "body"]
    with OUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
