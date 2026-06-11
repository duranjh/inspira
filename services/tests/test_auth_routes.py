"""FastAPI auth route tests — signup, login, logout, /me, session cookies.

The auth surface is thin but security-critical: any regression here
exposes the whole app. These tests exercise the happy paths, the
rejection paths (wrong password, duplicate email, short password,
bad email), and the cookie behaviors (httpOnly, persistence across
requests, tamper resistance via itsdangerous).

We avoid argon2-hash assertions directly — that's a library concern.
Instead we verify the behavior the rest of the app depends on:
``/api/auth/me`` returns the right user after each transition.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

try:
    # Works when tests are invoked as ``services.tests.test_auth_routes``
    from ._helpers import make_test_app, signup_and_login
except ImportError:
    # Works under ``python -m unittest discover -s services/tests`` where the
    # tests package context isn't set up for relative imports.
    from _helpers import make_test_app, signup_and_login  # type: ignore[no-redef]


class AuthMeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_me_with_no_cookie_mints_anonymous_user(self) -> None:
        """Missing cookie → mint a per-session anonymous user.

        Post-anonymous-canvas refactor: the backend no longer returns
        the shared ``user-system`` fallback. Each fresh visitor gets
        their own ``user-anon-<hex>`` row so their canvas data is
        scoped to them alone, not commingled with every other guest.
        They still present as ``is_system=True`` to the frontend — the
        UI contract "not yet signed in" covers both paths.
        """
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["is_system"])
        self.assertTrue(payload["user_id"].startswith("user-anon-"))

    def test_me_with_tampered_cookie_falls_back_to_system_user(self) -> None:
        """A forged/corrupted session cookie must NOT authenticate.

        itsdangerous rejects the signature; _resolve_user catches and
        returns the system user. Critical defense — if this falls
        through to a real user, session forgery is trivial.
        """
        self.client.cookies.set("inspira_session", "not-a-valid-signed-token")
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_system"])

    def test_me_default_workspace_id_null_for_anon(self) -> None:
        """Anon users never have a workspace; the field must be in the
        response shape (so the frontend type check holds) but null."""
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("default_workspace_id", payload)
        self.assertIsNone(payload["default_workspace_id"])

    def test_me_default_workspace_id_populated_after_workspace_creation(
        self,
    ) -> None:
        """After signing up and creating a workspace, ``/api/auth/me``
        surfaces the workspace_id so the post-login Kanban can render."""
        signup_and_login(
            self.client,
            email="kanban-default@example.org",
            password="s3cret-pass",
            display_name="Kanban User",
        )
        # Before workspace creation, the default is null even for a
        # signed-in user (the field exists, just empty).
        before = self.client.get("/api/auth/me").json()
        self.assertFalse(before["is_system"])
        self.assertIsNone(before["default_workspace_id"])
        # Creating the first workspace promotes it to the user's default
        # (test_create_sets_default_when_user_has_none in
        # test_workspace_endpoints.py covers the DB side).
        create = self.client.post(
            "/api/v2/workspaces",
            json={"slug": "kanban-acme", "name": "Kanban Acme"},
        )
        self.assertEqual(create.status_code, 201, create.text)
        ws_id = create.json()["workspace"]["workspace_id"]
        after = self.client.get("/api/auth/me").json()
        self.assertEqual(after["default_workspace_id"], ws_id)


class SignupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_signup_creates_user_and_sets_cookie(self) -> None:
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "s3cret-pass",
                "terms_accepted": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["email"], "alice@example.com")
        self.assertFalse(payload["is_system"])
        self.assertTrue(payload["user_id"].startswith("user-"))
        # display_name defaults to the local part of the email when blank
        self.assertEqual(payload["display_name"], "alice")
        # Cookie is set on the client — subsequent calls are authenticated
        self.assertIn("inspira_session", self.client.cookies)

    def test_signup_session_cookie_is_http_only(self) -> None:
        """Set-Cookie must include HttpOnly — a JS XSS should not read it."""
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "s3cret-pass",
                "terms_accepted": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn("inspira_session=", set_cookie)
        # httpx / starlette lowercase the directives — match loosely
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Path=/", set_cookie)

    def test_duplicate_email_signup_returns_409(self) -> None:
        first = self.client.post(
            "/api/auth/signup",
            json={
                "email": "dup@example.com",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(first.status_code, 201)
        # Drop the first user's session so the server sees the second
        # attempt as a fresh request, not a re-register by the same session.
        self.client.cookies.clear()
        dup = self.client.post(
            "/api/auth/signup",
            json={
                "email": "dup@example.com",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(dup.status_code, 409)
        detail = dup.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "email_in_use")

    def test_short_password_rejected_with_422(self) -> None:
        """Pydantic SignupBody enforces min_length=8 on ``password``.

        This is the only server-side password strength check today —
        if it ever loosens we want the test to fail loudly.
        """
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "alice@example.com",
                "password": "short",
                "terms_accepted": True,
            },
        )
        self.assertEqual(response.status_code, 422)
        # No user should have been created
        self.assertIsNone(self.store.get_user_by_email("alice@example.com"))

    def test_invalid_email_rejected_with_422(self) -> None:
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "not-an-email",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_signup_without_terms_accepted_returns_400(self) -> None:
        """Missing / false ``terms_accepted`` must block signup with a
        clean 400 ``{"error": "terms_required"}`` payload.

        The frontend gates the submit button on the checkbox, but
        script-driven callers that bypass the UI need to see a
        predictable error code so they don't silently create accounts
        that never accepted the Terms.
        """
        # Explicit False.
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "no-terms@example.com",
                "password": "password123",
                "terms_accepted": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "terms_required")
        # No user was created.
        self.assertIsNone(self.store.get_user_by_email("no-terms@example.com"))

        # Missing field defaults to False → same 400.
        self.client.cookies.clear()
        response2 = self.client.post(
            "/api/auth/signup",
            json={
                "email": "missing-terms@example.com",
                "password": "password123",
            },
        )
        self.assertEqual(response2.status_code, 400)
        detail2 = response2.json().get("detail") or {}
        self.assertEqual(detail2.get("error"), "terms_required")

    def test_signup_with_terms_accepted_persists_timestamp(self) -> None:
        """``terms_accepted=True`` → 201 + non-null ``terms_accepted_at``
        on the stored users row. The timestamp lets us later prove which
        version of the Terms a given user agreed to by cross-referencing
        ``created_at``.
        """
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "accepted@example.com",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        stored = self.store.get_user_by_email("accepted@example.com")
        self.assertIsNotNone(stored)
        assert stored is not None  # narrow for type-checkers
        self.assertIsNotNone(
            stored.get("terms_accepted_at"),
            "terms_accepted_at must be persisted on signup",
        )


class LoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Seed a user for the login tests. Use signup so argon2 is applied
        # the same way the real code path would hash it.
        self.email = "bob@example.com"
        self.password = "correct-horse-battery"
        signup_and_login(self.client, email=self.email, password=self.password)
        # Clear the post-signup session so each login test starts clean.
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_correct_credentials_return_200_and_set_cookie(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": self.password},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["email"], self.email)
        self.assertFalse(payload["is_system"])
        self.assertIn("inspira_session", self.client.cookies)

    def test_wrong_password_returns_401(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "invalid_credentials",
        )
        # No session cookie was set on failure
        self.assertNotIn("inspira_session", self.client.cookies)

    def test_unknown_email_returns_401(self) -> None:
        """Unknown-email 401 must mirror wrong-password 401.

        We explicitly do NOT distinguish the two — a different status
        or detail payload would allow user-enumeration via timing /
        response difference.
        """
        response = self.client.post(
            "/api/auth/login",
            json={"email": "ghost@example.com", "password": "whatever1"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "invalid_credentials",
        )


class LogoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_logout_clears_session_and_me_returns_anonymous(self) -> None:
        signup_and_login(
            self.client, email="eve@example.com", password="password123",
        )
        # Sanity: we are logged in as Eve
        me_before = self.client.get("/api/auth/me").json()
        self.assertEqual(me_before["email"], "eve@example.com")
        self.assertFalse(me_before["is_system"])

        # Log out — clears the cookie via Set-Cookie with an empty value
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("logged_out"))

        # After the logout response, httpx drops the expired cookie — /me
        # mints a fresh anonymous user (not the legacy system user).
        me_after = self.client.get("/api/auth/me").json()
        self.assertTrue(me_after["is_system"])
        self.assertTrue(me_after["user_id"].startswith("user-anon-"))


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_cookie_survives_across_requests(self) -> None:
        """One sign-in should carry across many requests on the same client.

        If the cookie does not persist, every request from the UI
        would be an unauthenticated system-user request, which would
        silently mask ownership bugs in tests.
        """
        signed_up = signup_and_login(
            self.client, email="fran@example.com", password="password123",
        )
        target_user_id = signed_up["user_id"]
        # Call /me three times — each response should resolve to the
        # same non-system user, proving the cookie round-trips.
        for _ in range(3):
            payload = self.client.get("/api/auth/me").json()
            self.assertEqual(payload["user_id"], target_user_id)
            self.assertFalse(payload["is_system"])


class SignupRaceTests(unittest.TestCase):
    """QA-found regression: parallel signups for the same email.

    Two callers race past ``get_user_by_email`` (neither has committed
    yet) and both reach ``store.create_user``. One wins; the loser's
    INSERT hits the UNIQUE-on-users.email constraint. Before the fix
    the loser's 500 leaked through the global handler. The fix catches
    the UniqueViolation / sqlite3.IntegrityError and returns the same
    409 / ``email_in_use`` shape the serial duplicate path uses.
    """

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_simulated_race_returns_409_not_500(self) -> None:
        """Monkey-patch ``get_user_by_email`` to always miss, so both callers
        reach ``create_user``; the second INSERT hits the UNIQUE constraint.

        This models the real race without needing two threads to
        interleave at exactly the right point; the observable outcome at
        the HTTP boundary is identical.

        ``PlanningStudioStore`` is a slotted dataclass, so patching the
        instance attribute raises AttributeError(read-only). Patch the
        class-level method instead; normal method resolution picks up
        our stub for the duration of the ``with`` block.
        """
        from planning_studio_service.store import PlanningStudioStore

        # First signup happens normally so the row exists.
        first = self.client.post(
            "/api/auth/signup",
            json={
                "email": "race@example.com",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(first.status_code, 201)
        self.client.cookies.clear()

        # Now pretend the pre-insert check always misses — that's the
        # race window. The INSERT must then trip UNIQUE.
        with patch.object(
            PlanningStudioStore,
            "get_user_by_email",
            lambda self, email: None,
        ):
            response = self.client.post(
                "/api/auth/signup",
                json={
                    "email": "race@example.com",
                    "password": "password123",
                    "terms_accepted": True,
                },
            )
        self.assertEqual(response.status_code, 409)
        detail = response.json().get("detail") or {}
        self.assertEqual(detail.get("error"), "email_in_use")

    def test_sqlite_integrity_error_on_unique_email_maps_to_409(self) -> None:
        """Direct unit-test of the recogniser helper.

        Double-insures the recogniser works even if the store later
        refactors its error surface (e.g. wraps sqlite3 errors into its
        own class). The recogniser is keyed on exception type + message
        substring, so the signup route stays decoupled from any
        psycopg-only types in sqlite-only test envs.
        """
        from planning_studio_service.auth import _is_unique_email_violation

        # The exact sqlite3 phrasing varies by build; keep this resilient
        # by asserting both canonical forms are recognised.
        canonical = sqlite3.IntegrityError(
            "UNIQUE constraint failed: users.email",
        )
        self.assertTrue(_is_unique_email_violation(canonical))

        alt = sqlite3.IntegrityError("column email is not unique")
        self.assertTrue(_is_unique_email_violation(alt))

        # Unrelated IntegrityError (e.g. NOT NULL on some other column)
        # must NOT be swallowed — the signup route must still 500 on
        # unexpected DB errors.
        unrelated = sqlite3.IntegrityError(
            "NOT NULL constraint failed: projects.title",
        )
        self.assertFalse(_is_unique_email_violation(unrelated))

        # Non-IntegrityError, non-UniqueViolation is not a match.
        self.assertFalse(_is_unique_email_violation(ValueError("nope")))


class SignupReservedTldTests(unittest.TestCase):
    """RFC 6761 reserved-TLD signup denylist — ``.example`` / ``.invalid`` / etc."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_example_tld_rejected_with_422(self) -> None:
        for email in (
            "qa@planning.example",
            "qa@planning.invalid",
            "qa@planning.test",
            "qa@localhost",
        ):
            with self.subTest(email=email):
                response = self.client.post(
                    "/api/auth/signup",
                    json={
                        "email": email,
                        "password": "password123",
                        "terms_accepted": True,
                    },
                )
                self.assertEqual(response.status_code, 422)
                # No user was created.
                self.assertIsNone(self.store.get_user_by_email(email))

    def test_real_tld_still_accepted(self) -> None:
        """Canary: a normal .ai signup must still succeed so the denylist
        hasn't accidentally blocked everything. Uses ``example.ai``, which
        is intentionally outside the reserved set (only the ``.example`` /
        ``.invalid`` / ``.test`` / ``localhost`` TLDs are blocked)."""
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "ok@example.ai",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(response.status_code, 201)


