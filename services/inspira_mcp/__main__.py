"""Launch entrypoint: ``python -m inspira_mcp``.

Boots a streamable-HTTP MCP server on ``INSPIRA_MCP_HOST``:``INSPIRA_MCP_PORT``
(defaults: ``0.0.0.0:4175``) and keeps it running. Used by the Fly
``[processes.mcp]`` process group — see ``services/fly.toml``.

The launcher is intentionally tiny — everything interesting happens in
``server.build_streamable_asgi_app``. Keeping this file at a few lines
means deploys and tests exercise the exact same code path; no divergent
boot logic hides in the launcher.
"""
from __future__ import annotations

import logging
import os

import uvicorn

from planning_studio_service._env_bootstrap import ensure_loaded

from .server import build_streamable_asgi_app


def main() -> None:
    ensure_loaded()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("INSPIRA_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("INSPIRA_MCP_PORT", "4175"))
    app = build_streamable_asgi_app()
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
