"""In-memory metrics collector.

Zero external dependencies — stdlib only. Threadsafe via a single
``threading.Lock``. Bounded in memory: a sliding 24h window of
per-minute buckets (1440 buckets) is the hard cap, with older buckets
evicted on write.

Why this shape:

- The Inspira backend is a single FastAPI process today. A per-process
  in-memory counter is the most honest representation of the truth;
  nothing is hidden behind a networked metrics store we haven't set up
  yet.
- Latency percentiles live in exponentially-bucketed histograms so we
  don't need ``numpy`` to compute P50/P95/P99. The buckets were chosen
  so that typical web requests (5ms-2s) and LLM calls (1s-60s) both
  land in enough buckets for the percentile estimates to be useful.
- ``snapshot()`` returns a JSON-serialisable dict consumed by
  ``GET /api/admin/metrics`` (see ``api.py``). No side effects.

Upgrade path — a future Prometheus integration only needs to expose the
internal counters via the Prometheus exposition format. The same
``snapshot()`` output is a superset of what a basic Prometheus collector
would publish, so swapping it in is additive: we expose a second route
that re-serialises the numbers, no change to the recording call sites.
Similarly, DataDog / BetterStack / a self-hosted tsdb can each poll
``/api/admin/metrics`` on an interval — the underlying shape stays
stable while the transport layer changes.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

# Bucket upper bounds (milliseconds). Roughly-exponential so coarse ranges
# (multi-second LLM calls) still land in distinct buckets. The last
# bucket "overflow" captures anything longer than 60s. Order matters —
# ``_bucket_index`` does a linear scan.
_LATENCY_BUCKETS_MS: tuple[int, ...] = (
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    10_000,
    20_000,
    30_000,
    45_000,
    60_000,
)

# Sliding window size — 24h at per-minute resolution.
_MAX_MINUTE_BUCKETS = 24 * 60


def _now_minute() -> int:
    """Current wall-clock minute, UTC. Tests patch this via ``time.time``."""
    return int(time.time() // 60)


def _bucket_index(duration_ms: float) -> int:
    """Find the smallest bucket whose upper bound is >= duration_ms.

    Returns ``len(_LATENCY_BUCKETS_MS)`` for values beyond the last
    bucket (the "overflow" slot).
    """
    for i, upper in enumerate(_LATENCY_BUCKETS_MS):
        if duration_ms <= upper:
            return i
    return len(_LATENCY_BUCKETS_MS)


def _percentile_from_buckets(
    counts: list[int],
    target_fraction: float,
) -> float | None:
    """Estimate a percentile from bucket counts.

    Returns the upper bound of the bucket containing the target rank,
    which over-estimates slightly (we don't interpolate). Good enough
    for SRE dashboards; call it out in the monitoring doc.
    """
    total = sum(counts)
    if total <= 0:
        return None
    target_rank = max(1, int(total * target_fraction))
    running = 0
    for i, c in enumerate(counts):
        running += c
        if running >= target_rank:
            if i < len(_LATENCY_BUCKETS_MS):
                return float(_LATENCY_BUCKETS_MS[i])
            # Overflow bucket — no known upper bound. Report the last
            # finite bucket boundary with a marker by returning the
            # final bucket's upper bound times 2. Honest about "we
            # don't know the tail."
            return float(_LATENCY_BUCKETS_MS[-1] * 2)
    return None


class MetricsCollector:
    """Thread-safe in-memory aggregator for request + LLM call metrics.

    Public surface:

    - ``record_request(status_code, duration_ms, route)``
    - ``record_llm_call(success, duration_ms, provider)``
    - ``update_token_utilization(user_id, ratio)``
    - ``snapshot()`` — JSON-shaped rollup for the admin endpoint

    All times are floats in milliseconds. ``status_code`` is an int.
    ``provider`` is a short string identifier (``"openai"``,
    ``"anthropic"``).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Per-minute buckets. Each key is an integer minute; value is a
        # small dict of counters + histograms for that minute.
        self._minutes: dict[int, dict[str, Any]] = {}

        # Per-user token-budget utilization ratio (0.0-1.0+). Intended
        # to be updated by the caller after every LLM-bearing request,
        # so ``snapshot()`` can report the "fraction of users over N%"
        # without having to scan the usage table.
        self._token_utilization: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_request(
        self,
        *,
        status_code: int,
        duration_ms: float,
        route: str,
    ) -> None:
        """Record a single HTTP request.

        ``route`` is a route template (``/api/v2/projects/{project_id}``),
        not the concrete URL, so the per-route counter doesn't explode.
        """
        bucket_idx = _bucket_index(duration_ms)
        is_error = status_code >= 500
        is_client_error = 400 <= status_code < 500
        with self._lock:
            bucket = self._get_minute_bucket_locked(_now_minute())
            bucket["requests_total"] += 1
            bucket["request_histogram"][bucket_idx] += 1
            bucket["requests_by_route"][route] += 1
            if is_error:
                bucket["requests_5xx"] += 1
            elif is_client_error:
                bucket["requests_4xx"] += 1

    def record_llm_call(
        self,
        *,
        success: bool,
        duration_ms: float,
        provider: str,
    ) -> None:
        """Record one LLM call — success/failure plus latency."""
        bucket_idx = _bucket_index(duration_ms)
        with self._lock:
            bucket = self._get_minute_bucket_locked(_now_minute())
            bucket["llm_calls_total"] += 1
            bucket["llm_histogram"][bucket_idx] += 1
            bucket["llm_calls_by_provider"][provider] += 1
            if not success:
                bucket["llm_failures"] += 1

    def update_token_utilization(self, *, user_id: str, ratio: float) -> None:
        """Replace the current token-budget utilization for one user.

        Ratio is ``spent_today / daily_budget``. Values above 1.0 are
        allowed (user is over quota and being served a 429). The
        cache is pruned to the last 10_000 users to stay bounded.
        """
        with self._lock:
            self._token_utilization[user_id] = float(ratio)
            if len(self._token_utilization) > 10_000:
                # Cheap pruning: drop the 1000 oldest by insertion order.
                # Python dicts preserve insertion order since 3.7.
                keys = list(self._token_utilization.keys())
                for k in keys[:1_000]:
                    self._token_utilization.pop(k, None)

    # ------------------------------------------------------------------
    # Internal — must hold ``self._lock``
    # ------------------------------------------------------------------

    def _get_minute_bucket_locked(self, minute: int) -> dict[str, Any]:
        """Return the bucket for ``minute``, creating + evicting as needed."""
        if minute not in self._minutes:
            self._minutes[minute] = _new_minute_bucket()
            # Evict anything older than MAX_MINUTE_BUCKETS back in time.
            cutoff = minute - _MAX_MINUTE_BUCKETS
            stale = [m for m in self._minutes if m < cutoff]
            for m in stale:
                self._minutes.pop(m, None)
        return self._minutes[minute]

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable rollup for the admin endpoint.

        Rollups:

        - ``requests_last_1h`` / ``requests_last_24h`` — totals.
        - ``error_rate_last_1h`` / ``error_rate_last_24h`` — fraction of
          5xx responses.
        - ``llm_success_rate_last_24h`` — 1 - (failures / total).
        - ``request_latency_ms`` / ``llm_latency_ms`` — P50/P95/P99
          across the last 24h.
        - ``token_budget_utilization`` — counts of users in bands.
        - ``window`` — metadata describing the sliding window.
        """
        now = _now_minute()
        window_1h = range(now - 60 + 1, now + 1)
        window_24h = range(now - _MAX_MINUTE_BUCKETS + 1, now + 1)

        with self._lock:
            # Shallow copy the minute map so we can release the lock
            # before the (still-cheap) aggregation pass.
            minute_snapshot = {m: _copy_bucket(b) for m, b in self._minutes.items()}
            utilization_snapshot = dict(self._token_utilization)

        req_1h = _sum_counter(minute_snapshot, window_1h, "requests_total")
        req_24h = _sum_counter(minute_snapshot, window_24h, "requests_total")
        err_1h = _sum_counter(minute_snapshot, window_1h, "requests_5xx")
        err_24h = _sum_counter(minute_snapshot, window_24h, "requests_5xx")
        client_err_24h = _sum_counter(minute_snapshot, window_24h, "requests_4xx")
        llm_total_24h = _sum_counter(minute_snapshot, window_24h, "llm_calls_total")
        llm_failures_24h = _sum_counter(minute_snapshot, window_24h, "llm_failures")

        request_hist = _sum_histogram(minute_snapshot, window_24h, "request_histogram")
        llm_hist = _sum_histogram(minute_snapshot, window_24h, "llm_histogram")

        utilization_bands = _utilization_bands(utilization_snapshot)

        return {
            "window": {
                "minute_buckets": len(minute_snapshot),
                "max_minute_buckets": _MAX_MINUTE_BUCKETS,
                "snapshot_minute_utc": now,
            },
            "requests": {
                "last_1h": req_1h,
                "last_24h": req_24h,
                "errors_5xx_last_1h": err_1h,
                "errors_5xx_last_24h": err_24h,
                "errors_4xx_last_24h": client_err_24h,
                "error_rate_last_1h": _safe_ratio(err_1h, req_1h),
                "error_rate_last_24h": _safe_ratio(err_24h, req_24h),
            },
            "llm_calls": {
                "last_24h": llm_total_24h,
                "failures_last_24h": llm_failures_24h,
                "success_rate_last_24h": 1.0
                - _safe_ratio(llm_failures_24h, llm_total_24h),
            },
            "request_latency_ms": {
                "p50": _percentile_from_buckets(request_hist, 0.50),
                "p95": _percentile_from_buckets(request_hist, 0.95),
                "p99": _percentile_from_buckets(request_hist, 0.99),
            },
            "llm_latency_ms": {
                "p50": _percentile_from_buckets(llm_hist, 0.50),
                "p95": _percentile_from_buckets(llm_hist, 0.95),
                "p99": _percentile_from_buckets(llm_hist, 0.99),
            },
            "token_budget_utilization": utilization_bands,
        }


def _new_minute_bucket() -> dict[str, Any]:
    """Fresh per-minute counter struct."""
    return {
        "requests_total": 0,
        "requests_5xx": 0,
        "requests_4xx": 0,
        "request_histogram": [0] * (len(_LATENCY_BUCKETS_MS) + 1),
        "requests_by_route": defaultdict(int),
        "llm_calls_total": 0,
        "llm_failures": 0,
        "llm_histogram": [0] * (len(_LATENCY_BUCKETS_MS) + 1),
        "llm_calls_by_provider": defaultdict(int),
    }


def _copy_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    """Shallow copy of a minute bucket, enough for aggregation."""
    return {
        "requests_total": bucket["requests_total"],
        "requests_5xx": bucket["requests_5xx"],
        "requests_4xx": bucket["requests_4xx"],
        "request_histogram": list(bucket["request_histogram"]),
        "requests_by_route": dict(bucket["requests_by_route"]),
        "llm_calls_total": bucket["llm_calls_total"],
        "llm_failures": bucket["llm_failures"],
        "llm_histogram": list(bucket["llm_histogram"]),
        "llm_calls_by_provider": dict(bucket["llm_calls_by_provider"]),
    }


def _sum_counter(
    minute_snapshot: dict[int, dict[str, Any]],
    window: range,
    key: str,
) -> int:
    total = 0
    for m in window:
        bucket = minute_snapshot.get(m)
        if bucket is not None:
            total += int(bucket.get(key, 0) or 0)
    return total


def _sum_histogram(
    minute_snapshot: dict[int, dict[str, Any]],
    window: range,
    key: str,
) -> list[int]:
    size = len(_LATENCY_BUCKETS_MS) + 1
    totals = [0] * size
    for m in window:
        bucket = minute_snapshot.get(m)
        if bucket is None:
            continue
        hist = bucket.get(key) or []
        for i, c in enumerate(hist):
            if i < size:
                totals[i] += int(c)
    return totals


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _utilization_bands(
    utilization_snapshot: dict[str, float],
) -> dict[str, Any]:
    """Band users by token-budget utilization.

    Returns counts per band plus a fraction of users over 90%, which
    ``docs/ops/monitoring.md`` uses as an alert threshold.
    """
    total = len(utilization_snapshot)
    bands = {
        "0-25": 0,
        "25-50": 0,
        "50-75": 0,
        "75-90": 0,
        "90-100": 0,
        "over_100": 0,
    }
    for ratio in utilization_snapshot.values():
        if ratio <= 0.25:
            bands["0-25"] += 1
        elif ratio <= 0.50:
            bands["25-50"] += 1
        elif ratio <= 0.75:
            bands["50-75"] += 1
        elif ratio <= 0.90:
            bands["75-90"] += 1
        elif ratio <= 1.0:
            bands["90-100"] += 1
        else:
            bands["over_100"] += 1
    over_90 = bands["90-100"] + bands["over_100"]
    return {
        "tracked_users": total,
        "bands": bands,
        "fraction_over_90pct": _safe_ratio(over_90, total),
    }


# ----------------------------------------------------------------------
# Module-level singleton
# ----------------------------------------------------------------------
# The API registers one collector per process. Tests can construct their
# own ``MetricsCollector()`` directly without touching the singleton.

metrics_collector = MetricsCollector()
