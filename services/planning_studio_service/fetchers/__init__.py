"""Server-side fetchers for external content Inspira attaches to a turn.

Browser-side ``fetch(url)`` is blocked by CORS for most real-world sites,
so URL attachments (the ``url:link`` AttachedSource kind) route through
this package instead. Live modules:

- ``url`` — fetch a URL with SSRF guards, size caps, and HTML-to-text
  extraction. The only fetcher today; future additions (e.g. RSS, PDF)
  would live alongside it.
"""
from __future__ import annotations

from .url import (
    FetchError,
    fetch_url_as_source,
)

__all__ = ["FetchError", "fetch_url_as_source"]
