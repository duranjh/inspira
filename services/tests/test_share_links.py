"""Tests for the read-only share-link surface.

Covers:
- Owner mints a link; an anonymous client fetches the shared payload.
- Revoking the link flips the public fetch to 404.
- Non-owners cannot mint, read, or revoke links on someone else's project.
- Minting a new link invalidates the old one.
- The public fetch exposes topics + relationships + decisions + turns,
  strips attachment payloads, and 404s on soft-deleted projects.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

try:
    from ._helpers import fake_kickoff_response, make_test_app, signup_and_login
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        make_test_app,
        signup_and_login,
    )


class ShareLinkTests(unittest.TestCase):
    """Single-user happy paths: mint, read, public-fetch, revoke."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="owner@example.com", password="owner-pw-123")
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.project_id = "proj-owned"
        self.client.post(
            f"/api/v2/projects/{self.project_id}/kickoff",
            json={"user_idea": "A small outdoor wine festival."},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_owner_mints_link_and_anonymous_fetch_succeeds(self) -> None:
        minted = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        )
        self.assertEqual(minted.status_code, 201, minted.text)
        body = minted.json()
        self.assertIn("share_link", body)
        token = body["share_link"]["token"]
        self.assertTrue(token)
        self.assertEqual(body["share_link"]["url_path"], f"/s/{token}")

        # Anonymous client — a brand-new TestClient with no cookies.
        anon = TestClient(self.client.app)
        shared = anon.get(f"/api/v2/shared/{token}")
        self.assertEqual(shared.status_code, 200, shared.text)
        envelope = shared.json()
        self.assertEqual(envelope["project"]["project_id"], self.project_id)
        # Topics + relationships + decisions should be present (from kickoff).
        self.assertTrue(len(envelope["topics"]) >= 1)
        self.assertIn("relationships", envelope)
        self.assertIn("decisions", envelope)
        self.assertIn("turns_by_topic", envelope)
        # Every topic has a (possibly empty) turns list.
        for topic in envelope["topics"]:
            self.assertIn(topic["topic_id"], envelope["turns_by_topic"])

    def test_anonymous_fetch_records_view_count(self) -> None:
        token = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]

        anon = TestClient(self.client.app)
        self.assertEqual(
            anon.get(f"/api/v2/shared/{token}").status_code, 200,
        )
        anon.get(f"/api/v2/shared/{token}")
        anon.get(f"/api/v2/shared/{token}")

        row = self.store.get_share_link_by_token(token)
        assert row is not None
        self.assertEqual(row["view_count"], 3)
        self.assertIsNotNone(row["last_viewed_at"])

    def test_owner_fetches_active_link_after_minting(self) -> None:
        self.client.post(f"/api/v2/projects/{self.project_id}/share")
        got = self.client.get(f"/api/v2/projects/{self.project_id}/share")
        self.assertEqual(got.status_code, 200)
        link = got.json()["share_link"]
        self.assertIsNotNone(link)
        self.assertTrue(link["token"])

    def test_get_active_link_returns_none_when_no_link(self) -> None:
        got = self.client.get(f"/api/v2/projects/{self.project_id}/share")
        self.assertEqual(got.status_code, 200)
        self.assertIsNone(got.json()["share_link"])

    def test_revoke_makes_anonymous_fetch_404(self) -> None:
        token = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]

        anon = TestClient(self.client.app)
        self.assertEqual(
            anon.get(f"/api/v2/shared/{token}").status_code, 200,
        )

        revoked = self.client.post(
            f"/api/v2/projects/{self.project_id}/share/revoke",
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["revoked"])

        blocked = anon.get(f"/api/v2/shared/{token}")
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(
            (blocked.json().get("detail") or {}).get("error"),
            "share_link_not_found",
        )

    def test_generating_new_link_invalidates_the_old_one(self) -> None:
        first = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]
        second = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]

        self.assertNotEqual(first, second)

        anon = TestClient(self.client.app)
        # Old token no longer works.
        self.assertEqual(anon.get(f"/api/v2/shared/{first}").status_code, 404)
        # New token works.
        self.assertEqual(anon.get(f"/api/v2/shared/{second}").status_code, 200)

    def test_unknown_token_returns_404(self) -> None:
        anon = TestClient(self.client.app)
        self.assertEqual(
            anon.get("/api/v2/shared/does-not-exist").status_code, 404,
        )

    def test_shared_fetch_strips_attachments_from_turns(self) -> None:
        """Attachment payloads must not leak through the public surface."""
        topics = self.store.list_topics(project_id=self.project_id)
        self.assertTrue(topics)
        topic_id = topics[0]["topic_id"]

        self.store.append_qna_turn(
            topic_id=topic_id,
            project_id=self.project_id,
            role="user",
            body="Here is what I attached.",
            status="answered",
            attachments=[
                {"display_name": "secret.txt", "kind": "file:text", "excerpt": "CONFIDENTIAL"},
            ],
        )

        token = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]

        anon = TestClient(self.client.app)
        envelope = anon.get(f"/api/v2/shared/{token}").json()
        turns = envelope["turns_by_topic"][topic_id]
        self.assertTrue(turns)
        for turn in turns:
            self.assertEqual(turn["attachments"], [])

    def test_shared_fetch_404s_on_soft_deleted_project(self) -> None:
        token = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]
        # Soft-delete the project.
        self.client.post(f"/api/v2/projects/{self.project_id}/delete")
        anon = TestClient(self.client.app)
        self.assertEqual(anon.get(f"/api/v2/shared/{token}").status_code, 404)

    def test_shared_fetch_strips_private_notes_from_every_topic(self) -> None:
        """Owner's ``private_notes`` MUST NOT leak through the public route.

        Regression test for a QA-flagged privacy bug: the shared canvas
        handler used to return ``_store.list_topics`` rows verbatim, and
        that list includes the ``private_notes`` column as of the private-
        notes migration. Anyone with the share URL could read the owner's
        private scratchpad for every topic.

        This test seeds a highly distinctive canary string on the first
        topic's ``private_notes``, fetches the shared envelope anonymously,
        and asserts three things:

        1. ``private_notes`` is NOT a key on any topic dict in the payload.
        2. The canary string appears NOWHERE in the entire JSON response
           body — not under a nested key, not inside a turn, not in
           the project block.
        3. Topics on the shared canvas still come back with the other
           expected fields (title, icon, position), so the scrub didn't
           accidentally strip more than it was meant to.
        """
        topics = self.store.list_topics(project_id=self.project_id)
        self.assertTrue(topics, "kickoff should have created at least one topic")
        first_topic_id = topics[0]["topic_id"]

        # A canary string specifically chosen to stand out in JSON output.
        canary = "PRIVATE_NOTES_LEAK_CANARY_9f8e7d6c5b4a"
        set_resp = self.client.post(
            f"/api/v2/topics/{first_topic_id}/private-notes",
            json={"notes": f"top secret: {canary}"},
        )
        self.assertEqual(set_resp.status_code, 200, set_resp.text)

        # Confirm the store round-trip actually persisted it — otherwise
        # the later "canary not in payload" assertion would be vacuous.
        persisted = next(
            t for t in self.store.list_topics(project_id=self.project_id)
            if t["topic_id"] == first_topic_id
        )
        self.assertIn(canary, persisted["private_notes"] or "")

        # Mint a share link and fetch anonymously.
        token = self.client.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]
        anon = TestClient(self.client.app)
        shared = anon.get(f"/api/v2/shared/{token}")
        self.assertEqual(shared.status_code, 200, shared.text)
        envelope = shared.json()

        # 1. No topic dict has a ``private_notes`` key at all.
        for topic in envelope["topics"]:
            self.assertNotIn(
                "private_notes",
                topic,
                f"private_notes leaked on topic {topic.get('topic_id')!r}",
            )
            # Sanity: the non-sensitive fields DID come through.
            self.assertIn("topic_id", topic)
            self.assertIn("title", topic)

        # 2. The canary string is not anywhere in the response body.
        import json as _json
        body_text = shared.text
        self.assertNotIn(canary, body_text)
        # And the same assertion on the parsed envelope catches any
        # non-JSON transport escaping that might have hidden the canary.
        self.assertNotIn(canary, _json.dumps(envelope))

        # 3. The ``private_notes`` key name itself must not surface
        # anywhere either — the frontend should have no reason to see
        # the key name on a public payload.
        self.assertNotIn("private_notes", body_text)


