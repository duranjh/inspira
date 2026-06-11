"""HTTP + store tests for the Wave F.6 Refresh PR flow (#147).

Covers:
- ``POST /api/v2/projects/{id}/refresh-overlay``:
  - fetches fresh main SHA via get_repo_metadata + get_branch_sha
  - passes the current scaffold as ``previous_scaffold`` kwarg to adapter
  - persists a new scaffold + flips latest_scaffold_id + resets baseline
  - 409 ``refresh_in_progress`` on concurrent kickoff
- ``GET /api/v2/projects/{id}/refresh-diff``:
  - 3-way diff (base + partner_edit + ai_redraft) for partner-edited file
  - 2-way diff (partner_edit=null) for unedited file
- ``POST /api/v2/projects/{id}/refresh-resolve``:
  - applies accept_redraft / keep_partner_edit / merged decisions
- Store-layer:
  - ``update_scaffold_file_content`` captures ``original_content`` on first edit
    + preserves it on subsequent edits
"""
from __future__ import annotations

import json as _json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
    files: list[dict[str, object]],
) -> str:
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


def _patch_refresh_github_calls(
    *,
    default_branch: str = "main",
    current_main_sha: str = "current-sha-bbb",
):
    """Stubs token mint + repo metadata + branch SHA for the refresh path.

    Refresh does NOT call compare_commits (that's staleness's job); it
    only resolves the current main SHA to write back as the new
    baseline. Patches the symbol on the refresh_pr module so the helper
    sees the stub.
    """
    metadata_body = {"default_branch": default_branch}
    return (
        patch(
            "planning_studio_service.agents.refresh_pr."
            "installation_access_token",
            new=_fake_install_token,
        ),
        patch(
            "planning_studio_service.connectors.github.client."
            "GitHubClient.get_repo_metadata",
            new=AsyncMock(return_value=metadata_body),
        ),
        patch(
            "planning_studio_service.connectors.github.client."
            "GitHubClient.get_branch_sha",
            new=AsyncMock(return_value=current_main_sha),
        ),
    )


def _patch_repo_context(
    payload: dict[str, object] | None = None,
):
    """Stub fetch_repo_context — F.6's helper imports it function-local."""
    return patch(
        "planning_studio_service.connectors.github.repo_context."
        "fetch_repo_context",
        new=AsyncMock(return_value=payload),
    )


def _ok_redraft_manifest() -> dict[str, object]:
    """Synthetic adapter response for refresh tests."""
    return {
        "framework": "react-vite",
        "language": "typescript",
        "files": [
            {"path": "README.md", "content": "# Refreshed\n"},
            {"path": "src/main.tsx", "content": "console.log('refreshed')\n"},
        ],
        "readme_preview": "Refreshed.",
        "post_install_steps": ["npm install"],
        "truncation_note": "",
    }


class _RefreshBase(unittest.TestCase):
    """Seeds GitHub env + a workspace + a project + scaffold owned by the user."""

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

        # Inject the scaffold adapter mock so the refresh route's
        # _artifact_resolve_dispatch resolves to it.
        self.scaffold_adapter = MagicMock()
        self.client.app.state.code_scaffold_adapter = self.scaffold_adapter
        self.client.app.state.claude_code_scaffold_adapter = None

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        repo_browse.reset_cache_for_tests()
        pr_overlay.reset_cache_for_tests()
        staleness.reset_cache_for_tests()


# --------------------------------------------------------------------
# POST /refresh-overlay — happy path
# --------------------------------------------------------------------


