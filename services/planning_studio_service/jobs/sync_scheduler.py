"""Connector-sync scheduler — 60-min cadence asyncio loop (W2 C3).

Runs as a lifespan-spawned asyncio task (mirrors the
``_trial_ending_sweep_loop`` pattern at ``api.py:1382``). Per
cycle:

1. Reconcile any orphaned 'running' rows from the previous
   process (catches Fly-restart orphans + crashes mid-cycle).
2. For each provider with a sync function, fetch active
   workspaces and sync each sequentially (no parallel — simpler,
   easier to reason about, less DB pool pressure for the
   single-machine assumption documented at C1).
3. Sleep until next cycle: ``INTERVAL_S + uniform(0, JITTER_S)``.
   Jitter prevents thundering-herd if multiple Fly machines
   spawn together (single-machine today; defense-in-depth for
   future autoscale).

Configuration via env (all optional):
- ``INSPIRA_CONNECTOR_SYNC=1``                    # enable the loop
- ``INSPIRA_CONNECTOR_SYNC_INTERVAL_S`` (3600)    # base cadence
- ``INSPIRA_CONNECTOR_SYNC_JITTER_S``  (600)      # 0..jitter spread (one-sided)

Operational notes (record for ops dashboards / runbook readers):

- **Cadence shift from one-sided jitter.** Average cycle is
  ``INTERVAL_S + JITTER_S/2`` ≈ 3900s ≈ **65 min**, not 60 min.
  One-sided jitter was chosen over two-sided so a slow machine
  never accidentally double-syncs (the floor is the interval, not
  ``interval - jitter``). Cycle metrics should expect ~65-min
  spacing between sync events.
- **Sequential per-workspace concurrency.** At 100+ workspaces
  with 30s avg sync, the sequential floor is ~50 min — getting
  tight inside a 60-min cycle. Pre-W7 hardening: introduce a
  bounded worker pool (``asyncio.Semaphore(5)`` over the
  per-workspace fan-out) if partner count crosses 50. The W3
  prioritization-agent layer is the right place for parallel
  *agent* coordination; this loop stays sequential until it
  outgrows.
- **Graceful-shutdown orphans are by design.** Because the 10s
  lifespan timeout is shorter than typical sync time (30s), most
  Fly deploys mid-cycle will leave a ``running`` row that the
  next startup reconciles to ``error``. Logs at **INFO** (not
  WARN) so this isn't alarming on dashboards. Expected behaviour;
  the next-startup reconciler is the recovery path.

Watch points addressed (per the C3 review):

1. **Startup ordering**: the scheduler enters its loop only after
   ``create_app`` returns — by which point the store has run
   ``_initialize_v2_schema`` synchronously and Alembic has been
   run via ``alembic upgrade head`` in the deploy script. The
   loop's first action is always the orphan reconciler, so even
   if a previous process died mid-sync, we clean up before the
   first new run.
2. **60-min cadence + jitter**: ``INTERVAL_S`` + uniform(0,
   ``JITTER_S``). The jitter is one-sided (never less than the
   interval) so a slow machine never accidentally double-syncs.
3. **Orphan reconciler**: runs once at scheduler startup BEFORE
   the first cycle, then once at the START of each cycle.
   Catches both pre-startup orphans and mid-cycle orphans.
4. **Per-workspace concurrency**: SEQUENTIAL per cycle. Two
   workspaces with aligned cycles run one after the other inside
   the same tick. Decision rationale: simpler, less DB pool
   pressure, and the W3 prioritization layer (which DOES use
   asyncio.gather over sub-agents) is the right place for parallel
   coordination — not the ingestion polling.
5. **Graceful shutdown**: the lifespan's ``finally`` block sets
   ``stop_event``. The loop checks the event after each tick and
   on every sleep wake-up. An in-flight sync inside ``sync_workspace``
   completes before the loop exits — Fly's 30s graceful-shutdown
   window is enough for one sync (max ~30s per sync at v2
   throughput). Any sync still 'running' at hard-kill is reconciled
   by the next process startup's orphan pass.
6. **Rate-limit backoff**: when ``sync_workspace`` returns
   ``status='rate_limited'`` with a ``rate_limit_reset_at``,
   the scheduler caches the per-workspace hold and skips the
   workspace on subsequent cycles until the reset has elapsed.
   Reads GitHub's ``X-RateLimit-Reset`` header (surfaced via
   ``GitHubRateLimited.reset_at``) so the next attempt is timed
   correctly rather than hammering at the next 60-min boundary.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..connectors import store as connectors_store
from ..connectors.github.app_jwt import GitHubAppConfig
from ..connectors.github.oauth import load_app_config_from_env
from ..connectors.github.sync import sync_workspace
from ..connectors.linear.sync import sync_workspace as linear_sync_workspace

if TYPE_CHECKING:
    from ..store import PlanningStudioStore


logger = logging.getLogger(__name__)


# Defaults chosen per W2 C3 review:
# - 3600s base cadence (1 hour) matches the engineering plan's
#   F3 "poll every 60 minutes" decision.
# - 600s jitter (±10 min, one-sided up) is wider than the C1
#   plan's ±2 min. Wider spread reduces thundering-herd risk on
#   multi-machine deploys without affecting partner-perceived
#   latency (60-min cadence is already coarse).
DEFAULT_INTERVAL_S = 3600
DEFAULT_JITTER_S = 600


def is_scheduler_enabled() -> bool:
    """Env-gate: ``INSPIRA_CONNECTOR_SYNC=1`` to spawn the loop."""
    return os.environ.get("INSPIRA_CONNECTOR_SYNC", "").strip() == "1"


def _interval_s() -> int:
    raw = os.environ.get("INSPIRA_CONNECTOR_SYNC_INTERVAL_S")
    if raw is None or not raw.strip().isdigit():
        return DEFAULT_INTERVAL_S
    return int(raw)


def _jitter_s() -> int:
    raw = os.environ.get("INSPIRA_CONNECTOR_SYNC_JITTER_S")
    if raw is None or not raw.strip().isdigit():
        return DEFAULT_JITTER_S
    return int(raw)


async def _sleep_until_stop_or_timeout(
    stop_event: asyncio.Event, seconds: float
) -> bool:
    """Sleep up to ``seconds``; return early if ``stop_event``
    fires. Returns True iff stopped (caller should exit).

    Always yields to the event loop at least once — even on
    seconds<=0 — so a tick → sleep(0) → tick spin doesn't starve
    other tasks (e.g. the test that sets stop_event while the loop
    is iterating fast).
    """
    if seconds <= 0:
        # Yield to give other tasks (notably the test that sets
        # stop_event) a chance to run between ticks. Without this
        # yield, an INTERVAL_S=0 / JITTER_S=0 configuration makes
        # the loop hog the event loop indefinitely.
        await asyncio.sleep(0)
        return stop_event.is_set()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _sync_one_workspace(
    *,
    store: "PlanningStudioStore",
    workspace_id: str,
    config: GitHubAppConfig,
    rate_limit_holds: dict[tuple[str, str], datetime],
) -> None:
    """Sync one workspace, tracking rate-limit holds across cycles."""
    hold_key = (workspace_id, "github")
    try:
        result = await sync_workspace(
            store=store,
            workspace_id=workspace_id,
            trigger="scheduled",
            config=config,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "scheduler sync failed for workspace %s",
            workspace_id,
        )
        return

    status = result.get("status")
    if status == "rate_limited":
        reset_at = result.get("rate_limit_reset_at")
        if isinstance(reset_at, datetime):
            rate_limit_holds[hold_key] = reset_at
            logger.info(
                "scheduler caching rate-limit hold for workspace "
                "%s until %s",
                workspace_id,
                reset_at.isoformat(),
            )
    else:
        # Any non-rate-limit outcome clears the prior hold so
        # we don't keep skipping a recovered workspace.
        rate_limit_holds.pop(hold_key, None)


async def _tick_once(
    store: "PlanningStudioStore",
    *,
    rate_limit_holds: dict[tuple[str, str], datetime],
) -> None:
    """Single sync cycle.

    Reconciles orphans, then iterates workspaces with active
    GitHub credentials sequentially. Skips workspaces under a
    cached rate-limit hold whose reset hasn't elapsed yet.
    """
    # Run reconciler at every tick boundary — catches mid-cycle
    # orphans from the previous tick (e.g. process killed during
    # one of the sequential workspace syncs).
    try:
        reconciled = connectors_store.reconcile_orphaned_runs(store)
        if reconciled:
            logger.info(
                "scheduler reconciled %d orphaned sync_run(s) "
                "to status=error",
                reconciled,
            )
    except Exception:  # noqa: BLE001
        logger.exception("scheduler orphan reconciler failed")

    # GitHub sync (W2 C2). Skipped silently when GitHub App config
    # is absent on this deploy.
    configs = load_app_config_from_env()
    now = datetime.now(timezone.utc)
    skipped = 0

    if configs is not None:
        app_config, _ = configs
        gh_workspaces = connectors_store.workspaces_with_active_credential(
            store, "github"
        )
        for workspace_id in gh_workspaces:
            hold_until = rate_limit_holds.get((workspace_id, "github"))
            if hold_until is not None and now < hold_until:
                skipped += 1
                continue
            if hold_until is not None and now >= hold_until:
                rate_limit_holds.pop((workspace_id, "github"), None)
            await _sync_one_workspace(
                store=store,
                workspace_id=workspace_id,
                config=app_config,
                rate_limit_holds=rate_limit_holds,
            )

    # Linear sync (W2 F4). API-key flow — no shared App config to
    # gate on. We just iterate workspaces with an active credential.
    linear_workspaces = connectors_store.workspaces_with_active_credential(
        store, "linear"
    )
    for workspace_id in linear_workspaces:
        try:
            await linear_sync_workspace(
                store=store,
                workspace_id=workspace_id,
                trigger="scheduled",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "scheduler linear sync failed for workspace %s",
                workspace_id,
            )

    if skipped:
        logger.info(
            "scheduler skipped %d workspace(s) under rate-limit hold",
            skipped,
        )


async def connector_sync_loop(
    store: "PlanningStudioStore",
    stop_event: asyncio.Event,
) -> None:
    """The lifespan-spawned async loop. Runs forever until
    ``stop_event`` is set."""
    interval_s = _interval_s()
    jitter_s = _jitter_s()
    rate_limit_holds: dict[tuple[str, str], datetime] = {}

    logger.info(
        "connector_sync_loop starting (interval=%ds jitter=0..%ds)",
        interval_s,
        jitter_s,
    )

    # First-cycle initial reconcile + small jittered delay before
    # the first sync. Two reasons for the initial delay:
    # 1. Defense-in-depth thundering-herd if multiple Fly machines
    #    boot simultaneously.
    # 2. Lets any startup-time DB connection-pool warmup settle.
    try:
        startup_reconciled = connectors_store.reconcile_orphaned_runs(
            store
        )
        if startup_reconciled:
            logger.info(
                "scheduler startup reconciled %d orphaned sync_run(s)",
                startup_reconciled,
            )
    except Exception:  # noqa: BLE001
        logger.exception("scheduler startup reconciler failed")

    initial_delay = random.uniform(0, jitter_s) if jitter_s > 0 else 0.0
    if await _sleep_until_stop_or_timeout(stop_event, initial_delay):
        logger.info("connector_sync_loop stopped during initial delay")
        return

    while not stop_event.is_set():
        try:
            await _tick_once(store, rate_limit_holds=rate_limit_holds)
        except Exception:  # noqa: BLE001
            # Broad catch — _tick_once already swallows per-call
            # errors, but a logic bug shouldn't kill the whole
            # loop. Log + continue to next cycle.
            logger.exception("scheduler tick failed; continuing")

        # Sleep INTERVAL_S + jitter (one-sided up).
        sleep_s = interval_s + (
            random.uniform(0, jitter_s) if jitter_s > 0 else 0.0
        )
        if await _sleep_until_stop_or_timeout(stop_event, sleep_s):
            logger.info("connector_sync_loop stopped during sleep")
            return

    logger.info("connector_sync_loop exited")
