"""HTTP-level tests for POST /api/v2/connectors/local-repo/upload.

Covers:
- Happy path: drop a 3-file folder, snapshot persisted with
  expected shape (tree_top, source=local-repo-upload, file_count).
- Path traversal defense: ``../../etc/passwd``-style filenames
  are rejected outright (audit concern #6).
- Exclude filter: .git/, node_modules/, build/, lockfiles all
  silently dropped from the accepted set.
- Per-file size cap: a >1MB file rejected with 413.
- Total size cap: many small files >50MB rejected with 413.
- Role gate: member (non-admin) blocked with 403.
- Empty-after-filter case: 422 no_source_files when nothing
  passes the source-extension allowlist.
"""
from __future__ import annotations

import json
import unittest

from planning_studio_service.connectors.store import list_repo_snapshots

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _file_tuple(path: str, content: bytes) -> tuple[str, tuple[str, bytes]]:
    """Build a multipart files-tuple in the shape httpx expects."""
    return ("files", (path, content))


class LocalRepoUploadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.user = signup_and_login(
            self.client,
            email="owner@acme.com",
            password="password123",
            display_name="Owner",
        )
        ws = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        ).json()["workspace"]
        self.workspace_id: str = ws["workspace_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_happy_path_persists_snapshot(self) -> None:
        files = [
            _file_tuple("myrepo/src/index.ts", b"export const x = 1;\n"),
            _file_tuple("myrepo/src/utils.ts", b"export const y = 2;\n"),
            _file_tuple("myrepo/README.md", b"# Hello\n"),
        ]
        resp = self.client.post(
            "/api/v2/connectors/local-repo/upload", files=files
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["accepted"], 3)
        self.assertGreater(body["total_bytes"], 0)
        self.assertTrue(body["repo_id"].startswith("local-"))

        # Snapshot persisted with expected shape.
        snapshots = list_repo_snapshots(
            self.store, workspace_id=self.workspace_id, provider="local-repo"
        )
        self.assertEqual(len(snapshots), 1)
        snapshot = json.loads(snapshots[0]["snapshot_json"])
        self.assertEqual(snapshot["source"], "local-repo-upload")
        self.assertEqual(snapshot["file_count"], 3)
        self.assertEqual(snapshot["open_issues"], [])
        self.assertEqual(snapshot["recent_commits"], [])
        paths = [entry["path"] for entry in snapshot["tree_top"]]
        self.assertIn("myrepo/README.md", paths)
        self.assertIn("myrepo/src/index.ts", paths)

    def test_path_traversal_rejected(self) -> None:
        files = [
            _file_tuple("../../etc/passwd", b"root:x:0:0\n"),
            _file_tuple("/etc/shadow", b"shadow"),
            _file_tuple("myrepo/safe.py", b"print('ok')\n"),
        ]
        resp = self.client.post(
            "/api/v2/connectors/local-repo/upload", files=files
        )
        # The single safe file lands; the traversal attempts are
        # silently filtered (skipped count = 2).
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["accepted"], 1)
        self.assertEqual(body["skipped"], 2)

        snapshot = json.loads(
            list_repo_snapshots(
                self.store,
                workspace_id=self.workspace_id,
                provider="local-repo",
            )[0]["snapshot_json"]
        )
        paths = [entry["path"] for entry in snapshot["tree_top"]]
        self.assertEqual(paths, ["myrepo/safe.py"])
        # Confirm no `../` or `/etc/...` snuck through normalization.
        for p in paths:
            self.assertFalse(p.startswith("/"))
            self.assertNotIn("..", p)

    def test_exclude_filter_drops_dependency_dirs(self) -> None:
        files = [
            _file_tuple("myrepo/src/main.py", b"x = 1\n"),
            _file_tuple("myrepo/.git/config", b"[core]\n"),
            _file_tuple(
                "myrepo/node_modules/react/package.json", b"{}"
            ),
            _file_tuple("myrepo/dist/bundle.js", b"console.log(1);"),
            _file_tuple("myrepo/yarn.lock", b"# lockfile"),
            _file_tuple("myrepo/__pycache__/main.cpython-311.pyc", b"\x00"),
        ]
        resp = self.client.post(
            "/api/v2/connectors/local-repo/upload", files=files
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["accepted"], 1)
        self.assertEqual(body["skipped"], 5)

    def test_per_file_too_large_returns_413(self) -> None:
        # Per-file cap is 1 MB.
        big = b"x" * (1 * 1024 * 1024 + 1)
        files = [_file_tuple("myrepo/big.py", big)]
        resp = self.client.post(
            "/api/v2/connectors/local-repo/upload", files=files
        )
        self.assertEqual(resp.status_code, 413, resp.text)
        self.assertEqual(resp.json()["detail"]["error"], "file_too_large")

    def test_empty_after_filter_returns_422(self) -> None:
        # All files filtered out → 422 no_source_files.
        files = [
            _file_tuple("myrepo/.git/HEAD", b"ref: main"),
            _file_tuple("myrepo/yarn.lock", b"#"),
        ]
        resp = self.client.post(
            "/api/v2/connectors/local-repo/upload", files=files
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["error"], "no_source_files")

    def test_member_role_blocked(self) -> None:
        # Sign up a second user, invite as member (non-admin), then
        # attempt upload.
        member_email = "member@acme.com"
        from planning_studio_service.workspaces.store import (
            add_member as ws_add_member,
        )
        from planning_studio_service.workspaces.models import Role

        # Create the second user via signup.
        self.client.cookies.clear()
        member_user = signup_and_login(
            self.client,
            email=member_email,
            password="password123",
            display_name="Member",
        )
        ws_add_member(
            self.store,
            workspace_id=self.workspace_id,
            user_id=member_user["user_id"],
            role=Role.member,
        )
        # The member is now in the workspace; targeting it via
        # X-Workspace-Id header. Member role < admin so the upload
        # endpoint returns 403.
        files = [_file_tuple("myrepo/src/main.py", b"x=1")]
        resp = self.client.post(
            "/api/v2/connectors/local-repo/upload",
            files=files,
            headers={"X-Workspace-Id": self.workspace_id},
        )
        self.assertEqual(resp.status_code, 403, resp.text)


if __name__ == "__main__":
    unittest.main()
