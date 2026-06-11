"""CLI entrypoint — launches the FastAPI app under uvicorn.

The legacy BaseHTTPServer path in ``app.py`` is kept for existing tests
and for local debugging when uvicorn isn't available. The production
deploy path is this one.
"""
from __future__ import annotations

import argparse
import os

from ._env_bootstrap import ensure_loaded
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Inspira backend")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help=(
            "Launch the deprecated BaseHTTPServer entry point (app.main). "
            "Use only if uvicorn isn't available; FastAPI+uvicorn is the "
            "supported production path."
        ),
    )
    args = parser.parse_args(argv)

    ensure_loaded()

    if args.legacy:
        from .app import main as legacy_main

        return legacy_main()

    # Uvicorn path.
    try:
        import uvicorn  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required to run the Inspira backend. Install the "
            "service package with `pip install -e services/` (which pulls "
            "fastapi + uvicorn) or pass --legacy to fall back to the "
            "stdlib BaseHTTPServer entry point.",
        ) from exc

    config = load_config()
    # Host/port from the same config used by the legacy server so existing
    # frontends and env vars keep pointing at the right place.
    host = os.environ.get("PLANNING_STUDIO_HOST", config.host)
    port = int(os.environ.get("PLANNING_STUDIO_PORT", config.port))

    # import string so uvicorn's reload loop (dev) picks up changes.
    uvicorn.run(
        "planning_studio_service.api:asgi_app",
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        reload=os.environ.get("UVICORN_RELOAD", "").lower() == "true",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
