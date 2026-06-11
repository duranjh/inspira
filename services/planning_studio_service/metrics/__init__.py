"""In-memory metrics collection for the Inspira backend.

This package owns the lightweight operational metrics surface consumed by
``GET /api/admin/metrics``. No external dependencies. See
``docs/ops/monitoring.md`` for the broader observability plan — this
collector is the "home base" that downstream pipelines (Prometheus,
DataDog, a future tsdb) can read from via the admin endpoint or by
swapping ``MetricsCollector.snapshot()`` for a Prometheus exposition
format.

Design notes:

- Single process, in-memory, bounded. Sized so that a multi-worker
  rollout would need a shared backend — but today's topology is one
  uvicorn process, so a per-process counter is honest.
- Thread-safe via a single ``threading.Lock``. All mutations take the
  lock; ``snapshot()`` takes the lock briefly while it copies state
  into a plain dict, then releases before formatting the response.
- Sliding 24-hour window of per-minute buckets (1440 max). Older
  buckets are evicted on write.
- Latency histograms use exponentially-spaced buckets (see
  ``_LATENCY_BUCKETS_MS``) so there are no external deps and P50/P95/P99
  are cheap-ish to compute from bucket counts.
"""

from .collector import MetricsCollector, metrics_collector

__all__ = ["MetricsCollector", "metrics_collector"]
