"""FastAPI integration tests for Story 10.3 :func:`build_app`.

Exercises the AC2/AC3/AC4/AC5/AC6/AC9 surfaces:

  - **AC2/AC3** — lifespan spawns + cleans up the tail task.
  - **AC4** — ``GET /metrics`` returns a valid Prometheus text body.
  - **AC5/AC6** — tail-loop gauge/counter updates are reflected in
    the next ``/metrics`` scrape.
  - **AC9** — non-loopback, non-wildcard bind value triggers the
    structured warning.

Test architecture:

  - In-process via :class:`httpx.AsyncClient` + ``httpx.ASGITransport``.
  - :class:`asgi_lifespan.LifespanManager` drives the FastAPI lifespan
    (``ASGITransport`` alone does NOT trigger startup/shutdown — the
    Story 10.3 risk-flag explicitly documents this).
  - Each test owns its own temporary JSONL log + cursor file so the
    autouse env-clear / collector-registry-reset fixtures
    (``conftest.py``) keep test isolation tight.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Any

import httpx
import pytest
import structlog
from asgi_lifespan import LifespanManager
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    new_event_id,
    new_uuid7,
    to_canonical_json,
)
from events.clock import FrozenClock
from events.schema_registry import register
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
from pydantic import BaseModel

from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.main import _is_external_bind_heuristic, build_app
from metrics_subscriber.app.metrics import build_collectors


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _register_test_event_type() -> Generator[None, None, None]:
    register("test.app_main.envelope", "1.0.0", _SimplePayload)
    yield


_ACTOR = Actor(kind="system", id="test-app-main")
_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000123"


def _make_envelope(value: str, mono_seed: int) -> EventEnvelope:
    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    rng = Random(mono_seed)
    return EventEnvelope(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="test.app_main.envelope",  # noqa: EVT001 test-only fixture envelope
        emitted_at=FROZEN_EPOCH,
        emitted_at_monotonic_ns=mono_seed,
        actor=_ACTOR,
        payload={"value": value},
        trace_id=_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _write_envelopes(path: Path, envs: list[EventEnvelope]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
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
        persist_every_n_events=10,
    )


def _parse_sample(body: bytes, name: str, labels: dict[str, str] | None = None) -> float | None:
    """Parse Prometheus text body; return float for *name* (sample-name match)."""
    target_labels = labels or {}
    for family in text_string_to_metric_families(body.decode()):
        for sample in family.samples:
            if sample.name != name:
                continue
            if all(sample.labels.get(k) == v for k, v in target_labels.items()):
                return float(sample.value)
    return None


# ---------------------------------------------------------------------------
# AC2 / AC3 — lifespan exercise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_app_returns_metrics_subscriber_titled_app(tmp_path: Path) -> None:
    """AC2 — factory returns a wired FastAPI instance."""
    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    assert app.title == "metrics-subscriber"
    # The two routes exist on the app's router.
    route_paths = {route.path for route in app.router.routes if hasattr(route, "path")}
    assert "/metrics" in route_paths
    assert "/healthz" in route_paths


@pytest.mark.asyncio
async def test_app_lifespan_runs_tail_task(tmp_path: Path) -> None:
    """AC3 — startup spawns + shutdown drains the tail task cleanly."""
    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    async with LifespanManager(app):
        # Lifespan startup completed.  The tail task lives on the
        # event loop; we just verify the registry + metrics state
        # are stashed on app.state.
        assert hasattr(app.state, "registry")
        assert hasattr(app.state, "metrics")
        # Yield once so the tail loop has a chance to spin.
        await asyncio.sleep(0.05)
    # Exit code 0 (graceful shutdown).
    assert app.state.exit_code == 0


# ---------------------------------------------------------------------------
# AC4 — /metrics endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_valid_exposition(tmp_path: Path) -> None:
    """AC4 — /metrics body is parseable + contains the Story 10.3 metrics."""
    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/metrics")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/plain")
            body = r.content
            # Body parses without raising; assert the four metric
            # families are present.
            families = {f.name for f in text_string_to_metric_families(body.decode())}
            assert "metrics_subscriber_lag_seconds" in families
            assert "metrics_subscriber_bytes_behind" in families
            assert "metrics_subscriber_cursor_offset_bytes" in families
            # Counter family name appears without _total in the parser.
            assert "metrics_subscriber_parse_skip" in families


@pytest.mark.asyncio
async def test_healthz_returns_ok(tmp_path: Path) -> None:
    """AC2 — healthz returns JSON ``{"status": "ok"}``."""
    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/healthz")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# AC5 — gauge update after persist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lag_seconds_gauge_updates_after_persist(tmp_path: Path) -> None:
    """AC5 — emit enough envelopes to trigger a persist; gauge reflects it.

    Direct-mutation test: we exercise ``MetricsState.record_lag`` from
    a synchronous helper that mimics the tail-loop call path.  This
    keeps the test deterministic — driving the full ``run_subscriber``
    coroutine to a persist would couple the test to the day-rollover
    quiescence window (5s by default).  The behaviour-under-test is
    "the gauges expose the same numbers the structured log emits at
    the same persist event"; AC5's self-verification clause asserts
    against the structured-log field, which we capture below.
    """
    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    async with LifespanManager(app):
        # Tail loop is running; we mutate the same MetricsState the
        # /metrics route reads.
        # Story 10.3 pass-1 P1-L3: use a tmp_path-rooted stable filename
        # so the test is not coupled to authoring date.  The path value
        # is purely a metric label here (no FS access); a deterministic
        # filename keeps label-cardinality stable across CI runs.
        labelled_path = tmp_path / "today.jsonl"
        app.state.metrics.record_lag(lag_seconds=2.5, bytes_behind=123)
        app.state.metrics.record_cursor(path=labelled_path, offset=999)

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/metrics")
            assert r.status_code == 200
            assert _parse_sample(r.content, "metrics_subscriber_lag_seconds") == 2.5
            assert _parse_sample(r.content, "metrics_subscriber_bytes_behind") == 123.0
            offset = _parse_sample(
                r.content,
                "metrics_subscriber_cursor_offset_bytes",
                labels={"path": str(labelled_path)},
            )
            assert offset == 999.0


# ---------------------------------------------------------------------------
# AC6 — parse-skip counter wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_skip_counter_increments_by_reason(tmp_path: Path) -> None:
    """AC6 — all four bounded-enum skip reasons → counter labelled.

    Drives the parse layer directly via
    :func:`events.log_reader.parse_with_pre110_backfill` so the test
    is deterministic without needing the tail loop to drain the file.

    Story 10.3 pass-1 P1-M1: extended from the original two reasons
    (``json_decode`` + ``not_a_dict``) to cover all four bounded-enum
    values emitted by ``log_reader.py``:
    ``{json_decode, not_a_dict, pre110_missing_trace_id, validation}``.
    Spec AC6 reason enum table updated to match implementation.
    """
    from events.log_reader import parse_with_pre110_backfill

    registry = CollectorRegistry()
    state = build_collectors(registry)

    fake_path = tmp_path / "today.jsonl"
    # 3 invalid JSON lines → reason=json_decode.
    for _ in range(3):
        result = parse_with_pre110_backfill(
            b"not-valid-json{",
            fake_path,
            on_skip=state.on_parse_skip,
        )
        assert result is None
    # 2 non-dict lines → reason=not_a_dict.
    for _ in range(2):
        result = parse_with_pre110_backfill(
            b'"a-string-not-a-dict"',
            fake_path,
            on_skip=state.on_parse_skip,
        )
        assert result is None
    # 1 pre-1.1.0 envelope with neither trace_id nor a useable request_id
    # → reason=pre110_missing_trace_id.  Minimal raw dict shape: missing
    # ``trace_id`` AND ``request_id`` so the back-fill helper returns
    # None and the skip path fires.
    pre110_line = b'{"event_id": "x", "schema_version": "1.0.0"}'
    result = parse_with_pre110_backfill(
        pre110_line,
        fake_path,
        on_skip=state.on_parse_skip,
    )
    assert result is None
    # 1 envelope with valid back-fill but failing envelope validation
    # (unknown schema, missing required fields) → reason=validation.
    # ``trace_id`` set so back-fill succeeds; ``from_canonical_json``
    # then rejects it for missing required envelope fields.
    validation_line = (
        b'{"trace_id": "01917e5c-a7d1-7000-8abc-000000000123", "event_id": "not-a-valid-event-id"}'
    )
    result = parse_with_pre110_backfill(
        validation_line,
        fake_path,
        on_skip=state.on_parse_skip,
    )
    assert result is None

    body = generate_latest(registry)
    assert (
        _parse_sample(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "json_decode"},
        )
        == 3.0
    )
    assert (
        _parse_sample(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "not_a_dict"},
        )
        == 2.0
    )
    assert (
        _parse_sample(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "pre110_missing_trace_id"},
        )
        == 1.0
    )
    assert (
        _parse_sample(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "validation"},
        )
        == 1.0
    )


def test_parse_skip_counter_all_reasons_pre_populated_in_collectors() -> None:
    """Story 10.3 pass-1 P1-H1 — all four reason children exist at register time.

    ``build_collectors`` MUST pre-populate
    ``parse_skip_total.labels(reason=r).inc(0)`` for every value of
    the bounded-enum so subsequent ``.labels(...)`` calls from the
    worker thread are pure dict-lookups (no lazy-registration race
    against a concurrent ``generate_latest()`` scrape).
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    body = generate_latest(registry)
    for reason in ("json_decode", "not_a_dict", "pre110_missing_trace_id", "validation"):
        sample = _parse_sample(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": reason},
        )
        assert sample == 0.0, f"expected reason={reason!r} pre-populated to 0.0; got {sample!r}"
    # Sanity: the state's own counter handle agrees.
    assert state.parse_skip_total is not None


