"""Push a project's generated scaffold to GitHub as a Pull Request.

YC-pitch alignment (founder direction 2026-05-04): Inspira's
"Send to GitHub" should push *code*, not just file an issue. This
module turns the artifact's manifest (list of {path, content}) into:

  1. A new branch  inspira/{project_id}-{timestamp}  off the default
  2. One commit per file (Contents API) on that branch
  3. A Pull Request from the new branch into the default branch

The PR title + body draw from the project metadata (title +
domain framing) so reviewers see why the scaffold exists, not just
the diff.

Returns ``{pr_url, pr_number, branch_name, commits}`` on success.

Failure modes — bubble up as exceptions for the router to map to
4xx / 5xx envelopes:

  ConnectorNotConfigured        — workspace has no GitHub cred
  DestinationNotConfigured      — cred has no default_owner / repo
  GitHubAppNotConfigured        — Fly secrets missing (503)
  ScaffoldNotReady              — project has no generated scaffold
  GitHubTransient (etc.)        — network / rate-limit / 5xx upstream
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx

from ..connectors import store as connectors_store
from ..connectors.github.app_jwt import installation_access_token
from ..connectors.github.client import (
    GitHubClient,
    GitHubNotFound,
    GitHubTransient,
    GitHubUnauthorized,
)
from ..connectors.github.oauth import load_app_config_from_env
from .github_send import GitHubAppNotConfigured
from .linear_send import ConnectorNotConfigured, DestinationNotConfigured

logger = logging.getLogger(__name__)


class ScaffoldNotReady(Exception):
    """The project has no scaffold (or the scaffold has zero files)."""


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _branch_name_for(project_id: str) -> str:
    """Inspira-prefixed, timestamp-uniquified branch name.

    Same project pushed twice mints two different branches (and PRs)
    rather than collide on the second push. The trailing ``int(time())``
    is intentionally human-readable so partners can read the branch list.
    """
    suffix = int(time.time())
    return f"inspira/{project_id}-{suffix}"


async def send_scaffold_as_pr(
    store,
    *,
    workspace_id: str,
    user_id: str,
    project_id: str,
    pr_title: str,
    pr_body: str,
) -> dict[str, Any]:
    """Push the scaffold of ``project_id`` to GitHub as a PR.

    See module docstring for failure modes. ``user_id`` is the
    actor whose ``get_scaffold`` permission scopes the read.
    """
    # 1. Load credential + repo destination.
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
        raise ConnectorNotConfigured("github")

    # 2. Load scaffold files.
    artifact = store.get_v2_project_artifact(project_id=project_id)
    if artifact is None:
        raise ScaffoldNotReady("artifact_not_generated")
    scaffold_id = artifact.get("latest_scaffold_id")
    if not scaffold_id:
        raise ScaffoldNotReady("scaffold_not_generated")
    row = store.get_scaffold(scaffold_id=str(scaffold_id), user_id=user_id)
    if row is None:
        raise ScaffoldNotReady("scaffold_row_missing")
    try:
        manifest = json.loads(row["manifest_json"] or "{}")
    except (TypeError, ValueError) as exc:
        raise ScaffoldNotReady("scaffold_manifest_unparseable") from exc
    files: list[dict[str, Any]] = [
        e for e in (manifest.get("files") or []) if isinstance(e, dict)
    ]
    if not files:
        raise ScaffoldNotReady("scaffold_zero_files")

    # 3. GitHub config.
    configs = load_app_config_from_env()
    if configs is None:
        raise GitHubAppNotConfigured(
            "GitHub App secrets are not set on the deployment."
        )
    app_config, _ = configs

    branch_name = _branch_name_for(project_id)
    commits: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as http:
        token, _expires_at = await installation_access_token(
            installation_id=installation_id,
            config=app_config,
            http=http,
        )
        client = GitHubClient(installation_token=token, http=http)

        # 4. Resolve default branch + its head SHA.
        try:
            repo_meta = await client.get_repo_metadata(
                repo_full_name=repo_full_name,
            )
        except GitHubUnauthorized:
            raise
        except GitHubNotFound:
            raise DestinationNotConfigured("github") from None
        default_branch = repo_meta.get("default_branch") or "main"
        try:
            base_sha = await client.get_branch_sha(
                repo_full_name=repo_full_name,
                branch=str(default_branch),
            )
        except GitHubNotFound:
            # Repo exists but default branch ref doesn't — empty repo
            # case. Treat as destination misconfig; partner needs to
            # push at least one commit (or change the default branch).
            raise DestinationNotConfigured("github") from None

        # 5. Create branch.
        try:
            await client.create_branch(
                repo_full_name=repo_full_name,
                new_branch=branch_name,
                from_sha=base_sha,
            )
        except GitHubTransient as exc:
            # Branch-already-exists is the typical surface here. The
            # timestamp suffix should make collisions vanishingly rare,
            # so we don't auto-retry — surface the error.
            logger.error("create_branch failed: %s", exc)
            raise

        # 6. Upload each file as one commit on the branch.
        for entry in files:
            path = str(entry.get("path") or "").lstrip("/")
            content = str(entry.get("content") or "")
            if not path:
                continue
            content_b64 = base64.b64encode(
                content.encode("utf-8"),
            ).decode("ascii")
            commit_msg = f"Inspira scaffold: {path}"
            try:
                resp = await client.put_file_contents(
                    repo_full_name=repo_full_name,
                    path=path,
                    content_b64=content_b64,
                    commit_message=commit_msg,
                    branch=branch_name,
                )
            except GitHubTransient as exc:
                logger.error(
                    "put_file_contents failed for %s: %s", path, exc,
                )
                # Best-effort: don't roll back partial commits — partner
                # can either review what landed or close the PR + delete
                # the branch.
                continue
            commit_sha = (
                resp.get("commit", {}).get("sha") if resp else None
            )
            if commit_sha:
                commits.append(str(commit_sha))

        # 7. Open PR.
        pr = await client.create_pull_request(
            repo_full_name=repo_full_name,
            title=pr_title,
            body=pr_body,
            head_branch=branch_name,
            base_branch=str(default_branch),
            draft=False,
        )

    # 8. Persist PR coordinates on the project so the verification
    # endpoint can poll check_runs without a second lookup.
    pr_metadata = {
        "repo_full_name": repo_full_name,
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url"),
        "branch_name": branch_name,
        "head_sha": commits[-1] if commits else None,
        "opened_at_iso": _iso_now(),
        "files_pushed": len(commits),
    }
    try:
        existing_proj = store._get_v2_project(project_id)
        if existing_proj is not None:
            current_md = existing_proj.get("metadata") or {}
            if not isinstance(current_md, dict):
                current_md = {}
            current_md["pr"] = pr_metadata
            with store._connect() as connection:
                connection.execute(
                    "UPDATE v2_projects "
                    "SET metadata_json = ?, updated_at = ? "
                    "WHERE project_id = ?",
                    (
                        __import__("json").dumps(current_md),
                        _iso_now(),
                        project_id,
                    ),
                )
                connection.commit()
    except Exception:  # noqa: BLE001
        # Persistence is best-effort — the PR is open regardless.
        logger.warning("scaffold_to_pr: failed to persist pr metadata")

    return {
        "pr_url": pr.get("html_url"),
        "pr_number": pr.get("number"),
        "branch_name": branch_name,
        "commits": commits,
        "files_pushed": len(commits),
    }
