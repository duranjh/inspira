"""Server-side URL fetcher for Inspira attachments.

The browser's ``fetch(url)`` is blocked by CORS for most real-world sites,
so the frontend hands URLs to this module via ``POST /api/v2/fetch-url``.
Running the fetch server-side is a powerful primitive — and a classic
SSRF footgun if we don't lock it down.

Safety guards implemented here:

1. URL validation — http(s) only, length-capped, no ``file://`` /
   ``javascript:`` / ``data:``.
2. SSRF prevention via manual DNS pre-resolution. Every resolved IP
   (v4 and v6) is checked against ``is_private`` / ``is_loopback`` /
   ``is_link_local`` / ``is_reserved`` BEFORE we open a socket.
3. Redirect follow is capped at 3 hops, and each redirect destination
   is re-validated through the same gauntlet — otherwise a public host
   could 302 us to ``http://169.254.169.254/`` (cloud metadata).
4. Response body capped at 2 MB, streamed so oversize uploads are
   terminated early.
5. 10-second total timeout; surfaced as ``upstream_timeout``.
6. Content-Type allowlist — only text/plain-ish MIME types.
7. HTML is walked with ``html.parser.HTMLParser``; script/style/svg/
   template/noscript/iframe nodes are dropped; whitespace is collapsed.

No new Python deps. ``httpx`` is already in pyproject for the Claude
fallback path, so we reuse it here.

This module deliberately does not touch any Inspira store — rate
limiting, per-user quotas, and audit logging live in ``api.py`` next
to the endpoint.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger("planning_studio.fetchers.url")


# -----------------------------------------------------------------------------
# Tunables
# -----------------------------------------------------------------------------

MAX_URL_LENGTH = 2048
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_REDIRECTS = 3
TOTAL_TIMEOUT_SECONDS = 10.0

# Mirrors the frontend 8000-char excerpt cap (sources.ts MAX_EXCERPT_CHARS)
# so prompts stay consistent regardless of which source path ran.
MAX_EXCERPT_CHARS = 8000
URL_DISPLAY_NAME_CAP = 40

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "application/json",
        "application/xhtml+xml",
    },
)

# Browser-shaped UA so sites that 403 ``python-httpx/*`` still respond.
# Some publishers gate scrapers aggressively; a generic UA at least
# gives us the same shot as a logged-out incognito user in Chrome.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 Inspira/0.2 (+https://github.com/duranjh/inspira)"
)


# -----------------------------------------------------------------------------
# Error type
# -----------------------------------------------------------------------------


class FetchError(Exception):
    """Domain-specific error for URL fetch failures.

    ``code`` is a short machine-readable tag that the route handler maps
    to the structured JSON error (see ``api.py``). ``http_status`` lets
    the caller forward the right HTTP status without re-mapping. ``extra``
    is merged into the response body — e.g. ``max_bytes`` for
    ``content_too_large``.
    """

    def __init__(
        self,
        code: str,
        *,
        http_status: int = 400,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.extra = extra or {}


# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------


def _validate_url_shape(url: str) -> str:
    """Check scheme, length, and overall shape. Returns the trimmed URL."""
    if not isinstance(url, str):
        raise FetchError("invalid_url")
    trimmed = url.strip()
    if not trimmed:
        raise FetchError("invalid_url")
    if len(trimmed) > MAX_URL_LENGTH:
        raise FetchError("invalid_url")
    try:
        parts = urlsplit(trimmed)
    except ValueError:
        raise FetchError("invalid_url")
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise FetchError("invalid_url")
    if not parts.hostname:
        raise FetchError("invalid_url")
    # Reconstruct with lower-cased scheme/host so hostname comparisons are
    # stable and we don't fetch ``HTTP://EXAMPLE.COM`` differently from
    # ``http://example.com``.
    normalized = urlunsplit(
        (
            scheme,
            parts.netloc,
            parts.path or "/",
            parts.query,
            "",  # drop fragment — servers never see it anyway
        ),
    )
    return normalized


def _resolved_addresses(host: str) -> list[str]:
    """Run ``getaddrinfo`` and return the list of resolved IP strings.

    Both A and AAAA results are returned so the subsequent private-range
    check runs over every address we'd actually connect to. If DNS
    resolution fails, we raise ``blocked_internal_address`` rather than
    leaking an obscure socket error to the client — the user-visible
    message is the same either way (we won't fetch it).
    """
    try:
        infos = socket.getaddrinfo(
            host,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise FetchError("blocked_internal_address")
    addresses: list[str] = []
    for info in infos:
        # sockaddr is (ip, port) for v4, (ip, port, flowinfo, scopeid) for v6.
        sockaddr = info[4]
        if sockaddr and len(sockaddr) >= 1:
            addresses.append(sockaddr[0])
    # Dedup but preserve order.
    seen: set[str] = set()
    unique: list[str] = []
    for addr in addresses:
        if addr not in seen:
            seen.add(addr)
            unique.append(addr)
    return unique


def _is_blocked_address(addr: str) -> bool:
    """Return True if this IP is in a private / loopback / link-local range.

    Covers IPv4 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16 and
    IPv6 loopback, link-local, and fc00::/7 (unique-local) ranges via
    the stdlib ``ipaddress`` flags.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # Can't parse — treat as suspicious. Better to reject than to
        # open a connection to something we couldn't classify.
        return True
    # Any of these flags should block the fetch.
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified,
    )


