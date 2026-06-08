"""Story 10.5 — cardinality discipline regression gate (FR62, NFR-O8).

CI-enforced regression tests that fingerprint the cardinality invariants
shipped in Story 10.4:

  * AC2 — baseline cardinality at steady state == 64 timeseries (exact;
    Story 11.2 pass-1 P1-H2 added ``key`` + ``capability`` to ``_EVENT_FAMILIES``
    bumping 51 → 53; Story 11.2.3 added the lock-wait Histogram (+8 → 61);
    Story 12.2 added the ``task.budget_enforcement_triggered`` lifecycle label
    and Story 12.3 added the ``budget`` family (→ 63); Story 13.4 added the
    ``replication`` family (→ 64) — the running exact baseline is asserted at
    each call site below).
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
``/metrics`` body — NEVER via the private ``counter._value.get()`` API
— EXCEPT ``._metrics`` for child-count assertions where no public
alternative exists (per Story 10.5 P1-M1).  Containment: pin
``prometheus-client>=0.20,<1.0`` in dev-deps so a breaking-change
release cannot silently invalidate these assertions; the inline
comments at each ``._metrics`` usage flag the exception explicitly.
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
    baseline drifts away from 53, the per-family breakdown shows exactly
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
    last_observed_sum: float = 0.0
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        while asyncio.get_running_loop().time() < deadline:
            r = await client.get("/metrics")
            if r.status_code == 200:
                last_observed_sum = _parse_appended_sum(r.text)
                if last_observed_sum >= expected:
                    return
            await asyncio.sleep(0.01)
    # P1-L2 — surface last observed sum so a CI timeout distinguishes
    # "tail loop is slow" from "tail loop is stuck at N envelopes".
    raise AssertionError(
        f"events_appended_total summed across families did not reach {expected} "
        f"within {timeout_s}s; last observed sum: {last_observed_sum}"
    )


@pytest_asyncio.fixture(loop_scope="function")
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
# AC2 — Baseline cardinality at steady state == 53 timeseries (exact).
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_cardinality_at_steady_state(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """AC2 — fresh ``build_app`` exposes exactly 53 canonical timeseries.

    Breakdown (per Story 10.4 DAR + ADR-0005 §Cardinality, updated by
    Story 11.2 pass-1 P1-H2 to include the ``key`` and ``capability``
    event families newly registered in this story):

      Story 10.3 baseline (lag, bytes_behind, parse_skip × 4)   =  6
      Task lifecycle (16 event types incl. budget_enforcement)  = 16
      Session lifecycle (5 phases)                              =  5
      Secret accessed (5 ActorKind values)                      =  5
      Event family (15 registered + 1 ``unknown`` fallback)     = 16
      Idempotency cache (2 outcomes — DEFERRED preview)         =  2
      Capability denied (3 tiers × 2 boundaries — DEFERRED)     =  6
      Event-log lock-wait Histogram (5 buckets + Inf+count+sum) =  8
      ─────────────────────────────────────────────────────────
                                                                = 64

    Story 11.2 P1-H2 added ``key`` (Story 11.5 ``key.rotated`` emission)
    and ``capability`` (Story 11.2.1 ``capability.denied`` emission) to
    ``_EVENT_FAMILIES`` — bumping the family count from 12 → 14 and the
    total canonical-timeseries count from 51 → 53.

    No envelopes emitted; per-task gauge has zero labelled children.
    Cursor-offset gauge has zero labelled children until the tail loop
    persists (no envelopes, no persist).  The bound is EXACT — any drift
    from 53 must be reconciled against this breakdown.
    """
    app, _ = cardinality_test_app
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200, f"/metrics returned {r.status_code}"
        body = r.text
    count = _count_canonical_timeseries(body)
    breakdown = _family_breakdown(body)
    # Story 11.2.3: exact count bumped 53 → 61 to accommodate the new
    # ``omb_event_log_lock_wait_ms`` Histogram. Bucket count was set in
    # ``metrics.py``: 5 explicit buckets [0.1, 1, 10, 100, 1000] + ``+Inf``
    # + ``_count`` + ``_sum`` = 8 series.
    # Story 13.4: exact count is 64. NOTE: the pre-13.4 literal in this assert
    # was a STALE 61 — the real runtime baseline had already drifted to 63 once
    # Story 12.2 added the ``task.budget_enforcement_triggered`` lifecycle label
    # (task family 15 → 16) and Story 12.3 added the ``budget`` event family;
    # those bumps were applied to the UNIT test (test_metrics_state.py) but not
    # to this integration assert. Story 13.4 reconciles the literal to the
    # empirically-verified value AND adds +1 for the new
    # ``omb_events_appended_total{event_family="replication"}`` child (NFR-R7).
    # Story 15.4: exact count 64 → 65 for the new pre-populated
    # ``omb_events_appended_total{event_family="git"}`` child (Epic 15 / FR72 —
    # git.committed / git.pushed). The event family count goes 16 → 17.
    # Epic 21 / Story 21-6: exact count 65 → 66 for the new pre-populated
    # ``omb_events_appended_total{event_family="browser"}`` child (FR83 / FR86).
    # The event family count goes 17 → 18.
    # Phase 8 hardening: exact count 66 → 69 for three new event families
    # (``deployment``, ``file``, ``worker``) registered during Epics 43-45.
    # The event family count goes 18 → 19, adding 3 canonical timeseries.
    assert count == 69, (
        f"baseline cardinality drift: got {count} canonical timeseries, expected 69. "
        f"Breakdown: {breakdown}. "
        f"Expected: 6 baseline + 16 task + 5 session + 5 secret + 21 family "
        f"+ 2 idempotency + 6 capability + 8 event_log_lock_wait = 69."
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
    """AC3 — 10_001 distinct task_id pairs (started + completed) → baseline.

    Emits 20_002 envelopes (10_001 pairs) into the test event-log so the
    ``_terminated_task_ids`` LRU (``maxlen=10_000``, Story 10.4 P1-H3)
    is forced into its eviction path at least once.  Two-phase
    verification (P1-H1 pass-1 review fix):

      Phase 1 — drain the 10_001 ``task.execution.started`` envelopes
                (each carrying ``tokens_used``) and assert exactly
                10_000 per-task gauge children materialise BEFORE any
                terminator is processed.  This proves the cleanup
                invariant has real work to do.  Asserting on 10_000
                (not 10_001) is correct here: by the time we observe
                the post-Phase-1 state, the tail loop may already have
                started consuming the contiguous block of completers
                that the writer wrote AFTER the starters; see inline
                comment below for the workaround.

      Phase 2 — emit the matching 10_001 ``task.completed`` terminators;
                wait for the full 20_002 envelope drain; assert zero
                per-task gauge children AND that
                ``_terminated_task_ids_set`` is at the LRU bound
                (``== 10_000``, exact — proves the eviction path
                fired).

    Wall-clock budget: 30 seconds on a typical CI runner (D4 from spec).

    Per Story 10.5 P1-H1 review patch: each ``task.execution.started``
    payload carries ``tokens_used=i`` so the per-task gauge children
    actually materialise.  Without this field the gauge updater is a
    no-op (``tokens is None`` branch) and the cleanup assertion below
    would be vacuously satisfied — masking a P1-H3 LRU regression.
    """
    app, event_dir = cardinality_test_app
    log_path = _today_log_path(event_dir)
    state: MetricsState = app.state.metrics

    # Per P1-M2 — 10_001 pairs (not 10_000) forces at least one LRU
    # eviction cycle.  The deque ``maxlen=10_000`` evicts the oldest
    # entry on the 10_001st append; without this we'd have a full deque
    # but the eviction code path stays unexercised.
    n_pairs = 10_001

    # Phase 1: write only the starters first.  Each carries
    # ``tokens_used=i`` so ``_update_task_tokens`` materialises a per-
    # task gauge child.  Without this field, gauge children are NEVER
    # created and the Phase-2 cleanup assertion would be vacuous (P1-H1
    # review fix).
    started_envs: list[EventEnvelope] = [
        _make_envelope(
            "task.execution.started",
            event_id_index=2 * i,
            payload={"task_id": f"t-cardinality-10k-{i:08d}", "tokens_used": i},
        )
        for i in range(n_pairs)
    ]
    _write_envelopes(log_path, started_envs)
    await _wait_for_total_appended(app, float(n_pairs), timeout_s=30.0)

    # Pre-cleanup assertion: at least 10_000 per-task gauge children
    # should be alive (10_001 minted minus at most 1 not-yet-processed).
    # We assert ``>= 10_000`` (not exactly 10_001) because the tail loop
    # may yield mid-batch — the load-bearing invariant is "gauges DID
    # materialise" so the Phase-2 cleanup has real work.  P1-H1 review
    # patch — without this, the post-cleanup assertion was vacuously
    # true.
    alive_before = len(list(state.task_tokens_spent._metrics))  # noqa: SLF001 — P1-M1 private API exception (no public child-count surface)
    assert alive_before >= 10_000, (
        f"per-task gauge children did not materialise as expected before "
        f"cleanup: got {alive_before}, expected >= 10_000.  This means the "
        f"P1-H1 cleanup invariant below would be vacuously satisfied — the "
        f"test would not catch a P1-H3 LRU regression."
    )

    # Phase 2: write the 10_001 terminators.
    completed_envs: list[EventEnvelope] = [
        _make_envelope(
            "task.completed",
            event_id_index=2 * i + 1,
            payload={
                "task_id": f"t-cardinality-10k-{i:08d}",
                "summary": "10K-test",
                "files_changed": 0,
                "lines_added": 0,
                "lines_removed": 0,
            },
        )
        for i in range(n_pairs)
    ]
    _write_envelopes(log_path, completed_envs)
    # Wait for the tail loop to drain all 20_002 envelopes.
    await _wait_for_total_appended(app, float(2 * n_pairs), timeout_s=30.0)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        body = r.text

    count = _count_canonical_timeseries(body)
    breakdown = _family_breakdown(body)
    # P1-H2 review patch — post-drain bound is <= 54 (NOT <= 53 as Story
    # 11.2 pass-1 P1-H2 baseline — this is one unit MORE PERMISSIVE than
    # the bumped baseline).  Reason: ``persist_every_n_events=1`` + the
    # first envelope causes the tail loop to persist the cursor,
    # materialising one ``omb_metrics_subscriber_cursor_offset_bytes{path=...}``
    # child.  The critical zero-leak invariant
    # (``task_tokens_spent._metrics`` empty) is asserted separately below.
    # Story 11.2.3 PP1 (pass-1 review, Edge P0 live-reproduced): baseline
    # bumped 53 → 61 (omb_event_log_lock_wait_ms Histogram +8 series).
    # Story 13.4: empirically-verified baseline is 64 (stale-61 reconciliation +
    # replication family — see AC2 note); bound is 64 + 1 cursor child = 65.
    # Story 15.4: baseline 64 → 65 (new event_family="git" child); bound 65+1=66.
    # Epic 21 / Story 21-6: baseline 65 → 66 (new event_family="browser" child); bound 66+1=67.
    # Phase 8 hardening: baseline 66 → 69 (3 new families); bound 69+1=70.
    assert count <= 70, (
        f"10K cardinality drift: got {count} canonical timeseries, expected <= 70 "
        f"(69 baseline + 1 cursor-offset path child). Breakdown: {breakdown}"
    )

    # Critical: the per-task gauge has zero labelled children after
    # cleanup.  This is the load-bearing P1-H3 invariant — and per P1-H1
    # is no longer vacuously true (gauges DID materialise in Phase 1).
    leaked = len(list(state.task_tokens_spent._metrics))  # noqa: SLF001 — P1-M1 private API exception (no public child-count surface)
    assert leaked == 0, (
        f"task_tokens_spent leaked {leaked} gauge children after "
        f"{n_pairs}-pair cleanup (P1-H3 LRU regression)"
    )

    # P1-M2 review patch — exact LRU bound after 10_001 completions.
    # The deque ``maxlen=10_000`` MUST have evicted exactly 1 entry,
    # leaving the companion set at exactly 10_000.  ``== 10_000`` (not
    # ``<= 10_000``) is load-bearing: it proves the eviction path
    # (``_remember_terminated_task`` when ``len == maxlen``) actually
    # fired.
    terminated_count = len(state._terminated_task_ids_set)  # noqa: SLF001 — P1-M1 private API exception (no public set-size surface)
    assert terminated_count == 10_000, (
        f"_terminated_task_ids_set size = {terminated_count}; "
        f"expected exact 10_000 (LRU eviction path must have fired "
        f"on the 10_001st completion — P1-M2 review patch)"
    )

    # Proof we actually processed the 20_002 envelopes (not just a subset).
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if (
                sample.name == "omb_events_appended_total"
                and sample.labels.get("event_family") == "task"
            ):
                assert float(sample.value) >= float(2 * n_pairs), (
                    f"events_appended_total{{event_family=task}} = {sample.value}; "
                    f"expected >= {2 * n_pairs} ({n_pairs} started + {n_pairs} completed)"
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

    # Baseline (53) + cursor-offset path child (1 — tail loop persists) +
    # N per-task gauge children = 53 + 1 + 100 = 154.
    # We assert >= 153 (baseline+N) and <= 154 (with one persist-cycle).
    # P1-L1 review patch — the lower bound assumes the asyncio
    # single-task tail loop never yields between
    # ``events_appended_total.inc()`` and ``_update_task_tokens.set()``
    # inside ``update_for()``.  This invariant holds today (single-task
    # asyncio + no ``await`` inside ``update_for``) but is fragile to
    # future refactors.  If the tail loop is ever refactored to ``await``
    # mid-envelope, relax the lower bound or add an explicit
    # synchronisation barrier (e.g. drain to ``events_appended_total ==
    # 2N`` AND then poll until ``len(state.task_tokens_spent._metrics)
    # >= N``).
    # Story 11.2.3: baseline bumped 53 → 61 (8 new series for the
    # omb_event_log_lock_wait_ms Histogram).
    # Story 13.4: empirically-verified baseline 64 (see AC2 note) + N gauges
    # +/- 1 cursor child → 164..165.
    # Story 15.4: baseline 64 → 65 (new event_family="git" child) → 165..166.
    # Epic 21 / Story 21-6: baseline 65 → 66 (new event_family="browser" child) → 166..167.
    # Phase 8 hardening: baseline 66 → 69 (3 new families) → 169..170.
    assert 169 <= count_mid <= 170, (
        f"mid-flight cardinality drift: got {count_mid}, expected 169..170 "
        f"(69 baseline + {n_tasks} per-task gauges +/- 1 cursor-offset path child). "
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
    # Story 11.2.3: baseline bumped 53 → 61 (event_log_lock_wait Histogram).
    # Story 13.4: empirically-verified baseline 64 (see AC2 note); bound 64+1=65.
    # Story 15.4: baseline 64 → 65 (new event_family="git" child); bound 65+1=66.
    # Epic 21 / Story 21-6: baseline 65 → 66 (new event_family="browser" child); bound 66+1=67.
    # Phase 8 hardening: baseline 66 → 69 (3 new families); bound 69+1=70.
    assert count_after <= 70, (
        f"post-drain cardinality drift: got {count_after}, expected <= 70 "
        f"(69 baseline + 1 cursor-offset path child). "
        f"Breakdown: {_family_breakdown(body)}"
    )

    state: MetricsState = app.state.metrics
    # noqa: SLF001 — P1-M1 private API exception (no public child-count surface)
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
    # Story 13.4: empirically-verified baseline 64 (see AC2 note).
    # 64 + 200 novel labelled children = 264.
    assert count >= 64 + leak_count, (
        f"deliberate violation did NOT materialise the cardinality leak: "
        f"got {count} canonical timeseries, expected >= {64 + leak_count}. "
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
    # P1-M3 review patch — exact ``== 53`` (not ``<= 54``) is correct
    # here because AC7 bypasses JSONL: it calls ``update_for`` directly.
    # The tail loop processes 0 bytes, no cursor persist fires, so no
    # ``omb_metrics_subscriber_cursor_offset_bytes{path=...}`` child
    # materialises.  If a future fixture change ever pre-warms the log
    # with a real envelope (or wires this test through
    # ``LifespanManager + log writes``), relax to ``<= 54``.
    # Story 13.4: empirically-verified baseline is 64 (stale-61 reconciliation +
    # replication event-family child — see the AC2 test for the full breakdown).
    # Story 15.4: 64 → 65 for the new ``event_family="git"`` child (Epic 15 / FR72).
    # Epic 21 / Story 21-6: 65 → 66 for the new ``event_family="browser"`` child (FR83/FR86).
    # Phase 8 hardening: 66 → 69 for three new event families (``deployment``,
    # ``file``, ``worker``) — see AC2 test for the full breakdown.
    count = _count_canonical_timeseries(body)
    assert count == 69, (
        f"AC7 cardinality drift: got {count}, expected 69 (no novel families "
        f"created). Breakdown: {_family_breakdown(body)}"
    )


# ===========================================================================
# P1-L7 — Unit test for the ``_count_canonical_timeseries`` helper itself.
# ===========================================================================


@pytest.mark.integration
def test_count_canonical_timeseries_filters_created_metadata() -> None:
    """P1-L7 — ``_count_canonical_timeseries`` filters ``_created`` samples.

    The load-bearing helper used by every other test in this file
    deserves a direct unit test — a bug in the ``_created`` filter would
    silently inflate or deflate every cardinality count and the failure
    mode would manifest as confusing AC drift rather than a clear
    "helper is broken" signal.

    Construct a synthetic Prometheus exposition body with both
    ``_created`` metadata samples (TYPE-counter sidecars emitted by
    ``prometheus_client`` for every counter labelset) and non-
    ``_created`` canonical samples; assert the helper returns the
    non-``_created`` count exactly.
    """
    # Synthetic exposition body: 3 canonical counter samples + 3
    # ``_created`` metadata samples + 1 canonical gauge sample = 4
    # canonical timeseries.  The ``_created`` filter MUST exclude the
    # 3 metadata lines without touching the 4 canonical ones.
    body = """\
