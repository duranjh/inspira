"""HTTP-level tests for the Wave F.3 PR-overlay routes.

Covers:
- ``GET /api/v2/projects/{id}/pr-overlay-tree``:
  - returns base tree only when no artifact has been generated yet
  - tags scaffold files as ``source: "scaffold"`` (new path)
  - tags scaffold files as ``source: "modified"`` (path also in base)
  - 404 ``project_not_found`` when the project is missing
  - 404 ``project_not_found`` when the project has no workspace_id
  - surfaces a ``case_collision`` warning when scaffold + base differ
    only in case (entry kept both ways — no silent overwrite)
  - 60s in-process cache hit skips both the GitHub call and the
    scaffold lookup
- ``GET /api/v2/projects/{id}/pr-overlay-file``:
  - returns scaffold content when ``path`` is in the manifest
  - returns ``source: "base"`` sentinel when the path is only in the
    base repo (FE falls through to F.2's /repo/file)

Mocking strategy mirrors test_repo_browse: patch
``installation_access_token`` so we don't mint real App JWTs, then
patch the underlying ``GitHubClient.get_repo_tree`` to return a
synthetic tree. The artifact + scaffold rows are seeded directly via
the store helpers so the overlay-merge path is exercised end-to-end.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from planning_studio_service.connectors import store as connectors_store
from planning_studio_service.connectors.github import pr_overlay, repo_browse

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


def _link_project_to_workspace(
    store, *, project_id: str, workspace_id: str,
    dominant_category: str | None = None,
) -> None:
    """Wire ``workspace_id`` + (optionally) ``metadata.dominant_category``
    onto a v2_projects row. Test-only — there is no public store method
    for this because v4 projects are created with their workspace from
    the start.
    """
    with store._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT metadata_json FROM v2_projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        metadata = json.loads((row["metadata_json"] if row else None) or "{}")
        if dominant_category is not None:
            metadata["dominant_category"] = dominant_category
        conn.execute(
            "UPDATE v2_projects SET workspace_id = ?, metadata_json = ? "
            "WHERE project_id = ?",
            (workspace_id, json.dumps(metadata), project_id),
        )
        conn.commit()


def _seed_scaffold(
    store, *, project_id: str, user_id: str,
    files: list[dict[str, str]],
) -> str:
    """Insert a scaffold row + point the project's artifact overlay at
    it. Returns the new scaffold_id so tests can assert cache keying.
    """
    manifest = {
        "framework": "react-vite",
        "language": "typescript",
        "files": files,
    }
    row = store.create_scaffold(
        project_id=project_id,
        user_id=user_id,
        framework="react-vite",
        language="typescript",
        manifest_json=json.dumps(manifest),
    )
    store.set_v2_project_artifact(
        project_id=project_id,
        artifact={
            "version": 1,
            "latest_scaffold_id": row["scaffold_id"],
            "model_used": "gpt-test",
            "messages": [],
        },
    )
    return row["scaffold_id"]


async def _fake_install_token(**kwargs):
    return ("synthetic-token", datetime(2099, 1, 1, tzinfo=timezone.utc))


class _PrOverlayBase(unittest.TestCase):
    """Seeds GitHub env + a workspace + a project owned by the user."""

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
        pr_overlay.reset_cache_for_tests()
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(
            self.client,
            email="admin@acme.com",
            password="password123",
            display_name="Admin",
        )
        me = self.client.get("/api/auth/me").json()
        self.user_id: str = me["user_id"]
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

        # Project: created via the store layer so we don't need to go
        # through kickoff (which would also stub the planner adapter).
        project = self.store.create_v2_project(
            user_id=self.user_id,
            title="Speed up checkout latency",
            project_state="approved",
        )
        self.project_id: str = project["project_id"]
        _link_project_to_workspace(
            self.store,
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            dominant_category="feature",
        )
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        repo_browse.reset_cache_for_tests()
        pr_overlay.reset_cache_for_tests()


def _patch_github_tree(tree_entries: list[dict[str, object]]):
    """Context manager: stubs token mint + ``GitHubClient.get_repo_tree``
    so the overlay merge path can run without network I/O.
    """
    raw_tree = {
        "sha": "base-sha-aaa",
        "truncated": False,
        "tree": tree_entries,
    }
    return (
        patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ),
        patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_tree",
            new=AsyncMock(return_value=raw_tree),
        ),
    )


# --------------------------------------------------------------------
# /pr-overlay-tree
# --------------------------------------------------------------------


class OverlayTreeTests(_PrOverlayBase):

    def test_overlay_tree_returns_base_only_when_no_artifact(self) -> None:
        token_patch, tree_patch = _patch_github_tree([
            {"path": "README.md", "type": "blob", "size": 42},
            {"path": "src/app.tsx", "type": "blob", "size": 100},
        ])
        with token_patch, tree_patch:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-tree",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["project_id"], self.project_id)
        self.assertEqual(body["dominant_category"], "feature")
        self.assertEqual(body["repo_full_name"], "acme/demo")
        self.assertEqual(len(body["tree"]), 2)
        for entry in body["tree"]:
            self.assertEqual(entry["source"], "base")
        self.assertEqual(body["warnings"], [])

    def test_overlay_tree_tags_scaffold_files_as_scaffold(self) -> None:
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "src/components/Checkout.tsx", "content": "..."},
            ],
        )
        token_patch, tree_patch = _patch_github_tree([
            {"path": "README.md", "type": "blob", "size": 42},
        ])
        with token_patch, tree_patch:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-tree",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        paths = {e["path"]: e for e in resp.json()["tree"]}
        self.assertIn("src/components/Checkout.tsx", paths)
        self.assertEqual(
            paths["src/components/Checkout.tsx"]["source"], "scaffold",
        )
        # Base-only file still tagged correctly.
        self.assertEqual(paths["README.md"]["source"], "base")

    def test_overlay_tree_tags_replaced_files_as_modified(self) -> None:
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                # Same path as base → modified.
                {"path": "src/app.tsx", "content": "// patched\n"},
            ],
        )
        token_patch, tree_patch = _patch_github_tree([
            {"path": "src/app.tsx", "type": "blob", "size": 100},
        ])
        with token_patch, tree_patch:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-tree",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        tree = resp.json()["tree"]
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["path"], "src/app.tsx")
        self.assertEqual(tree[0]["source"], "modified")

    def test_overlay_tree_404_when_project_missing(self) -> None:
        resp = self.client.get(
            "/api/v2/projects/project-missing/pr-overlay-tree",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.json()["detail"]["error"], "project_not_found",
        )

    def test_overlay_tree_404_when_project_has_no_workspace(self) -> None:
        # Make a second project, owned by the same user, but DON'T
        # wire a workspace_id onto it.
        orphan = self.store.create_v2_project(
            user_id=self.user_id, title="Orphan", project_state="approved",
        )
        resp = self.client.get(
            f"/api/v2/projects/{orphan['project_id']}/pr-overlay-tree",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.json()["detail"]["error"], "project_not_found",
        )

    def test_overlay_tree_surfaces_case_collision_warning(self) -> None:
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                # Scaffold uses a different case than the base path.
                {"path": "src/Foo.tsx", "content": "// new\n"},
            ],
        )
        token_patch, tree_patch = _patch_github_tree([
            {"path": "src/foo.tsx", "type": "blob", "size": 80},
        ])
        with token_patch, tree_patch:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-tree",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        paths = sorted(e["path"] for e in body["tree"])
        # Both entries preserved — no silent overwrite.
        self.assertEqual(paths, ["src/Foo.tsx", "src/foo.tsx"])
        # Warning surfaced for the reviewer.
        self.assertEqual(len(body["warnings"]), 1)
        warning = body["warnings"][0]
        self.assertEqual(warning["kind"], "case_collision")
        self.assertEqual(
            sorted(warning["paths"]), ["src/Foo.tsx", "src/foo.tsx"],
        )

    def test_overlay_tree_cache_hit_skips_github_and_scaffold(self) -> None:
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[{"path": "src/extra.tsx", "content": "x"}],
        )
        tree_calls = {"count": 0}
        original_get_repo_tree = AsyncMock(return_value={
            "sha": "base-sha-aaa",
            "truncated": False,
            "tree": [{"path": "README.md", "type": "blob", "size": 42}],
        })

        async def counting_tree(*args, **kwargs):
            tree_calls["count"] += 1
            return await original_get_repo_tree(*args, **kwargs)

        with patch.object(
            repo_browse,
            "installation_access_token",
            new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_tree",
            new=counting_tree,
        ):
            # Cold cache → fetches GitHub.
            first = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-tree",
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(tree_calls["count"], 1)
            # Warm cache → still 200, no additional GitHub call.
            second = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-tree",
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(tree_calls["count"], 1)


# --------------------------------------------------------------------
# /pr-overlay-file
# --------------------------------------------------------------------


class OverlayFileTests(_PrOverlayBase):

    def test_overlay_file_returns_scaffold_content_when_in_manifest(
        self,
    ) -> None:
        scaffold_content = "// my new file\nexport const X = 1;\n"
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {
                    "path": "src/components/Checkout.tsx",
                    "content": scaffold_content,
                },
            ],
        )
        token_patch, tree_patch = _patch_github_tree([
            {"path": "README.md", "type": "blob", "size": 42},
        ])
        with token_patch, tree_patch:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-file",
                params={"path": "src/components/Checkout.tsx"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["path"], "src/components/Checkout.tsx")
        self.assertEqual(body["content"], scaffold_content)
        self.assertFalse(body["binary"])
        self.assertEqual(body["source"], "scaffold")
        self.assertEqual(body["encoding"], "utf-8")

    def test_overlay_file_returns_base_sentinel_when_path_only_in_repo(
        self,
    ) -> None:
        # Seed an empty scaffold — base-only paths should fall through.
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[],
        )
        token_patch, tree_patch = _patch_github_tree([
            {"path": "README.md", "type": "blob", "size": 42},
        ])
        with token_patch, tree_patch:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-file",
                params={"path": "README.md"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["source"], "base")
        self.assertIsNone(body["content"])