class ResetPasswordAliasTests(unittest.TestCase):
    """The /reset-password route accepts BOTH ``new_password`` and legacy ``password``."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        self.email = "alias@example.com"
        signup_and_login(
            self.client, email=self.email, password="original-horse-9",
        )
        # Drop the session so the reset call is anonymous-shaped.
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _mint_token(self) -> str:
        user = self.store.get_user_by_email(self.email)
        assert user is not None
        return self.store.create_password_reset_token(user["user_id"])

    def test_legacy_password_field_name_is_accepted(self) -> None:
        """An old client posting ``{token, password}`` must still reset."""
        raw = self._mint_token()
        response = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw, "password": "brandnew-legacy-42"},
        )
        self.assertEqual(response.status_code, 200)
        # The new password works; the old one doesn't.
        new_login = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": "brandnew-legacy-42"},
        )
        self.assertEqual(new_login.status_code, 200)

    def test_canonical_new_password_still_accepted(self) -> None:
        """Happy-path regression — the canonical field name hasn't regressed."""
        raw = self._mint_token()
        response = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw, "new_password": "brandnew-canonical-9"},
        )
        self.assertEqual(response.status_code, 200)

    def test_new_password_wins_when_both_present(self) -> None:
        """If a client sends BOTH keys, ``new_password`` is the source of truth."""
        raw = self._mint_token()
        response = self.client.post(
            "/api/auth/reset-password",
            json={
                "token": raw,
                "new_password": "canonical-wins-ok",
                "password": "other-value-ignored",
            },
        )
        self.assertEqual(response.status_code, 200)
        # Canonical value is what logs the user in.
        login = self.client.post(
            "/api/auth/login",
            json={"email": self.email, "password": "canonical-wins-ok"},
        )
        self.assertEqual(login.status_code, 200)

    def test_short_alias_still_rejected(self) -> None:
        """``password: "abc"`` must still fail min_length=8 on ``new_password``."""
        raw = self._mint_token()
        response = self.client.post(
            "/api/auth/reset-password",
            json={"token": raw, "password": "short"},
        )
        self.assertEqual(response.status_code, 422)


