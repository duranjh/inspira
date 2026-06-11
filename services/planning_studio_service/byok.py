"""Bring Your Own Key (BYOK) — encryption + storage + live verification.

Technical users paste their own OpenAI or Anthropic API key into Account
Settings. The backend encrypts with Fernet (symmetric, authenticated) and
persists the ciphertext on the ``users`` row. Planner turns that find a
stored key for the provider they're about to call pass it as an
``api_key_override`` to the adapter and skip the credit charge — the user
is paying the provider directly.

Contract
--------

- ``INSPIRA_BYOK_SECRET`` env var MUST be set at boot. It is a raw
  URL-safe base64 Fernet key (44 chars). Generate with::

      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

  Missing / malformed secrets fail loudly at first-use. Silent
  fall-through to unencrypted storage would be a data-loss risk the
  moment an operator forgot to set the env var.

- The raw key never appears in logs and never leaves ``byok.py`` /
  ``store.py`` unencrypted. HTTP responses never echo the raw value back
  — the status endpoint carries a boolean and a ``last_verified_at``
  timestamp only.

- Rotation: stored ciphertexts are self-describing via Fernet's version
  byte. A future secondary key can decrypt old ciphertext while a new
  primary encrypts incoming writes. We keep the API simple for now —
  single-key — and add a MultiFernet indirection when the first rotation
  is scheduled.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fernet — module-level singleton, built lazily so imports don't crash when
# the secret is unset in a unit test that only touches shapes.
# ---------------------------------------------------------------------------


_BYOK_SECRET_ENV = "INSPIRA_BYOK_SECRET"
_fernet_singleton: Any | None = None


def _get_fernet() -> Any:
    """Return a cached Fernet instance. Raises if the secret is missing.

    We cache the instance on the module so repeated encrypt/decrypt calls
    don't pay the parse cost on every request. Rotation will replace the
    singleton with a MultiFernet chain.
    """
    global _fernet_singleton
    if _fernet_singleton is not None:
        return _fernet_singleton

    raw = os.environ.get(_BYOK_SECRET_ENV, "").strip()
    if not raw:
        # Hard fail — the BYOK feature is dead if we can't encrypt. We
        # don't silently degrade to plaintext storage because that would
        # be a regulatory and data-loss landmine. Refuse to serve.
        raise RuntimeError(
            f"{_BYOK_SECRET_ENV} is not set. BYOK is unavailable. "
            "Generate a key with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )

    try:
        from cryptography.fernet import Fernet  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — pyproject pins cryptography
        raise RuntimeError(
            "The 'cryptography' package is not installed. "
            "Run: pip install cryptography"
        ) from exc

    try:
        _fernet_singleton = Fernet(raw.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        # Malformed key — non-base64 or wrong length. Fernet keys are
        # exactly 44 URL-safe base64 characters. Surface the specific
        # failure so ops gets a clean "fix your env var" message.
        raise RuntimeError(
            f"{_BYOK_SECRET_ENV} is not a valid Fernet key (base64, 32 bytes). "
            "Generate a new key with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc
    return _fernet_singleton


def reset_fernet_cache_for_tests() -> None:
    """Clear the cached Fernet singleton. Test-only hook.

    Exposed so a test that sets ``INSPIRA_BYOK_SECRET`` after importing
    the module can still pick up the value. Production code never calls
    this.
    """
    global _fernet_singleton
    _fernet_singleton = None


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


def encrypt_api_key(raw: str) -> str:
    """Return the Fernet ciphertext for a raw API key.

    Empty / whitespace input raises — the caller should have rejected
    it upstream.
    """
    if not raw or not raw.strip():
        raise ValueError("api_key must be a non-empty string")
    token_bytes: bytes = _get_fernet().encrypt(raw.encode("utf-8"))
    return token_bytes.decode("utf-8")


def decrypt_api_key(encrypted: str) -> str:
    """Return the plaintext key from a Fernet ciphertext.

    Raises ``RuntimeError`` on corruption / unknown version. Callers in
    the request path should wrap this and degrade gracefully (fall back
    to the house key) rather than letting a single bad row 500 every
    turn for one user.
    """
    if not encrypted:
        raise ValueError("encrypted ciphertext is empty")
    try:
        from cryptography.fernet import InvalidToken  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cryptography not installed") from exc
    try:
        plain: bytes = _get_fernet().decrypt(encrypted.encode("utf-8"))
    except InvalidToken as exc:
        raise RuntimeError(
            "BYOK ciphertext decryption failed — the stored row was "
            "either corrupted or encrypted with a different key. "
            "Rotate the user's key via the clear/save flow."
        ) from exc
    return plain.decode("utf-8")


# ---------------------------------------------------------------------------
# Provider type
# ---------------------------------------------------------------------------


Provider = Literal["openai", "anthropic"]

_VALID_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic"})


def _validate_provider(provider: str) -> Provider:
    """Return ``provider`` when valid, raise ``ValueError`` otherwise."""
    if provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"Unknown BYOK provider: {provider!r}. "
            f"Valid: {sorted(_VALID_PROVIDERS)}"
        )
    return provider  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Live verification — cheap pings to confirm the key is valid.
# ---------------------------------------------------------------------------


_VERIFY_TIMEOUT_S = 5.0


def verify_openai_key(key: str) -> bool:
    """Hit OpenAI's ``/v1/models`` endpoint and return True on 2xx.

    Any other status or network error returns False. We never raise —
    verification is a UX hint ("Key verified" vs "That key didn't
    authenticate") and failing hard on a transient outage would be a
    strictly worse product.
    """
    if not key or not key.strip():
        return False
    try:
        resp = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key.strip()}"},
            timeout=_VERIFY_TIMEOUT_S,
        )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("verify_openai_key network error: %s", exc)
        return False
    return 200 <= resp.status_code < 300


def verify_anthropic_key(key: str) -> bool:
    """Hit Anthropic's ``/v1/models`` endpoint and return True on 2xx.

    Anthropic requires an ``anthropic-version`` header on every call; we
    send the 2023-06-01 stable baseline. The endpoint returns 200 on a
    valid key, 401/403 on a bad one.
    """
    if not key or not key.strip():
        return False
    try:
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": key.strip(),
                "anthropic-version": "2023-06-01",
            },
            timeout=_VERIFY_TIMEOUT_S,
        )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning("verify_anthropic_key network error: %s", exc)
        return False
    return 200 <= resp.status_code < 300


def verify_key(provider: str, key: str) -> bool:
    """Dispatch helper used by the API route."""
    provider = _validate_provider(provider)
    if provider == "openai":
        return verify_openai_key(key)
    if provider == "anthropic":
        return verify_anthropic_key(key)
    return False  # unreachable — _validate_provider raised


# ---------------------------------------------------------------------------
# Store facade — thin wrappers around the raw-ciphertext accessors on
# ``PlanningStudioStore``. We hang the verified-at timestamp inside the
# user's ``metadata_json`` blob to avoid another column + migration; the
# entire BYOK feature is two columns + one small timestamp-per-provider.
# ---------------------------------------------------------------------------


class _Store:
    """Attribute-style access so callers read ``store.set_user_byok(...)``.

    We hand-roll this rather than using a ``@staticmethod`` class because
    the test suite mocks the module-level ``store.set_user_byok`` with
    ``patch`` — a bound method is easier to monkeypatch than a callable
    on a class.
    """

    def set_user_byok(
        self,
        db: Any,
        user_id: str,
        provider: str,
        raw_key: str,
    ) -> None:
        """Encrypt + persist a raw key + stamp ``last_verified_at``.

        Overwrites any prior ciphertext for this provider. The caller is
        responsible for verifying the key FIRST (via ``verify_key``); this
        helper trusts its input and records the verification timestamp.
        """
        provider = _validate_provider(provider)
        if not raw_key or not raw_key.strip():
            raise ValueError("raw_key must be non-empty")
        ciphertext = encrypt_api_key(raw_key.strip())
        db.set_byok_ciphertext(user_id, provider, ciphertext)
        _set_verified_at(db, user_id, provider, _now_iso())

    def get_user_byok(
        self,
        db: Any,
        user_id: str,
        provider: str,
    ) -> str | None:
        """Return the decrypted raw key for this user, or ``None``.

        Decryption errors (corrupt ciphertext, stale Fernet key) log a
        warning and return ``None`` so the caller falls back to the
        house key path instead of 500'ing the turn.
        """
        provider = _validate_provider(provider)
        ciphertext = db.get_byok_ciphertext(user_id, provider)
        if ciphertext is None:
            return None
        try:
            return decrypt_api_key(ciphertext)
        except RuntimeError as exc:
            logger.warning(
                "byok decrypt failed user=%s provider=%s: %s",
                user_id, provider, exc,
            )
            return None

    def clear_user_byok(
        self,
        db: Any,
        user_id: str,
        provider: str,
    ) -> None:
        """Remove the stored key + verification timestamp for this provider."""
        provider = _validate_provider(provider)
        db.set_byok_ciphertext(user_id, provider, None)
        _set_verified_at(db, user_id, provider, None)

    def status(
        self,
        db: Any,
        user_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Return the non-secret status block for both providers.

        Shape matches the API response for ``GET /api/v2/auth/byok/status``::

            {
                "openai":    {"configured": bool, "last_verified_at": iso|null},
                "anthropic": {"configured": bool, "last_verified_at": iso|null},
            }
        """
        out: dict[str, dict[str, Any]] = {}
        verified_map = _read_verified_at_map(db, user_id)
        for provider in ("openai", "anthropic"):
            out[provider] = {
                "configured": db.get_byok_ciphertext(user_id, provider) is not None,
                "last_verified_at": verified_map.get(provider),
            }
        return out


store = _Store()


# ---------------------------------------------------------------------------
# Timestamp bookkeeping — stored inside ``users.metadata_json``.
# ---------------------------------------------------------------------------


_META_KEY = "byok_verified_at"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_user_metadata(db: Any, user_id: str) -> dict[str, Any]:
    row = db.get_user_by_id(user_id) if hasattr(db, "get_user_by_id") else None
    if row is None:
        return {}
    meta = row.get("metadata") if isinstance(row, dict) else None
    if not isinstance(meta, dict):
        return {}
    return dict(meta)


def _write_user_metadata(db: Any, user_id: str, metadata: dict[str, Any]) -> None:
    if hasattr(db, "update_user_metadata"):
        db.update_user_metadata(user_id, metadata)
        return
    # Fallback: direct JSON column write. Kept for stores without the
    # helper method; the built-in PlanningStudioStore carries the helper
    # so production always hits the branch above.
    import json
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db._connect() as connection:  # noqa: SLF001 — intentional fallback
        connection.execute(
            "UPDATE users SET metadata_json = ?, updated_at = ? WHERE user_id = ?",
            (json.dumps(metadata), now, user_id),
        )
        connection.commit()


def _read_verified_at_map(db: Any, user_id: str) -> dict[str, str | None]:
    meta = _read_user_metadata(db, user_id)
    blob = meta.get(_META_KEY) or {}
    if not isinstance(blob, dict):
        return {}
    return {
        "openai": blob.get("openai") or None,
        "anthropic": blob.get("anthropic") or None,
    }


def _set_verified_at(
    db: Any, user_id: str, provider: str, iso: str | None,
) -> None:
    meta = _read_user_metadata(db, user_id)
    blob = meta.get(_META_KEY)
    if not isinstance(blob, dict):
        blob = {}
    if iso is None:
        blob.pop(provider, None)
    else:
        blob[provider] = iso
    meta[_META_KEY] = blob
    _write_user_metadata(db, user_id, meta)
