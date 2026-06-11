"""HTTP-level tests for the Wave F.2 repo-browse routes.

Covers:
- GET /github/repo/tree — returns FE-shaped recursive payload + extracts
  truncated flag from raw GitHub response.
- GET /github/repo/tree — 409 ``github_not_connected`` when the credential
  row is missing.
- GET /github/repo/tree — 60s cache hits skip the GitHub call (per
  installation rate-limit budget).
- GET /github/repo/file — base64 → UTF-8 decode for text files.
- GET /github/repo/file — 413 ``file_too_large`` when GitHub reports size
  over 1 MiB.
- GET /github/repo/file — binary (UTF-8 decode failure) returns
  ``{content: null, binary: true}`` so the FE can render a "cannot
  preview" placeholder.

Mocking strategy: patches ``installation_access_token`` so we don't mint
real App JWTs, and patches ``GitHubClient.get_repo_tree`` /
``get_file_contents`` directly to return synthetic responses. This goes
deeper than mocking ``fetch_repo_tree`` itself so the cache + decode
paths inside the helper are exercised.
"""
from __future__ import annotations

import base64
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from planning_studio_service.connectors import store as connectors_store
from planning_studio_service.connectors.github import repo_browse

try:
    from ._github_helpers import make_test_rsa_pem
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _github_helpers import make_test_rsa_pem  # type: ignore[no-redef]
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


_TEST_SESSION_SECRET = "test-session-secret-do-not-use-in-prod"


def _github_env(rsa_pem: str) -> dict[str, str]:
    return {
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_PRIVATE_KEY": rsa_pem,
        "GITHUB_APP_SLUG": "inspira-test",
        "GITHUB_APP_CLIENT_ID": "Iv1.fake",
        "GITHUB_APP_CLIENT_SECRET": "ghs_fake",
        "INSPIRA_SESSION_SECRET": _TEST_SESSION_SECRET,
    }


def _seed_github_credential(
    store, *, workspace_id: str, owner: str = "acme", repo: str = "demo",
) -> None:
    """Plant a connected credential row + default destination so the
    repo-browse routes resolve the workspace's repo."""
    connectors_store.upsert_credential(
        store,
        workspace_id=workspace_id,
        provider="github",
        encrypted_token="ct",
        installation_id="INST-001",
    )
    connectors_store.set_credential_metadata(
        store,
        workspace_id=workspace_id,
        provider="github",
        metadata={"default_owner": owner, "default_repo": repo},
    )


async def _fake_install_token(**kwargs):
    """Drop-in for ``installation_access_token`` — returns a synthetic
    token + a far-future expiry so the helper's mint path completes
    without touching real GitHub."""
    return ("synthetic-token", datetime(2099, 1, 1, tzinfo=timezone.utc))


