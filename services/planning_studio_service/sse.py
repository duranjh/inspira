"""Server-Sent Events helpers for Inspira's streaming endpoints.

Phase 1 of the SSE streaming architecture. The motivation is purely a
TTFB win: Inspira's kickoff and topic_turn calls block on a 6-12s LLM
round-trip before returning anything, leaving the user staring at a
blank wait. By switching to SSE we can flush a ``heartbeat`` frame
immediately so the UI flips to "AI is thinking…" within ~50ms — even
though the full envelope still takes the same wall-clock time to assemble.

Wire format::

    event: heartbeat
    data: {"status": "thinking", "message": "Building your plan…"}

    event: complete
    data: {<full envelope same shape as the non-streaming response>}

    event: error
    data: {"code": "planner_error", "message": "..."}

The vocabulary is deliberately tiny — only ``heartbeat``, ``complete``
and ``error``. Phase 2 (skeleton + enrichment two-call split) will add
``skeleton`` and ``enrichment`` events without breaking this contract.

Headers worth calling out:

- ``Content-Type: text/event-stream`` — the only thing browsers /
  EventSource libraries actually require.
- ``Cache-Control: no-cache`` — keeps proxies and the browser's HTTP
  cache from collapsing the response.
- ``X-Accel-Buffering: no`` — **CRITICAL** for production deploys.
  Fly.io's HTTP/2 proxy (and nginx-style reverse proxies in general)
  will otherwise buffer the entire response body, defeating the whole
  point of the heartbeat. This header tells the proxy "don't buffer
  this stream, flush every chunk to the client immediately."
- ``Connection: keep-alive`` — mostly cosmetic on HTTP/2 (the field is
  actually disallowed there) but harmless and well-understood by the
  HTTP/1.1 dev-server path used in tests.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi.responses import StreamingResponse


def format_sse(event: str, data: Any) -> str:
    """Format an SSE frame.

    The wire format is ``event: <name>\\ndata: <json>\\n\\n``. Every
    frame ends with a blank line so the client knows the message is
    complete; partial frames stay buffered on the reader side until
    the terminator arrives.

    ``data`` is always JSON-encoded so multi-line payloads round-trip
    cleanly — the SSE spec splits ``data:`` lines on newlines and
    re-joins them, which silently corrupts anything the encoder
    emits with embedded ``\\n``. JSON is the lingua franca for our
    frontend reader anyway.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def sse_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Standard SSE response headers, optionally merged with extras."""
    headers = {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache",
        # Without this, Fly.io's HTTP/2 proxy buffers the entire
        # response and the heartbeat lands AFTER the LLM call returns
        # — defeating the entire purpose of Phase 1. See module docstring.
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    if extra:
        headers.update(extra)
    return headers


def sse_stream(
    gen: AsyncIterator[str],
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Wrap an async generator in a StreamingResponse with SSE headers.

    The generator should yield strings already formatted by
    :func:`format_sse`. The response itself uses ``media_type`` of
    ``text/event-stream`` so FastAPI / Starlette won't second-guess
    the content-type.
    """
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers=sse_headers(extra_headers),
    )