class TransferAnonErrorCodeSplitTests(unittest.TestCase):
    """``/api/v2/auth/transfer-anonymous-projects`` error codes must split."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Need an anon id the session knows about so auth checks pass
        # before the malformed-id / already-claimed branches are reached.
        me = self.client.get("/api/auth/me").json()
        self.anon_id = me["user_id"]
        signup_and_login(
            self.client, email="jane@example.com", password="password123",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_malformed_id_returns_malformed_anon_id(self) -> None:
        """Body that doesn't match the ``user-anon-<hex>`` shape → 400 malformed."""
        for bad in ("user-system", "not-anything", "user-anon-zzz", "user-anon-"):
            with self.subTest(bad=bad):
                response = self.client.post(
                    "/api/v2/auth/transfer-anonymous-projects",
                    json={"anonymous_user_id": bad},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    (response.json().get("detail") or {}).get("error"),
                    "malformed_anon_id",
                )

    def test_already_claimed_id_returns_409(self) -> None:
        """Correct shape, but the row has been promoted → 409 already_claimed.

        Simulate "previous anon has been promoted" by mutating the
        store row directly: anon users always have ``password_hash=None``
        (see ``auth._create_anon_user``), so writing a hash onto the
        ``user-anon-<hex>`` row tells the handler the row was already
        upgraded to a real account. The split must then return the
        distinct ``anon_user_already_claimed`` code instead of the
        generic ``anon_id_mismatch``.
        """
        with self.store._connect() as conn:  # noqa: SLF001 -- test-only
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                ("argon2-fake-hash-value-for-test", self.anon_id),
            )
            conn.commit()
        response = self.client.post(
            "/api/v2/auth/transfer-anonymous-projects",
            json={"anonymous_user_id": self.anon_id},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            (response.json().get("detail") or {}).get("error"),
            "anon_user_already_claimed",
        )