class RefreshOverlayRouteTests(_RefreshBase):

    def test_refresh_overlay_fetches_fresh_main_sha_and_resets_baseline(
        self,
    ) -> None:
        """The refresh helper resolves the current main SHA and writes
        it as the new baseline via reset_project_base_main_sha, so the
        staleness banner clears on next poll."""
        self.store.set_project_base_main_sha(
            project_id=self.project_id,
            base_main_sha="old-sha-aaa",
        )
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[{"path": "src/main.tsx", "content": "hello"}],
        )
        self.scaffold_adapter.generate.return_value = _ok_redraft_manifest()

        token_p, meta_p, sha_p = _patch_refresh_github_calls(
            current_main_sha="new-sha-ccc",
        )
        with token_p, meta_p, sha_p, _patch_repo_context(None):
            response = self.client.post(
                f"/api/v2/projects/{self.project_id}/refresh-overlay",
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["base_main_sha"], "new-sha-ccc")
        # Baseline reset unconditionally.
        post_row = self.store._get_v2_project(self.project_id)  # noqa: SLF001
        self.assertEqual(post_row["base_main_sha"], "new-sha-ccc")

    def test_refresh_overlay_passes_previous_scaffold_to_adapter(
        self,
    ) -> None:
        """The current scaffold's {path: content} is threaded to the
        adapter as the previous_scaffold kwarg so the LLM can redraft
        on top of partner intent."""
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "README.md", "content": "# Original\n"},
                {"path": "src/index.ts", "content": "// original"},
            ],
        )
        self.scaffold_adapter.generate.return_value = _ok_redraft_manifest()

        token_p, meta_p, sha_p = _patch_refresh_github_calls()
        with token_p, meta_p, sha_p, _patch_repo_context(None):
            response = self.client.post(
                f"/api/v2/projects/{self.project_id}/refresh-overlay",
            )
        self.assertEqual(response.status_code, 200, response.text)

        gen_kwargs = self.scaffold_adapter.generate.call_args.kwargs
        self.assertIn("previous_scaffold", gen_kwargs)
        previous = gen_kwargs["previous_scaffold"]
        self.assertEqual(previous.get("README.md"), "# Original\n")
        self.assertEqual(previous.get("src/index.ts"), "// original")

    def test_refresh_overlay_persists_new_scaffold_and_flips_latest(
        self,
    ) -> None:
        """A new scaffold row lands and the artifact overlay's
        latest_scaffold_id advances to it."""
        old_scaffold_id = _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[{"path": "README.md", "content": "# Old\n"}],
        )
        self.scaffold_adapter.generate.return_value = _ok_redraft_manifest()

        token_p, meta_p, sha_p = _patch_refresh_github_calls()
        with token_p, meta_p, sha_p, _patch_repo_context(None):
            response = self.client.post(
                f"/api/v2/projects/{self.project_id}/refresh-overlay",
            )
        self.assertEqual(response.status_code, 200, response.text)

        rows = self.store.list_scaffolds_for_project(
            project_id=self.project_id, user_id=self.user_id,
        )
        self.assertEqual(len(rows), 2)
        overlay = self.store.get_v2_project_artifact(project_id=self.project_id)
        self.assertNotEqual(overlay["latest_scaffold_id"], old_scaffold_id)
        self.assertEqual(
            overlay["latest_scaffold_id"], response.json()["scaffold_id"],
        )

    def test_refresh_overlay_409_when_already_in_progress(self) -> None:
        """A pre-existing in_progress refresh blocks a second kickoff
        with a deterministic 409 — no race window."""
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[{"path": "README.md", "content": "# Old\n"}],
        )
        # Pre-seed an in_progress row directly to simulate a concurrent
        # POST landing first.
        self.store.create_scaffold_refresh_history(
            project_id=self.project_id,
            base_main_sha_before="old-sha",
            previous_scaffold_id=None,
        )

        token_p, meta_p, sha_p = _patch_refresh_github_calls()
        with token_p, meta_p, sha_p, _patch_repo_context(None):
            response = self.client.post(
                f"/api/v2/projects/{self.project_id}/refresh-overlay",
            )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["error"], "refresh_in_progress",
        )
        # Adapter must NOT have been called.
        self.scaffold_adapter.generate.assert_not_called()