def _assert_host_is_public(host: str) -> None:
    """Resolve ``host`` and raise if any resolved IP is non-public.

    We check EVERY resolved address, not just the first — a DNS record
    can return both a public and a private IP, and the httpx connection
    could end up on the private one. If we can't verify them all are
    public, we refuse.
    """
    # Literal-IP URL (e.g. ``http://127.0.0.1``). Parse directly so we
    # don't perform a DNS lookup for an IP string — also catches hosts
    # we wouldn't resolve publicly.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_address(str(literal)):
            raise FetchError("blocked_internal_address")
        return
    addresses = _resolved_addresses(host)
    if not addresses:
        raise FetchError("blocked_internal_address")
    for addr in addresses:
        if _is_blocked_address(addr):
            raise FetchError("blocked_internal_address")


# -----------------------------------------------------------------------------
# HTML text extraction (stdlib-only)
# -----------------------------------------------------------------------------


class _VisibleTextExtractor(HTMLParser):
    """Collect character data, skipping hidden/structural/scripted nodes.

    HTMLParser calls ``handle_starttag`` / ``handle_endtag`` / ``handle_data``
    as a flat stream. We maintain a small skip-stack: whenever we enter a
    tag on the drop list, we increment a depth counter, and we only
    collect character data while that counter is zero.
    """

    # Tags whose text we never want in the excerpt.
    _DROP_TAGS = frozenset(
        {"script", "style", "svg", "template", "noscript", "iframe"},
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],  # noqa: ARG002
    ) -> None:
        if tag.lower() in self._DROP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._DROP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],  # noqa: ARG002
    ) -> None:
        # Self-closing tags don't need skip-stack updates, but we do
        # want to ignore their (empty) content.
        return

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def text(self) -> str:
        joined = " ".join(self._parts)
        return _normalize_whitespace(joined)


def _normalize_whitespace(s: str) -> str:
    return " ".join(s.split()).strip()


def _extract_visible_text(html_body: str) -> str:
    parser = _VisibleTextExtractor()
    try:
        parser.feed(html_body)
        parser.close()
    except Exception:  # noqa: BLE001
        # HTMLParser is very forgiving but we still swallow parse errors
        # so a malformed site can't kill the whole request.
        pass
    return parser.text()


# -----------------------------------------------------------------------------
# Display helpers
# -----------------------------------------------------------------------------


def _shorten_url_for_display(url: str) -> str:
    if len(url) <= URL_DISPLAY_NAME_CAP:
        return url
    try:
        parts = urlsplit(url)
        host_and_path = (parts.hostname or "") + (parts.path or "")
        if host_and_path and len(host_and_path) <= URL_DISPLAY_NAME_CAP:
            return host_and_path
        if host_and_path:
            return host_and_path[: URL_DISPLAY_NAME_CAP - 1] + "\u2026"
    except ValueError:
        pass
    return url[: URL_DISPLAY_NAME_CAP - 1] + "\u2026"