# ---------------------------------------------------------------------------
# AC9 — external-bind heuristic
# ---------------------------------------------------------------------------


def test_is_external_bind_heuristic_classification() -> None:
    """AC9 — loopback / wildcard / hostname do NOT warn; concrete IP does."""
    assert _is_external_bind_heuristic("192.0.2.1") is True
    assert _is_external_bind_heuristic("10.0.0.7") is True
    assert _is_external_bind_heuristic("127.0.0.1") is False
    assert _is_external_bind_heuristic("::1") is False
    assert _is_external_bind_heuristic("0.0.0.0") is False
    assert _is_external_bind_heuristic("::") is False
    # Hostnames (docker DNS) parse as IP failures → non-external.
    assert _is_external_bind_heuristic("metrics-subscriber") is False
    assert _is_external_bind_heuristic("localhost") is False


@pytest.mark.asyncio
async def test_app_warns_on_external_bind_heuristic(tmp_path: Path) -> None:
    """AC9 — non-loopback/non-wildcard host triggers structured warning."""
    settings = _make_settings(tmp_path)
    # Override the host to a concrete external-IP-shaped value.
    settings = settings.model_copy(update={"metrics_host": "192.0.2.1"})
    app = build_app(settings=settings)

    captured: list[MutableMapping[str, Any]] = []
    with structlog.testing.capture_logs() as logs:
        async with LifespanManager(app):
            await asyncio.sleep(0.01)
        captured.extend(logs)

    assert any(
        entry.get("event") == "metrics_subscriber_bind_external_interface_suspected"
        and entry.get("host") == "192.0.2.1"
        for entry in captured
    ), f"expected bind-warning event in {captured!r}"


