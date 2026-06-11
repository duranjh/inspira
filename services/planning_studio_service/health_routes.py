"""Extra health-probe endpoints for the public /status page.

The core ``GET /api/health`` already lives inside ``api.create_app()`` and
returns the service banner. This module adds the deeper probes the status
page cares about — currently just a DB liveness check — without bloating
``api.py``.

Wired via a single line in ``api.create_app``::

    from . import health_routes
    health_routes.bind(_store)
    app.include_router(health_routes.router)

Routes
------
GET /api/health/db
    200 with ``{"status": "ok", "dialect": "sqlite"|"postgres", "latency_ms": <int>}``
    when a ``SELECT 1`` round-trip succeeds. 503 with ``{"status":"down"}`` if
    the store errors out. No auth required — this is a public probe, matches
    the semantics of ``/api/health``.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .store import PlanningStudioStore

logger = logging.getLogger("planning_studio.health")

router = APIRouter()

# Small holder mirroring the auth module's pattern — keeps the router
# importable without requiring a store at import time. ``bind`` is called
# from ``create_app`` once the store is constructed; tests that spin up a
# fresh app via ``make_test_app`` go through the same path and don't need
# their own wiring.
_store_holder: dict[str, PlanningStudioStore | None] = {"store": None}


def bind(store: PlanningStudioStore) -> None:
    """Attach the store instance the health router should probe."""
    _store_holder["store"] = store


def _get_store() -> PlanningStudioStore | None:
    return _store_holder["store"]


@router.get("/api/health/db", tags=["meta"])
def health_db() -> Any:
    """Return 200 if the DB answers SELECT 1, 503 otherwise.

    The probe uses the store's existing connection helper so it honours
    the same SQLite / Postgres branch the rest of the service takes. We
    deliberately do NOT leak driver-level error strings — the public
    status page only needs ``ok`` / ``down``, and error messages would
    be reconnaissance for an attacker.
    """
    store = _get_store()
    if store is None:
        # Misconfiguration — bind() was never called. Return a 503 so the
        # frontend correctly surfaces "db check unavailable" rather than
        # silently flipping the dot to green.
        return JSONResponse(
            status_code=503,
            content={"status": "down", "reason": "not_bound"},
        )

    start = time.perf_counter()
    try:
        with store._connect() as connection:  # noqa: SLF001 — private intentional
            # ``execute`` returns a dialect-appropriate cursor. We just need
            # the round-trip; no fetch necessary, but we pull one row to
            # ensure the driver actually talks to the server (psycopg will
            # defer send until the first fetch otherwise).
            cur = connection.execute("SELECT 1")
            try:
                cur.fetchone()
            except Exception:  # noqa: BLE001
                # Some cursor wrappers raise on fetchone of a SELECT 1 that
                # already materialised; ignore — the execute() succeeded.
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("health/db probe failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "down"},
        )

    latency_ms = int((time.perf_counter() - start) * 1000)
    dialect = "postgres" if getattr(store, "_is_postgres", False) else "sqlite"
    return {
        "status": "ok",
        "dialect": dialect,
        "latency_ms": latency_ms,
    }
