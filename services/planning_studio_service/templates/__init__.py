"""Starter project templates for Inspira.

This package ships the ten built-in "starter pack" templates a user can
pick from the kickoff screen instead of staring at a blank textarea.

Each template is a deterministic, hand-authored ``Template`` describing:

- ``slug`` — stable ID used by the API (URL-safe, kebab-case).
- ``title`` — short display name for the card (e.g. "Novel").
- ``tagline`` — one-sentence editorial pitch, italicised in the UI.
- ``description`` — 1-2 sentences describing what kind of project this is.
- ``domain_framing`` — which planner domain label to anchor on (same
  vocabulary the OpenAI adapter already uses: "novel", "event", etc.).
- ``topics`` — 5-7 seeded topics with title, icon, and a one-sentence
  ``why_this_topic`` note.
- ``relationships`` — 5-10 directed edges between those topics. Endpoints
  reference topic titles (canonical within the template), not IDs — the
  HTTP layer resolves titles to freshly-minted topic IDs when it seeds
  a new project.

No LLM involvement — templates are static content, shipped with the code.

Exports:
- ``TEMPLATES`` — the full ordered list of ``Template`` objects.
- ``get_template(slug)`` — lookup helper. Returns ``None`` on unknown slugs
  so the HTTP layer can 404 cleanly.
"""
from __future__ import annotations

from .definitions import (
    DOC_TYPE_ORPHAN_SLUGS,
    Template,
    TemplateRelationship,
    TemplateTopic,
    TEMPLATES,
)


def get_template(slug: str) -> Template | None:
    """Return the template with the given slug, or None if unknown.

    The comparison is case-sensitive on purpose — slugs are canonical
    URL-safe identifiers, not free-text search. Callers that want
    forgiving matching should normalise before calling.
    """
    for template in TEMPLATES:
        if template.slug == slug:
            return template
    return None


__all__ = [
    "DOC_TYPE_ORPHAN_SLUGS",
    "TEMPLATES",
    "Template",
    "TemplateRelationship",
    "TemplateTopic",
    "get_template",
]
