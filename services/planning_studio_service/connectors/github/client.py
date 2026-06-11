"""HTTP wrapper around GitHub's REST API for installation-scoped reads.

Only accepts installation tokens (1-hour TTL) — never an App JWT.
Per W2 watch point #2, the App JWT lives at ``app_jwt.py`` and
authenticates AS the app (only valid for installation-token mint).
This client authenticates AS the installation (valid for repo /
issue / commit reads).

Typed exceptions let the sync layer route each failure mode to
the right runtime state:

- ``GitHubUnauthorized``  → mark credential needs_reauth
- ``GitHubRateLimited``   → close run rate_limited; back off
- ``GitHubTransient``     → retry up to 3x with backoff
- ``GitHubNotFound``      → skip the offending repo, continue

No tenacity in the deps stack; manual retry with backoff inside
the methods that need it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Base class for typed GitHub-client errors."""


class GitHubUnauthorized(GitHubError):
    """401 from GitHub. Token expired or revoked."""


class GitHubRateLimited(GitHubError):
    """403 + X-RateLimit-Remaining=0, or 429."""

    def __init__(
        self, message: str, *, reset_at: datetime | None = None
    ) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GitHubNotFound(GitHubError):
    """404 — repo or path doesn't exist (anymore)."""


class GitHubTransient(GitHubError):
    """5xx or network error. Retry-eligible."""


