"""Background jobs spawned in the FastAPI lifespan context.

Currently:
- ``sync_scheduler`` — connector-sync polling loop (W2 C3).

Each job is exposed via:
- ``is_<job>_enabled() -> bool`` — env-gate check (lifespan reads
  this to decide whether to spawn the task).
- ``<job>_loop(store, stop_event)`` — async coroutine the lifespan
  task wraps. Loop exits cleanly when stop_event is set.
"""
from . import sync_scheduler

__all__ = ["sync_scheduler"]
