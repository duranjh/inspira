"""Linear connector — API key flow.

Strategy mirrors GitHub's connector module but simpler (no App
JWT, no installation token dance — Linear API keys are
long-lived and signed straight into the Authorization header).

Public surface:

- ``client`` — HTTP wrapper around Linear's GraphQL endpoint
- ``sync``   — pulls issues, writes feedback_items rows
"""
from . import client, sync

__all__ = ["client", "sync"]
