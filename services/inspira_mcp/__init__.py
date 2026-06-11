"""inspira_mcp — MCP server + shared handlers for the Claude/ChatGPT surface.

This package exposes the same 11 tool operations over two protocols:

- **MCP (Claude / Anthropic)** — ``inspira_mcp.server`` builds an MCP server
  using the upstream ``mcp`` SDK. Claude.ai ingests the MCP manifest, the
  model calls tools over the streamable-HTTP transport, and we resolve the
  bearer PAT to the user on every call.
- **OpenAPI (ChatGPT Custom GPT)** — ``planning_studio_service.api``
  mounts a ``/api/v2/mcp/*`` route group that delegates into the same
  handlers (see ``tool_handlers``). FastAPI auto-generates the OpenAPI
  schema, which the Custom GPT builder consumes.

Both surfaces share:

- ``schemas`` — Pydantic request/response models.
- ``tool_handlers`` — one handler per operation. Every handler takes
  ``store``, ``user_id``, and the already-validated Pydantic input. The
  handler owns the IDOR check via ``verify_project_ownership`` /
  ``get_*_with_ownership``. Handlers never import FastAPI or MCP types
  — they return plain dicts or Pydantic models.
- ``auth`` — bearer-token resolver. Looks up the token hash in the
  ``user_access_tokens`` table (owned by a concurrent agent), returns a
  user_id or raises AuthError.
- ``markdown_export`` — Python port of the frontend ``projectToMarkdown``
  so ``export_markdown`` can run entirely server-side without round-
  tripping to the browser.

The two entrypoints live beside each other so a change to a tool's
surface lands in exactly one place (the handler) and both callers stay
in sync automatically.
"""
from __future__ import annotations

__all__ = [
    "server",
    "tool_handlers",
    "schemas",
    "auth",
    "markdown_export",
]
