"""Text embedding wrapper (W2 F5+).

Routes to OpenAI's text-embedding-3-small (1536 dims) — the
cheapest decent embedding model in May 2026, $0.02 per 1M
tokens. A 200-row CSV import → ~10K tokens → $0.0002 per import.
Negligible cost for the v4 demo phase.

Why OpenAI and not Anthropic?
- Anthropic doesn't ship first-party embeddings; they recommend
  Voyage AI which adds a separate API key + vendor.
- The classifier already uses Anthropic Claude haiku-4.5; using
  OpenAI for embeddings means the two pipelines fail
  independently. If Anthropic has an outage, embeddings still
  work; if OpenAI does, classification still works.

Feature flag
------------

``INSPIRA_EMBEDDINGS=1`` + ``OPENAI_API_KEY``. Off by default —
import paths short-circuit to ``embed_text() → None`` and skip
the cluster-assignment step.

Failure mode
------------

Any error (auth, rate-limit, network, parse) returns ``None``.
Cluster assignment skips items whose embedding is None — they
remain ``cluster_id = NULL`` until the next sync re-attempts. No
exception escapes this module to the caller.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536  # text-embedding-3-small output dimension

# Per-call timeout. Single-text embeddings are very fast (~150ms
# typical), so a 5s budget is loose-but-safe.
DEFAULT_TIMEOUT_S = 5.0


def is_embeddings_enabled() -> bool:
    """Env-gate. Returns False if either the flag is off or the
    OpenAI key is missing."""
    if os.environ.get("INSPIRA_EMBEDDINGS", "").strip() != "1":
        return False
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _truncate_for_embed(text: str) -> str:
    """Trim to a sane length. text-embedding-3-small accepts up to
    8191 tokens; we cap at ~1500 chars (~ 400 tokens) since
    feedback items rarely exceed that and we save tokens."""
    if not text:
        return ""
    cleaned = text.strip()
    if len(cleaned) > 1500:
        return cleaned[:1500]
    return cleaned


def embed_text(
    text: str,
    *,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[float] | None:
    """Compute an embedding for a single text. Returns None on
    error. Caller can inject ``client`` for tests; otherwise lazy-
    constructs an ``openai.OpenAI()``."""
    cleaned = _truncate_for_embed(text)
    if not cleaned:
        return None

    if client is None:
        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(timeout=timeout_s)
        except Exception:  # noqa: BLE001
            logger.warning(
                "embedding: openai SDK not importable",
                exc_info=True,
            )
            return None

    try:
        response = client.embeddings.create(model=model, input=cleaned)
    except Exception as exc:  # noqa: BLE001
        logger.info("embedding: API call failed (%s); skipping", exc)
        return None

    try:
        data = getattr(response, "data", None) or []
        if not data:
            return None
        first = data[0]
        vec = getattr(first, "embedding", None) or first.get("embedding")  # type: ignore[union-attr]
        if not isinstance(vec, list):
            return None
        # Sanity check the dimension — guards against config drift
        # (different model accidentally selected).
        if len(vec) != EMBEDDING_DIMS:
            logger.info(
                "embedding: unexpected dimension %d; skipping",
                len(vec),
            )
            return None
        return [float(v) for v in vec]
    except Exception:  # noqa: BLE001
        return None


def embed_texts_batch(
    texts: list[str],
    *,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[list[float] | None]:
    """Embed multiple texts in a single API call.

    Returns a list of per-text vectors (or None for failures);
    same order as input.

    OpenAI's embeddings endpoint accepts ``input`` as either a
    string or a list of strings. Batched is cheaper + faster.
    """
    if not texts:
        return []
    cleaned = [_truncate_for_embed(t) for t in texts]
    # Drop empty entries — flag them as None in the output but
    # don't waste an embed call.
    indexed = [(i, t) for i, t in enumerate(cleaned) if t]
    if not indexed:
        return [None] * len(texts)

    if client is None:
        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(timeout=timeout_s)
        except Exception:  # noqa: BLE001
            logger.warning(
                "embedding: openai SDK not importable",
                exc_info=True,
            )
            return [None] * len(texts)

    try:
        response = client.embeddings.create(
            model=model, input=[t for _, t in indexed]
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("embedding: batch API call failed (%s)", exc)
        return [None] * len(texts)

    out: list[list[float] | None] = [None] * len(texts)
    try:
        data = getattr(response, "data", None) or []
        if len(data) != len(indexed):
            return [None] * len(texts)
        for (orig_idx, _), entry in zip(indexed, data):
            vec = getattr(entry, "embedding", None) or entry.get("embedding")  # type: ignore[union-attr]
            if isinstance(vec, list) and len(vec) == EMBEDDING_DIMS:
                out[orig_idx] = [float(v) for v in vec]
    except Exception:  # noqa: BLE001
        return [None] * len(texts)
    return out
