"""Connector registry — hardcoded LIVE / COMING_SOON / FUTURE lists.

Source of truth for the Connectors page (B1.3). The FE reads
``GET /api/v2/connectors`` to render the three sections; this
module is what populates that response.

Tier discipline (founder direction 2026-05-02):
- LIVE entries are interactive — click → OAuth flow or file drop.
- COMING_SOON entries have only a ``mailto:`` chip; no OAuth path.
- FUTURE entries are greyed; no actions, no contact route.

Capability-vs-usage: descriptors describe the connector's
capability ("Pull issues, PRs, and discussions into your decision
feed"). Never make usage claims ("Used by 500+ teams"). Never
mention partner names in the summaries.
"""
from __future__ import annotations

from .base import ConnectorDescriptor, ConnectorTier


# --- LIVE -----------------------------------------------------------
# Three entries: GitHub, Linear, CSV/JSON. GitHub gets OAuth in C2;
# Linear gets API-key flow in C5; CSV/JSON is the universal escape
# hatch (no auth, paste-in / file-drop endpoint in C5).

GITHUB = ConnectorDescriptor(
    provider="github",
    display_name="GitHub",
    tier=ConnectorTier.live,
    summary="Connect repositories for plan-impact awareness.",
    logo_slug="github",
)

LINEAR = ConnectorDescriptor(
    provider="linear",
    display_name="Linear",
    tier=ConnectorTier.live,
    summary="Pull issues, projects, and triage queues into the feedback inbox.",
    logo_slug="linear",
)

CSV_JSON = ConnectorDescriptor(
    provider="csv_json",
    display_name="CSV / JSON import",
    tier=ConnectorTier.live,
    summary="Paste a feedback export — the universal escape hatch.",
    logo_slug="csv-json",
)

# --- COMING_SOON ---------------------------------------------------
# All four carry mailto contact routes. The subject line is encoded
# so a partner clicking through lands on a pre-populated mail
# composer that names the connector they care about.

INTERCOM = ConnectorDescriptor(
    provider="intercom",
    display_name="Intercom",
    tier=ConnectorTier.coming_soon,
    summary="Customer conversations and support tickets.",
    contact_route="mailto:hello@inspira.app?subject=Intercom%20connector",
)

PRODUCTBOARD = ConnectorDescriptor(
    provider="productboard",
    display_name="Productboard",
    tier=ConnectorTier.coming_soon,
    summary="Insight and idea management for product teams.",
    contact_route="mailto:hello@inspira.app?subject=Productboard%20connector",
)

SALESFORCE = ConnectorDescriptor(
    provider="salesforce",
    display_name="Salesforce",
    tier=ConnectorTier.coming_soon,
    summary="Account-team feedback and renewal-risk signals.",
    contact_route="mailto:hello@inspira.app?subject=Salesforce%20connector",
)

HELPSCOUT = ConnectorDescriptor(
    provider="helpscout",
    display_name="Help Scout",
    tier=ConnectorTier.coming_soon,
    summary="Email-based support conversations.",
    contact_route="mailto:hello@inspira.app?subject=Help%20Scout%20connector",
)

# --- FUTURE ---------------------------------------------------------
# Greyed in the FE. No actions, no contact route. These are for the
# "we know we'll need this eventually" surface that signals roadmap
# without committing to a date.

JIRA = ConnectorDescriptor(
    provider="jira",
    display_name="Jira",
    tier=ConnectorTier.future,
    summary="Issue tracking integration.",
)

ZENDESK = ConnectorDescriptor(
    provider="zendesk",
    display_name="Zendesk",
    tier=ConnectorTier.future,
    summary="Customer-support ticketing.",
)

NOTION = ConnectorDescriptor(
    provider="notion",
    display_name="Notion",
    tier=ConnectorTier.future,
    summary="Internal-doc and PRD imports.",
)


LIVE: tuple[ConnectorDescriptor, ...] = (GITHUB, LINEAR, CSV_JSON)
COMING_SOON: tuple[ConnectorDescriptor, ...] = (
    INTERCOM,
    PRODUCTBOARD,
    SALESFORCE,
    HELPSCOUT,
)
FUTURE: tuple[ConnectorDescriptor, ...] = (JIRA, ZENDESK, NOTION)


def all_descriptors() -> tuple[ConnectorDescriptor, ...]:
    """Flat tuple of every descriptor across all three tiers."""
    return LIVE + COMING_SOON + FUTURE


def descriptor_for(provider: str) -> ConnectorDescriptor | None:
    """Look up a descriptor by provider slug. Returns None when
    absent — caller is responsible for 404 handling."""
    for d in all_descriptors():
        if d.provider == provider:
            return d
    return None
