"""Health-probe helpers (Story 11.3.9 / FR17 / NFR-R8).

Probes are pure-async functions that query registry-state's SQLite store
(read-only) and return small typed results. The route layer at
:mod:`registry_api.routes.health` runs them in parallel under per-probe
:func:`asyncio.wait_for` budgets so a single hung probe cannot blow the
``/v1/health`` p95 budget (AC4).

A future ``/v1/ready`` readiness probe can reuse the same functions — the
indirection is deliberate so the wire-shape route stays thin.
"""

from __future__ import annotations

from registry_api.probes._budget import run_probe_with_budget
from registry_api.probes.health_probes import (
    QUEUE_LOOKBACK_S_DEFAULT,
    WORKER_WINDOW_S_DEFAULT,
    probe_queue_depth,
    probe_registry_reachable,
    probe_worker_recently_active,
)

__all__ = [
    "QUEUE_LOOKBACK_S_DEFAULT",
    "WORKER_WINDOW_S_DEFAULT",
    "probe_queue_depth",
    "probe_registry_reachable",
    "probe_worker_recently_active",
    "run_probe_with_budget",
]
