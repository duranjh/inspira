"""Tests for Bring Your Own Key (BYOK).

Three layers:

1. Pure-function tests of ``byok.encrypt_api_key`` / ``decrypt_api_key`` and
   the store facade ``byok.store.set/get/clear_user_byok``.
2. Provider-verification tests — ``verify_openai_key`` / ``verify_anthropic_key``
   against mocked ``httpx.get`` responses.
3. HTTP-level tests of the three routes + the credit-skip + ``X-Inspira-Llm-Mode``
   header wiring in ``v2_topic_turn``.

Run with ``INSPIRA_BYOK_SECRET`` set (``_helpers`` sets a deterministic one).
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

try:
    from ._helpers import (
        fake_kickoff_response,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )
except ImportError:
    from _helpers import (  # type: ignore[no-redef]
        fake_kickoff_response,
        fake_turn_response,
        make_test_app,
        signup_and_login,
    )

from planning_studio_service import byok as byok_module


def _set_fernet_key() -> None:
    """Ensure the module picks up whatever secret ``_helpers`` set.

    The byok module caches the Fernet singleton — resetting it here lets
    a test that tweaks the env var see the new value. Individual tests
    only need this when they override ``INSPIRA_BYOK_SECRET``.
    """
    byok_module.reset_fernet_cache_for_tests()


class EncryptionRoundTripTests(unittest.TestCase):
    """``encrypt_api_key`` / ``decrypt_api_key`` round-trip cleanly."""

    def setUp(self) -> None:
        _set_fernet_key()

    def test_round_trip_openai_like_key(self) -> None:
        raw = "sk-proj-" + "a" * 48
        ciphertext = byok_module.encrypt_api_key(raw)
        self.assertNotEqual(ciphertext, raw, "ciphertext must not equal plaintext")
        # Fernet tokens start with a version byte (0x80 = 'gAAAAA' in base64).
        self.assertTrue(ciphertext.startswith("gAAAAA"))
        self.assertEqual(byok_module.decrypt_api_key(ciphertext), raw)

    def test_round_trip_anthropic_like_key(self) -> None:
        raw = "sk-ant-" + "z" * 80
        ciphertext = byok_module.encrypt_api_key(raw)
        self.assertEqual(byok_module.decrypt_api_key(ciphertext), raw)

    def test_encrypt_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            byok_module.encrypt_api_key("")
        with self.assertRaises(ValueError):
            byok_module.encrypt_api_key("   ")

    def test_decrypt_raises_on_garbage(self) -> None:
        with self.assertRaises(RuntimeError):
            byok_module.decrypt_api_key("not-a-valid-fernet-token")


class StoreFacadeTests(unittest.TestCase):
    """``byok.store`` round-trips through ``PlanningStudioStore``."""

    def setUp(self) -> None:
        _set_fernet_key()
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="byoksetter@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_set_then_get_returns_plaintext(self) -> None:
        raw = "sk-proj-example-abc123"
        byok_module.store.set_user_byok(
            self.store, self.user_id, "openai", raw,
        )
        got = byok_module.store.get_user_byok(
            self.store, self.user_id, "openai",
        )
        self.assertEqual(got, raw)

    def test_get_missing_returns_none(self) -> None:
        got = byok_module.store.get_user_byok(
            self.store, self.user_id, "openai",
        )
        self.assertIsNone(got)

    def test_clear_removes_stored_key(self) -> None:
        byok_module.store.set_user_byok(
            self.store, self.user_id, "openai", "sk-proj-abc",
        )
        byok_module.store.clear_user_byok(
            self.store, self.user_id, "openai",
        )
        self.assertIsNone(
            byok_module.store.get_user_byok(
                self.store, self.user_id, "openai",
            )
        )

    def test_providers_are_isolated(self) -> None:
        """Setting openai must not leak into anthropic, and vice versa."""
        byok_module.store.set_user_byok(
            self.store, self.user_id, "openai", "sk-proj-openai",
        )
        byok_module.store.set_user_byok(
            self.store, self.user_id, "anthropic", "sk-ant-1234",
        )
        self.assertEqual(
            byok_module.store.get_user_byok(
                self.store, self.user_id, "openai",
            ),
            "sk-proj-openai",
        )
        self.assertEqual(
            byok_module.store.get_user_byok(
                self.store, self.user_id, "anthropic",
            ),
            "sk-ant-1234",
        )
        # Clearing one does not affect the other.
        byok_module.store.clear_user_byok(
            self.store, self.user_id, "openai",
        )
        self.assertIsNone(
            byok_module.store.get_user_byok(
                self.store, self.user_id, "openai",
            )
        )
        self.assertEqual(
            byok_module.store.get_user_byok(
                self.store, self.user_id, "anthropic",
            ),
            "sk-ant-1234",
        )

    def test_status_reflects_configured_state(self) -> None:
        status_before = byok_module.store.status(self.store, self.user_id)
        self.assertFalse(status_before["openai"]["configured"])
        self.assertIsNone(status_before["openai"]["last_verified_at"])
        self.assertFalse(status_before["anthropic"]["configured"])

        byok_module.store.set_user_byok(
            self.store, self.user_id, "openai", "sk-proj-xyz",
        )
        status_after = byok_module.store.status(self.store, self.user_id)
        self.assertTrue(status_after["openai"]["configured"])
        self.assertIsNotNone(status_after["openai"]["last_verified_at"])
        self.assertFalse(status_after["anthropic"]["configured"])

    def test_unknown_provider_raises(self) -> None:
        with self.assertRaises(ValueError):
            byok_module.store.set_user_byok(
                self.store, self.user_id, "azure", "sk-xxx",
            )


class VerifyKeyTests(unittest.TestCase):
    """``verify_openai_key`` / ``verify_anthropic_key`` call the provider."""

    def setUp(self) -> None:
        _set_fernet_key()

    def _fake_response(self, status_code: int) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        return resp

    def test_verify_openai_happy_path(self) -> None:
        with patch.object(byok_module.httpx, "get") as mock_get:
            mock_get.return_value = self._fake_response(200)
            ok = byok_module.verify_openai_key("sk-proj-valid")
        self.assertTrue(ok)
        args, kwargs = mock_get.call_args
        self.assertIn("openai.com", args[0])
        self.assertEqual(
            kwargs["headers"]["Authorization"], "Bearer sk-proj-valid",
        )

    def test_verify_openai_rejects_401(self) -> None:
        with patch.object(byok_module.httpx, "get") as mock_get:
            mock_get.return_value = self._fake_response(401)
            ok = byok_module.verify_openai_key("sk-proj-bad")
        self.assertFalse(ok)

    def test_verify_openai_swallows_network_error(self) -> None:
        with patch.object(byok_module.httpx, "get") as mock_get:
            mock_get.side_effect = byok_module.httpx.ConnectError("oops")
            ok = byok_module.verify_openai_key("sk-proj-network-fail")
        self.assertFalse(ok)

    def test_verify_anthropic_happy_path(self) -> None:
        with patch.object(byok_module.httpx, "get") as mock_get:
            mock_get.return_value = self._fake_response(200)
            ok = byok_module.verify_anthropic_key("sk-ant-valid")
        self.assertTrue(ok)
        args, kwargs = mock_get.call_args
        self.assertIn("anthropic.com", args[0])
        self.assertEqual(kwargs["headers"]["x-api-key"], "sk-ant-valid")
        self.assertIn("anthropic-version", kwargs["headers"])

    def test_verify_anthropic_rejects_403(self) -> None:
        with patch.object(byok_module.httpx, "get") as mock_get:
            mock_get.return_value = self._fake_response(403)
            ok = byok_module.verify_anthropic_key("sk-ant-forbidden")
        self.assertFalse(ok)

    def test_verify_key_dispatches_on_provider(self) -> None:
        with patch.object(byok_module, "verify_openai_key", return_value=True) as m:
            self.assertTrue(byok_module.verify_key("openai", "sk-proj-x"))
            m.assert_called_once()
        with patch.object(byok_module, "verify_anthropic_key", return_value=True) as m:
            self.assertTrue(byok_module.verify_key("anthropic", "sk-ant-x"))
            m.assert_called_once()


class ByokApiRouteTests(unittest.TestCase):
    """``POST/DELETE/GET /api/v2/auth/byok/*`` routes."""

    def setUp(self) -> None:
        _set_fernet_key()
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="byokapi@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_status_empty_on_fresh_account(self) -> None:
        resp = self.client.get("/api/v2/auth/byok/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["openai"]["configured"])
        self.assertIsNone(body["openai"]["last_verified_at"])
        self.assertFalse(body["anthropic"]["configured"])

    def test_save_rejects_unknown_provider(self) -> None:
        resp = self.client.post(
            "/api/v2/auth/byok",
            json={"provider": "azure", "api_key": "sk-whatever"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "invalid_provider")

    def test_save_rejects_empty_key(self) -> None:
        resp = self.client.post(
            "/api/v2/auth/byok",
            json={"provider": "openai", "api_key": ""},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"]["error"], "api_key_required")

    def test_save_rejects_key_that_fails_verification(self) -> None:
        with patch.object(byok_module, "verify_openai_key", return_value=False):
            resp = self.client.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-bad"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["detail"]["error"], "key_verification_failed",
        )
        # And nothing was persisted.
        status = self.client.get("/api/v2/auth/byok/status").json()
        self.assertFalse(status["openai"]["configured"])

    def test_save_happy_path_encrypts_and_returns_verified_at(self) -> None:
        with patch.object(byok_module, "verify_openai_key", return_value=True):
            resp = self.client.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-good"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["provider"], "openai")
        self.assertIsNotNone(body["verified_at"])
        # Response NEVER echoes the raw key back.
        self.assertNotIn("sk-proj-good", resp.text)
        # Ciphertext is stored, not plaintext.
        raw_ct = self.store.get_byok_ciphertext(self.user_id, "openai")
        self.assertIsNotNone(raw_ct)
        self.assertNotEqual(raw_ct, "sk-proj-good")
        # But round-trip decryption yields the original.
        self.assertEqual(
            byok_module.store.get_user_byok(self.store, self.user_id, "openai"),
            "sk-proj-good",
        )

    def test_delete_clears_stored_key(self) -> None:
        with patch.object(byok_module, "verify_openai_key", return_value=True):
            self.client.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-del"},
            )
        resp = self.client.delete("/api/v2/auth/byok/openai")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["cleared"])
        status = self.client.get("/api/v2/auth/byok/status").json()
        self.assertFalse(status["openai"]["configured"])


class ByokTurnWiringTests(unittest.TestCase):
    """BYOK threads ``api_key_override`` through to the adapter and
    surfaces the X-Inspira-Llm-Mode header. PR 2 deleted the credit
    ledger so the old "skips credit charge" assertion is gone — the
    header is now the source of truth for which provider key was
    used."""

    def setUp(self) -> None:
        _set_fernet_key()
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client, email="byokturn@example.com")
        me = self.client.get("/api/auth/me").json()
        self.user_id = me["user_id"]

        # Seed a project + topic via kickoff (mocked adapter).
        self.adapter.kickoff.return_value = fake_kickoff_response()
        kick = self.client.post(
            "/api/v2/projects/proj-byok/kickoff",
            json={"user_idea": "A small wine festival."},
        ).json()
        self.project_id = "proj-byok"
        self.venue_id = next(
            t["topic_id"] for t in kick["topics"] if t["title"] == "Venue"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_turn_uses_byok_key_when_set(self) -> None:
        # Stash a key (skip real verification).
        byok_module.store.set_user_byok(
            self.store, self.user_id, "openai", "sk-proj-user-key",
        )
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        resp = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Outdoor park preferred."},
        )
        self.assertEqual(resp.status_code, 201, resp.text)

        # The adapter saw the user's key.
        call_kwargs = self.adapter.topic_turn.call_args.kwargs
        self.assertEqual(call_kwargs["api_key_override"], "sk-proj-user-key")

        # Response header advertises BYOK mode.
        self.assertEqual(resp.headers.get("X-Inspira-Llm-Mode"), "byok")

    def test_turn_without_byok_uses_house_key(self) -> None:
        """Baseline — no stored key → house path, header="house"."""
        self.adapter.topic_turn.return_value = fake_turn_response(action="ask")
        resp = self.client.post(
            f"/api/v2/topics/{self.venue_id}/turn",
            json={"user_answer": "Budget first."},
        )
        self.assertEqual(resp.status_code, 201, resp.text)

        call_kwargs = self.adapter.topic_turn.call_args.kwargs
        self.assertIsNone(call_kwargs["api_key_override"])
        self.assertEqual(resp.headers.get("X-Inspira-Llm-Mode"), "house")


class ByokIdorTests(unittest.TestCase):
    """User A cannot read or modify user B's BYOK state."""

    def setUp(self) -> None:
        _set_fernet_key()
        self.client_a, self.store, self.adapter, self.temp_dir = make_test_app()
        signup_and_login(self.client_a, email="usera@example.com")
        me_a = self.client_a.get("/api/auth/me").json()
        self.user_a_id = me_a["user_id"]

        # Second client, fresh session cookies (same app/store).
        from fastapi.testclient import TestClient
        self.client_b = TestClient(self.client_a.app)
        signup_and_login(self.client_b, email="userb@example.com")
        me_b = self.client_b.get("/api/auth/me").json()
        self.user_b_id = me_b["user_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_user_b_cannot_see_user_a_byok_status(self) -> None:
        # A stores a key.
        with patch.object(byok_module, "verify_openai_key", return_value=True):
            self.client_a.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-A-secret"},
            )

        # B hits the status endpoint; sees their OWN status, not A's.
        resp_b = self.client_b.get("/api/v2/auth/byok/status")
        self.assertEqual(resp_b.status_code, 200)
        body_b = resp_b.json()
        self.assertFalse(body_b["openai"]["configured"])
        self.assertFalse(body_b["anthropic"]["configured"])

    def test_user_b_cannot_clear_user_a_key(self) -> None:
        with patch.object(byok_module, "verify_openai_key", return_value=True):
            self.client_a.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-A-secret"},
            )
        # B issues DELETE — the route is scoped by session cookie, so
        # this only touches B's row (who never had a key). A's key must
        # survive.
        self.client_b.delete("/api/v2/auth/byok/openai")
        a_status = self.client_a.get("/api/v2/auth/byok/status").json()
        self.assertTrue(a_status["openai"]["configured"])

    def test_user_b_save_does_not_overwrite_user_a_key(self) -> None:
        with patch.object(byok_module, "verify_openai_key", return_value=True):
            self.client_a.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-A-secret"},
            )
            self.client_b.post(
                "/api/v2/auth/byok",
                json={"provider": "openai", "api_key": "sk-proj-B-key"},
            )
        # Each user sees their OWN stored key via decryption.
        self.assertEqual(
            byok_module.store.get_user_byok(
                self.store, self.user_a_id, "openai",
            ),
            "sk-proj-A-secret",
        )
        self.assertEqual(
            byok_module.store.get_user_byok(
                self.store, self.user_b_id, "openai",
            ),
            "sk-proj-B-key",
        )


if __name__ == "__main__":
    unittest.main()
