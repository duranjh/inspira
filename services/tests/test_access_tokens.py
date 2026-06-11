"""Tests for Personal Access Tokens (PATs).

Exercises both the store layer (mint / list / resolve / revoke) and
the HTTP routes (/api/v2/auth/tokens) plus the bearer-auth fallthrough
on a representative v2 route.  The PAT surface is security-critical:
any regression means either a token leaks or a revoked token keeps
working, both of which we'd rather catch in CI than in production.

Coverage plan:
  - mint_access_token returns a raw token matching sha256 -> resolvable
  - resolve after revoke returns None (revocation takes effect)
  - last_used_at updates on resolve (list-view freshness)
  - IDOR: user A cannot revoke user B's token
  - HTTP: POST /api/v2/auth/tokens returns raw token once
  - HTTP: GET  /api/v2/auth/tokens never leaks the hash
  - Bearer header authenticates on /api/v2/projects
  - Malformed bearer header -> unauth on protected, not 500
  - Unknown/revoked bearer -> unauth on protected (fallback to anon)
"""
from __future__ import annotations

import hashlib
import time
import unittest

try:
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class StoreAccessTokenTests(unittest.TestCase):
    """Direct store-level coverage — avoids HTTP for the core invariants."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Sign up on the client so we have a real user_id to attach
        # tokens to.  The client is then used in later HTTP tests; the
        # store tests here just need a valid user_id.
        payload = signup_and_login(
            self.client, email="pat-alice@inspira.io", password="password123",
        )
        self.user_id = payload["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_mint_returns_raw_token_matching_sha256_and_resolvable(self) -> None:
        """Raw token must hash to the stored row and resolve to the owner."""
        token_id, raw = self.store.mint_access_token(self.user_id, "Zapier")
        self.assertTrue(token_id.startswith("tok_"))
        # Raw token has the Inspira-origin prefix so it's grep-able in logs
        # and obvious in pastebins.
        self.assertTrue(raw.startswith("inspira_pat_"))
        # Suffix is 32 hex chars after the prefix.
        suffix = raw[len("inspira_pat_"):]
        self.assertEqual(len(suffix), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in suffix))
        # Resolve the raw token -> owner's user_id.  Also sanity-checks the
        # sha256 invariant from the other side (if mint stored a different
        # hash than resolve computes, this fails).
        resolved_user_id = self.store.resolve_access_token(raw)
        self.assertEqual(resolved_user_id, self.user_id)
        # Row in list_access_tokens has the matching token_id and name.
        rows = self.store.list_access_tokens(self.user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["token_id"], token_id)
        self.assertEqual(rows[0]["name"], "Zapier")
        self.assertIsNone(rows[0]["revoked_at"])
        # Listing NEVER leaks the hash or raw token.
        self.assertNotIn("token_hash", rows[0])
        self.assertNotIn("token", rows[0])

    def test_resolve_after_revoke_returns_none(self) -> None:
        _token_id, raw = self.store.mint_access_token(self.user_id, "CLI")
        self.assertEqual(self.store.resolve_access_token(raw), self.user_id)
        self.assertTrue(self.store.revoke_access_token(self.user_id, _token_id))
        self.assertIsNone(self.store.resolve_access_token(raw))
        # The row is still present (soft delete) with a revoked_at stamp.
        rows = self.store.list_access_tokens(self.user_id)
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["revoked_at"])

    def test_last_used_at_updates_on_resolve(self) -> None:
        _token_id, raw = self.store.mint_access_token(self.user_id, "poll")
        before = self.store.list_access_tokens(self.user_id)[0]
        self.assertIsNone(before["last_used_at"])
        # now_timestamp() is second-granular — wait a tick so the UPDATE
        # doesn't produce the same string as created_at on a fast runner.
        time.sleep(1.1)
        self.store.resolve_access_token(raw)
        after = self.store.list_access_tokens(self.user_id)[0]
        self.assertIsNotNone(after["last_used_at"])
        self.assertNotEqual(after["last_used_at"], before["created_at"])

    def test_idor_cannot_revoke_another_users_token(self) -> None:
        """User A's call to revoke with user B's token_id matches nothing."""
        # Carve out a second user directly via the store — avoids the
        # TestClient needing a second authenticated client for this
        # pure data-layer assertion.
        other = self.store.create_user(
            email="pat-bob@inspira.io",
            password_hash="x",
            display_name="Bob",
        )
        token_id_bob, _raw_bob = self.store.mint_access_token(
            other["user_id"], "Bob's script",
        )
        # Alice tries to revoke Bob's token.  Must return False; Bob's
        # token stays active.
        self.assertFalse(
            self.store.revoke_access_token(self.user_id, token_id_bob),
        )
        rows_bob = self.store.list_access_tokens(other["user_id"])
        self.assertEqual(len(rows_bob), 1)
        self.assertIsNone(rows_bob[0]["revoked_at"])
        # Defensive: Alice's list is still empty, revoking didn't leak.
        self.assertEqual(self.store.list_access_tokens(self.user_id), [])

    def test_unknown_and_malformed_raw_tokens_resolve_to_none(self) -> None:
        """Prefix-less / empty / nonsense strings must all resolve to None."""
        self.assertIsNone(self.store.resolve_access_token(""))
        self.assertIsNone(self.store.resolve_access_token("not-a-pat"))
        self.assertIsNone(
            self.store.resolve_access_token("inspira_pat_" + "z" * 32),
        )
        # Correct prefix but wrong hash also returns None.
        bogus = "inspira_pat_" + "a" * 32
        self.assertEqual(
            hashlib.sha256(bogus.encode()).hexdigest(),
            hashlib.sha256(bogus.encode()).hexdigest(),
        )
        self.assertIsNone(self.store.resolve_access_token(bogus))


class AccessTokenHttpTests(unittest.TestCase):
    """End-to-end tests through FastAPI's TestClient."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.signup = signup_and_login(
            self.client, email="pat-carol@inspira.io", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_post_tokens_returns_raw_token_once(self) -> None:
        response = self.client.post(
            "/api/v2/auth/tokens", json={"name": "Zapier"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("token_id", payload)
        self.assertIn("token", payload)
        self.assertTrue(payload["token"].startswith("inspira_pat_"))
        self.assertEqual(payload["name"], "Zapier")
        # The raw token resolves to the owner via the store, confirming
        # the response and the persisted hash agree.
        self.assertEqual(
            self.store.resolve_access_token(payload["token"]),
            self.signup["user_id"],
        )

    def test_list_tokens_never_returns_hash(self) -> None:
        self.client.post("/api/v2/auth/tokens", json={"name": "Zapier"})
        response = self.client.get("/api/v2/auth/tokens")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["tokens"]), 1)
        row = payload["tokens"][0]
        # Metadata is safe; the hash + raw value are never in the list.
        self.assertEqual(row["name"], "Zapier")
        self.assertIsNone(row["revoked_at"])
        self.assertNotIn("token_hash", row)
        self.assertNotIn("token", row)

    def test_delete_revokes_and_second_delete_404s(self) -> None:
        mint = self.client.post(
            "/api/v2/auth/tokens", json={"name": "CLI"},
        ).json()
        # First revoke succeeds.
        response = self.client.delete(
            f"/api/v2/auth/tokens/{mint['token_id']}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["revoked"])
        # Second revoke misses the ``revoked_at IS NULL`` guard — 404.
        response2 = self.client.delete(
            f"/api/v2/auth/tokens/{mint['token_id']}",
        )
        self.assertEqual(response2.status_code, 404)

    def test_bearer_header_authenticates_on_v2_route(self) -> None:
        """A valid PAT grants read access via Authorization: Bearer."""
        mint = self.client.post(
            "/api/v2/auth/tokens", json={"name": "Zapier"},
        ).json()
        raw_token = mint["token"]
        # Drop the session cookie so ONLY the bearer header
        # authenticates.  If the route authenticated via cookie the
        # test would pass for the wrong reason.
        self.client.cookies.clear()
        response = self.client.get(
            "/api/v2/projects",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        self.assertEqual(response.status_code, 200)
        # The user sees their own empty project list (no projects yet),
        # not the system user's legacy seed projects.
        self.assertEqual(response.json(), {"projects": []})

    def test_malformed_bearer_header_falls_through_to_anon_not_500(self) -> None:
        """Garbage bearer values must NOT produce a 500 on protected routes.

        After dropping the cookie, the wrong bearer fails silently and
        the request falls through to anonymous minting — so the v2
        projects route responds 200 with the anon user's empty list,
        not 500.  The important assertion is "not 5xx".
        """
        self.client.cookies.clear()
        for bad in ("", "Basic xxx", "Bearer", "Bearer    ", "garbage"):
            response = self.client.get(
                "/api/v2/projects", headers={"Authorization": bad},
            )
            self.assertLess(response.status_code, 500, f"malformed={bad!r}")

    def test_revoked_bearer_does_not_authenticate(self) -> None:
        """Once revoked, the bearer header no longer authenticates.

        We confirm this by checking that ``/api/auth/me`` returns an
        anon (``is_system=True``) identity when presented with the
        revoked bearer — a non-revoked bearer for the signed-in user
        would flip this to ``is_system=False`` on /api/auth/me (which
        ALSO accepts bearer via the wrapped dependency chain — though
        /api/auth/me uses a Cookie-only path in auth.py so it stays
        cookie-scoped).  Instead we test on a v2 route where the
        bearer IS the auth source.
        """
        mint = self.client.post(
            "/api/v2/auth/tokens", json={"name": "CLI"},
        ).json()
        raw_token = mint["token"]
        # Sanity: before revoke, bearer authenticates and lists the
        # owner's tokens.
        self.client.cookies.clear()
        pre = self.client.get(
            "/api/v2/auth/tokens",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        self.assertEqual(pre.status_code, 200)
        self.assertEqual(len(pre.json()["tokens"]), 1)
        # Revoke (via cookie — re-login with the existing store).
        login = self.client.post(
            "/api/auth/login",
            json={"email": "pat-carol@inspira.io", "password": "password123"},
        )
        self.assertEqual(login.status_code, 200)
        self.client.delete(f"/api/v2/auth/tokens/{mint['token_id']}")
        self.client.cookies.clear()
        # Bearer is now revoked.  The /api/v2/auth/tokens route's
        # ``is_system`` gate fires on the fallthrough-to-anon path and
        # returns 403 -- proving the bearer did NOT authenticate the
        # original owner.
        post_revoke = self.client.get(
            "/api/v2/auth/tokens",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        self.assertEqual(post_revoke.status_code, 403)

    def test_system_user_cannot_mint_token(self) -> None:
        """Anonymous / system sessions are blocked from minting PATs.

        A PAT outlives any tab; it only makes sense for a real account.
        """
        client2, _store2, _adapter2, temp2 = make_test_app()
        try:
            response = client2.post(
                "/api/v2/auth/tokens", json={"name": "AnonZap"},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.json().get("detail", {}).get("error"),
                "sign_in_required",
            )
        finally:
            temp2.cleanup()

    def test_empty_name_is_rejected(self) -> None:
        """Name is required — Pydantic validation kicks in before the store."""
        response = self.client.post(
            "/api/v2/auth/tokens", json={"name": "   "},
        )
        # Blank-after-strip is a semantic rejection from the store layer;
        # pure-whitespace passes the min_length=1 pydantic check because
        # Pydantic doesn't strip by default.  We accept either 400 or 422
        # here — both are client errors, neither leaks state.
        self.assertIn(response.status_code, (400, 422))


if __name__ == "__main__":
    unittest.main()
