"""Wave F.4 — artifact-comments router HTTP endpoint tests.

Covers create / list / update lifecycle, body-edit ownership gate,
resolve-by-any-member, threaded replies, and the SHA-256[:16] line-hash
anchor contract that the FE relies on for stale-state detection.
"""
from __future__ import annotations

import hashlib
import unittest
from typing import Any

from planning_studio_service.workspaces.models import Role
from planning_studio_service.workspaces.store import add_member

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


def _logout(client) -> None:
    client.post("/api/auth/logout")


def _signup(client, *, email: str, password: str = "password123") -> dict[str, Any]:
    return signup_and_login(client, email=email, password=password)


class ArtifactCommentRouteTests(unittest.TestCase):
    """Common scaffolding: workspace + project owned by self.owner."""

    def setUp(self) -> None:
        self.client, self.store, _, self.temp_dir = make_test_app()
        self.owner = _signup(self.client, email="owner@acme.com")
        ws_resp = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "acme", "name": "Acme"},
        )
        self.workspace_id: str = ws_resp.json()["workspace"]["workspace_id"]
        self.client.headers["X-Workspace-Id"] = self.workspace_id

        self.project = self.store.create_v2_project(
            user_id=self.owner["user_id"], title="Test Project",
        )
        self.project_id: str = self.project["project_id"]
        # Bind the project to the workspace so the router's workspace
        # scope check accepts invited members (not just the project
        # creator). Real-world flows attach workspace_id via the kickoff
        # / orchestrator paths; the bare ``create_v2_project`` helper
        # used here doesn't, so we patch it inline.
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE v2_projects SET workspace_id = ? "
                "WHERE project_id = ?",
                (self.workspace_id, self.project_id),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _post_comment(
        self,
        *,
        line_number: int = 7,
        line_content: str = "    return 42",
        category: str = "question",
        body: str = "Why 42?",
        parent_comment_id: str | None = None,
        file_path: str = "src/main.py",
    ) -> dict[str, Any]:
        payload = {
            "file_path": file_path,
            "line_number": line_number,
            "line_content": line_content,
            "category": category,
            "body": body,
        }
        if parent_comment_id is not None:
            payload["parent_comment_id"] = parent_comment_id
        resp = self.client.post(
            f"/api/v2/projects/{self.project_id}/artifact/comments",
            json=payload,
        )
        return resp

    def test_create_comment_persists(self) -> None:
        line_content = "    return 42"
        resp = self._post_comment(line_content=line_content)
        self.assertEqual(resp.status_code, 201)
        comment = resp.json()["comment"]
        self.assertTrue(comment["comment_id"].startswith("comment-"))
        self.assertEqual(comment["project_id"], self.project_id)
        self.assertEqual(comment["file_path"], "src/main.py")
        self.assertEqual(comment["line_number"], 7)
        self.assertEqual(comment["category"], "question")
        self.assertEqual(comment["body"], "Why 42?")
        self.assertEqual(comment["author_user_id"], self.owner["user_id"])
        self.assertIsNone(comment["parent_comment_id"])
        self.assertIsNone(comment["resolved_at"])
        # Hash anchor contract: SHA-256 over UTF-8 bytes, first 16 hex.
        expected_hash = hashlib.sha256(
            line_content.encode("utf-8"),
        ).hexdigest()[:16]
        self.assertEqual(comment["line_content_hash"], expected_hash)
        # GET round-trip.
        listing = self.client.get(
            f"/api/v2/projects/{self.project_id}/artifact/comments",
        )
        self.assertEqual(listing.status_code, 200)
        comments = listing.json()["comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["comment_id"], comment["comment_id"])

    def test_list_comments_excludes_resolved_by_default(self) -> None:
        a_id = self._post_comment(body="A").json()["comment"]["comment_id"]
        # Resolve A.
        resolve = self.client.patch(
            f"/api/v2/projects/{self.project_id}/artifact/comments/{a_id}",
            json={"resolved": True},
        )
        self.assertEqual(resolve.status_code, 200)
        self.assertIsNotNone(resolve.json()["comment"]["resolved_at"])
        # B stays open.
        b_id = self._post_comment(
            body="B", line_number=9,
        ).json()["comment"]["comment_id"]
        listing = self.client.get(
            f"/api/v2/projects/{self.project_id}/artifact/comments",
        )
        comments = listing.json()["comments"]
        self.assertEqual([c["comment_id"] for c in comments], [b_id])

    def test_list_comments_includes_resolved_with_flag(self) -> None:
        a_id = self._post_comment(body="A").json()["comment"]["comment_id"]
        self.client.patch(
            f"/api/v2/projects/{self.project_id}/artifact/comments/{a_id}",
            json={"resolved": True},
        )
        b_id = self._post_comment(
            body="B", line_number=9,
        ).json()["comment"]["comment_id"]
        listing = self.client.get(
            f"/api/v2/projects/{self.project_id}/artifact/comments"
            "?include_resolved=true",
        )
        ids = {c["comment_id"] for c in listing.json()["comments"]}
        self.assertEqual(ids, {a_id, b_id})

    def test_update_comment_body_owner_only(self) -> None:
        # Owner creates a comment.
        a_id = self._post_comment(body="orig").json()["comment"]["comment_id"]
        # Signup a second user; invite as a member so they can hit the
        # router; have them try to edit owner's comment body → 403.
        _logout(self.client)
        stranger = _signup(self.client, email="stranger@acme.com")
        add_member(
            self.store,
            workspace_id=self.workspace_id,
            user_id=stranger["user_id"],
            role=Role.member,
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        resp = self.client.patch(
            f"/api/v2/projects/{self.project_id}/artifact/comments/{a_id}",
            json={"body": "rewritten by stranger"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json()["detail"]["error"], "comment_edit_forbidden",
        )
        # Confirm body unchanged.
        self.assertEqual(
            self.store.get_artifact_comment(a_id)["body"], "orig",
        )

    def test_resolve_comment_any_member(self) -> None:
        # Owner creates a comment.
        a_id = self._post_comment(body="orig").json()["comment"]["comment_id"]
        # Stranger (member, not owner) can resolve.
        _logout(self.client)
        stranger = _signup(self.client, email="stranger@acme.com")
        add_member(
            self.store,
            workspace_id=self.workspace_id,
            user_id=stranger["user_id"],
            role=Role.member,
        )
        self.client.headers["X-Workspace-Id"] = self.workspace_id
        resp = self.client.patch(
            f"/api/v2/projects/{self.project_id}/artifact/comments/{a_id}",
            json={"resolved": True},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()["comment"]["resolved_at"])

    def test_threaded_reply_creates_child_comment(self) -> None:
        parent = self._post_comment(body="parent").json()["comment"]
        reply_resp = self._post_comment(
            body="reply",
            parent_comment_id=parent["comment_id"],
        )
        self.assertEqual(reply_resp.status_code, 201)
        reply = reply_resp.json()["comment"]
        self.assertEqual(reply["parent_comment_id"], parent["comment_id"])
        listing = self.client.get(
            f"/api/v2/projects/{self.project_id}/artifact/comments",
        ).json()["comments"]
        ids = {c["comment_id"]: c["parent_comment_id"] for c in listing}
        self.assertEqual(ids[parent["comment_id"]], None)
        self.assertEqual(ids[reply["comment_id"]], parent["comment_id"])