class CrossUserShareLinkTests(unittest.TestCase):
    """Tenant isolation — a non-owner cannot touch another user's link."""

    def setUp(self) -> None:
        self.a, self.store, self.adapter, self.temp_dir = make_test_app()
        self.b = TestClient(self.a.app)
        signup_and_login(self.a, email="alice@example.com", password="alice-pw-1")
        signup_and_login(self.b, email="bob@example.com", password="bob-pw-123")
        self.adapter.kickoff.return_value = fake_kickoff_response()
        self.project_id = "proj-alices"
        self.a.post(
            f"/api/v2/projects/{self.project_id}/kickoff",
            json={"user_idea": "Anniversary dinner plan."},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bob_cannot_mint_link_on_alices_project(self) -> None:
        response = self.b.post(f"/api/v2/projects/{self.project_id}/share")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "project_not_found",
        )

    def test_bob_cannot_read_alices_link(self) -> None:
        self.a.post(f"/api/v2/projects/{self.project_id}/share")
        response = self.b.get(f"/api/v2/projects/{self.project_id}/share")
        self.assertEqual(response.status_code, 404)

    def test_bob_cannot_revoke_alices_link(self) -> None:
        token = self.a.post(
            f"/api/v2/projects/{self.project_id}/share",
        ).json()["share_link"]["token"]

        response = self.b.post(
            f"/api/v2/projects/{self.project_id}/share/revoke",
        )
        # Ownership check short-circuits as 404 (same pattern as other
        # ownership helpers — don't leak that the project ID is real).
        self.assertEqual(response.status_code, 404)

        # And the link still works anonymously.
        anon = TestClient(self.a.app)
        self.assertEqual(
            anon.get(f"/api/v2/shared/{token}").status_code, 200,
        )


if __name__ == "__main__":
    unittest.main()
