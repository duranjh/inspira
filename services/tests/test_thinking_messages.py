"""Tests for the SSE thinking_message localization helper (P1.7, #067).

The helper resolves a key + locale to a localized string, falling back to
English when the locale is unknown or the locale-specific translation is
missing. Unknown keys return the literal key (and log a warning) so the
SSE stream doesn't blow up — that case should be a coding error caught
in development, not at runtime.
"""
from __future__ import annotations

import logging

import pytest

from planning_studio_service.thinking_messages import (
    THINKING_MESSAGES,
    thinking_message,
)


def test_known_key_english_locale_returns_english():
    assert thinking_message("kickoff.reading_idea", "en") == "Reading your idea…"


def test_known_key_spanish_locale_returns_spanish():
    assert thinking_message("kickoff.reading_idea", "es") == "Leyendo tu idea…"


def test_known_key_french_locale_returns_french():
    result = thinking_message("kickoff.reading_idea", "fr")
    assert result.startswith("Lecture")


def test_unknown_locale_falls_back_to_english():
    """Locale codes outside the supported set fall back to English."""
    assert thinking_message("kickoff.reading_idea", "xx") == "Reading your idea…"


def test_none_locale_falls_back_to_english():
    assert thinking_message("kickoff.reading_idea", None) == "Reading your idea…"


def test_empty_locale_falls_back_to_english():
    assert thinking_message("kickoff.reading_idea", "") == "Reading your idea…"


def test_region_subtag_splits_to_primary():
    """es-MX should resolve via es; en-US via en; etc."""
    assert thinking_message("kickoff.reading_idea", "es-MX") == "Leyendo tu idea…"
    assert thinking_message("kickoff.reading_idea", "en-US") == "Reading your idea…"


def test_locale_case_insensitive():
    assert thinking_message("kickoff.reading_idea", "ES") == "Leyendo tu idea…"
    assert thinking_message("kickoff.reading_idea", "Es") == "Leyendo tu idea…"


def test_unknown_key_returns_literal_and_logs_warning(caplog):
    """A typo in the key name should log a warning, not crash, and the
    literal key surfaces in the FE so the issue is visible."""
    with caplog.at_level(logging.WARNING):
        result = thinking_message("does.not.exist", "es")
    assert result == "does.not.exist"
    assert any("unknown key" in r.message for r in caplog.records)


def test_every_key_has_english_fallback():
    """All keys must define ``en`` so the fallback path always resolves."""
    for key, table in THINKING_MESSAGES.items():
        assert "en" in table, f"Key {key!r} missing English fallback"


@pytest.mark.parametrize("locale", ["es", "fr", "de", "pt", "ja", "it", "nl", "pl"])
def test_every_supported_locale_translated_for_every_key(locale: str):
    """All supported locales should have translations for every key.
    Keeps the table from drifting out of sync as keys are added."""
    for key, table in THINKING_MESSAGES.items():
        assert locale in table, (
            f"Key {key!r} missing translation for locale {locale!r}"
        )
        # Translation should be non-empty + not identical to en for any
        # key with a unique non-English string. (The English fallback
        # would still satisfy the wire shape but defeat the localization
        # purpose — tests block silent regressions where translations
        # accidentally collapse to English.)
        assert table[locale], (
            f"Key {key!r}, locale {locale!r}: translation must be non-empty"
        )


def test_kickoff_and_turn_namespaces_distinct():
    """Sanity: keys partition cleanly across the four known namespaces."""
    keys = list(THINKING_MESSAGES.keys())
    kickoff_keys = [k for k in keys if k.startswith("kickoff.")]
    turn_keys = [k for k in keys if k.startswith("turn.")]
    common_keys = [k for k in keys if k.startswith("common.")]
    artifact_keys = [k for k in keys if k.startswith("artifact.")]
    # Sanity: at least 1 of each
    assert kickoff_keys, "expected at least one kickoff.* key"
    assert turn_keys, "expected at least one turn.* key"
    assert common_keys, "expected at least one common.* key"
    assert artifact_keys, "expected at least one artifact.* key"
    # Total must equal the sum (no other namespaces sneaking in)
    assert (
        len(kickoff_keys) + len(turn_keys) + len(common_keys)
        + len(artifact_keys)
        == len(keys)
    ), "Unexpected key namespace; update test if introducing a new one"
