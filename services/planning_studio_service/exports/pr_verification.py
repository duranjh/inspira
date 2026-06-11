"""Verify a pushed PR by polling its CI status (v0).

Founder direction (2026-05-04): "once it's actually in GitHub, Inspira
should re-download to verify the change actually landed and is working
as expected."

v0 implementation: poll GitHub's check_runs / Actions API for the PR's
head commit. Surfaces pass / fail / pending / no_ci_configured. This
is the safe, reliable, zero-extra-infrastructure path — Inspira reports
what the partner's existing CI says.

v1 (multi-day, deferred): spin up a sandboxed test runner that clones
the merged branch, detects the test command, runs tests, captures
output. Needs Fly machine isolation per-call, repo-runtime install
discovery, network egress controls. Out of scope today.

Returns a stable shape so the FE has one verification surface to read:

    {
      "status": "pending" | "passed" | "failed" | "no_ci_configured"
                | "pr_not_open" | "no_pr_metadata",
      "pr_number": int | None,
      "pr_url": str | None,
      "head_sha": str | None,
      "merged": bool,
      "checks": [
        {"name": str, "status": str, "conclusion": str | None,
         "details_url": str | None, "started_at": str | None,
         "completed_at": str | None}
      ],
      "summary": str,        # e.g. "3 passed, 1 failed, 0 pending"
      "fetched_at": str,
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..connectors.github.app_jwt import installation_access_token
from ..connectors.github.client import (
    GitHubClient,
    GitHubNotFound,
    GitHubTransient,
    GitHubUnauthorized,
)
from ..connectors.github.oauth import load_app_config_from_env
from ..connectors import store as connectors_store
from .github_send import GitHubAppNotConfigured
from .linear_send import ConnectorNotConfigured

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _summarize_runs(runs: list[dict[str, Any]]) -> tuple[str, str]:
    """Aggregate a list of check_run dicts into (overall_status, summary).

    GitHub check_run conclusions: success / failure / neutral /
    cancelled / skipped / timed_out / action_required / stale.
    Statuses (in-flight): queued / in_progress / completed.
    We bucket them into pass / fail / pending for the partner-facing
    summary.
    """
    if not runs:
        return ("no_ci_configured", "No CI workflows ran on this commit yet.")

    passed = 0
    failed = 0
    pending = 0
    for run in runs:
        status = (run.get("status") or "").lower()
        conclusion = (run.get("conclusion") or "").lower()
        if status != "completed":
            pending += 1
            continue
        if conclusion in {"success", "neutral", "skipped"}:
            passed += 1
        elif conclusion in {
            "failure", "cancelled", "timed_out", "action_required",
            "stale",
        }:
            failed += 1
        else:
            pending += 1

    summary_parts: list[str] = []
    if passed:
        summary_parts.append(f"{passed} passed")
    if failed:
        summary_parts.append(f"{failed} failed")
    if pending:
        summary_parts.append(f"{pending} pending")
    summary = ", ".join(summary_parts) if summary_parts else "0 runs"

    if pending > 0:
        overall = "pending"
    elif failed > 0:
        overall = "failed"
    elif passed > 0:
        overall = "passed"
    else:
        overall = "no_ci_configured"
    return (overall, summary)


async def fetch_pr_verification(
    store,
    *,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Poll the GitHub Actions / check_runs status for a project's PR.

    Returns a stable verification dict (see module docstring). When the
    project has no `pr` metadata yet (Send-to-GitHub hasn't been clicked),
    returns status="no_pr_metadata" so the FE can render a different
    state.
    """
    proj = store._get_v2_project(project_id)
    if proj is None or proj.get("workspace_id") != workspace_id:
        return {
            "status": "no_pr_metadata",
            "pr_number": None,
            "pr_url": None,
            "head_sha": None,
            "merged": False,
            "checks": [],
            "summary": "Project not found.",
            "fetched_at": _iso_now(),
        }
    md = proj.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    pr_md = md.get("pr") or {}
    if not isinstance(pr_md, dict) or not pr_md.get("pr_number"):
        return {
            "status": "no_pr_metadata",
            "pr_number": None,
            "pr_url": None,
            "head_sha": None,
            "merged": False,
            "checks": [],
            "summary": "No PR opened yet — click Send to GitHub on the artifact viewer first.",
            "fetched_at": _iso_now(),
        }

    repo_full_name = pr_md.get("repo_full_name")
    pr_number = pr_md.get("pr_number")
    head_sha_stored = pr_md.get("head_sha")

    cred = connectors_store.get_credential(
        store, workspace_id=workspace_id, provider="github",
    )
    if cred is None:
        raise ConnectorNotConfigured("github")
    installation_id = cred.get("installation_id")
    if not installation_id:
        raise ConnectorNotConfigured("github")
    configs = load_app_config_from_env()
    if configs is None:
        raise GitHubAppNotConfigured(
            "GitHub App secrets are not set on the deployment."
        )
    app_config, _ = configs

    async with httpx.AsyncClient(timeout=12.0) as http:
        token, _expires_at = await installation_access_token(
            installation_id=installation_id,
            config=app_config,
            http=http,
        )
        client = GitHubClient(installation_token=token, http=http)

        try:
            pr = await client.get_pull_request(
                repo_full_name=repo_full_name, pr_number=int(pr_number),
            )
        except GitHubUnauthorized:
            raise
        except GitHubNotFound:
            return {
                "status": "pr_not_open",
                "pr_number": pr_number,
                "pr_url": pr_md.get("pr_url"),
                "head_sha": head_sha_stored,
                "merged": False,
                "checks": [],
                "summary": "PR no longer exists on GitHub.",
                "fetched_at": _iso_now(),
            }
        except GitHubTransient as exc:
            logger.warning("pr_verification: PR fetch transient: %s", exc)
            raise

        if pr is None:
            return {
                "status": "pr_not_open",
                "pr_number": pr_number,
                "pr_url": pr_md.get("pr_url"),
                "head_sha": head_sha_stored,
                "merged": False,
                "checks": [],
                "summary": "PR not found.",
                "fetched_at": _iso_now(),
            }

        merged = bool(pr.get("merged"))
        head_sha = (pr.get("head") or {}).get("sha") or head_sha_stored
        ref_for_checks = pr.get("merge_commit_sha") if merged else head_sha
        if not ref_for_checks:
            return {
                "status": "pending",
                "pr_number": pr_number,
                "pr_url": pr.get("html_url") or pr_md.get("pr_url"),
                "head_sha": head_sha,
                "merged": merged,
                "checks": [],
                "summary": "Waiting for GitHub to assign a head SHA.",
                "fetched_at": _iso_now(),
            }

        try:
            runs = await client.list_check_runs_for_ref(
                repo_full_name=repo_full_name,
                ref=ref_for_checks,
            )
        except GitHubTransient as exc:
            logger.warning("pr_verification: check_runs transient: %s", exc)
            raise

    overall, summary = _summarize_runs(runs)
    checks = [
        {
            "name": r.get("name"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "details_url": r.get("html_url"),
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
        }
        for r in runs
    ]
    if merged and overall == "passed":
        summary = f"Merged. {summary} on the merge commit."
    elif merged and overall == "failed":
        summary = f"Merged BUT {summary}. Inspect the failing run."
    elif merged and overall == "no_ci_configured":
        summary = "Merged. No CI configured — verification skipped."

    return {
        "status": overall,
        "pr_number": pr_number,
        "pr_url": pr.get("html_url") or pr_md.get("pr_url"),
        "head_sha": head_sha,
        "merged": merged,
        "checks": checks,
        "summary": summary,
        "fetched_at": _iso_now(),
    }