class GitHubClient:
    """Installation-scoped GitHub REST client.

    Each instance wraps a single installation access token. Token
    rotation is the caller's responsibility — when the token
    expires (1 hour), mint a new one via
    ``app_jwt.installation_access_token`` and create a new client.

    Usage:
        async with httpx.AsyncClient(timeout=30) as http:
            client = GitHubClient(installation_token=t, http=http)
            repos = await client.list_installation_repos()
    """

    BASE_URL = "https://api.github.com"
    DEFAULT_HEADERS = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    MAX_RETRIES = 3
    BACKOFF_BASE_S = 1.0  # 1s, 4s, 16s

    def __init__(
        self,
        *,
        installation_token: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._token = installation_token
        self._http = http

    def _auth_headers(self) -> dict[str, str]:
        return {
            **self.DEFAULT_HEADERS,
            "Authorization": f"Bearer {self._token}",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        """Issue an authenticated request with retry on 5xx /
        network errors. Returns the parsed JSON body on 2xx.

        Raises one of ``GitHubUnauthorized`` / ``GitHubRateLimited``
        / ``GitHubNotFound`` / ``GitHubTransient`` based on the
        response shape. ``json`` carries an optional request body
        for POST/PATCH/PUT.
        """
        url = (
            path if path.startswith("http") else f"{self.BASE_URL}{path}"
        )
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._auth_headers(),
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == self.MAX_RETRIES - 1:
                    raise GitHubTransient(
                        f"{method} {url}: {exc}"
                    ) from exc
                await asyncio.sleep(
                    self.BACKOFF_BASE_S * (4 ** attempt)
                )
                continue

            status = response.status_code
            if 200 <= status < 300:
                if response.content:
                    return response.json()
                return None
            if status == 401:
                raise GitHubUnauthorized(
                    f"{method} {url}: 401 unauthorized"
                )
            if status == 404:
                raise GitHubNotFound(f"{method} {url}: 404")
            if status == 429 or (
                status == 403
                and response.headers.get("X-RateLimit-Remaining") == "0"
            ):
                reset = response.headers.get("X-RateLimit-Reset")
                reset_at: datetime | None = None
                if reset and reset.isdigit():
                    reset_at = datetime.fromtimestamp(
                        int(reset), tz=timezone.utc
                    )
                raise GitHubRateLimited(
                    f"{method} {url}: rate-limited",
                    reset_at=reset_at,
                )
            if 500 <= status < 600:
                last_exc = GitHubTransient(
                    f"{method} {url}: HTTP {status}"
                )
                if attempt == self.MAX_RETRIES - 1:
                    raise last_exc
                await asyncio.sleep(
                    self.BACKOFF_BASE_S * (4 ** attempt)
                )
                continue
            # 4xx other than 401/404/429 → unrecoverable. Surface
            # as a transient with the body for the sync log.
            raise GitHubTransient(
                f"{method} {url}: HTTP {status}: "
                f"{response.text[:200]}"
            )
        # Should be unreachable — every branch above either returns
        # or raises. Belt-and-braces.
        raise GitHubTransient(
            f"{method} {url}: exhausted retries"
        ) from last_exc

    # -----------------------------------------------------------
    # Installation-scoped reads
    # -----------------------------------------------------------

    async def list_installation_repos(
        self, *, per_page: int = 100
    ) -> list[dict[str, Any]]:
        """List repos this installation can access. Single page —
        the W2 sync only consumes the top 3 by pushed_at, so
        pagination is unnecessary."""
        body = await self._request(
            "GET",
            "/installation/repositories",
            params={"per_page": per_page},
        )
        if not body:
            return []
        return body.get("repositories", [])

    async def get_repo_tree(
        self,
        *,
        repo_full_name: str,
        ref: str,
        recursive: bool = True,
    ) -> dict[str, Any]:
        """Fetch the git tree at ``ref``. ``recursive=True`` flattens
        subdirectories into one payload. Limit: GitHub truncates at
        100k entries; for our top-of-tree W2 use case that's
        irrelevant."""
        params: dict[str, Any] = {}
        if recursive:
            params["recursive"] = "1"
        return await self._request(
            "GET",
            f"/repos/{repo_full_name}/git/trees/{ref}",
            params=params,
        )

    async def get_file_contents(
        self,
        *,
        repo_full_name: str,
        path: str,
        ref: str | None = None,
    ) -> dict[str, Any] | None:
        """Read a single file via the Contents API.

        Returns ``{name, path, sha, content (base64), encoding, size}``
        on success, or ``None`` on 404. Caller decodes base64 to get
        the actual text.
        """
        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref
        try:
            body = await self._request(
                "GET",
                f"/repos/{repo_full_name}/contents/{path}",
                params=params,
            )
        except GitHubNotFound:
            return None
        if not isinstance(body, dict):
            return None
        return body

    async def list_open_issues(
        self,
        *,
        repo_full_name: str,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """List the most recent open issues. Excludes PRs (GitHub's
        ``/issues`` endpoint conflates them; we filter
        ``pull_request`` in the response)."""
        body = await self._request(
            "GET",
            f"/repos/{repo_full_name}/issues",
            params={
                "state": "open",
                "per_page": per_page,
                "sort": "created",
                "direction": "desc",
            },
        )
        if not body:
            return []
        return [i for i in body if "pull_request" not in i]

    async def list_recent_commits(
        self,
        *,
        repo_full_name: str,
        branch: str,
        per_page: int = 10,
    ) -> list[dict[str, Any]]:
        """Most recent commits on the given branch."""
        body = await self._request(
            "GET",
            f"/repos/{repo_full_name}/commits",
            params={"sha": branch, "per_page": per_page},
        )
        return body or []

    # -----------------------------------------------------------
    # Installation-scoped writes — used by exports/ (W2)
    # -----------------------------------------------------------

    async def ensure_label(
        self,
        *,
        repo_full_name: str,
        name: str,
        color: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently create a label on the repo.

        ``color`` is a 6-char hex (no leading ``#``). If the label
        already exists, returns the existing payload without
        re-applying ``color`` / ``description`` (avoids stomping on
        partner customizations).
        """
        try:
            existing = await self._request(
                "GET",
                f"/repos/{repo_full_name}/labels/{name}",
            )
            return existing or {"name": name}
        except GitHubNotFound:
            payload: dict[str, Any] = {"name": name, "color": color}
            if description:
                payload["description"] = description
            created = await self._request(
                "POST",
                f"/repos/{repo_full_name}/labels",
                json=payload,
            )
            return created or {"name": name, "color": color}

    async def create_issue(
        self,
        *,
        repo_full_name: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an issue on the repo.

        Returns the GitHub issue payload — relevant fields are
        ``html_url``, ``number``, ``id``. Tasks live in the body as
        ``- [ ] {topic}`` lines; GitHub renders them as checkboxes.
        """
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = list(labels)
        created = await self._request(
            "POST",
            f"/repos/{repo_full_name}/issues",
            json=payload,
        )
        if not isinstance(created, dict):
            raise GitHubTransient(
                f"GitHub issues POST returned non-dict: {created!r}"
            )
        return created

    # -----------------------------------------------------------
    # Pull-request authoring — used by exports/scaffold_to_pr.
    # -----------------------------------------------------------

    async def get_repo_metadata(
        self,
        *,
        repo_full_name: str,
    ) -> dict[str, Any]:
        """Fetch repo info — caller mostly cares about ``default_branch``."""
        body = await self._request(
            "GET",
            f"/repos/{repo_full_name}",
        )
        if not isinstance(body, dict):
            raise GitHubTransient(
                f"GET /repos returned non-dict: {body!r}"
            )
        return body

    async def get_branch_sha(
        self,
        *,
        repo_full_name: str,
        branch: str,
    ) -> str:
        """Resolve a branch name to its head commit SHA via Git Refs API."""
        body = await self._request(
            "GET",
            f"/repos/{repo_full_name}/git/refs/heads/{branch}",
        )
        if not isinstance(body, dict):
            raise GitHubTransient(
                f"GET /git/refs returned non-dict for {branch!r}: {body!r}"
            )
        sha = body.get("object", {}).get("sha")
        if not isinstance(sha, str) or not sha:
            raise GitHubTransient(
                f"branch {branch!r} ref has no object.sha: {body!r}"
            )
        return sha

    async def compare_commits(
        self,
        *,
        repo_full_name: str,
        base: str,
        head: str,
    ) -> dict[str, Any]:
        """Wrap GitHub's compare API.

        Returns the parsed body, which includes (among others)::

            {
              "status": "ahead" | "behind" | "identical" | "diverged",
              "ahead_by": int,
              "behind_by": int,
              "total_commits": int,
              "commits": [{...}, ...],
              "files":   [{"filename": str, "status": str, ...}, ...]
            }

        **Pagination cap**: GitHub returns the first ~300 files in a
        single page; large diffs are silently truncated. Wave F.5
        treats truncation as acceptable for v1 — the caller flags the
        response with ``truncated=True`` and the UI hints to the user
        that the displayed count is a lower bound. F.6's 3-way diff
        will follow ``Link: rel="next"`` headers when needed.
        """
        body = await self._request(
            "GET",
            f"/repos/{repo_full_name}/compare/{base}...{head}",
        )
        if not isinstance(body, dict):
            raise GitHubTransient(
                f"GET /compare returned non-dict for {base}...{head}: {body!r}"
            )
        return body

    async def create_branch(
        self,
        *,
        repo_full_name: str,
        new_branch: str,
        from_sha: str,
    ) -> dict[str, Any]:
        """Create a new branch pointing at ``from_sha``.

        Errors if the ref already exists — the caller's responsibility
        to suffix branch names with a uniqueness token (timestamp /
        scaffold_id).
        """
        return await self._request(
            "POST",
            f"/repos/{repo_full_name}/git/refs",
            json={
                "ref": f"refs/heads/{new_branch}",
                "sha": from_sha,
            },
        )

    async def put_file_contents(
        self,
        *,
        repo_full_name: str,
        path: str,
        content_b64: str,
        commit_message: str,
        branch: str,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create-or-update a single file via GitHub Contents API.

        ``content_b64`` is the file content base64-encoded. Pass
        ``sha`` of the existing file when overwriting; omit when
        creating. Each call is one commit on ``branch`` — for a
        scaffold of N files this means N commits, which is fine for
        a v0 PR (folds nicely into the PR's commit list and the
        commit messages tell the story file-by-file).
        """
        payload: dict[str, Any] = {
            "message": commit_message,
            "content": content_b64,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        body = await self._request(
            "PUT",
            f"/repos/{repo_full_name}/contents/{path}",
            json=payload,
        )
        if not isinstance(body, dict):
            raise GitHubTransient(
                f"PUT /contents returned non-dict for {path!r}: {body!r}"
            )
        return body

    async def create_pull_request(
        self,
        *,
        repo_full_name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        """Open a Pull Request from ``head_branch`` → ``base_branch``."""
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }
        if draft:
            payload["draft"] = True
        created = await self._request(
            "POST",
            f"/repos/{repo_full_name}/pulls",
            json=payload,
        )
        if not isinstance(created, dict):
            raise GitHubTransient(
                f"POST /pulls returned non-dict: {created!r}"
            )
        return created

    async def list_check_runs_for_ref(
        self,
        *,
        repo_full_name: str,
        ref: str,
    ) -> list[dict[str, Any]]:
        """List GitHub Actions / check_run results for a commit / branch.

        Used by the PR-push verification flow — Inspira polls the head
        commit of the PR branch to surface pass/fail back to the
        partner. Returns an empty list when the repo has no CI
        configured (no workflow runs registered against the ref).
        """
        body = await self._request(
            "GET",
            f"/repos/{repo_full_name}/commits/{ref}/check-runs",
        )
        if not isinstance(body, dict):
            return []
        runs = body.get("check_runs")
        if not isinstance(runs, list):
            return []
        return [r for r in runs if isinstance(r, dict)]

    async def get_pull_request(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
    ) -> dict[str, Any] | None:
        """Read a PR — caller cares about ``state`` (open|closed),
        ``merged`` (bool), ``head.sha``, ``merge_commit_sha``."""
        try:
            body = await self._request(
                "GET",
                f"/repos/{repo_full_name}/pulls/{pr_number}",
            )
        except GitHubNotFound:
            return None
        return body if isinstance(body, dict) else None

    async def add_labels_to_issue(
        self,
        *,
        repo_full_name: str,
        issue_number: int,
        labels: list[str],
    ) -> Any:
        """Apply labels to an issue (or PR — same endpoint on GitHub)."""
        return await self._request(
            "POST",
            f"/repos/{repo_full_name}/issues/{issue_number}/labels",
            json={"labels": list(labels)},
        )
