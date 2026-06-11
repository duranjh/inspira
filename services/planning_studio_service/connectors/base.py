"""Connector primitives — types shared by registry + per-provider modules.

Three pieces:

- ``ConnectorTier`` — design-tier the connector belongs to. Drives
  which section the FE renders the descriptor under.
- ``ConnectorDescriptor`` — static metadata per connector (name,
  summary, mailto for coming-soon entries).
- ``ConnectorState`` — runtime state the FE renders into the tile's
  visual variant.
- ``BaseConnector`` Protocol — what every provider module must
  expose. The factory pattern keeps the registry decoupled from the
  concrete provider implementations.

Per founder direction 2026-05-02: the ConnectorTile has exactly
four visual states (idle Live / connected / mailto / future-greyed /
error) — DO NOT add a fifth. The state machine here mirrors that:
``connected | not_connected | needs_reauth | error | not_implemented``
where ``not_implemented`` collapses to the "idle Live" visual until
the provider's OAuth lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


class ConnectorTier(str, Enum):
    """Three design tiers per B1.3."""

    live = "live"
    coming_soon = "coming_soon"
    future = "future"


class ConnectorStatus(str, Enum):
    """Runtime state of a connector for a given workspace.

    The FE collapses these to the four visual variants on B1.3:

    - ``not_connected`` / ``not_implemented`` → idle Live (sage CTA)
    - ``connected``                            → connected (with meta line)
    - ``needs_reauth`` / ``error``             → error (rust "Retry →")

    The mailto and future-greyed variants are driven by the
    descriptor's tier, not the runtime status.
    """

    not_connected = "not_connected"
    connected = "connected"
    needs_reauth = "needs_reauth"
    error = "error"
    not_implemented = "not_implemented"  # provider listed but OAuth not yet wired


@dataclass(frozen=True)
class ConnectorDescriptor:
    """Static metadata for a connector.

    Identity:
    - ``provider`` is the slug used in URL paths and the
      connector_credentials.provider column.
    - ``display_name`` is what the FE renders.

    Tier-specific fields:
    - ``contact_route`` (LIVE/coming_soon only): ``mailto:`` URL the
      "Talk to us" chip on a coming-soon tile uses. None for LIVE
      and FUTURE entries.
    - ``logo_slug`` (LIVE only): asset slug the FE looks up to render
      the brand mark in monochrome sage. None for coming-soon and
      future entries (rendered as text-only chips).
    """

    provider: str
    display_name: str
    tier: ConnectorTier
    summary: str = ""
    contact_route: str | None = None
    logo_slug: str | None = None


@dataclass(frozen=True)
class ConnectorState:
    """Runtime state for a single (workspace, connector) pair.

    The fields the FE reads to render the connected-tile meta line
    (B1.3: "Connected · Acme Corp / acme-platform · 3 repos · last
    sync 2 min ago") and the error variant (B1.3: "Sync failed ·
    last successful 6 hours ago · Retry →").
    """

    status: ConnectorStatus = ConnectorStatus.not_connected
    account: str | None = None
    primary_repo_full_name: str | None = None
    repo_count: int = 0
    last_sync_at: str | None = None
    last_successful_sync_at: str | None = None
    last_error: str | None = None


class BaseConnector(Protocol):
    """Contract every provider module exposes.

    Per the W2 plan, only GitHub implements this fully in C2.
    Linear + CSV/JSON ship in C2/C5 with stub implementations that
    return ``ConnectorStatus.not_implemented`` until their OAuth /
    paste-in flows wire up.
    """

    descriptor: ClassVar[ConnectorDescriptor]

    async def status_for(
        self, store: "PlanningStudioStore", workspace_id: str
    ) -> ConnectorState:
        """Return the current runtime state for this workspace."""
        ...

    async def sync(
        self,
        store: "PlanningStudioStore",
        workspace_id: str,
        *,
        trigger: str,
    ) -> dict[str, Any]:
        """Trigger a sync run; return run_id + initial status."""
        ...
