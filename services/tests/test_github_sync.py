"""Unit tests for ``connectors.github.sync.sync_workspace`` (W2 C2).

Covers:
- Happy path: top-3 repos picked + 3 snapshots upserted +
  sync_run closes with status='ok'.
- Idempotency: re-running the sync replaces snapshot rows in
  place (no duplicates) — pinned by composite PK
  (workspace_id, provider, repo_id) + INSERT...ON CONFLICT.
- 401 on installation_repos → mark credential needs_reauth +
  close run as needs_reauth.
- Rate limit on installation_repos → close run rate_limited.
- Per-repo 404 → skipped silently, sync continues with the rest.
- No credential → returns skipped without opening a sync_run row.
"""
from __future__ import annotations

import unittest
from urllib.parse import urlsplit

import httpx

from planning_studio_service.connectors import store as connectors_store
from planning_studio_service.connectors.github.app_jwt import (
    GitHubAppConfig,
)
from planning_studio_service.connectors.github.sync import sync_workspace
from planning_studio_service.workspaces.store import create_workspace

try:
    from ._github_helpers import make_test_rsa_pem
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _github_helpers import make_test_rsa_pem  # type: ignore[no-redef]
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _ok_install_token_response() -> httpx.Response:
    from datetime import datetime, timedelta, timezone

    return httpx.Response(
        201,
        json={
            "token": "ghs_install_xyz",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat().replace("+00:00", "Z"),
        },
    )


def _ok_repo_payload(repo_id: int, full_name: str) -> dict:
    return {
        "id": repo_id,
        "full_name": full_name,
        "default_branch": "main",
        "private": True,
        "pushed_at": f"2026-05-0{repo_id}T00:00:00Z",
    }


