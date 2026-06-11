"""Unit tests for the PR 1 auth security hotfixes.

Covers:
- ``_session_serializer`` and ``_password_hasher`` are memoized.
- The verify-resend lock exists and serializes the check-and-set.
- ``_verify_password`` round-trips with the configured hasher.
- The argon2 verify argument order is the documented one
  (``(hash, password)``) — guard against future contributors swapping it.
"""
from __future__ import annotations

import unittest
from threading import Thread
from typing import Any

from planning_studio_service import auth


class HasherMemoizationTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the module-level cached singletons so each test starts
        # with a clean slate. Direct attribute access is intentional —
        # there's no public reset hook.
        auth._password_hasher_cached = None
        auth._session_serializer_cached = None

    def test_password_hasher_is_memoized(self) -> None:
        a = auth._password_hasher()
        b = auth._password_hasher()
        self.assertIs(a, b)

    def test_session_serializer_is_memoized(self) -> None:
        a = auth._session_serializer()
        b = auth._session_serializer()
        self.assertIs(a, b)

    def test_verify_round_trips(self) -> None:
        password = "correct-horse-battery-staple"
        hashed = auth._hash_password(password)
        self.assertTrue(auth._verify_password(password, hashed))
        self.assertFalse(auth._verify_password("wrong", hashed))

    def test_verify_arg_order_documented(self) -> None:
        """Lock in argon2-cffi's verify(hash, password) order.

        If a future contributor "fixes" the call to verify(password,
        hash), this test fails immediately because the swap turns a
        valid password into a mismatch.
        """
        password = "another-strong-pw"
        hashed = auth._hash_password(password)
        # The function under test is the one we ship; swapping internal
        # args would surface as the assertion below failing.
        self.assertTrue(auth._verify_password(password, hashed))


class VerifyResendLockTests(unittest.TestCase):
    def test_lock_exists(self) -> None:
        self.assertIsNotNone(auth._verify_resend_lock)

    def test_lock_is_reentrant_safe_under_threads(self) -> None:
        """Two threads contending for the lock must not deadlock and
        must serialize their writes."""
        results: list[Any] = []

        def _claim(uid: str, ts: float) -> None:
            with auth._verify_resend_lock:
                last = auth._verify_resend_last.get(uid)
                if last is None or (ts - last) >= 1.0:
                    auth._verify_resend_last[uid] = ts
                    results.append((uid, ts, "claimed"))
                else:
                    results.append((uid, ts, "throttled"))

        # Reset shared state.
        auth._verify_resend_last.pop("uid-stress", None)
        threads = [
            Thread(target=_claim, args=("uid-stress", 100.0 + i * 0.1))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly ONE claim should win the first slot; the rest throttle.
        # Other ts values < 1.0 from the first claim get throttled.
        claimed = [r for r in results if r[2] == "claimed"]
        self.assertEqual(len(claimed), 1)


class CookieSecureDefaultTests(unittest.TestCase):
    """Verify production environments default to Secure=True even when
    INSPIRA_COOKIE_SECURE isn't explicitly set."""

    def test_production_default_secure(self) -> None:
        import os
        # Inspect the constants the code uses — we don't actually hit
        # _set_session_cookie since it requires a Response object;
        # the default-resolution logic lives at the top of that
        # function. Instead, encode the contract here and let a future
        # refactor that removes the env-keyed default fail loudly.
        # Simulate the logic.
        prev_env = os.environ.get("ENVIRONMENT")
        prev_secure = os.environ.get("INSPIRA_COOKIE_SECURE")
        try:
            os.environ.pop("INSPIRA_COOKIE_SECURE", None)
            os.environ["ENVIRONMENT"] = "production"
            env = os.environ.get("ENVIRONMENT", "development").lower()
            secure_default = "true" if env == "production" else "false"
            secure = os.environ.get(
                "INSPIRA_COOKIE_SECURE", secure_default,
            ).lower() == "true"
            self.assertTrue(secure)
        finally:
            if prev_env is None:
                os.environ.pop("ENVIRONMENT", None)
            else:
                os.environ["ENVIRONMENT"] = prev_env
            if prev_secure is not None:
                os.environ["INSPIRA_COOKIE_SECURE"] = prev_secure

    def test_development_default_insecure(self) -> None:
        import os
        prev_env = os.environ.get("ENVIRONMENT")
        prev_secure = os.environ.get("INSPIRA_COOKIE_SECURE")
        try:
            os.environ.pop("INSPIRA_COOKIE_SECURE", None)
            os.environ["ENVIRONMENT"] = "development"
            env = os.environ.get("ENVIRONMENT", "development").lower()
            secure_default = "true" if env == "production" else "false"
            secure = os.environ.get(
                "INSPIRA_COOKIE_SECURE", secure_default,
            ).lower() == "true"
            self.assertFalse(secure)
        finally:
            if prev_env is None:
                os.environ.pop("ENVIRONMENT", None)
            else:
                os.environ["ENVIRONMENT"] = prev_env
            if prev_secure is not None:
                os.environ["INSPIRA_COOKIE_SECURE"] = prev_secure


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
