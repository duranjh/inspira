"""Pydantic models for the feedback_items domain (W2 F4)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FeedbackItemStatus = Literal[
    "queued", "classified", "discarded", "promoted"
]


class FeedbackItem(BaseModel):
    """Read shape for a single ingested feedback item."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    workspace_id: str
    source: str
    external_id: str | None = None
    content_hash: str
    title: str
    body: str = ""
    author: str | None = None
    author_email: str | None = None
    received_at: str | None = None
    ingested_at: str
    type_hint: str | None = None
    status: FeedbackItemStatus = "queued"
    cluster_id: str | None = None


class FeedbackItemCount(BaseModel):
    """Aggregate counts for the connector tile's connected meta line."""

    model_config = ConfigDict(frozen=True)

    total: int
    queued: int
    last_ingested_at: str | None = None