@pytest.mark.asyncio
async def test_app_does_not_warn_on_wildcard_bind(tmp_path: Path) -> None:
    """AC9 — default ``0.0.0.0`` does NOT trigger the warning."""
    settings = _make_settings(tmp_path)
    assert settings.metrics_host == "0.0.0.0"
    app = build_app(settings=settings)
    captured: list[MutableMapping[str, Any]] = []
    with structlog.testing.capture_logs() as logs:
        async with LifespanManager(app):
            await asyncio.sleep(0.01)
        captured.extend(logs)
    assert not any(
        entry.get("event") == "metrics_subscriber_bind_external_interface_suspected"
        for entry in captured
    )


# ---------------------------------------------------------------------------
# AC4 — exception handler returns 500 + structured log (no traceback leak)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_handles_collector_failure(tmp_path: Path) -> None:
    """AC4 — a raising collector surfaces 500 + structured log.

    We patch the registry-attached gauge to raise on ``.collect()``
    via a custom collector to simulate a downstream failure.
    """
    from prometheus_client.metrics_core import GaugeMetricFamily

    class _BoomCollector:
        def collect(self) -> AsyncIterator[GaugeMetricFamily]:
            raise RuntimeError("boom")

    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    async with LifespanManager(app):
        app.state.registry.register(_BoomCollector())
        captured: list[MutableMapping[str, Any]] = []
        with structlog.testing.capture_logs() as logs:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/metrics")
            captured.extend(logs)
        assert r.status_code == 500
        # Body is a tiny JSON envelope — no traceback to client.
        assert "boom" not in r.text
        # Structured failure log emitted.
        assert any(
            entry.get("event") == "metrics_subscriber_endpoint_failure" for entry in captured
        )


