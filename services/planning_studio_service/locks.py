"""Postgres advisory-lock helpers (#089 / Item 2).

Inspira occasionally needs short-lived per-resource locks to serialize
"start an LLM generation" requests so two concurrent clicks don't both
fire the same expensive call. The first such site is the Next Steps
generator (gpt-5, ~15-60s, charged against the user's monthly cap).

Design choices
--------------

- **Postgres advisory locks**, not in-process ``threading.Lock``. Inspira
  runs two Fly machines per region; an in-process dict-of-locks would
  not coordinate between them, so two concurrent requests landing on
  different machines could each insert an ``in_progress`` artifact and
  fire two LLM calls. ``pg_try_advisory_lock`` is held by the Postgres
  session (request transaction) and is honoured cluster-wide, which is
  exactly the property we want.

- **Try-lock semantics, not blocking-lock.** Callers that lose the race
  return 409 to the client (with the ``artifact_id`` of the in-flight
  call so the FE can poll the same row), they don't queue up waiting
  for the first call to finish. Blocking would tie up FastAPI workers
  while gpt-5 spins for 30+ seconds.

- **Hash-based key derivation.** Postgres advisory locks take a 64-bit
  signed integer key. We hash the project_id (TEXT UUID-ish slug) to
  the bigint range with SHA-256 → first 8 bytes, signed. Hash collisions
  across projects are vanishingly unlikely (2^-32 birthday-bound across
  ~65k projects); a collision would just over-serialize two unrelated
  projects, never miss a lock.

- **SQLite no-op.** The test harness uses SQLite (no advisory-lock
  primitive) and never has multiple concurrent requests on the same
  project anyway — yielding ``True`` from the context manager is the
  right behavior for test execution. Production runs Postgres
  exclusively.

- **Placeholders.** The store's ``_PostgresConnection`` wrapper
  translates ``?`` → ``%s`` so the helper here uses the same SQLite-
  style placeholders as the rest of the codebase, not psycopg's
  native ``%s``.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


def project_advisory_lock_key(project_id: str) -> int:
    """Derive a stable 64-bit signed int from a project_id string.

    Postgres ``pg_try_advisory_lock(bigint)`` takes a single 8-byte
    signed integer. SHA-256 hash of the UTF-8 project_id, take the
    first 8 bytes, interpret as big-endian signed. Collisions across
    distinct project_ids are negligible at our scale.

    Stable across machines and process restarts: same project_id ⇒
    same key, so two requests on different Fly machines compete for
    the same lock.
    """
    digest = hashlib.sha256(project_id.encode("utf-8")).digest()
    # Take the first 8 bytes; signed=True keeps the value within
    # Postgres's bigint range (-2^63 to 2^63-1).
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def try_project_advisory_lock(
    store: Any,
    project_id: str,
) -> Iterator[bool]:
    """Try to acquire a Postgres advisory lock keyed on ``project_id``.

    Yields ``True`` if the lock was acquired (caller has exclusive
    serialization on this project), ``False`` otherwise (caller should
    treat as "another request is already doing this work").

    The lock is released when the surrounding ``with`` block exits —
    either via ``pg_advisory_unlock`` (Postgres) or implicitly because
    the connection used to acquire it closes (also fine; Postgres
    cleans up session-scoped advisory locks on disconnect).

    On SQLite (test harness), always yields ``True``. The test harness
    is single-process / single-threaded for endpoint requests; there's
    no cross-process serialization concern.

    Usage::

        with try_project_advisory_lock(_store, project_id) as acquired:
            if not acquired:
                # someone else is generating; return their artifact_id
                ...
            else:
                # we have the lock; insert the in_progress row
                ...

    Errors during lock acquisition itself are swallowed and treated as
    ``acquired=True`` on the assumption that the cap-check + in-flight-
    row check upstream are still running — better to occasionally
    over-fire than to 5xx because the lock primitive misbehaved.
    """
    if not getattr(store, "_is_postgres", False):
        # SQLite (tests) — no real lock needed; always claim acquired.
        yield True
        return

    key = project_advisory_lock_key(project_id)
    acquired = False
    try:
        with store._connect() as connection:
            try:
                cursor = connection.execute(
                    "SELECT pg_try_advisory_lock(?)",
                    (key,),
                )
                row = cursor.fetchone()
                acquired = bool(row[0]) if row else False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pg_try_advisory_lock failed for project=%s key=%d: %s — "
                    "letting request through (caller's in-flight check "
                    "remains as defense in depth)",
                    project_id, key, exc,
                )
                acquired = True
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        connection.execute(
                            "SELECT pg_advisory_unlock(?)",
                            (key,),
                        )
                        connection.commit()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "pg_advisory_unlock failed for project=%s key=%d: %s "
                            "— Postgres releases on session close as fallback",
                            project_id, key, exc,
                        )
    except Exception as exc:  # noqa: BLE001
        # Connection-level failure: log + yield True so the request
        # can still try to do the work. The endpoint's stale-orphan
        # guard + in-flight check still catch double-fires.
        logger.warning(
            "advisory lock connection failed for project=%s: %s",
            project_id, exc,
        )
        yield True
