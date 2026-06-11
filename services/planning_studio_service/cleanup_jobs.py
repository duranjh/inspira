"""Scheduled cleanup jobs — prune stale state nightly.

The jobs in this module are idempotent, read- and write-limited to a small
set of well-understood tables, and each returns a count of rows removed so
the caller can log + alert on anomalies.

Invocation model
----------------

The cleaner is designed to run as a Fly.io scheduled machine, not as an
in-process background task. See ``docs/ops/cleanup-jobs.md`` for setup.
Running it out-of-process avoids the "what if the task never fires"
failure mode an ``asyncio.create_task`` inside the API lifespan would have
(machine sleeps, lifespan never ticks over 24h, etc.).

The CLI entry point is::

    python -m planning_studio_service.cleanup_jobs

which runs all three jobs and prints the counts as JSON. On a scheduled
Fly machine the command ends up as::

    fly machine run registry.fly.io/inspira-backend \\
        --schedule daily \\
        --app inspira-backend \\
        --command "python -m planning_studio_service.cleanup_jobs"

What each job does
------------------

1. ``prune_expired_sessions`` — drop password_reset_tokens whose expires_at
   has passed (and a belt-and-suspenders UPDATE on used_at for any that
   are already marked used but somehow still linger in the active set).
   ``password_reset_tokens`` is the only table in the schema with a true
   ``expires_at`` column; the v1 planning "sessions" table and the auth
   session cookie (itsdangerous, stateless) have no persisted TTL that
   needs sweeping.

2. ``prune_abandoned_anonymous_accounts`` — no-op. The only ``is_system``
   user is a single shared fallback account (``SYSTEM_USER_ID``) that
   owns pre-auth seed content. Deleting it would strand the demo project
   and the v2 projects still assigned to the system tenant. Returns 0 and
   logs a skip message so the cron output still documents the pass.

3. ``prune_stale_share_tokens`` — revoke (set ``revoked_at``) share tokens
   where ``last_viewed_at IS NULL`` and ``created_at`` is older than N
   days. Revocation (not deletion) because the same table powers view
   analytics via ``view_count``; we want to keep the audit row even when
   the token is dead. Already-revoked rows are skipped so the count
   reflects only newly-revoked tokens.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import PlanningStudioStore

logger = logging.getLogger("planning_studio.cleanup_jobs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cutoff_iso(older_than_days: int) -> str:
    """Return the ISO-8601 UTC timestamp N days in the past.

    Jobs compare stored ``created_at`` strings to this cutoff lexically —
    safe because all of our timestamps are written in a fixed-width,
    timezone-normalised format by ``now_timestamp``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    return cutoff.isoformat(timespec="seconds")


def prune_expired_sessions(store: "PlanningStudioStore") -> int:
    """Delete password_reset_tokens whose ``expires_at`` has passed.

    Returns the number of rows removed. Idempotent: a second call
    immediately afterwards returns 0 because the cut rows are gone.

    The ``used_at`` predicate is intentionally left out — the goal here
    is to reclaim ROW STORAGE, not to track reset attempts. Consumed
    tokens past their expiry are just as stale as unconsumed ones, and
    ``consume_password_reset_token`` already refuses to mint a change
    on an expired row so there's no safety cost to dropping them too.
    """
    now_iso = _now_iso()
    with store._connect() as connection:
        cursor = connection.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at <= ?",
            (now_iso,),
        )
        count = int(cursor.rowcount or 0)
        connection.commit()
    logger.info(
        "cleanup.prune_expired_sessions removed=%s cutoff=%s", count, now_iso,
    )
    return count


def prune_abandoned_anonymous_accounts(
    store: "PlanningStudioStore", older_than_days: int = 7,
) -> int:
    """No-op: ``is_system`` is a derived flag on a single shared user.

    See module docstring. This function exists so the cron job interface
    stays stable — if/when anonymous signup lands (one user row per
    anonymous visitor), the body becomes a real DELETE.

    Returns 0 and logs the skip. ``older_than_days`` is accepted for
    signature parity with the future implementation.
    """
    del store, older_than_days  # kept for interface parity
    logger.info(
        "cleanup.prune_abandoned_anonymous_accounts skipped "
        "reason=single-shared-system-user",
    )
    return 0


def prune_stale_share_tokens(
    store: "PlanningStudioStore", older_than_days: int = 90,
) -> int:
    """Revoke share tokens that have never been viewed and are older than N days.

    The schema tracks revocation via ``revoked_at`` rather than deleting
    rows so the view-count history survives. This function flips
    ``revoked_at`` on tokens where ``last_viewed_at IS NULL`` AND
    ``created_at <= cutoff`` AND ``revoked_at IS NULL``. Already-revoked
    rows are untouched so the return value reflects only newly-revoked
    tokens. Second call returns 0.

    Returns the number of rows revoked.
    """
    cutoff = _cutoff_iso(older_than_days)
    now = _now_iso()
    with store._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE shared_links
            SET revoked_at = ?
            WHERE last_viewed_at IS NULL
              AND created_at <= ?
              AND revoked_at IS NULL
            """,
            (now, cutoff),
        )
        count = int(cursor.rowcount or 0)
        connection.commit()
    logger.info(
        "cleanup.prune_stale_share_tokens revoked=%s cutoff=%s", count, cutoff,
    )
    return count


def run_cleanup(store: "PlanningStudioStore") -> dict[str, int]:
    """Run all three jobs sequentially and return their row counts.

    Jobs are called in a fixed order; a failure in one does not abort
    the others — each has its own try/except so a schema drift on one
    table can't stop the others from running. The failing job reports
    ``-1`` in the return dict and logs the exception for alerting.
    """
    counts: dict[str, int] = {}

    for name, fn in (
        ("sessions", lambda: prune_expired_sessions(store)),
        ("anon_accounts", lambda: prune_abandoned_anonymous_accounts(store)),
        ("share_tokens", lambda: prune_stale_share_tokens(store)),
    ):
        try:
            counts[name] = int(fn())
        except Exception:  # noqa: BLE001 — best-effort sweep, keep going
            logger.exception("cleanup job %s failed", name)
            counts[name] = -1

    logger.info("cleanup.run_cleanup complete counts=%s", counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Writes JSON counts to stdout, logs to stderr.

    Exit codes:
      0 — all three jobs ran to completion (any may have removed 0 rows).
      1 — at least one job raised; its count is -1 in the output.
    """
    parser = argparse.ArgumentParser(
        description="Run Inspira's scheduled cleanup jobs.",
    )
    parser.add_argument(
        "--log-level", default="info",
        help="Python logging level (default: info)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # Imports deferred so `-h` and argparse errors don't touch the DB.
    from ._env_bootstrap import ensure_loaded
    from .config import load_config
    from .store import PlanningStudioStore

    ensure_loaded()
    config = load_config()
    store = PlanningStudioStore(config=config)

    counts = run_cleanup(store)
    sys.stdout.write(json.dumps(counts) + "\n")
    sys.stdout.flush()

    # Non-zero exit if any job failed, so Fly's scheduled-machine status
    # goes red and we get paged from the Fly dashboard.
    return 0 if all(v >= 0 for v in counts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