# HELP omb_events_appended_total events appended
# TYPE omb_events_appended_total counter
omb_events_appended_total{event_family="task"} 5.0
omb_events_appended_total_created{event_family="task"} 1.7e9
omb_events_appended_total{event_family="session"} 3.0
omb_events_appended_total_created{event_family="session"} 1.7e9
omb_events_appended_total{event_family="unknown"} 0.0
omb_events_appended_total_created{event_family="unknown"} 1.7e9
# HELP omb_lag_seconds tail-loop lag
# TYPE omb_lag_seconds gauge
omb_lag_seconds 0.001
"""
    count = _count_canonical_timeseries(body)
    assert count == 4, (
        f"_count_canonical_timeseries miscount: got {count}, expected 4 "
        f"(3 canonical counter samples + 1 canonical gauge sample; the 3 "
        f"_created metadata samples must be filtered)."
    )


# ===========================================================================
# P1-L8 — ``task.stop_requested`` terminal cleanup mirrors ``task.completed``.
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_stop_requested_also_cleans_gauge(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """P1-L8 — ``task.stop_requested`` clears the per-task gauge child.

    ``_update_task_lifecycle_and_clear_task_gauge`` is wired for BOTH
    terminal envelope types (``task.completed`` and
    ``task.stop_requested``).  AC3 / AC4 only exercise ``task.completed``
    so the ``task.stop_requested`` cleanup path is untested at
    integration level.  This test fingerprints that path.

    Sequence: emit ``task.execution.started`` with ``tokens_used`` (so a
    per-task gauge child materialises) → emit ``task.stop_requested``
    for the same task_id; assert the gauge child is removed.
    """
    app, event_dir = cardinality_test_app
    log_path = _today_log_path(event_dir)
    state: MetricsState = app.state.metrics
    task_id = "t-stop-requested-fingerprint-0001"

    # Phase 1: materialise a per-task gauge child.
    started_env = _make_envelope(
        "task.execution.started",
        event_id_index=500_000,
        payload={"task_id": task_id, "tokens_used": 42},
    )
    _write_envelopes(log_path, [started_env])
    await _wait_for_total_appended(app, 1.0, timeout_s=15.0)

    # Sanity: gauge child IS alive before the terminator.
    alive_before = len(list(state.task_tokens_spent._metrics))  # noqa: SLF001 — P1-M1 private API exception (no public child-count surface)
    assert alive_before == 1, (
        f"per-task gauge child did not materialise after task.execution.started "
        f"with tokens_used: got {alive_before} children, expected 1.  This means "
        f"the cleanup path below would be vacuously satisfied."
    )

    # Phase 2: terminate via ``task.stop_requested`` (NOT task.completed).
    stop_env = _make_envelope(
        "task.stop_requested",
        event_id_index=500_001,
        payload={"task_id": task_id, "reason": "test-fingerprint"},
    )
    _write_envelopes(log_path, [stop_env])
    await _wait_for_total_appended(app, 2.0, timeout_s=15.0)

    # Gauge child MUST be cleaned (same invariant as task.completed).
    leaked = len(list(state.task_tokens_spent._metrics))  # noqa: SLF001 — P1-M1 private API exception (no public child-count surface)
    assert leaked == 0, (
        f"task_tokens_spent leaked {leaked} gauge children after "
        f"task.stop_requested cleanup (terminal-path regression)"
    )
    # And the task_id is remembered in the ghost-gauge guard set.
    assert task_id in state._terminated_task_ids_set, (  # noqa: SLF001 — P1-M1 private API exception (no public set-membership surface)
        f"task_id {task_id!r} not in _terminated_task_ids_set after "
        f"task.stop_requested — ghost-gauge guard not armed"
    )


# ===========================================================================
# P1-L9 — Ghost-gauge prevention: ``task.budget_exceeded`` after terminator.
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ghost_gauge_prevented_by_terminated_task_ids(
    cardinality_test_app: tuple[FastAPI, Path],
) -> None:
    """P1-L9 — out-of-order ``task.budget_exceeded`` MUST NOT resurrect gauge.

    Fingerprints Story 10.4 P1-H3's ghost-gauge guard at the integration
    layer.  Sequence:

      1. ``task.execution.started`` (with ``tokens_used``) →
         per-task gauge child materialises.
      2. ``task.completed`` → gauge child cleared, task_id appended to
         ``_terminated_task_ids_set``.
      3. ``task.budget_exceeded`` for the SAME task_id (out-of-order
         arrival simulating replay or clock skew).

    Without the guard added in Story 10.4 P1-H3, step 3 would call
    ``state.task_tokens_spent.labels(task_id=...).set(...)`` and
    resurrect a gauge child with no subsequent cleanup — a monotonic
    cardinality leak.  The guard short-circuits via the
    ``_terminated_task_ids_set`` membership check.
    """
    app, event_dir = cardinality_test_app
    log_path = _today_log_path(event_dir)
    state: MetricsState = app.state.metrics
    task_id = "t-ghost-gauge-fingerprint-0001"

    started_env = _make_envelope(
        "task.execution.started",
        event_id_index=600_000,
        payload={"task_id": task_id, "tokens_used": 10},
    )
    completed_env = _make_envelope(
        "task.completed",
        event_id_index=600_001,
        payload={
            "task_id": task_id,
            "summary": "ghost-gauge-test",
            "files_changed": 0,
            "lines_added": 0,
            "lines_removed": 0,
        },
    )
    # Out-of-order token-bearing envelope arrives AFTER the terminator.
    budget_env = _make_envelope(
        "task.budget_exceeded",
        event_id_index=600_002,
        payload={
            "task_id": task_id,
            "token_limit": 100,
            "tokens_used": 99,
            "step": 1,
        },
    )
    _write_envelopes(log_path, [started_env, completed_env, budget_env])
    await _wait_for_total_appended(app, 3.0, timeout_s=15.0)

    # Critical: the gauge MUST NOT have a child for this task_id.
    # The Story 10.4 P1-H3 guard inside ``_update_task_tokens`` short-
    # circuits because the task_id is in ``_terminated_task_ids_set``.
    leaked = len(list(state.task_tokens_spent._metrics))  # noqa: SLF001 — P1-M1 private API exception (no public child-count surface)
    assert leaked == 0, (
        f"ghost-gauge regression: task_tokens_spent has {leaked} children "
        f"after out-of-order task.budget_exceeded; the _terminated_task_ids "
        f"guard (Story 10.4 P1-H3) must short-circuit but did not."
    )
    # And the task_id is still in the guard set (the guard fired).
    assert task_id in state._terminated_task_ids_set, (  # noqa: SLF001 — P1-M1 private API exception (no public set-membership surface)
        f"task_id {task_id!r} dropped from _terminated_task_ids_set — "
        f"future ghost-gauge events would not be guarded"
    )
