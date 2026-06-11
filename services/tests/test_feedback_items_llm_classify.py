"""LLM classifier + fallback tests (W2 F5 follow-up).

Mocks the OpenAI ``chat.completions.create`` call directly — we
don't need the real SDK shape, just objects with the
``choices[0].message.content`` path. Validates:

- Happy path: well-formed JSON response → categories returned
- Code-fence stripping (defense-in-depth; json_object mode should
  never emit fences, but cheap to handle if it does)
- Wrong-length response → falls back to rule-based
- Bad JSON → falls back
- API exception → falls back
- Empty input → empty output (no API call)
- ``is_llm_enabled`` toggles off without env, off without API key
"""
from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from planning_studio_service.feedback_items import llm_classify


def _fake_response(text: str) -> SimpleNamespace:
    """Build a minimal OpenAI ChatCompletion response shape."""
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _fake_client(response_text: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(response_text)
    return client


def _items() -> list[llm_classify.ItemForClassify]:
    return [
        llm_classify.ItemForClassify(
            title="Login crashes on Safari",
            body="Tried clearing cache",
        ),
        llm_classify.ItemForClassify(
            title="Can we add bulk export?",
            body="Whole team is asking.",
        ),
        llm_classify.ItemForClassify(
            title="Take my money — best product ever",
        ),
    ]


class IsLlmEnabledTests(unittest.TestCase):

    def setUp(self) -> None:
        self._old_flag = os.environ.pop("INSPIRA_LLM_CLASSIFIER", None)
        self._old_key = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self) -> None:
        if self._old_flag is not None:
            os.environ["INSPIRA_LLM_CLASSIFIER"] = self._old_flag
        else:
            os.environ.pop("INSPIRA_LLM_CLASSIFIER", None)
        if self._old_key is not None:
            os.environ["OPENAI_API_KEY"] = self._old_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_off_when_flag_unset(self) -> None:
        self.assertFalse(llm_classify.is_llm_enabled())

    def test_off_when_flag_set_but_key_missing(self) -> None:
        os.environ["INSPIRA_LLM_CLASSIFIER"] = "1"
        self.assertFalse(llm_classify.is_llm_enabled())

    def test_on_when_flag_and_key_set(self) -> None:
        os.environ["INSPIRA_LLM_CLASSIFIER"] = "1"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertTrue(llm_classify.is_llm_enabled())


class ClassifyBatchTests(unittest.TestCase):

    def test_happy_path(self) -> None:
        client = _fake_client(
            json.dumps({"categories": ["bug", "feature", "praise"]})
        )
        out = llm_classify.classify_batch(_items(), client=client)
        self.assertEqual(out, ["bug", "feature", "praise"])
        client.chat.completions.create.assert_called_once()

    def test_strips_code_fences(self) -> None:
        body = '```json\n{"categories":["bug","feature","praise"]}\n```'
        client = _fake_client(body)
        out = llm_classify.classify_batch(_items(), client=client)
        self.assertEqual(out, ["bug", "feature", "praise"])

    def test_wrong_length_falls_back(self) -> None:
        # LLM returns 2 entries for 3 items.
        client = _fake_client(
            json.dumps({"categories": ["bug", "feature"]})
        )
        out = llm_classify.classify_batch(_items(), client=client)
        # Falls back to rule-based: "Login crashes" → bug,
        # "Can we add bulk export" → feature, "Take my money" → praise.
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], "bug")
        self.assertEqual(out[1], "feature")
        self.assertEqual(out[2], "praise")

    def test_invalid_category_falls_back(self) -> None:
        client = _fake_client(
            json.dumps({"categories": ["bug", "wat", "praise"]})
        )
        out = llm_classify.classify_batch(_items(), client=client)
        self.assertEqual(len(out), 3)
        # Rule-based, not the bad LLM output.
        self.assertEqual(out[0], "bug")

    def test_bad_json_falls_back(self) -> None:
        client = _fake_client("definitely not json")
        out = llm_classify.classify_batch(_items(), client=client)
        self.assertEqual(len(out), 3)

    def test_api_exception_falls_back(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("boom")
        out = llm_classify.classify_batch(_items(), client=client)
        self.assertEqual(len(out), 3)

    def test_empty_input(self) -> None:
        client = MagicMock()
        out = llm_classify.classify_batch([], client=client)
        self.assertEqual(out, [])
        client.chat.completions.create.assert_not_called()


class ClassifyChunkedTests(unittest.TestCase):

    def test_chunks_into_multiple_batches(self) -> None:
        # 25 items, chunk_size=10 → 3 calls (10, 10, 5).
        items = [
            llm_classify.ItemForClassify(title=f"item-{i}", body="x")
            for i in range(25)
        ]
        # Each call returns category list of correct size.
        client = MagicMock()

        def respond(*args, **kwargs):
            # Inspect the prompt for "([N])" item lines and respond
            # with that many "noise" entries.
            messages = kwargs.get("messages") or (args[0] if args else [])
            user_msg = next(
                (m for m in messages if m.get("role") == "user"), None,
            )
            prompt = user_msg["content"] if user_msg else ""
            n = prompt.count("] title:")
            return _fake_response(
                json.dumps({"categories": ["noise"] * n})
            )

        client.chat.completions.create.side_effect = respond
        out = llm_classify.classify_chunked(
            items, chunk_size=10, client=client
        )
        self.assertEqual(len(out), 25)
        self.assertEqual(client.chat.completions.create.call_count, 3)


class ClassifyItemsWithFallbackTests(unittest.TestCase):

    def setUp(self) -> None:
        self._old_flag = os.environ.pop("INSPIRA_LLM_CLASSIFIER", None)
        self._old_key = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self) -> None:
        if self._old_flag is not None:
            os.environ["INSPIRA_LLM_CLASSIFIER"] = self._old_flag
        else:
            os.environ.pop("INSPIRA_LLM_CLASSIFIER", None)
        if self._old_key is not None:
            os.environ["OPENAI_API_KEY"] = self._old_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_flag_off_routes_to_rule_based(self) -> None:
        # No LLM env → rule-based path. Pinned by checking that
        # the result matches the rule-based output exactly.
        out = llm_classify.classify_items_with_fallback(_items())
        self.assertEqual(out, ["bug", "feature", "praise"])


if __name__ == "__main__":
    unittest.main()