class LoginRateLimitTests(unittest.TestCase):
    """``/login`` is throttled to 10/minute/IP (audit hardening)."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Seed a user so login attempts have SOMETHING to verify against;
        # the rate limit fires on attempt count regardless of outcome.
        signup_and_login(
            self.client, email="rl@example.com", password="password123",
        )
        self.client.cookies.clear()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_eleventh_login_attempt_in_a_minute_is_rate_limited(self) -> None:
        # Hammer the route 10 times — each wrong-password → 401, not 429.
        for i in range(10):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "rl@example.com", "password": f"wrong-{i}"},
            )
            self.assertIn(
                response.status_code, (401, 429),
                f"iteration {i}: status={response.status_code}",
            )
        # The 11th must trip the 10/minute limit.
        final = self.client.post(
            "/api/auth/login",
            json={"email": "rl@example.com", "password": "wrong-final"},
        )
        self.assertEqual(
            final.status_code, 429,
            f"expected 429 on 11th login attempt, got {final.status_code}",
        )
        body = final.json()
        self.assertEqual(body.get("error"), "rate_limited")
        # Retry-after hint must be present and non-zero.
        self.assertIn("retry_after_seconds", body)
        self.assertGreater(body["retry_after_seconds"], 0)
        self.assertIn("Retry-After", final.headers)


class SignupRateLimitTests(unittest.TestCase):
    """``/signup`` is throttled to 5/hour/IP so bots can't burn accounts."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sixth_signup_from_same_ip_is_rate_limited(self) -> None:
        # 5 signups in a row — distinct emails so ``email_in_use`` doesn't
        # fire first. Each might return 201 or 429 if the hour window is
        # already partial from a prior test; accept both.
        for i in range(5):
            self.client.cookies.clear()
            response = self.client.post(
                "/api/auth/signup",
                json={
                    "email": f"bulk{i}@example.com",
                    "password": "password123",
                    "terms_accepted": True,
                },
            )
            self.assertIn(
                response.status_code, (201, 429),
                f"iteration {i}: {response.status_code} / {response.text}",
            )
        # The 6th must 429 — 5/hour is a hard ceiling.
        self.client.cookies.clear()
        final = self.client.post(
            "/api/auth/signup",
            json={
                "email": "bulk5@example.com",
                "password": "password123",
                "terms_accepted": True,
            },
        )
        self.assertEqual(
            final.status_code, 429,
            f"expected 429 on 6th signup, got {final.status_code}",
        )