class _RepoBrowseBase(unittest.TestCase):
    """Seeds GitHub env + a workspace with a connected credential.

    Each test starts with a cold ``_TREE_CACHE`` so the 60s in-process
    cache from one test doesn't bleed into the next.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.rsa_pem = make_test_rsa_pem()
        cls._env_patch = patch.dict("os.environ", _github_env(cls.rsa_pem))
        cls._env_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env_patch.stop()

    def setUp(self) -> None:
        repo_browse.reset_cache_for_tests()
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.user = signup_and_login(
            self.client,
            email="admin@acme.com",
            password="password123",
            display_name="Admin",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        repo_browse.reset_cache_for_tests()


class RepoTreeTests(_RepoBrowseBase):

    def test_repo_tree_returns_full_recursive_tree(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        fake_raw_tree = {
            "sha": "treesha123",
            "truncated": False,
            "tree": [
                {
                    "path": "README.md",
                    "type": "blob",
                    "size": 42,
                    "mode": "100644",
                    "sha": "blobsha1",
                    "url": "https://api.github.com/...",
                },
                {
                    "path": "src",
                    "type": "tree",
                    "mode": "040000",
                    "sha": "treesha2",
                    "url": "https://api.github.com/...",
                },
                {
                    "path": "src/app.tsx",
                    "type": "blob",
                    "size": 1024,
                    "mode": "100644",
                    "sha": "blobsha3",
                    "url": "https://api.github.com/...",
                },
            ],
        }
        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_tree",
            new=AsyncMock(return_value=fake_raw_tree),
        ):
            resp = self.client.get(
                "/api/v2/connectors/github/repo/tree",
                params={"ref": "main", "recursive": "true"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["repo_full_name"], "acme/demo")
        self.assertEqual(body["ref"], "main")
        self.assertEqual(body["sha"], "treesha123")
        self.assertFalse(body["truncated"])
        # FE-shaped subset only — mode/sha/url dropped.
        self.assertEqual(len(body["tree"]), 3)
        first = body["tree"][0]
        self.assertEqual(first["path"], "README.md")
        self.assertEqual(first["type"], "blob")
        self.assertEqual(first["size"], 42)
        self.assertNotIn("mode", first)
        self.assertNotIn("url", first)

    def test_repo_tree_surfaces_truncated_flag(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        fake_raw_tree = {
            "sha": "treesha",
            "truncated": True,  # GitHub truncates over 100k entries
            "tree": [{"path": "README.md", "type": "blob", "size": 10}],
        }
        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_tree",
            new=AsyncMock(return_value=fake_raw_tree),
        ):
            resp = self.client.get(
                "/api/v2/connectors/github/repo/tree",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["truncated"])

    def test_repo_tree_409_when_no_connector(self) -> None:
        # No credential seeded — should 409 immediately, no GitHub call.
        resp = self.client.get(
            "/api/v2/connectors/github/repo/tree",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["detail"]["error"], "github_not_connected"
        )

    def test_repo_tree_409_when_no_default_destination(self) -> None:
        # Credential exists but ``default_owner`` / ``default_repo``
        # not yet set — partner connected but didn't pick a repo.
        connectors_store.upsert_credential(
            self.store,
            workspace_id=self.workspace_id,
            provider="github",
            encrypted_token="ct",
            installation_id="INST-001",
        )
        resp = self.client.get(
            "/api/v2/connectors/github/repo/tree",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json()["detail"]["error"], "github_not_connected"
        )

    def test_repo_tree_cache_hit_skips_github(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        fake_raw_tree = {
            "sha": "treesha",
            "truncated": False,
            "tree": [{"path": "README.md", "type": "blob", "size": 10}],
        }
        token_mock = AsyncMock(side_effect=_fake_install_token)
        tree_mock = AsyncMock(return_value=fake_raw_tree)
        with patch.object(
            repo_browse, "installation_access_token", new=token_mock,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_tree",
            new=tree_mock,
        ):
            r1 = self.client.get(
                "/api/v2/connectors/github/repo/tree",
            )
            r2 = self.client.get(
                "/api/v2/connectors/github/repo/tree",
            )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        # Cache hit on r2 → no second token mint, no second tree fetch.
        self.assertEqual(token_mock.await_count, 1)
        self.assertEqual(tree_mock.await_count, 1)


class RepoFileTests(_RepoBrowseBase):

    def test_repo_file_returns_decoded_content(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        text = "hello world\n"
        fake_file = {
            "name": "README.md",
            "path": "README.md",
            "sha": "blobsha",
            "size": len(text.encode("utf-8")),
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        }
        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_file_contents",
            new=AsyncMock(return_value=fake_file),
        ):
            resp = self.client.get(
                "/api/v2/connectors/github/repo/file",
                params={"path": "README.md", "ref": "main"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["path"], "README.md")
        self.assertEqual(body["content"], text)
        self.assertFalse(body["binary"])
        self.assertEqual(body["sha"], "blobsha")
        self.assertEqual(body["encoding"], "utf-8")

    def test_repo_file_413_when_oversized(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        oversized = {
            "name": "big.bin",
            "path": "big.bin",
            "sha": "blobsha",
            "size": 2_000_000,  # 2 MB, GitHub Contents inline cap is 1 MB
            "content": "",
            "encoding": "base64",
        }
        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_file_contents",
            new=AsyncMock(return_value=oversized),
        ):
            resp = self.client.get(
                "/api/v2/connectors/github/repo/file",
                params={"path": "big.bin"},
            )
        self.assertEqual(resp.status_code, 413)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error"], "file_too_large")
        self.assertEqual(detail["size"], 2_000_000)

    def test_repo_file_handles_binary_gracefully(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        # Non-UTF-8 bytes — strict decode fails, route flips to binary.
        raw_bytes = b"\xff\xfe\x00\x01\xff\xfe"
        fake_file = {
            "name": "logo.png",
            "path": "logo.png",
            "sha": "blobsha",
            "size": len(raw_bytes),
            "content": base64.b64encode(raw_bytes).decode("ascii"),
            "encoding": "base64",
        }
        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_file_contents",
            new=AsyncMock(return_value=fake_file),
        ):
            resp = self.client.get(
                "/api/v2/connectors/github/repo/file",
                params={"path": "logo.png"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["content"])
        self.assertTrue(body["binary"])
        self.assertEqual(body["encoding"], "base64")

    def test_repo_file_404_when_path_missing(self) -> None:
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )
        # GitHub Contents returns None inside ``get_file_contents`` when
        # the path 404s — surface as a clean 404 with a typed error.
        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_file_contents",
            new=AsyncMock(return_value=None),
        ):
            resp = self.client.get(
                "/api/v2/connectors/github/repo/file",
                params={"path": "missing.txt"},
            )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.json()["detail"]["error"], "file_not_found"
        )


if __name__ == "__main__":
    unittest.main()