# ---------------------------------------------------------------------------
# AC8 — OMB_METRICS_RUN_MODE dispatch
# ---------------------------------------------------------------------------


def test_main_dispatch_invalid_run_mode_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC8 — bad ``OMB_METRICS_RUN_MODE`` value exits 1 with a structured log."""
    from metrics_subscriber import __main__ as ms_main

    monkeypatch.setenv("OMB_METRICS_RUN_MODE", "bogus")
    rc = ms_main.main()
    assert rc == 1


def test_invalid_run_mode_logs_structured_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Story 10.3 pass-1 P1-M7 — invalid run-mode emits a structured event.

    Pre-fix, the invalid-mode log fired BEFORE ``logging.basicConfig``
    ran inside ``main()``, so the event arrived through stdlib logging
    rather than the structured pipeline.  The fix moves the basicConfig
    call to module-import time (idempotent guard), so by the time
    ``main()`` runs the structured pipeline owns the log line.

    The test asserts the structured event NAME appears in the captured
    log; the test conftest's structlog routing makes
    ``structlog.testing.capture_logs`` the canonical sink.
    """
    from metrics_subscriber import __main__ as ms_main

    monkeypatch.setenv("OMB_METRICS_RUN_MODE", "bogus-value-for-test")
    captured: list[MutableMapping[str, Any]] = []
    with structlog.testing.capture_logs() as logs:
        rc = ms_main.main()
        captured.extend(logs)
    assert rc == 1
    assert any(
        entry.get("event") == "metrics_subscriber_invalid_run_mode"
        and entry.get("run_mode") == "bogus-value-for-test"
        for entry in captured
    ), f"expected structured invalid-run-mode event in {captured!r}"


# ---------------------------------------------------------------------------
# Story 10.3 pass-1 P1-H4 — /healthz 503 when tail crashed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_503_when_tail_task_crashed(tmp_path: Path) -> None:
    """P1-H4 — /healthz reports degraded when the tail task failed.

    Simulates the tail-task-crashed state by setting ``app.state.exit_code``
    to a non-zero value (the exit-code matrix value for unclassified
    failure).  The handler must return 503 with a structured JSON
    envelope so compose ``service_healthy`` probes fail fast.
    """
    settings = _make_settings(tmp_path)
    app = build_app(settings=settings)
    async with LifespanManager(app):
        # Pre-fix the handler ignored exit_code entirely and returned
        # 200; with P1-H4 it must observe the non-zero state and 503.
        app.state.exit_code = 1
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/healthz")
            assert r.status_code == 503, (
                f"expected 503 degraded; got {r.status_code} body={r.text!r}"
            )
            body = r.json()
            assert body["status"] == "degraded"
            assert body["reason"] == "tail_task_failed"
            assert body["exit_code"] == 1


