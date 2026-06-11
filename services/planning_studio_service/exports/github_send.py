"""GitHub send-to-tracker orchestration for W2.

Posts a single GitHub Issue with a tasks-as-checkboxes body section.
Unlike Linear (which uses sub-issues), GitHub renders ``- [ ] task``
inside the issue body as interactive checkboxes — that pattern
matches the design's "Tasks" card on the GitHub modal.

Label-create-if-missing: GitHub labels are repo-scoped and may not
exist on a fresh repo, so we ``ensure_label`` before issue creation.
Colors match Inspira's priority palette (P0 rust, P1 gold, P2 sage).
"""
from __future__ import annotations

from typing import Any

import httpx

from ..connectors import store as connectors_store
from ..connectors.github.app_jwt import installation_access_token
from ..connectors.github.client import (
    GitHubClient,
    GitHubNotFound,
    GitHubUnauthorized,
)
from ..connectors.github.oauth import load_app_config_from_env
from .builders import IssueBody, github_body_with_tasks
from .linear_send import ConnectorNotConfigured, DestinationNotConfigured


class GitHubAppNotConfigured(Exception):
    """Raised when the deploy is missing GitHub App env secrets.

    Translates to 503 in the router so a missing-secrets state
    surfaces as a deploy-config error rather than a generic 500.
    """


# Hex colors (no leading #) for the GitHub label palette. Approximate
# the design tokens — Inspira's --rust / --gold / --sage land roughly
# in the same hue family. Partners can recolor labels manually after
# first export; ``ensure_label`` won't restomp existing colors.
PRIORITY_LABEL_COLORS: dict[str, str] = {
    "P0": "b3471d",  # rust
    "P1": "c89d3b",  # gold
    "P2": "7a8c5a",  # sage
}


async def send_to_github(
    store,
    *,
    workspace_id: str,
    body: IssueBody,
) -> dict[str, Any]:
    """Push one project canvas to GitHub as a single issue.

    Returns ``{issue_url, issue_number, issue_id}``.
    """
    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="github"
    )
    if cred is None:
        raise ConnectorNotConfigured("github")
    metadata = cred.get("metadata") or {}
    owner = metadata.get("default_owner")
    repo = metadata.get("default_repo")
    if not owner or not repo:
        raise DestinationNotConfigured("github")
    repo_full_name = f"{owner}/{repo}"

    installation_id = cred.get("installation_id")
    if not installation_id:
        # The credential row was persisted without an installation_id
        # — the GitHub App OAuth flow always sets one, so this is an
        # unrecoverable state for the export path. Surface as
        # connector-not-configured so the modal points the partner
        # at the Connect flow rather than a generic 502.
        raise ConnectorNotConfigured("github")

    configs = load_app_config_from_env()
    if configs is None:
        raise GitHubAppNotConfigured(
            "GitHub App secrets are not set on the deployment."
        )
    app_config, _ = configs
    full_body = github_body_with_tasks(
        body.body_markdown, topic_titles=body.topic_titles
    )
    labels: list[str] | None = None
    async with httpx.AsyncClient(timeout=20.0) as http:
        token, _expires_at = await installation_access_token(
            installation_id=installation_id, config=app_config, http=http
        )
        client = GitHubClient(installation_token=token, http=http)

        if body.priority_label:
            color = PRIORITY_LABEL_COLORS.get(body.priority_label, "ededed")
            try:
                await client.ensure_label(
                    repo_full_name=repo_full_name,
                    name=body.priority_label,
                    color=color,
                    description=f"Inspira priority {body.priority_label}",
                )
                labels = [body.priority_label]
            except GitHubUnauthorized:
                # Re-raise — token-level failure should surface to
                # the router as a 502, not be swallowed.
                raise
            except GitHubNotFound:
                # Repo missing entirely is the destination-config
                # problem in disguise. Treat as such; the modal will
                # ask the partner to fix the configured repo.
                raise DestinationNotConfigured("github") from None

        try:
            issue = await client.create_issue(
                repo_full_name=repo_full_name,
                title=body.title,
                body=full_body,
                labels=labels,
            )
        except GitHubNotFound:
            raise DestinationNotConfigured("github") from None

    return {
        "issue_url": issue.get("html_url"),
        "issue_number": issue.get("number"),
        "issue_id": issue.get("id"),
    }