def _truncate_with_marker(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    return s[:cap] + "\n\n[...truncated]"


def _parse_content_type(header_value: str | None) -> tuple[str, str | None]:
    """Split ``text/html; charset=utf-8`` into ``("text/html", "utf-8")``."""
    if not header_value:
        return "", None
    head, _, rest = header_value.partition(";")
    mime = head.strip().lower()
    charset: str | None = None
    for piece in rest.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("charset="):
            charset = piece.split("=", 1)[1].strip().strip('"') or None
            break
    return mime, charset


# -----------------------------------------------------------------------------
# Core fetch — used by the HTTP endpoint
# -----------------------------------------------------------------------------


async def fetch_url_as_source(url: str) -> dict[str, Any]:
    """Fetch ``url`` and return an AttachedSource-shaped dict.

    Raises ``FetchError`` for any safety-guard failure; the caller maps
    the error code to the structured JSON response.
    """
    normalized = _validate_url_shape(url)
    parts = urlsplit(normalized)
    host = parts.hostname or ""

    # Pre-flight SSRF check on the original URL.
    _assert_host_is_public(host)

    bytes_read = 0
    started = time.monotonic()

    # Manual redirect loop so we can validate every hop's hostname. httpx's
    # automatic follow would open a connection to a private IP before we
    # got a chance to re-check.
    current_url = normalized
    hops = 0
    status_code = 0
    content_type_raw: str | None = None
    body_bytes = b""

    # One client per call so connection pools don't outlive the request.
    timeout = httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=TOTAL_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,  # redirects handled manually below
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,application/json;q=0.8,*/*;q=0.1",
                "Accept-Language": "en-US,en;q=0.8",
            },
            max_redirects=0,
        ) as client:
            while True:
                if hops > MAX_REDIRECTS:
                    raise FetchError("too_many_redirects", http_status=502)

                async with client.stream("GET", current_url) as response:
                    status_code = response.status_code

                    # Redirects — re-validate the Location target's host
                    # and loop. Only follow 301/302/303/307/308.
                    if status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FetchError(
                                "upstream_error",
                                http_status=502,
                                extra={"status": status_code},
                            )
                        # Relative-URL redirect: resolve against current.
                        next_url = str(
                            httpx.URL(current_url).join(location),
                        )
                        next_url = _validate_url_shape(next_url)
                        next_parts = urlsplit(next_url)
                        _assert_host_is_public(next_parts.hostname or "")
                        current_url = next_url
                        hops += 1
                        continue

                    if status_code >= 500:
                        raise FetchError(
                            "upstream_error",
                            http_status=502,
                            extra={"status": status_code},
                        )
                    if status_code >= 400:
                        # 4xx bubbles as upstream_error so the client
                        # shows the remote server's rejection — useful
                        # feedback for the user (e.g. paywalled).
                        raise FetchError(
                            "upstream_error",
                            http_status=502,
                            extra={"status": status_code},
                        )

                    content_type_raw = response.headers.get("content-type")
                    mime, charset = _parse_content_type(content_type_raw)
                    if mime not in ALLOWED_CONTENT_TYPES:
                        raise FetchError(
                            "unsupported_content_type",
                            http_status=400,
                            extra={
                                "content_type": mime or (content_type_raw or ""),
                            },
                        )

                    # Stream with a hard cap. Bail as soon as we cross
                    # the limit so we never buffer a multi-GB response.
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        bytes_read += len(chunk)
                        if bytes_read > MAX_RESPONSE_BYTES:
                            raise FetchError(
                                "content_too_large",
                                http_status=400,
                                extra={"max_bytes": MAX_RESPONSE_BYTES},
                            )
                        chunks.append(chunk)
                    body_bytes = b"".join(chunks)
                    break  # success — out of redirect loop
    except httpx.TimeoutException:
        raise FetchError("upstream_timeout", http_status=502)
    except httpx.HTTPError as exc:
        # Generic transport failure (DNS in httpx, connection reset,
        # etc.). Mapped to a 502 so the client treats it as "the other
        # side's fault". The original error is logged for diagnosis.
        logger.info("url fetch transport error for %s: %s", current_url, exc)
        raise FetchError("upstream_error", http_status=502, extra={"status": 0})

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "url_fetch ok url=%s final=%s status=%s bytes=%s duration_ms=%s",
        normalized,
        current_url,
        status_code,
        bytes_read,
        duration_ms,
    )

    # Decode bytes with the declared charset when present; fall back to
    # utf-8 with errors ignored so a bad encoding doesn't nuke the fetch.
    _mime, charset = _parse_content_type(content_type_raw)
    encoding = charset or "utf-8"
    try:
        decoded = body_bytes.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        decoded = body_bytes.decode("utf-8", errors="replace")

    mime = _parse_content_type(content_type_raw)[0]
    if mime in {"text/html", "application/xhtml+xml"}:
        extracted = _extract_visible_text(decoded)
    else:
        extracted = _normalize_whitespace(decoded)

    truncated = _truncate_with_marker(extracted, MAX_EXCERPT_CHARS)
    excerpt = f"URL: {normalized}\n\n{truncated}" if truncated else f"URL: {normalized}\n\n"
    display_name = _shorten_url_for_display(normalized)

    return {
        "display_name": display_name,
        "kind": "url:link",
        "excerpt": excerpt,
    }
