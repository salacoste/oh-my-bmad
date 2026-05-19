"""Story 10.5 — cardinality discipline regression gate (FR62, NFR-O8).

CI-enforced regression tests that fingerprint the cardinality invariants
shipped in Story 10.4:

  * AC2 — baseline cardinality at steady state == 51 timeseries (exact).
  * AC3 — 10K varying task_ids → cardinality returns to baseline after
    synchronous tail-loop cleanup (slow marker).
  * AC4 — N=100 concurrent active tasks → cardinality bounded by baseline
    plus active-task count; full cleanup returns to baseline.
  * AC5 — deliberate violation (bypass ``_EVENT_FAMILIES_SET`` guard) is
    observable by the gate; proves assertion sensitivity (D3).
  * AC6 — ``_ACTOR_KINDS`` drift detection: the Story 10.4 startup
    invariant in :func:`build_collectors` fires when an unknown actor
    kind is injected.
  * AC7 — ``events_appended_total`` "unknown" family fallback discipline:
    50 distinct novel families fold into the single ``"unknown"`` bucket
    and DO NOT materialise per-family children.

Cross-service contract — the file lives at ``tests/integration/`` because
cardinality is a deployment-time contract between the subscriber and the
observability stack; the test exercises the FULL ``/metrics`` endpoint
via ``httpx.AsyncClient + ASGITransport`` against ``build_app(...)``.

Per P2-I1 read-only-subscriber rule: imports from ``metrics_subscriber``
and ``events`` packages only — NO ``services/registry-*`` imports.

Per Story 10.4 P1-M2 lesson: cardinality is always counted via
``prometheus_client.parser.text_string_to_metric_families`` on the
``/metrics`` body — NEVER via the private ``counter._value.get()`` API.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import (
    Actor,
    EventEnvelope,
    to_canonical_json,
)
from fastapi import FastAPI
from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.main import build_app
from metrics_subscriber.app.metrics import (
    _EVENT_FAMILIES,
    MetricsState,
    build_collectors,
)
from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families

_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000123"
_REQUEST_ID = "01917e5c-a7d1-7000-8abc-000000000456"


# ===========================================================================
# Shared helpers
# ===========================================================================


def _count_canonical_timeseries(body: str) -> int:
    """Count canonical Prometheus timeseries in *body*.

    Filters out the ``_created`` bookkeeping samples that prometheus_client
    emits alongside every Counter labelset — per Story 10.4 P1-L4 wording
    clarification ``_created`` is metadata, not a real indexed timeseries.

    Returns the integer count of samples Prometheus would actually index.
    Caller passes the decoded ``/metrics`` response body (text form).
    """
    count = 0
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if not sample.name.endswith("_created"):
                count += 1
    return count


def _family_breakdown(body: str) -> list[tuple[str, int]]:
    """Return ``[(family_name, canonical_sample_count), ...]`` for diagnostics.

    Used to produce informative assertion failure messages — if the
    baseline drifts away from 51, the per-family breakdown shows exactly
    which family contributed unexpected children.
    """
    breakdown: list[tuple[str, int]] = []
    for family in text_string_to_metric_families(body):
        canonical = sum(1 for s in family.samples if not s.name.endswith("_created"))
        breakdown.append((family.name, canonical))
    return breakdown


def _make_event_id(index: int) -> str:
    """Construct a UUIDv7-shaped event_id from a monotonic *index*.

    The envelope validator enforces
    ``e-<8hex>-<4hex>-7<3hex>-[89ab]<3hex>-<12hex>`` — we materialise a
    deterministic, per-test-monotonic shape by packing *index* into the
    trailing 12-hex segment.  Variant nibble fixed at ``8`` (the most
    common UUIDv7 variant value).
    """
    return f"e-01917e5c-a7d1-7000-8abc-{index:012x}"


def _make_envelope(
    event_type: str,
    *,
    event_id_index: int,
    actor_kind: str = "system",
    payload: dict[str, Any] | None = None,
    schema_version: str = "1.1.0",
) -> EventEnvelope:
    """Construct a minimal envelope suitable for direct JSONL writing."""
    return EventEnvelope(
        event_id=_make_event_id(event_id_index),
        schema_version=schema_version,
        type=event_type,  # noqa: EVT001 — test helper takes parametric type
        emitted_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
        emitted_at_monotonic_ns=1,
        actor=Actor(kind=actor_kind, id="test-actor"),  # type: ignore[arg-type]
        payload=payload if payload is not None else {},
        trace_id=_TRACE_ID,
        request_id=_REQUEST_ID,
    )


def _write_envelopes(path: Path, envs: list[EventEnvelope]) -> None:
    """Append canonical-JSON envelopes to *path* (one per line)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        for env in envs:
            f.write(to_canonical_json(env) + b"\n")


