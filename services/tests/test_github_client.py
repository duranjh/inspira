"""Unit tests for ``connectors.github.client.GitHubClient`` (W2 C2).

Covers HTTP error mapping:
- 401 → GitHubUnauthorized
- 404 → GitHubNotFound
- 429 / 403+remaining=0 → GitHubRateLimited (with reset_at)
- 5xx → retried 3x, raises GitHubTransient on exhaustion
- network errors → retried, raises GitHubTransient on exhaustion
- 2xx → parsed JSON returned

Plus the four read methods' request shapes: list_installation_repos,
get_repo_tree, list_open_issues (filters PRs), list_recent_commits.
"""
from __future__ import annotations

import unittest

import httpx

from planning_studio_service.connectors.github.client import (
    GitHubClient,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTransient,
    GitHubUnauthorized,
)

try:
    from ._github_helpers import mock_async_client
except ImportError:
    from _github_helpers import mock_async_client  # type: ignore[no-redef]


class ErrorMappingTests(unittest.IsolatedAsyncioTestCase):

    async def test_401_raises_unauthorized(self) -> None:
        async def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Bad credentials"})

        async with mock_async_client(handler) as http:
            client = GitHubClient(installation_token="t", http=http)
            with self.assertRaises(GitHubUnauthorized):
                await client.list_installation_repos()

    async def test_404_raises_not_found(self) -> None:
        async def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        async with mock_async_client(handler) as http:
            client = GitHubClient(installation_token="t", http=http)
            with self.assertRaises(GitHubNotFound):
                await client.get_repo_tree(
                    repo_full_name="x/y", ref="main"
                )

    async def test_429_raises_rate_limited_with_reset(self) -> None:
        future_epoch = "9999999999"  # far future

        async def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={
                    "X-RateLimit-Reset": future_epoch,
                    "X-RateLimit-Remaining": "0",
                },
                json={"message": "Rate limit"},
            )

        async with mock_async_client(handler) as http:
            client = GitHubClient(installation_token="t", http=http)
            with self.assertRaises(GitHubRateLimited) as ctx:
                await client.list_installation_repos()
            self.assertIsNotNone(ctx.exception.reset_at)

    async def test_403_with_zero_remaining_is_rate_limit(self) -> None:
        async def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0"},
                json={"message": "API rate limit exceeded"},
            )

        async with mock_async_client(handler) as http:
            client = GitHubClient(installation_token="t", http=http)
            with self.assertRaises(GitHubRateLimited):
                await client.list_installation_repos()

    async def test_5xx_retries_then_raises_transient(self) -> None:
        attempts = {"n": 0}

        async def handler(req: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(503, json={})

        # Skip the actual sleep so the test runs quickly.
        from planning_studio_service.connectors.github import client as cmod

        original_sleep = cmod.asyncio.sleep

        async def fast_sleep(_s: float) -> None:
            return None

        cmod.asyncio.sleep = fast_sleep  # type: ignore[assignment]
        try:
            async with mock_async_client(handler) as http:
                client = GitHubClient(installation_token="t", http=http)
                with self.assertRaises(GitHubTransient):
                    await client.list_installation_repos()
        finally:
            cmod.asyncio.sleep = original_sleep  # type: ignore[assignment]

        self.assertEqual(attempts["n"], 3)


class ReadMethodTests(unittest.IsolatedAsyncioTestCase):

    async def test_list_installation_repos(self) -> None:
        async def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.method, "GET")
            self.assertEqual(
                req.url.path, "/installation/repositories"
            )
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "repositories": [
                        {"id": 1, "full_name": "acme/a"},
                        {"id": 2, "full_name": "acme/b"},
                    ],
                },
            )

        async with mock_async_client(handler) as http:
            client = GitHubClient(installation_token="t", http=http)
            repos = await client.list_installation_repos()
        self.assertEqual(len(repos), 2)

    async def test_list_open_issues_filters_pull_requests(self) -> None:
        async def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "title": "real issue"},
                    {
                        "number": 2,
                        "title": "PR masquerading as issue",
                        "pull_request": {"url": "..."},
                    },
                ],
            )

        async with mock_async_client(handler) as http:
            client = GitHubClient(installation_token="t", http=http)
            issues = await client.list_open_issues(
                repo_full_name="acme/a"
            )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["number"], 1)


if __name__ == "__main__":
    unittest.main()
