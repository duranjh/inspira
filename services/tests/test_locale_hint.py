"""Tests for locale_hint() helper and locale threading through the kickoff route.

Coverage:
- Unit tests for every corner case of locale_hint().
- One integration test: POST /kickoff with locale="fr" → adapter receives a
  prompt containing "Respond in French". The OpenAI client is mocked so no
  network call is made.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from planning_studio_service.agents.prompts import locale_hint


# ---------------------------------------------------------------------------
# Unit tests: locale_hint()
# ---------------------------------------------------------------------------


def test_locale_hint_none_returns_empty():
    assert locale_hint(None) == ""


def test_locale_hint_english_returns_empty():
    assert locale_hint("en") == ""


def test_locale_hint_french_returns_non_empty():
    result = locale_hint("fr")
    assert result != ""
    assert "French" in result


def test_locale_hint_french_uppercase_case_insensitive():
    assert locale_hint("FR") == locale_hint("fr")


def test_locale_hint_french_region_tag_splits_primary():
    """fr-CA should behave identically to fr."""
    assert locale_hint("fr-CA") == locale_hint("fr")


def test_locale_hint_unknown_code_returns_empty():
    assert locale_hint("xx") == ""


@pytest.mark.parametrize("code,lang_name", [
    ("es", "Spanish"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("ja", "Japanese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
])
def test_locale_hint_all_supported_languages(code: str, lang_name: str):
    result = locale_hint(code)
    assert lang_name in result
    assert "Respond in" in result
    # JSON keys / schema field names clause must be present.
    assert "JSON keys" in result


def test_locale_hint_json_keys_clause_always_present_for_non_english():
    """The 'JSON keys stay English' clause must survive in every non-EN hint."""
    for code in ("fr", "de", "es", "pt", "ja", "it", "nl", "pl"):
        result = locale_hint(code)
        assert "JSON keys" in result, f"Missing JSON-keys clause for {code!r}"


# ---------------------------------------------------------------------------
# Integration test: kickoff route threads locale into system prompt
# ---------------------------------------------------------------------------


def _make_fake_openai_response(tool_name: str, payload: dict) -> MagicMock:
    """Build a minimal mock that looks like an OpenAI chat completion response."""
    tool_call = MagicMock()
    tool_call.function.name = tool_name
    tool_call.function.arguments = json.dumps(payload)

    message = MagicMock()
    message.tool_calls = [tool_call]
    message.content = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    return response


def test_kickoff_locale_fr_injects_french_hint_into_system_prompt():
    """POST /kickoff with locale='fr' → the adapter's system prompt contains
    'Respond in French'. We mock the OpenAI client so no network call is made.
    """
    from tests._helpers import make_test_app, signup_and_login, fake_kickoff_response

    client, store, _adapter_mock, temp_dir = make_test_app()
    try:
        signup_and_login(client)

        # Create a project.
        proj_resp = client.post("/api/v2/projects", json={"title": "Test project"})
        proj_resp.raise_for_status()
        project_id = proj_resp.json()["project"]["project_id"]

        # Replace the lazy-loaded adapter with a real OpenAIPlanningInterviewer
        # but with a mocked OpenAI client so no network call is made.
        from planning_studio_service.agents.openai_adapter import OpenAIPlanningInterviewer

        fake_response = _make_fake_openai_response(
            "kickoff_response", fake_kickoff_response()
        )
        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create.return_value = fake_response

        real_adapter = OpenAIPlanningInterviewer(client=mock_openai_client)
        client.app.state  # ensure app.state exists
        # Inject via the adapter holder (the route uses _require_adapter()
        # which checks the injected holder first).
        # We access the closure variable via the test app's adapter injection
        # path: create_app(adapter=...) was called with a MagicMock; we
        # need to replace it so the route picks up our real adapter.
        # The simplest approach: rebuild the app with our real adapter injected.
        from planning_studio_service.api import create_app
        from planning_studio_service.store import PlanningStudioStore
        from planning_studio_service.config import load_config

        app2 = create_app(store=store, adapter=real_adapter)
        from fastapi.testclient import TestClient
        client2 = TestClient(app2)

        # We need to re-authenticate on the new client (same store = same DB).
        from tests._helpers import signup_and_login
        import time
        email2 = f"locale-test-{time.time()}@example.com"
        signup_and_login(client2, email=email2, password="password123")

        # Create a project on client2.
        proj_resp2 = client2.post("/api/v2/projects", json={"title": "Locale test"})
        proj_resp2.raise_for_status()
        project_id2 = proj_resp2.json()["project"]["project_id"]

        # Kickoff with locale="fr".
        resp = client2.post(
            f"/api/v2/projects/{project_id2}/kickoff",
            json={"user_idea": "Build a French cooking school", "locale": "fr"},
        )
        resp.raise_for_status()

        # Inspect the call the adapter made to the OpenAI client.
        assert mock_openai_client.chat.completions.create.called
        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = (call_kwargs.kwargs or call_kwargs[1]).get("messages") or call_kwargs[0][0]
        # Locate the system message.
        system_content = None
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                system_content = msg["content"]
                break
        assert system_content is not None, "No system message found in OpenAI call"
        assert "Respond in French" in system_content, (
            f"Expected 'Respond in French' in system prompt, got:\n{system_content[:500]}"
        )
    finally:
        temp_dir.cleanup()