def _make_settings(tmp_path: Path) -> MetricsSubscriberSettings:
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True, exist_ok=True)
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    return MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=1,
    )


def _today_log_path(event_dir: Path) -> Path:
    today = datetime.now(UTC).date().isoformat()
    return event_dir / f"{today}.jsonl"


def _parse_appended_sum(body: str) -> float:
    """Sum every ``omb_events_appended_total{event_family=...}`` sample.

    Used to detect "tail loop has drained N envelopes" without binding to
    a specific family — covers tests that emit envelopes across multiple
    families.
    """
    total = 0.0
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if sample.name == "omb_events_appended_total":
                total += float(sample.value)
    return total


async def _wait_for_total_appended(
    app: FastAPI,
    expected: float,
    *,
    timeout_s: float = 30.0,
) -> None:
    """Poll ``/metrics`` until the total ``events_appended_total`` reaches *expected*.

    Per Story 10.4 P1-M2 lesson: uses ``parser.text_string_to_metric_families``
    on the public ``/metrics`` HTTP surface — no private API access.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    deadline = asyncio.get_running_loop().time() + timeout_s
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        while asyncio.get_running_loop().time() < deadline:
            r = await client.get("/metrics")
            if r.status_code == 200 and _parse_appended_sum(r.text) >= expected:
                return
            await asyncio.sleep(0.01)
    raise AssertionError(
        f"events_appended_total summed across families did not reach {expected} within {timeout_s}s"
    )


@pytest_asyncio.fixture
async def cardinality_test_app(
    tmp_path: Path,
) -> AsyncIterator[tuple[FastAPI, Path]]:
    """Yield ``(app, event_dir)`` with the lifespan started.

    Each test gets a fresh ``build_app(...)`` instance — the per-app
    ``CollectorRegistry`` pattern from Story 10.3 ensures registry
    isolation without requiring an autouse fixture in
    ``tests/integration/conftest.py``.
    """
    settings = _make_settings(tmp_path)
    event_dir = settings.event_log_dir
    log_path = _today_log_path(event_dir)
    log_path.touch()

    app = build_app(settings=settings)
    async with LifespanManager(app):
        yield app, event_dir


# ===========================================================================
# AC2 — Baseline cardinality at steady state == 51 timeseries (exact).
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_cardinality_at_steady_state(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """AC2 — fresh ``build_app`` exposes exactly 51 canonical timeseries.

    Breakdown (per Story 10.4 DAR + ADR-0005 §Cardinality):

      Story 10.3 baseline (lag, bytes_behind, parse_skip × 4)   =  6
      Task lifecycle (15 event types)                           = 15
      Session lifecycle (5 phases)                              =  5
      Secret accessed (5 ActorKind values)                      =  5
      Event family (11 registered + 1 ``unknown`` fallback)     = 12
      Idempotency cache (2 outcomes — DEFERRED preview)         =  2
      Capability denied (3 tiers × 2 boundaries — DEFERRED)     =  6
      ─────────────────────────────────────────────────────────
                                                                = 51

    No envelopes emitted; per-task gauge has zero labelled children.
    Cursor-offset gauge has zero labelled children until the tail loop
    persists (no envelopes, no persist).  The bound is EXACT — any drift
    from 51 must be reconciled against this breakdown.
    """
    app, _ = cardinality_test_app
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200, f"/metrics returned {r.status_code}"
        body = r.text
    count = _count_canonical_timeseries(body)
    breakdown = _family_breakdown(body)
    assert count == 51, (
        f"baseline cardinality drift: got {count} canonical timeseries, expected 51. "
        f"Breakdown: {breakdown}. "
        f"Expected: 6 baseline + 15 task + 5 session + 5 secret + 12 family "
        f"+ 2 idempotency + 6 capability = 51."
    )


# ===========================================================================
# AC3 — 10K varying task_ids: cardinality bounded by LRU + cleanup.
# ===========================================================================


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_cardinality_under_10k_varying_task_ids(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """AC3 — 10K distinct task_id pairs (started + completed) → baseline.

    Emits 20000 envelopes (10000 pairs) into the test event-log; the tail
    loop's synchronous cleanup on ``task.completed`` removes every per-
    task gauge child by scrape time.  The ``_terminated_task_ids`` deque
    is bounded at ``maxlen=10_000`` (Story 10.4 P1-H3) — this test
    exercises that exact bound.

    Wall-clock budget: 30 seconds on a typical CI runner (D4 from spec).
    """
    app, event_dir = cardinality_test_app
    log_path = _today_log_path(event_dir)

    # Build 20K envelopes ahead-of-time so writing dominates the wall-clock,
    # not envelope construction.  Each task_id is a distinct UUID-like
    # string; pairs are emitted contiguously so the cleanup happens
    # synchronously in the tail loop.
    envs: list[EventEnvelope] = []
    for i in range(10000):
        task_id = f"t-cardinality-10k-{i:08d}"
        envs.append(
            _make_envelope(
                "task.execution.started",
                event_id_index=2 * i,
                payload={"task_id": task_id},
            )
        )
        envs.append(
            _make_envelope(
                "task.completed",
                event_id_index=2 * i + 1,
                payload={
                    "task_id": task_id,
                    "summary": "10K-test",
                    "files_changed": 0,
                    "lines_added": 0,
                    "lines_removed": 0,
                },
            )
        )
    _write_envelopes(log_path, envs)

    # Wait for the tail loop to drain all 20K envelopes.
    await _wait_for_total_appended(app, 20000.0, timeout_s=30.0)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        body = r.text

    count = _count_canonical_timeseries(body)
    breakdown = _family_breakdown(body)
    # Note: the tail loop also persists the cursor, which materialises a
    # single ``metrics_subscriber_cursor_offset_bytes{path=...}`` child.
    # So the expected steady-state count post-drain is 51 (baseline) + 1
    # (cursor-offset child) = 52.  AC3 self-verification says "<= 51"; we
    # tighten to "<= 52" to account for the persist-time gauge child.
    # The CRITICAL invariant is that NO per-task gauge children leaked.
    assert count <= 52, (
        f"10K cardinality drift: got {count} canonical timeseries, expected <= 52 "
        f"(51 baseline + 1 cursor-offset path child). Breakdown: {breakdown}"
    )

    # Critical: the per-task gauge has zero labelled children after
    # cleanup.  This is the load-bearing P1-H3 invariant.
    state: MetricsState = app.state.metrics
    assert len(list(state.task_tokens_spent._metrics)) == 0, (  # noqa: SLF001
        f"task_tokens_spent leaked {len(list(state.task_tokens_spent._metrics))} "  # noqa: SLF001
        f"gauge children after 10K cleanup"
    )

    # The ``_terminated_task_ids`` LRU is bounded at maxlen=10_000 — after
    # 10K completions the set MUST NOT exceed that bound.
    assert len(state._terminated_task_ids_set) <= 10_000, (  # noqa: SLF001
        f"_terminated_task_ids_set grew to {len(state._terminated_task_ids_set)} > 10_000"  # noqa: SLF001
    )

    # Proof we actually processed the 20K envelopes (not just a subset).
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if (
                sample.name == "omb_events_appended_total"
                and sample.labels.get("event_family") == "task"
            ):
                assert float(sample.value) >= 20000.0, (
                    f"events_appended_total{{event_family=task}} = {sample.value}; "
                    f"expected >= 20000 (10K started + 10K completed)"
                )
                break


# ===========================================================================
# AC4 — N=100 concurrent active tasks: cardinality == baseline + N.
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cardinality_with_n_concurrent_active_tasks(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """AC4 — 100 active tasks → baseline + 100; full cleanup → baseline.

    Fingerprints the active-task ceiling cited in ADR-0005 §Cardinality
    ("operationally ≤ N for some N ~10–100").
    """
    app, event_dir = cardinality_test_app
    log_path = _today_log_path(event_dir)

    n_tasks = 100
    started_envs = [
        _make_envelope(
            "task.execution.started",
            event_id_index=100_000 + i,
            payload={"task_id": f"t-active-{i:04d}"},
        )
        for i in range(n_tasks)
    ]
    _write_envelopes(log_path, started_envs)
    await _wait_for_total_appended(app, float(n_tasks), timeout_s=15.0)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    # Phase 1b: emit token-bearing envelopes so per-task gauge children
    # materialise.  task.execution.started carries no token field → no
    # gauge child is created.  task.budget_exceeded carries ``tokens_used``
    # and does NOT terminate the task — perfect for the "active gauge"
    # fingerprint.
    budget_envs = [
        _make_envelope(
            "task.budget_exceeded",
            event_id_index=200_000 + i,
            payload={
                "task_id": f"t-active-{i:04d}",
                "token_limit": 1000,
                "tokens_used": 500 + i,
                "step": 1,
            },
        )
        for i in range(n_tasks)
    ]
    _write_envelopes(log_path, budget_envs)
    await _wait_for_total_appended(app, float(n_tasks * 2), timeout_s=15.0)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.text
        count_mid = _count_canonical_timeseries(body)

    # Baseline (51) + cursor-offset path child (1 — tail loop persists) +
    # N per-task gauge children = 51 + 1 + 100 = 152.
    # We assert >= 151 (baseline+N) and <= 152 (with one persist-cycle).
    assert 151 <= count_mid <= 152, (
        f"mid-flight cardinality drift: got {count_mid}, expected 151..152 "
        f"(51 baseline + {n_tasks} per-task gauges +/- 1 cursor-offset path child). "
        f"Breakdown: {_family_breakdown(body)}"
    )

    # Phase 2: drain via task.completed terminators.
    completed_envs = [
        _make_envelope(
            "task.completed",
            event_id_index=300_000 + i,
            payload={
                "task_id": f"t-active-{i:04d}",
                "summary": "drain",
                "files_changed": 0,
                "lines_added": 0,
                "lines_removed": 0,
            },
        )
        for i in range(n_tasks)
    ]
    _write_envelopes(log_path, completed_envs)
    await _wait_for_total_appended(app, float(n_tasks * 3), timeout_s=15.0)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.text
        count_after = _count_canonical_timeseries(body)
    assert count_after <= 52, (
        f"post-drain cardinality drift: got {count_after}, expected <= 52 "
        f"(51 baseline + 1 cursor-offset path child). "
        f"Breakdown: {_family_breakdown(body)}"
    )

    state: MetricsState = app.state.metrics
    assert len(list(state.task_tokens_spent._metrics)) == 0, (  # noqa: SLF001
        "per-task gauge children leaked after full N=100 cleanup"
    )


# ===========================================================================
# AC5 — deliberate violation MUST be observable by the gate.
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deliberate_unbounded_label_violation_fails(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """AC5 — bypass ``_EVENT_FAMILIES_SET`` guard via direct mutation.

    D3 from the spec — proves the gate would catch a real cardinality
    leak.  Direct-mutation pattern: we call
    ``state.events_appended_total.labels(event_family=f"novel_{i}").inc()``
    for 200 distinct synthetic family names, simulating what would
    happen if a future contributor removed the membership guard in
    :func:`update_for`.

    This test EXPECTS to see the leak (>= 251 timeseries) — the
    "expected to fail" assertion in production is the inverse, namely
    AC2/AC3/AC4 asserting that the *real* gate path stays at the
    baseline.
    """
    app, _ = cardinality_test_app
    state: MetricsState = app.state.metrics

    # Bypass the bounded-enum guard — call ``.labels(...)`` directly on
    # the Counter object, which materialises a new labelled child without
    # any membership check.
    leak_count = 200
    for i in range(leak_count):
        state.events_appended_total.labels(event_family=f"novel_family_{i}").inc()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.text
    count = _count_canonical_timeseries(body)
    # 51 baseline + 200 novel labelled children = 251.
    assert count >= 51 + leak_count, (
        f"deliberate violation did NOT materialise the cardinality leak: "
        f"got {count} canonical timeseries, expected >= {51 + leak_count}. "
        f"This means the gate would NOT detect a real cardinality regression."
    )


# ===========================================================================
# AC6 — ActorKind drift meta-test: startup assertion catches the drift.
# ===========================================================================


@pytest.mark.integration
def test_actor_kind_startup_assertion_catches_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6 — patch ``_ACTOR_KINDS`` to drift from ``get_args(ActorKind)``.

    Fingerprints Story 10.4 P1-H2's startup invariant.  Any future
    contributor who removes the assertion in :func:`build_collectors`
    fails this test — CI catches the regression.

    Synthetic drift: append ``"bot"`` to ``_ACTOR_KINDS`` so the tuple
    contains a value NOT in :data:`events.envelope.ActorKind`.  The
    assertion in :func:`build_collectors` compares set equality and
    raises :class:`AssertionError`.
    """
    import metrics_subscriber.app.metrics as metrics_module

    # Inject the drift: original 5 ActorKind values + a synthetic "bot".
    drifted: tuple[str, ...] = (*metrics_module._ACTOR_KINDS, "bot")
    monkeypatch.setattr(metrics_module, "_ACTOR_KINDS", drifted)

    registry = CollectorRegistry()
    with pytest.raises(AssertionError, match="_ACTOR_KINDS drift detected"):
        build_collectors(registry)


