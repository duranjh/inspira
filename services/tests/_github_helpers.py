"""Shared GitHub-test fixtures: synthetic RSA key + mock HTTP factory.

Kept out of ``_helpers.py`` so the existing tests don't pay the
cryptography import cost.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def make_test_rsa_pem() -> str:
    """Generate a synthetic RSA-2048 private key for App JWT tests.

    Real GitHub Apps use RSA-2048 per their docs. ~80ms to
    generate; tests cache it at the class level.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


def mock_async_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """Build an AsyncClient backed by MockTransport.

    Tests call this with a handler that switches on
    ``request.url`` to dispatch GitHub responses by route.
    """
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)
