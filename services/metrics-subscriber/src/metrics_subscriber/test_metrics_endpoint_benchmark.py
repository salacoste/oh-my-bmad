"""NFR-O8 latency benchmark for the Story 10.3 /metrics endpoint.

Marked ``@pytest.mark.slow`` so the PR-gate CI run skips it; merges +
nightly invoke ``pytest -m slow`` and enforce the budget.

NFR-O8 budget: ``/metrics`` p95 < 100 ms on the ``ubuntu-latest`` CI
runner, measured over 100 iterations against an app preloaded with
the Story 10.4 metric count (~30 metrics) so the exposition body
reflects the realistic future scale.  Story 10.4 hasn't shipped yet;
the fixture populates the registry with ad-hoc gauges + counters
to simulate the load.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from prometheus_client import Counter, Gauge

from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.main import build_app


def _populate_state_to_story_10_4_scale(app_state_metrics_registry: object) -> None:
    """Register ~30 additional metrics to simulate Story 10.4 scale.

    Story 10.3 ships 4 collectors; Story 10.4 adds the FR62 metric
    set (counters/gauges/histograms for task lifecycle, session
    state, capability tier).  We approximate the exposition size
    here so the benchmark reflects realistic post-10.4 load.
    """
    registry = app_state_metrics_registry  # already-typed as CollectorRegistry
    # ~10 gauges, ~10 counters, ~10 labelled gauges with 3 label values
    # each → ~50 timeseries in the exposition body.
    for i in range(10):
        Gauge(
            f"metrics_subscriber_fixture_gauge_{i}",
            f"Fixture gauge {i} simulating Story 10.4 scale.",
            registry=registry,  # type: ignore[arg-type]
        ).set(float(i))
    for i in range(10):
        Counter(
            f"metrics_subscriber_fixture_counter_{i}",
            f"Fixture counter {i} simulating Story 10.4 scale.",
            registry=registry,  # type: ignore[arg-type]
        ).inc(float(i + 1))
    for i in range(10):
        labelled = Gauge(
            f"metrics_subscriber_fixture_labelled_{i}",
            f"Fixture labelled gauge {i}.",
            labelnames=("tier",),
            registry=registry,  # type: ignore[arg-type]
        )
        for tier in ("safe", "review", "operator"):
            labelled.labels(tier=tier).set(float(i))


@pytest.mark.slow
@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_metrics_endpoint_p95_under_100ms(tmp_path: Path) -> None:
    """NFR-O8 — 100 scrapes; p95 < 100ms on ubuntu-latest CI runner.

    The benchmark uses ``time.perf_counter`` (monotonic, ns resolution)
    via the in-process ``httpx.ASGITransport`` + lifespan manager,
    so no network or process-spawn overhead skews the measurement.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True, exist_ok=True)
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.5,
        persist_every_n_events=1000,
    )
    app = build_app(settings=settings)
    async with LifespanManager(app):
        _populate_state_to_story_10_4_scale(app.state.registry)
        # Mutate the four Story 10.3 collectors so the body has real values.
        app.state.metrics.record_lag(lag_seconds=0.42, bytes_behind=10_000)
        # Story 10.3 pass-1 P1-L3: use a stable tmp_path-rooted filename
        # so the label-cardinality is not coupled to the authoring date.
        app.state.metrics.record_cursor(path=tmp_path / "today.jsonl", offset=5_000)
        app.state.metrics.on_parse_skip("validation")

        # Story 10.3 pass-1 P1-L1: ``raise_app_exceptions=False`` for
        # consistency with the other test surfaces — exceptions surface
        # as 5xx responses we can assert on, not raw raises that
        # confuse failure triage.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Warm-up: a single request that primes the route resolver.
            warm = await client.get("/metrics")
            assert warm.status_code == 200

            latencies: list[float] = []
            for _ in range(100):
                t0 = time.perf_counter()
                r = await client.get("/metrics")
                latencies.append(time.perf_counter() - t0)
                assert r.status_code == 200

    latencies.sort()
    p50 = latencies[49]
    p95 = latencies[94]
    p99 = latencies[98]
    print(
        f"\nNFR-O8 /metrics latency (100 samples): "
        f"p50={p50 * 1000:.2f}ms p95={p95 * 1000:.2f}ms p99={p99 * 1000:.2f}ms"
    )
    assert p95 < 0.1, f"NFR-O8 violation: /metrics p95={p95 * 1000:.1f}ms >= 100ms"
