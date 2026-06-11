"""HTTP + store tests for the Wave F.5 staleness path.

Covers:
- ``/api/v2/projects/{id}/pr-overlay-staleness``:
  - returns ``is_stale=False`` when current main matches the recorded
    ``base_main_sha`` (no compare_commits call made)
  - returns ``is_stale=True`` when the SHAs diverge AND the GitHub diff
    overlaps the project's scaffold paths
  - intersects compare-API files with the scaffold manifest — drift in
    unrelated paths does not flip ``is_stale``
  - pre-F.5 projects (NULL ``base_main_sha``) get ``legacy=True``
    + ``is_stale=False`` and no GitHub call
  - 404 ``project_not_found`` when the project is missing
- Store-layer:
  - ``set_project_last_partner_edit`` writes the timestamp
  - ``set_project_base_main_sha`` is idempotent (second call no-ops)

Mocking strategy mirrors test_pr_overlay: patch
``staleness.installation_access_token`` so we don't mint real App JWTs,
then patch ``GitHubClient.get_repo_metadata`` /
``GitHubClient.get_branch_sha`` / ``GitHubClient.compare_commits`` to
return synthetic responses. ``base_main_sha`` is seeded via the new
store setter so the path through ``compute_staleness`` matches what
``build_overlay_tree``'s write-through actually does in production.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from planning_studio_service.connectors.github import (
    pr_overlay,
    repo_browse,
    staleness,
)

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
    from planning_studio_service.connectors import store as connectors_store

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
) -> None:
    import json as _json

    with store._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT metadata_json FROM v2_projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        metadata = _json.loads((row["metadata_json"] if row else None) or "{}")
        metadata["dominant_category"] = "feature"
        conn.execute(
            "UPDATE v2_projects SET workspace_id = ?, metadata_json = ? "
            "WHERE project_id = ?",
            (workspace_id, _json.dumps(metadata), project_id),
        )
        conn.commit()


def _seed_scaffold(
    store, *, project_id: str, user_id: str,
    files: list[dict[str, str]],
) -> str:
    import json as _json

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
        manifest_json=_json.dumps(manifest),
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


def _patch_github_calls(
    *,
    default_branch: str = "main",
    current_main_sha: str = "current-sha-bbb",
    compare_files: list[dict[str, object]] | None = None,
    compare_commits: list[dict[str, object]] | None = None,
):
    """Context-manager tuple: stubs token mint + repo metadata + branch
    SHA + compare. Tests unpack and use ``with`` for all four.

    ``compare_files=None`` means ``compare_commits`` is NOT expected to
    be called (the test path stays in the SHA-equal short-circuit). When
    the test does expect compare, pass an explicit list.
    """
    metadata_body = {"default_branch": default_branch}
    compare_body: dict[str, object] = {
        "status": "ahead" if compare_files else "identical",
        "files": compare_files or [],
        "commits": compare_commits or [
            {
                "sha": current_main_sha,
                "commit": {
                    "committer": {"date": "2026-05-13T15:30:00Z"},
                },
            },
        ],
    }
    return (
        patch.object(
            staleness, "installation_access_token", new=_fake_install_token,
        ),
        patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_metadata",
            new=AsyncMock(return_value=metadata_body),
        ),
        patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_branch_sha",
            new=AsyncMock(return_value=current_main_sha),
        ),
        patch(
            "planning_studio_service.connectors.github.client.GitHubClient.compare_commits",
            new=AsyncMock(return_value=compare_body),
        ),
    )


class _StalenessBase(unittest.TestCase):
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
        staleness.reset_cache_for_tests()
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
        )
        _seed_github_credential(
            self.store, workspace_id=self.workspace_id,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        repo_browse.reset_cache_for_tests()
        pr_overlay.reset_cache_for_tests()
        staleness.reset_cache_for_tests()


# --------------------------------------------------------------------
# /pr-overlay-staleness — happy + drift paths
# --------------------------------------------------------------------


class StalenessRouteTests(_StalenessBase):

    def test_staleness_returns_not_stale_when_base_sha_matches_current_main(
        self,
    ) -> None:
        # Recorded baseline equals what GitHub will return as current head.
        self.store.set_project_base_main_sha(
            project_id=self.project_id,
            base_main_sha="match-sha-111",
        )
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "src/checkout/Pricing.tsx", "content": "..."},
            ],
        )
        token_p, meta_p, sha_p, compare_p = _patch_github_calls(
            current_main_sha="match-sha-111",
            compare_files=None,  # not used on the equality short-circuit
        )
        with token_p, meta_p, sha_p, compare_p:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-staleness",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["is_stale"])
        self.assertEqual(body["base_main_sha"], "match-sha-111")
        self.assertEqual(body["current_main_sha"], "match-sha-111")
        self.assertEqual(body["affected_files_count"], 0)
        self.assertEqual(body["scaffold_files_count"], 1)
        self.assertFalse(body["legacy"])

    def test_staleness_returns_stale_when_base_sha_diverges(self) -> None:
        self.store.set_project_base_main_sha(
            project_id=self.project_id,
            base_main_sha="old-sha-aaa",
        )
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "src/checkout/Pricing.tsx", "content": "..."},
                {"path": "src/checkout/Cart.tsx", "content": "..."},
            ],
        )
        token_p, meta_p, sha_p, compare_p = _patch_github_calls(
            current_main_sha="new-sha-bbb",
            compare_files=[
                # One file in scope (overlaps scaffold) + one out of scope.
                {"filename": "src/checkout/Pricing.tsx", "status": "modified"},
                {"filename": "docs/architecture.md", "status": "modified"},
            ],
        )
        with token_p, meta_p, sha_p, compare_p:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-staleness",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["is_stale"])
        self.assertEqual(body["base_main_sha"], "old-sha-aaa")
        self.assertEqual(body["current_main_sha"], "new-sha-bbb")
        self.assertEqual(body["affected_files_count"], 1)
        self.assertEqual(body["scaffold_files_count"], 2)
        self.assertIn(
            "src/checkout/Pricing.tsx", body["affected_paths_sample"],
        )
        self.assertNotIn(
            "docs/architecture.md", body["affected_paths_sample"],
        )
        self.assertEqual(body["main_moved_at"], "2026-05-13T15:30:00Z")

    def test_staleness_counts_only_files_intersecting_scaffold(self) -> None:
        # Many changed files on main, none touching the scaffold — should
        # report SHAs diverged but ``is_stale=False`` because no overlap.
        self.store.set_project_base_main_sha(
            project_id=self.project_id,
            base_main_sha="old-sha-aaa",
        )
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "src/checkout/Pricing.tsx", "content": "..."},
            ],
        )
        token_p, meta_p, sha_p, compare_p = _patch_github_calls(
            current_main_sha="new-sha-bbb",
            compare_files=[
                {"filename": "docs/README.md", "status": "modified"},
                {"filename": "services/api.py", "status": "modified"},
                {"filename": "app/src/HomePage.tsx", "status": "modified"},
            ],
        )
        with token_p, meta_p, sha_p, compare_p:
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-staleness",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # Drift in unrelated corners doesn't dirty this overlay.
        self.assertFalse(body["is_stale"])
        self.assertEqual(body["affected_files_count"], 0)
        self.assertEqual(body["scaffold_files_count"], 1)
        self.assertEqual(body["affected_paths_sample"], [])

    def test_staleness_handles_missing_base_main_sha_returns_legacy_flag(
        self,
    ) -> None:
        # No base_main_sha recorded → legacy=True, is_stale=False, and
        # NO GitHub calls made. We assert this by patching the client
        # methods with AsyncMocks that would fail the test if called.
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "src/checkout/Pricing.tsx", "content": "..."},
            ],
        )
        get_meta = AsyncMock(side_effect=AssertionError(
            "get_repo_metadata should not be called for legacy projects",
        ))
        get_sha = AsyncMock(side_effect=AssertionError(
            "get_branch_sha should not be called for legacy projects",
        ))
        compare = AsyncMock(side_effect=AssertionError(
            "compare_commits should not be called for legacy projects",
        ))
        with patch.object(
            staleness, "installation_access_token", new=_fake_install_token,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_repo_metadata",
            new=get_meta,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.get_branch_sha",
            new=get_sha,
        ), patch(
            "planning_studio_service.connectors.github.client.GitHubClient.compare_commits",
            new=compare,
        ):
            resp = self.client.get(
                f"/api/v2/projects/{self.project_id}/pr-overlay-staleness",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["legacy"])
        self.assertFalse(body["is_stale"])
        self.assertIsNone(body["base_main_sha"])
        self.assertIsNone(body["current_main_sha"])

    def test_staleness_404_when_project_not_found(self) -> None:
        resp = self.client.get(
            "/api/v2/projects/does-not-exist/pr-overlay-staleness",
        )
        self.assertEqual(resp.status_code, 404, resp.text)


# --------------------------------------------------------------------
# Store-layer tests for the new setters
# --------------------------------------------------------------------


class StalenessStoreTests(_StalenessBase):

    def test_set_last_partner_edit_updates_timestamp(self) -> None:
        # Pass an explicit timestamp for deterministic assertion.
        stamp = "2026-05-14T10:00:00+00:00"
        result = self.store.set_project_last_partner_edit(
            project_id=self.project_id, ts=stamp,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["last_partner_edit"], stamp)
        # Round-trip via _get_v2_project so we also exercise the
        # widened SELECT column list.
        row = self.store._get_v2_project(self.project_id)  # noqa: SLF001
        self.assertEqual(row["last_partner_edit"], stamp)

    def test_set_project_base_main_sha_is_idempotent(self) -> None:
        # First write fills the NULL column.
        first = self.store.set_project_base_main_sha(
            project_id=self.project_id, base_main_sha="snapshot-aaa",
        )
        self.assertEqual(first["base_main_sha"], "snapshot-aaa")

        # Second write with a DIFFERENT SHA must NOT overwrite —
        # preserves the original snapshot across overlay rebuilds.
        second = self.store.set_project_base_main_sha(
            project_id=self.project_id, base_main_sha="should-not-win-bbb",
        )
        self.assertEqual(second["base_main_sha"], "snapshot-aaa")

        # Direct DB read confirms.
        row = self.store._get_v2_project(self.project_id)  # noqa: SLF001
        self.assertEqual(row["base_main_sha"], "snapshot-aaa")


if __name__ == "__main__":
    unittest.main()
