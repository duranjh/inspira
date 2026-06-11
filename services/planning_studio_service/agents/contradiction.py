"""Contradiction-detection adapter — LLM judge for "does decision A
conflict with any recent decision in the project?".

The contract is intentionally narrow: one call = one yes/no + the
specific decision_id it conflicts with. We run on a cheap, fast model
(gpt-4o-mini) because this fires on every decision save; a 4+ second
latency here would feel like the app froze.

Fail-open: any exception → return no-contradiction. Never block the
user's save.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

# Module-level OpenAI client, lazy-initialized. Tests can inject a
# fake via ContradictionAdapter(client=fake).
_openai_client: Any | None = None


def _get_openai_client() -> Any:
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openai package not installed") from exc
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    _openai_client = OpenAI(api_key=api_key, timeout=5.0)
    return _openai_client


_PROMPT = """You decide whether a new decision contradicts any earlier
decision in the same project. A contradiction means the two cannot
both be true at the same time for the same project — opposing choices
("will use X" vs "will not use X"), mutually exclusive numbers
(guest count of 10 vs guest count of 50), incompatible strategies,
etc. Two decisions that address DIFFERENT aspects of the same topic
are NOT a contradiction.

Return ONLY valid JSON matching this schema:
  {"contradicts_id": <string decision_id of the earlier decision that is contradicted, or null>,
   "reason": <short one-sentence plain-English explanation, or null>}

If nothing contradicts, return {"contradicts_id": null, "reason": null}.
"""


@dataclass(slots=True)
class ContradictionConfig:
    model: str = "gpt-4o-mini"
    timeout_s: float = 5.0
    max_earlier: int = 30


class ContradictionAdapter:
    """Single-method adapter. The route layer calls
    ``check(new_statement, earlier_decisions)`` after persisting a new
    decision; returns {"contradicts_id": str|None, "reason": str|None}.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        config: ContradictionConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or ContradictionConfig()

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        return _get_openai_client()

    def check(
        self,
        *,
        new_statement: str,
        earlier_decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return ``{"contradicts_id": str|None, "reason": str|None}``.

        ``earlier_decisions`` is a list of
        ``{"decision_id", "statement", "author_display_name"}`` dicts.
        We take the most recent ``max_earlier`` — the LLM doesn't need
        the whole history for the judgment.

        Any exception → ``{"contradicts_id": None, "reason": None}``
        so the caller can safely trust the result.
        """
        if not new_statement.strip() or not earlier_decisions:
            return {"contradicts_id": None, "reason": None}
        try:
            client = self._get_client()
            trimmed = earlier_decisions[: self._config.max_earlier]
            earlier_text = "\n".join(
                f"- [{d.get('decision_id')}] ({d.get('author_display_name', 'someone')}):"
                f" {d.get('statement', '').strip()}"
                for d in trimmed
            )
            user_msg = (
                f"New decision:\n  {new_statement.strip()}\n\n"
                f"Earlier decisions:\n{earlier_text}"
            )
            resp = client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": _PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                timeout=self._config.timeout_s,
            )
            content = resp.choices[0].message.content or "{}"
            parsed = json.loads(content)
            cid = parsed.get("contradicts_id")
            reason = parsed.get("reason")
            # Validate: cid must match one of the decision_ids we passed in,
            # otherwise the model hallucinated an id and we drop the result.
            valid_ids = {d.get("decision_id") for d in trimmed}
            if cid is not None and cid not in valid_ids:
                return {"contradicts_id": None, "reason": None}
            return {
                "contradicts_id": cid if isinstance(cid, str) else None,
                "reason": reason if isinstance(reason, str) else None,
            }
        except Exception as exc:  # noqa: BLE001
            _log.info("contradiction check failed (fail-open): %s", exc)
            return {"contradicts_id": None, "reason": None}