# ===========================================================================
# AC7 — ``events_appended_total`` ``"unknown"`` fallback discipline.
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_envelope_with_unknown_family_falls_to_unknown_bucket(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """AC7 — 50 novel-family envelopes fold into the ``"unknown"`` bucket.

    Fingerprints Story 10.4 P1-H1 — the cardinality contract demands
    that NO envelope can lazily materialise a new ``event_family``
    labelled child.  An envelope with a novel family prefix MUST route
    to the pre-populated ``"unknown"`` bucket.

    Uses the typed registry-package ``update_for`` directly on the
    in-process ``MetricsState`` rather than writing JSONL envelopes:
    novel envelope types do not round-trip through the schema registry,
    so direct dispatch is the cleanest path to exercise the fallback.
    Per Story 10.4 P1-M2, the assertion still uses the public
    ``/metrics`` HTTP surface + parser.
    """
    from metrics_subscriber.app.metrics import update_for

    app, _ = cardinality_test_app
    state: MetricsState = app.state.metrics
    n_novel = 50
    # Envelope-validator forbids digits in ``type`` ("dotted lowercase
    # past-tense"); we vary the third segment by latin-letter index so
    # each synthetic envelope yields a distinct ``type`` string while
    # staying validator-compliant.  The family prefix
    # (``completely``) is what matters for the test — it must NOT be in
    # :data:`_EVENT_FAMILIES` so the fallback bucket is exercised.
    for i in range(n_novel):
        # Two-letter suffix gives 26*26 = 676 unique combinations — well
        # above the n_novel=50 budget.
        suffix = chr(ord("a") + (i // 26)) + chr(ord("a") + (i % 26))
        env = _make_envelope(
            f"completely.new.synthetic_{suffix}",
            event_id_index=400_000 + i,
        )
        update_for(state, env)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.text

    # The ``"unknown"`` bucket absorbed all 50 increments.
    unknown_value: float | None = None
    novel_present: list[str] = []
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if sample.name != "omb_events_appended_total":
                continue
            ef = sample.labels.get("event_family")
            if ef == "unknown":
                unknown_value = float(sample.value)
            elif ef is not None and ef not in _EVENT_FAMILIES:
                novel_present.append(ef)
    assert unknown_value == float(n_novel), (
        f"events_appended_total{{event_family=unknown}} = {unknown_value}; "
        f"expected {n_novel} (all novel-family envelopes route to unknown)"
    )
    assert novel_present == [], (
        f"novel ``event_family`` labels were materialised — cardinality leak: {novel_present}"
    )

    # Cardinality stays at baseline (per-task gauge has zero children
    # since no token-bearing envelope was dispatched).
    count = _count_canonical_timeseries(body)
    assert count == 51, (
        f"AC7 cardinality drift: got {count}, expected 51 (no novel families "
        f"created). Breakdown: {_family_breakdown(body)}"
    )