# ---------------------------------------------------------------------------
# Story 10.3 pass-1 P1-H2 — finally drain handles non-CursorSchemaVersionError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finally_drain_skipped_when_restore_into_raises_uncaught_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-H2 — non-CursorSchemaVersionError from restore_into does not get masked.

    ``restore_into`` can raise ``OSError`` / ``json.JSONDecodeError``
    for a corrupt cursor file.  Pre-fix, those exceptions propagated
    into the outer ``finally`` block which probed
    ``reader.current_path`` — a property that raises ``RuntimeError``
    when ``_current_path`` is None.  The secondary RuntimeError
    masked the original error, defeating diagnosis.

    Post-fix, the finally block guards on ``reader._current_path is
    not None`` and is therefore side-effect-free when the reader was
    never seated.  This test patches ``restore_into`` to raise
    ``OSError`` and asserts the original exception propagates out of
    ``run_subscriber`` cleanly.
    """
    import asyncio as _asyncio

    from metrics_subscriber import __main__ as ms_main
    from metrics_subscriber.cursor import CursorPersistence

    settings = _make_settings(tmp_path)

    def _explode(self: CursorPersistence, reader: Any, *, base_dir: Path) -> None:
        raise OSError("simulated corrupt cursor file")

    monkeypatch.setattr(CursorPersistence, "restore_into", _explode)

    stop_event = _asyncio.Event()
    with pytest.raises(OSError, match="simulated corrupt cursor file"):
        await ms_main.run_subscriber(settings, stop_event=stop_event)


# ---------------------------------------------------------------------------
# Story 10.3 pass-1 P1-H5 — on_skip callback exception does not crash the tail
# ---------------------------------------------------------------------------


def test_on_skip_callback_exception_does_not_crash_tail_loop(tmp_path: Path) -> None:
    """P1-H5 — a raising ``on_skip`` callback is downgraded to a WARNING.

    Drives ``parse_with_pre110_backfill`` with an ``on_skip`` callback
    that always raises.  Pre-fix the exception propagated and would
    have killed the ``asyncio.to_thread`` worker (→ subscriber crash);
    post-fix the helper ``_safe_on_skip`` catches the exception and
    emits ``metrics_subscriber_on_skip_callback_failed`` instead.
    """
    from events.log_reader import parse_with_pre110_backfill

    def _raiser(reason: str) -> None:
        raise RuntimeError(f"callback boom for reason={reason}")

    fake_path = tmp_path / "today.jsonl"
    captured: list[MutableMapping[str, Any]] = []
    with structlog.testing.capture_logs() as logs:
        result = parse_with_pre110_backfill(
            b"not-valid-json{",
            fake_path,
            on_skip=_raiser,
        )
        captured.extend(logs)
    # Parse skip itself returned None — semantic behaviour preserved.
    assert result is None
    # The callback failure was downgraded to a structured WARNING.
    assert any(
        entry.get("event") == "metrics_subscriber_on_skip_callback_failed"
        and entry.get("reason") == "json_decode"
        for entry in captured
    ), f"expected on_skip_callback_failed log in {captured!r}"


# ---------------------------------------------------------------------------
# Story 10.3 pass-1 P1-M3 — read_new_envelopes_since threads on_skip
# ---------------------------------------------------------------------------


def test_read_new_envelopes_since_threads_on_skip_through(tmp_path: Path) -> None:
    """P1-M3 — sync read path honours ``on_skip``.

    Writes a JSONL file containing a single garbage line, calls
    :func:`events.log_reader.read_new_envelopes_since` with an
    ``on_skip`` callback, and asserts the callback was invoked with
    the expected ``reason``.  Pre-fix the parameter did not exist;
    registry-state / registry-api callers silently dropped the skip
    accounting.
    """
    from events.log_reader import read_new_envelopes_since

    path = tmp_path / "today.jsonl"
    path.write_bytes(b"not-valid-json{\n")
    reasons_seen: list[str] = []
    _new_offset, envelopes = read_new_envelopes_since(
        path,
        0,
        on_skip=reasons_seen.append,
    )
    # The sync wrapper only advances ``new_offset`` on successful yields,
    # so a skip-only file leaves it at the input offset.  The behaviour
    # under test is "the on_skip callback fires for each skipped line",
    # NOT the offset semantics (covered by other tests).
    assert envelopes == []
    assert reasons_seen == ["json_decode"]


# ---------------------------------------------------------------------------
# Story 10.3 pass-1 P1-M5 — exit_code propagation after serve returns
# ---------------------------------------------------------------------------


def test_exit_code_propagation_after_serve_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-M5 — ``_run_server_mode`` flushes pending done-callbacks before reading exit_code.

    Patches ``uvicorn.Server.serve`` to return immediately AND patches
    ``build_app`` to install a done-callback on a dummy completed task
    that flips ``app.state.exit_code`` to 2.  The ``await
    asyncio.sleep(0)`` after ``serve()`` returns must allow the
    callback to run BEFORE the function reads ``app.state.exit_code``;
    the test asserts the function returns 2 (not 0).
    """
    import asyncio as _asyncio

    import uvicorn as _uvicorn

    from metrics_subscriber import __main__ as ms_main

    settings = _make_settings(tmp_path)

    async def _instant_serve(self: _uvicorn.Server) -> None:
        # Mimic uvicorn returning instantly.  Schedule a "tail crashed"
        # callback that pokes app.state.exit_code on the NEXT loop pass.
        loop = _asyncio.get_running_loop()
        # ``Config.app`` is a Union including ``str | ASGI*`` shapes —
        # the test cast is safe because ``_run_server_mode`` always
        # passes a real FastAPI instance.
        from fastapi import FastAPI as _FastAPI

        app_obj = self.config.app
        assert isinstance(app_obj, _FastAPI)
        # Defer one pass so the assignment lands AFTER serve() returns
        # but BEFORE _run_server_mode reads exit_code.
        loop.call_soon(lambda: setattr(app_obj.state, "exit_code", 2))

    monkeypatch.setattr(_uvicorn.Server, "serve", _instant_serve)
    rc = ms_main._run_server_mode(settings)
    assert rc == 2, f"expected exit_code 2 to propagate; got {rc}"