class RequestIdMiddlewareTests(unittest.TestCase):
    """Every response carries ``X-Request-ID`` (new middleware)."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_response_carries_x_request_id(self) -> None:
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)
        self.assertTrue(response.headers["X-Request-ID"].strip())

    def test_inbound_x_request_id_is_echoed(self) -> None:
        """When a proxy sends an id we must round-trip it, not overwrite it."""
        response = self.client.get(
            "/api/auth/me",
            headers={"X-Request-ID": "caller-supplied-abc123"},
        )
        self.assertEqual(
            response.headers.get("X-Request-ID"), "caller-supplied-abc123",
        )

    def test_inbound_fly_request_id_is_echoed(self) -> None:
        """Fly's edge header must take precedence over a missing X-Request-ID."""
        response = self.client.get(
            "/api/auth/me",
            headers={"Fly-Request-Id": "fly-edge-zzz999"},
        )
        self.assertEqual(
            response.headers.get("X-Request-ID"), "fly-edge-zzz999",
        )


class GenericFiveHundredTests(unittest.TestCase):
    """Unhandled exceptions render as JSON with a ``reference`` correlation id."""

    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        # Starlette's TestClient re-raises by default; set raise_server_exceptions=False
        # so the exception handler actually runs.
        self.client = TestClient(
            self.client.app, raise_server_exceptions=False,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unhandled_exception_returns_json_shape(self) -> None:
        """Inject a route that raises; assert the JSON shape + X-Request-ID."""
        from fastapi import FastAPI  # noqa: F401 -- import keeps attribute path stable

        app = self.client.app

        async def _boom() -> None:  # pragma: no cover -- it's supposed to raise
            raise RuntimeError("kaboom")

        # Register lazily so only this test sees the booby-trapped route.
        app.add_api_route("/__test__/boom", _boom, methods=["GET"])
        try:
            response = self.client.get("/__test__/boom")
        finally:
            # Remove the route so other tests in the same process don't see it.
            app.router.routes = [
                r for r in app.router.routes
                if getattr(r, "path", None) != "/__test__/boom"
            ]
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body.get("error"), "internal_server_error")
        self.assertTrue(body.get("reference"))
        self.assertEqual(
            response.headers.get("X-Request-ID"), body["reference"],
        )


if __name__ == "__main__":
    unittest.main()
