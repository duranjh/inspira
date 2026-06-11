"""Maps BCP-47 primary subtags to English language names the LLM prompt
uses. Unknown codes return None so callers can decide whether to emit
the hint at all."""

_LOCALE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ja": "Japanese",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
}


def language_name(locale: str | None) -> str | None:
    if not locale:
        return None
    return _LOCALE_NAMES.get(locale.lower().split("-")[0])