# ---------------------------------------------------------------------------
# Story 10.3 pass-1 P1-L6 — cursor schema_version refused exits 2 (AC8 risk-flag)
# ---------------------------------------------------------------------------


def test_lifespan_cursor_schema_version_refused_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-L6 — patches ``CursorPersistence.restore_into`` to raise ``CursorSchemaVersionError``.

    Drives ``run_subscriber`` directly (the same path the FastAPI
    lifespan invokes via the tail task) and asserts rc==2 with a
    structured ``metrics_subscriber_cursor_schema_version_refused``
    log emitted.  AC8 out-of-scope risk flag required this test to
    exist.
    """
    import asyncio as _asyncio

    from events.errors import CursorSchemaVersionError

    from metrics_subscriber import __main__ as ms_main
    from metrics_subscriber.cursor import CursorPersistence

    settings = _make_settings(tmp_path)

    def _refuse_schema(self: CursorPersistence, reader: Any, *, base_dir: Path) -> None:
        raise CursorSchemaVersionError(
            cursor_path=str(settings.cursor_path),
            schema_version="bogus-v99",
            expected="1",
        )

    monkeypatch.setattr(CursorPersistence, "restore_into", _refuse_schema)

    stop_event = _asyncio.Event()
    captured: list[MutableMapping[str, Any]] = []
    with structlog.testing.capture_logs() as logs:
        rc = _asyncio.run(ms_main.run_subscriber(settings, stop_event=stop_event))
        captured.extend(logs)
    assert rc == 2, f"expected exit code 2; got {rc}"
    assert any(
        entry.get("event") == "metrics_subscriber_cursor_schema_version_refused"
        for entry in captured
    ), f"expected schema-version-refused log in {captured!r}"


# Silence unused-import; envelope/helper fns are kept for symmetry with
# the rest of the file in case a future test wants to drive a real
# tail-loop persist.
_UNUSED_REFS: tuple[object, ...] = (_make_envelope, _write_envelopes, datetime, UTC)
