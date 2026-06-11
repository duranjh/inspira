"""Linear send-to-tracker orchestration for W2 κ.

Owns the multi-step Linear flow: resolve priority label id (if any),
create the parent issue, then create one sub-issue per topic with
``parentId`` linkage. Returns the parent issue's url + identifier so
the modal can land a "Created ACM-249 · Open →" toast.

Provider exception classes (``LinearAuthError``, ``LinearRateLimited``,
``LinearTransient``) bubble out unmodified — the router translates
them to HTTP status codes.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..connectors import store as connectors_store
from ..connectors.linear import client as linear_client
from ..byok import decrypt_api_key
from .builders import IssueBody


class DestinationNotConfigured(Exception):
    """Raised when the Linear credential row lacks a default team_id.

    Surfaced as 400 by the router so the modal can render its
    configure-destination CTA inline.
    """


class ConnectorNotConfigured(Exception):
    """Raised when no Linear credential row exists for the workspace."""


def _resolve_label_id(
    labels: list[dict[str, Any]], *, name: str
) -> str | None:
    """Case-insensitive name → id lookup against the team's label list.

    Returns ``None`` when the team has no label with that name. The
    caller decides whether to silently skip the label or surface a
    warning; for v1 we silently skip — partners would rather see
    the issue land than block on label availability.
    """
    target = name.casefold()
    for label in labels:
        if (label.get("name") or "").casefold() == target:
            return label.get("id")
    return None


async def send_to_linear(
    store,
    *,
    workspace_id: str,
    body: IssueBody,
) -> dict[str, Any]:
    """Push one project canvas to Linear as parent + sub-issues.

    Returns ``{issue_url, issue_id, identifier, sub_issue_count}``.
    """
    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="linear"
    )
    if cred is None:
        raise ConnectorNotConfigured("linear")
    metadata = cred.get("metadata") or {}
    team_id = metadata.get("default_team_id")
    if not team_id:
        raise DestinationNotConfigured("linear")
    project_id_linear = metadata.get("default_project_id")

    api_key = decrypt_api_key(cred["encrypted_token"])

    async with httpx.AsyncClient(timeout=15.0) as http:
        label_ids: list[str] | None = None
        if body.priority_label:
            labels = await linear_client.list_team_labels(
                api_key, team_id=team_id, http=http
            )
            resolved = _resolve_label_id(labels, name=body.priority_label)
            if resolved:
                label_ids = [resolved]

        parent = await linear_client.create_issue(
            api_key,
            team_id=team_id,
            title=body.title,
            description=body.body_markdown,
            label_ids=label_ids,
            project_id=project_id_linear,
            http=http,
        )

        sub_count = 0
        for topic_title in body.topic_titles:
            await linear_client.create_issue(
                api_key,
                team_id=team_id,
                title=topic_title,
                parent_id=parent["id"],
                project_id=project_id_linear,
                http=http,
            )
            sub_count += 1

    return {
        "issue_url": parent.get("url"),
        "issue_id": parent.get("id"),
        "identifier": parent.get("identifier"),
        "sub_issue_count": sub_count,
    }
