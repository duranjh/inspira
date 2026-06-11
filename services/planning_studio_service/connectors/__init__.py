"""Connector primitives for the v4 ingestion surface.

Layout:

- ``base``     — ``ConnectorTier`` enum, ``ConnectorDescriptor``
                 dataclass, ``BaseConnector`` Protocol.
- ``registry`` — hardcoded LIVE / COMING_SOON / FUTURE lists that
                 drive the Connectors page (B1.3).
- ``store``    — free functions over ``PlanningStudioStore`` for
                 ``connector_credentials``, ``repo_snapshots``, and
                 ``connector_sync_runs``. Workspace-scoped via the
                 composite PK on the credentials table.
- ``router``   — the ``/api/v2/connectors`` APIRouter (GET state for
                 current workspace; POST mutations land in W2 C2 once
                 the GitHub App OAuth flow is wired).
- ``github``   — provider-specific OAuth + sync (W2 C2).

Workspace-scoping invariant: every store helper takes
``workspace_id`` as a keyword arg. There is no user-keyed lookup
path. A user who belongs to two workspaces and connects GitHub on
each writes two separate rows under the composite PK
``(workspace_id, 'github')`` — never a shared row.
"""
from .base import (
    BaseConnector,
    ConnectorDescriptor,
    ConnectorState,
    ConnectorTier,
)

__all__ = [
    "BaseConnector",
    "ConnectorDescriptor",
    "ConnectorState",
    "ConnectorTier",
]
