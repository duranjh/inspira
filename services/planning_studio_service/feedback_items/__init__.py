"""Feedback items — workspace-scoped storage for ingested feedback.

The shared landing zone for everything Inspira ingests on a
partner's behalf — Linear issues, CSV/JSON pastes, and (future)
Intercom / Productboard / Salesforce / Help Scout.

The W2 F5 ingestion pipeline picks queued items, classifies +
dedupes, and flips them to ``status='classified'``. F4 ships the
write path.

Public surface:

- ``models`` — pydantic ``FeedbackItem`` (read shape)
- ``store`` — write helpers (``upsert_item``, ``list_items``, etc.)
"""
from . import models, store

__all__ = ["models", "store"]