# --------------------------------------------------------------------
# GET /refresh-diff — 3-way vs 2-way
# --------------------------------------------------------------------


class RefreshDiffRouteTests(_RefreshBase):

    def _run_refresh(self) -> str:
        """Helper — POST refresh-overlay and return the refresh_id."""
        token_p, meta_p, sha_p = _patch_refresh_github_calls()
        with token_p, meta_p, sha_p, _patch_repo_context(None):
            response = self.client.post(
                f"/api/v2/projects/{self.project_id}/refresh-overlay",
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["refresh_id"]

    def test_refresh_diff_returns_3way_for_partner_edited_file(
        self,
    ) -> None:
        """A file with non-null original_content (i.e. partner edited
        it since AI generation) gets base/partner_edit/ai_redraft all
        populated."""
        scaffold_id = _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[{"path": "README.md", "content": "# AI original\n"}],
        )
        # Simulate a partner edit — first PATCH captures original.
        self.store.update_scaffold_file_content(
            scaffold_id=scaffold_id,
            user_id=self.user_id,
            path="README.md",
            content="# Partner edit\n",
        )
        self.scaffold_adapter.generate.return_value = _ok_redraft_manifest()
        refresh_id = self._run_refresh()

        response = self.client.get(
            f"/api/v2/projects/{self.project_id}/refresh-diff",
            params={"refresh_id": refresh_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        readme = next(f for f in body["files"] if f["path"] == "README.md")
        self.assertEqual(readme["base"], "# AI original\n")
        self.assertEqual(readme["partner_edit"], "# Partner edit\n")
        self.assertEqual(readme["ai_redraft"], "# Refreshed\n")
        self.assertTrue(readme["conflict"])

    def test_refresh_diff_returns_2way_for_unedited_file(self) -> None:
        """A file never partner-edited has null partner_edit so the FE
        falls through to a 2-way diff."""
        _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[{"path": "README.md", "content": "# Untouched\n"}],
        )
        self.scaffold_adapter.generate.return_value = _ok_redraft_manifest()
        refresh_id = self._run_refresh()

        response = self.client.get(
            f"/api/v2/projects/{self.project_id}/refresh-diff",
            params={"refresh_id": refresh_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        readme = next(
            f for f in response.json()["files"] if f["path"] == "README.md"
        )
        self.assertEqual(readme["base"], "# Untouched\n")
        self.assertIsNone(readme["partner_edit"])
        self.assertEqual(readme["ai_redraft"], "# Refreshed\n")


# --------------------------------------------------------------------
# POST /refresh-resolve — decisions apply
# --------------------------------------------------------------------


class RefreshResolveRouteTests(_RefreshBase):

    def test_refresh_resolve_applies_per_file_decisions(self) -> None:
        """accept_redraft leaves the new scaffold's content as-is.
        keep_partner_edit rewrites it to the previous content.
        merged writes the partner-provided merged_content."""
        scaffold_id = _seed_scaffold(
            self.store,
            project_id=self.project_id,
            user_id=self.user_id,
            files=[
                {"path": "README.md", "content": "# Pre-edit AI\n"},
                {"path": "src/a.ts", "content": "// a-old"},
                {"path": "src/b.ts", "content": "// b-old"},
            ],
        )
        # Partner edits README so it has a partner_edit overlay; a.ts +
        # b.ts stay AI-original.
        self.store.update_scaffold_file_content(
            scaffold_id=scaffold_id,
            user_id=self.user_id,
            path="README.md",
            content="# Partner edit\n",
        )
        self.scaffold_adapter.generate.return_value = {
            "framework": "react-vite",
            "language": "typescript",
            "files": [
                {"path": "README.md", "content": "# AI redraft\n"},
                {"path": "src/a.ts", "content": "// a-new\n"},
                {"path": "src/b.ts", "content": "// b-new\n"},
            ],
            "readme_preview": "x",
            "post_install_steps": [],
            "truncation_note": "",
        }

        token_p, meta_p, sha_p = _patch_refresh_github_calls()
        with token_p, meta_p, sha_p, _patch_repo_context(None):
            kickoff = self.client.post(
                f"/api/v2/projects/{self.project_id}/refresh-overlay",
            )
        self.assertEqual(kickoff.status_code, 200, kickoff.text)
        new_scaffold_id = kickoff.json()["scaffold_id"]
        refresh_id = kickoff.json()["refresh_id"]

        # README → keep_partner_edit, a.ts → accept_redraft (default),
        # b.ts → merged with custom content.
        decisions = {
            "README.md": {"decision": "keep_partner_edit"},
            "src/a.ts": {"decision": "accept_redraft"},
            "src/b.ts": {
                "decision": "merged",
                "merged_content": "// b-merged",
            },
        }
        response = self.client.post(
            f"/api/v2/projects/{self.project_id}/refresh-resolve",
            json={"refresh_id": refresh_id, "decisions": decisions},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["diff_summary"]["accepted"], 1)
        self.assertEqual(body["diff_summary"]["kept"], 1)
        self.assertEqual(body["diff_summary"]["merged"], 1)

        # Verify the new scaffold reflects the decisions.
        new_row = self.store.get_scaffold(
            scaffold_id=new_scaffold_id, user_id=self.user_id,
        )
        manifest = _json.loads(new_row["manifest_json"])
        by_path = {f["path"]: f["content"] for f in manifest["files"]}
        # keep_partner_edit means "keep what I had pre-refresh" — i.e.
        # the previous scaffold's current content (which IS the
        # partner-edited overlay), not the deep AI-original baseline.
        self.assertEqual(by_path["README.md"], "# Partner edit\n")
        self.assertEqual(by_path["src/a.ts"], "// a-new\n")
        self.assertEqual(by_path["src/b.ts"], "// b-merged")


# --------------------------------------------------------------------
# Store-layer — update_scaffold_file_content captures original_content
# --------------------------------------------------------------------


class CaptureOriginalContentTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        signup_and_login(
            self.client,
            email="orig@example.com",
            password="password123",
            display_name="Orig",
        )
        me = self.client.get("/api/auth/me").json()
        self.user_id: str = me["user_id"]
        project = self.store.create_v2_project(
            user_id=self.user_id, title="t",
        )
        self.scaffold_id = _seed_scaffold(
            self.store,
            project_id=project["project_id"],
            user_id=self.user_id,
            files=[{"path": "README.md", "content": "# AI v1\n"}],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _read_file_entry(self, path: str) -> dict[str, object]:
        row = self.store.get_scaffold(
            scaffold_id=self.scaffold_id, user_id=self.user_id,
        )
        manifest = _json.loads(row["manifest_json"])
        return next(f for f in manifest["files"] if f["path"] == path)

    def test_first_edit_captures_original_content(self) -> None:
        ok = self.store.update_scaffold_file_content(
            scaffold_id=self.scaffold_id, user_id=self.user_id,
            path="README.md", content="# Partner v1\n",
        )
        self.assertTrue(ok)
        entry = self._read_file_entry("README.md")
        self.assertEqual(entry["original_content"], "# AI v1\n")
        self.assertEqual(entry["content"], "# Partner v1\n")

    def test_subsequent_edits_preserve_original_content(self) -> None:
        self.store.update_scaffold_file_content(
            scaffold_id=self.scaffold_id, user_id=self.user_id,
            path="README.md", content="# Partner v1\n",
        )
        self.store.update_scaffold_file_content(
            scaffold_id=self.scaffold_id, user_id=self.user_id,
            path="README.md", content="# Partner v2\n",
        )
        entry = self._read_file_entry("README.md")
        # Original baseline stays at the FIRST AI-generated state.
        self.assertEqual(entry["original_content"], "# AI v1\n")
        self.assertEqual(entry["content"], "# Partner v2\n")


if __name__ == "__main__":
    unittest.main()
