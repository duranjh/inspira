"""Shared input validators for Inspira v2 request bodies.

All user-supplied text flows through these helpers before it ever touches
the store or the LLM adapters.  The primary concern is NUL bytes (``\\x00``):
SQLite silently truncates or corrupts rows when a bound parameter contains a
NUL byte, and downstream text processing (tiktoken, regex, export serializers)
can mishandle the embedded null.

Secondary concern: other C0 control characters (``\\x01``-``\\x08``,
``\\x0b``-``\\x0c``, ``\\x0e``-``\\x1f``, ``\\x7f``) are invisible to users,
serve no legitimate purpose in free-text fields, and have been used in
injection probes.  We preserve the three that carry real whitespace meaning —
``\\t`` (0x09), ``\\n`` (0x0a), ``\\r`` (0x0d) — and strip everything else.

Usage in Pydantic models
------------------------
Annotate string fields that come from user input with ``SanitizedStr``::

    from .validators import SanitizedStr
    from pydantic import BaseModel, Field

    class MyBody(BaseModel):
        title: SanitizedStr = Field(default="", max_length=200)

Or call ``sanitize_text`` directly in route handlers when you need
one-off cleaning outside a model::

    from .validators import sanitize_text
    cleaned = sanitize_text(raw_value)
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator


# Matches NUL plus C0 control characters that have no legitimate place in
# user-facing text.  Preserved: \\t (0x09), \\n (0x0a), \\r (0x0d).
# Stripped:  \\x00 (NUL) + \\x01-\\x08, \\x0b, \\x0c, \\x0e-\\x1f, \\x7f (DEL).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(value: str) -> str:
    """Strip NUL bytes and non-whitespace C0 control characters from *value*.

    Preserves tab (``\\t``), newline (``\\n``), and carriage-return (``\\r``).
    Non-string values are returned unchanged so that ``None`` / ``int`` fields
    annotated elsewhere don't break if this is accidentally applied to them.
    """
    if not isinstance(value, str):
        return value  # type: ignore[return-value]
    return _CTRL_RE.sub("", value)


# Pydantic v2 annotated type — drop this on any ``str`` field to get
# automatic sanitization at validation time.
SanitizedStr = Annotated[str, BeforeValidator(sanitize_text)]