class SyncWorkspaceSetUp(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key_pem = make_test_rsa_pem()
        cls.config = GitHubAppConfig(
            app_id="12345",
            private_key_pem=cls.private_key_pem,
            app_slug="inspira-test",
        )

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        owner = signup_and_login(
            self.client,
            email="owner@acme.com",
            password="password123",
            display_name="Owner",
        )
        self.workspace = create_workspace(
            self.store,
            owner_user_id=owner["user_id"],
            slug="acme",
            name="Acme",
        )
        # Seed a credential so sync_workspace finds something.
        connectors_store.upsert_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
            encrypted_token="ct",
            installation_id="INST-001",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class SyncHappyPathTests(SyncWorkspaceSetUp):

    async def test_top3_repos_get_snapshotted(self) -> None:
        repos_payload = [
            _ok_repo_payload(1, "acme/alpha"),
            _ok_repo_payload(2, "acme/beta"),
            _ok_repo_payload(3, "acme/gamma"),
            _ok_repo_payload(4, "acme/delta"),  # 4th — should be skipped
        ]

        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("/access_tokens"):
                return _ok_install_token_response()
            if path == "/installation/repositories":
                return httpx.Response(
                    200,
                    json={
                        "total_count": 4,
                        "repositories": repos_payload,
                    },
                )
            if "/git/trees/" in path:
                return httpx.Response(200, json={"tree": []})
            if path.endswith("/issues"):
                return httpx.Response(200, json=[])
            if path.endswith("/commits"):
                return httpx.Response(200, json=[])
            return httpx.Response(404, json={"message": "Unexpected"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            result = await sync_workspace(
                store=self.store,
                workspace_id=self.workspace.workspace_id,
                trigger="install",
                config=self.config,
                http=http,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["repos_synced"], 3)
        snapshots = connectors_store.list_repo_snapshots(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(len(snapshots), 3)
        # Top 3 by pushed_at descending → repo_id 4, 3, 2 (since 4th
        # in payload has the highest digit). Wait — pushed_at uses
        # the same digit, so order is alpha-pushed-at: 4, 3, 2 are
        # the top 3.
        synced_ids = {s["repo_id"] for s in snapshots}
        self.assertEqual(synced_ids, {"4", "3", "2"})

    async def test_resync_idempotent_no_duplicates(self) -> None:
        """Re-running the sync replaces snapshot rows in place
        (composite PK upsert). W2 watch point #4."""
        repos_payload = [_ok_repo_payload(1, "acme/alpha")]

        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("/access_tokens"):
                return _ok_install_token_response()
            if path == "/installation/repositories":
                return httpx.Response(
                    200,
                    json={"repositories": repos_payload},
                )
            if "/git/trees/" in path:
                return httpx.Response(200, json={"tree": []})
            if path.endswith("/issues"):
                return httpx.Response(200, json=[])
            if path.endswith("/commits"):
                return httpx.Response(200, json=[])
            return httpx.Response(404, json={})

        # First sync.
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            await sync_workspace(
                store=self.store,
                workspace_id=self.workspace.workspace_id,
                trigger="manual",
                config=self.config,
                http=http,
            )
        # Second sync.
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            await sync_workspace(
                store=self.store,
                workspace_id=self.workspace.workspace_id,
                trigger="manual",
                config=self.config,
                http=http,
            )
        # Still exactly one snapshot row — composite PK absorbed
        # the second insert into an UPDATE.
        snapshots = connectors_store.list_repo_snapshots(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        self.assertEqual(len(snapshots), 1)


class SyncFailurePathTests(SyncWorkspaceSetUp):

    async def test_401_marks_needs_reauth(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("/access_tokens"):
                return _ok_install_token_response()
            if path == "/installation/repositories":
                return httpx.Response(
                    401, json={"message": "Bad credentials"}
                )
            return httpx.Response(404, json={})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            result = await sync_workspace(
                store=self.store,
                workspace_id=self.workspace.workspace_id,
                trigger="manual",
                config=self.config,
                http=http,
            )
        self.assertEqual(result["status"], "needs_reauth")

        cred = connectors_store.get_credential(
            self.store,
            workspace_id=self.workspace.workspace_id,
            provider="github",
        )
        assert cred is not None
        self.assertEqual(cred["status"], "needs_reauth")

    async def test_rate_limit_closes_run_as_rate_limited(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("/access_tokens"):
                return _ok_install_token_response()
            if path == "/installation/repositories":
                return httpx.Response(
                    429,
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "9999999999",
                    },
                    json={"message": "Rate limit"},
                )
            return httpx.Response(404, json={})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            result = await sync_workspace(
                store=self.store,
                workspace_id=self.workspace.workspace_id,
                trigger="scheduled",
                config=self.config,
                http=http,
            )
        self.assertEqual(result["status"], "rate_limited")

    async def test_per_repo_404_skipped_others_continue(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("/access_tokens"):
                return _ok_install_token_response()
            if path == "/installation/repositories":
                return httpx.Response(
                    200,
                    json={
                        "repositories": [
                            _ok_repo_payload(1, "acme/alpha"),
                            _ok_repo_payload(2, "acme/beta"),
                        ]
                    },
                )
            # Tree on alpha 404s; everything else OK.
            if path.startswith("/repos/acme/alpha"):
                return httpx.Response(404, json={})
            if "/git/trees/" in path:
                return httpx.Response(200, json={"tree": []})
            if path.endswith("/issues"):
                return httpx.Response(200, json=[])
            if path.endswith("/commits"):
                return httpx.Response(200, json=[])
            return httpx.Response(404, json={})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            result = await sync_workspace(
                store=self.store,
                workspace_id=self.workspace.workspace_id,
                trigger="manual",
                config=self.config,
                http=http,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["repos_synced"], 1)
        self.assertEqual(result["skipped"], 1)


class SyncWithoutCredentialTests(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = GitHubAppConfig(
            app_id="12345",
            private_key_pem=make_test_rsa_pem(),
            app_slug="inspira-test",
        )

    async def test_skipped_with_no_credential(self) -> None:
        client, store, adapter, temp_dir = make_test_app()
        try:
            owner = signup_and_login(
                client,
                email="solo@acme.com",
                password="password123",
                display_name="Solo",
            )
            ws = create_workspace(
                store,
                owner_user_id=owner["user_id"],
                slug="solo",
                name="Solo",
            )

            def handler(req: httpx.Request) -> httpx.Response:
                self.fail("HTTP should not be called when no credential")

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as http:
                result = await sync_workspace(
                    store=store,
                    workspace_id=ws.workspace_id,
                    trigger="manual",
                    config=self.config,
                    http=http,
                )
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "no_credential")
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
