"""Integration tests for the Story 10.4 FR62 metric set.

Each test:

  1. Spins up :func:`build_app` via :class:`asgi_lifespan.LifespanManager`.
  2. Writes controlled envelopes to the test event-log directory.
  3. Polls the cursor until the tail loop drains the log.
  4. Scrapes ``/metrics`` via :class:`httpx.AsyncClient` +
     :class:`httpx.ASGITransport`.
  5. Parses the body via
     :func:`prometheus_client.parser.text_string_to_metric_families`.
  6. Asserts metric values + label combinations exactly.

ACs covered: AC1 (task lifecycle), AC2 (session lifecycle), AC3
(secret.accessed), AC4 (events_appended family), AC5 (task tokens gauge
+ cleanup), AC7 (dispatch hooked into tail loop, crash-resilient),
AC9 (integration test methodology).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
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
from events.schema_registry import register
from fastapi import FastAPI
from prometheus_client.parser import text_string_to_metric_families
from pydantic import BaseModel

from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.main import build_app

_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000123"
_REQUEST_ID = "01917e5c-a7d1-7000-8abc-000000000456"


class _DispatchTestPayload(BaseModel):
    """Permissive payload used by integration-test envelope writers."""

    model_config = {"extra": "allow"}


@pytest.fixture(autouse=True)
def _register_dispatch_test_event_types() -> Generator[None, None, None]:
    """Register a permissive payload under the bounded ``"task"`` family.

    The Story 10.4 tail loop dispatches envelopes that round-trip through
    :func:`events.log_reader.iter_new_envelopes_since` — which validates
    against the schema registry.

    Story 10.4 P1-M4 — the test event type uses the existing ``"task"``
    family prefix so an accidental tail-loop emission cannot leak an
    out-of-enum ``event_family`` label.  The schema_registry has no
    documented unregister API, so we keep the autouse-without-teardown
    pattern but constrain the side-effect to an enum-safe prefix.  Story
    10.4 integration tests primarily use the PRODUCTION event types
    (task.*, session.*, secret.accessed); the permissive payload is
    reserved for the unknown-family stress test path.
    """
    register("task.test_dispatch_synthetic", "1.0.0", _DispatchTestPayload)
    yield


def _make_envelope(
    event_type: str,
    *,
    event_id_suffix: str,
    actor_kind: str = "system",
    payload: dict[str, Any] | None = None,
    schema_version: str = "1.1.0",
) -> EventEnvelope:
    """Construct an envelope for direct JSONL writing."""
    return EventEnvelope(
        event_id=f"e-01917e5c-a7d1-7000-8abc-{event_id_suffix:>012}",
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


def _parse_sample(body: bytes, name: str, labels: dict[str, str] | None = None) -> float | None:
    target_labels = labels or {}
    for family in text_string_to_metric_families(body.decode()):
        for sample in family.samples:
            if sample.name != name:
                continue
            if all(sample.labels.get(k) == v for k, v in target_labels.items()):
                return float(sample.value)
    return None


async def _wait_for_event_appended(
    app: FastAPI,
    family: str,
    expected: float,
    *,
    timeout_s: float = 5.0,
) -> None:
    """Poll the ``/metrics`` endpoint until the events_appended counter reaches *expected*.

    Story 10.4 P1-M2 — uses the public Prometheus HTTP exposition + the
    canonical :func:`prometheus_client.parser.text_string_to_metric_families`
    parser instead of the private ``Counter._value.get()`` introspection.
    Slightly slower (HTTP round-trip per poll) but immune to
    prometheus_client minor-version churn.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    deadline = asyncio.get_running_loop().time() + timeout_s
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        while asyncio.get_running_loop().time() < deadline:
            r = await client.get("/metrics")
            if r.status_code == 200:
                value = _parse_sample(
                    r.content,
                    "omb_events_appended_total",
                    labels={"event_family": family},
                )
                if value is not None and value >= expected:
                    return
            await asyncio.sleep(0.01)
    raise AssertionError(
        f"events_appended_total{{event_family={family}}} did not reach "
        f"{expected} within {timeout_s}s"
    )


@pytest_asyncio.fixture(loop_scope="function")
async def test_app_with_event_dir(
    tmp_path: Path,
) -> AsyncIterator[tuple[FastAPI, Path]]:
    """Yield (app, event_dir) with the lifespan started (Story 10.4 AC9)."""
    settings = _make_settings(tmp_path)
    today = datetime.now(UTC).date().isoformat()
    event_dir = settings.event_log_dir
    log_path = event_dir / f"{today}.jsonl"
    log_path.touch()

    app = build_app(settings=settings)
    async with LifespanManager(app):
        yield app, event_dir


def _today_log_path(event_dir: Path) -> Path:
    today = datetime.now(UTC).date().isoformat()
    return event_dir / f"{today}.jsonl"


# ===========================================================================
# AC1 — task lifecycle counter integration.
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_task_lifecycle_counter_increments(
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC1 integration — write 3 task.* envelopes; counters reflect them."""
    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)
    _write_envelopes(
        log_path,
        [
            _make_envelope("task.created", event_id_suffix="001", payload={"task_id": "t-a"}),
            _make_envelope(
                "task.planning.started", event_id_suffix="002", payload={"task_id": "t-a"}
            ),
            _make_envelope("task.plan.ready", event_id_suffix="003", payload={"task_id": "t-a"}),
        ],
    )
    await _wait_for_event_appended(app, "task", 3.0)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        for event_type in ("task.created", "task.planning.started", "task.plan.ready"):
            value = _parse_sample(
                r.content,
                "omb_task_lifecycle_events_total",
                labels={"event_type": event_type},
            )
            assert value == 1.0, f"event_type={event_type} expected 1.0, got {value}"


# ===========================================================================
# AC2 — session lifecycle counter integration.
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_session_lifecycle_counter_per_phase(
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC2 integration — session.* envelopes route to per-phase labels."""
    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)
    _write_envelopes(
        log_path,
        [
            _make_envelope(
                "session.started",
                event_id_suffix="010",
                payload={
                    "session_id": "sess-01917e5c-a7d1-7000-8abc-000000000111",
                    "operator_id": "op-1",
                },
            ),
            _make_envelope(
                "session.heartbeat",
                event_id_suffix="011",
                payload={"session_id": "sess-01917e5c-a7d1-7000-8abc-000000000111"},
            ),
            _make_envelope(
                "session.finished",
                event_id_suffix="012",
                payload={"session_id": "sess-01917e5c-a7d1-7000-8abc-000000000111"},
            ),
        ],
    )
    await _wait_for_event_appended(app, "session", 3.0)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.content
        for phase in ("started", "heartbeat", "finished"):
            value = _parse_sample(
                body, "omb_session_lifecycle_events_total", labels={"phase": phase}
            )
            assert value == 1.0, f"phase={phase} expected 1.0, got {value}"


# ===========================================================================
# AC3 — secret.accessed counter integration.
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_secret_accessed_counter_by_actor_kind(
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC3 integration — secret.accessed routes through envelope.actor.kind."""
    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)
    _write_envelopes(
        log_path,
        [
            _make_envelope(
                "secret.accessed",
                event_id_suffix="020",
                actor_kind="operator",
                payload={
                    "secret_name": "github_token",
                    "session_id": "sess-01917e5c-a7d1-7000-8abc-000000000222",
                    "task_id": None,
                    "purpose": "github_push",
                },
            ),
            _make_envelope(
                "secret.accessed",
                event_id_suffix="021",
                actor_kind="system",
                payload={
                    "secret_name": "github_token",
                    "session_id": "sess-01917e5c-a7d1-7000-8abc-000000000222",
                    "task_id": None,
                    "purpose": "github_push",
                },
            ),
        ],
    )
    await _wait_for_event_appended(app, "secret", 2.0)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.content
        assert (
            _parse_sample(body, "omb_secret_accessed_total", labels={"actor_kind": "operator"})
            == 1.0
        )
        assert (
            _parse_sample(body, "omb_secret_accessed_total", labels={"actor_kind": "system"}) == 1.0
        )


# ===========================================================================
# AC4 — events_appended counter integration.
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_events_appended_counter_per_family(
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC4 integration — write envelopes of 3 distinct families."""
    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)
    _write_envelopes(
        log_path,
        [
            _make_envelope("task.created", event_id_suffix="030", payload={"task_id": "t-b"}),
            _make_envelope(
                "session.started",
                event_id_suffix="031",
                payload={
                    "session_id": "sess-01917e5c-a7d1-7000-8abc-000000000333",
                    "operator_id": "op-1",
                },
            ),
            _make_envelope(
                "secret.accessed",
                event_id_suffix="032",
                actor_kind="operator",
                payload={
                    "secret_name": "anthropic_key",
                    "session_id": "sess-01917e5c-a7d1-7000-8abc-000000000333",
                    "task_id": None,
                    "purpose": "claude_call",
                },
            ),
        ],
    )
    await _wait_for_event_appended(app, "task", 1.0)
    await _wait_for_event_appended(app, "session", 1.0)
    await _wait_for_event_appended(app, "secret", 1.0)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.content
        for family in ("task", "session", "secret"):
            value = _parse_sample(
                body, "omb_events_appended_total", labels={"event_family": family}
            )
            assert value == 1.0, f"family={family} expected 1.0, got {value}"


# ===========================================================================
# AC5 — per-task token-spend gauge set + cleanup.
# ===========================================================================


@pytest.mark.asyncio
async def test_integration_task_tokens_spent_gauge_set_and_cleared(
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC5 integration — task.budget_exceeded sets gauge; task.completed clears it."""
    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)
    task_id = "task-integration-tokens"
    # First wave: set the gauge.
    _write_envelopes(
        log_path,
        [
            _make_envelope(
                "task.budget_exceeded",
                event_id_suffix="040",
                payload={
                    "task_id": task_id,
                    "token_limit": 1000,
                    "tokens_used": 750,
                    "step": 3,
                },
            ),
        ],
    )
    await _wait_for_event_appended(app, "task", 1.0)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        body = r.content
        value = _parse_sample(body, "omb_task_tokens_spent", labels={"task_id": task_id})
        assert value == 750.0, f"expected 750 after budget_exceeded, got {value}"

        # Second wave: clean it up.
        _write_envelopes(
            log_path,
            [
                _make_envelope(
                    "task.completed",
                    event_id_suffix="041",
                    payload={
                        "task_id": task_id,
                        "summary": "test cleanup",
                        "files_changed": 0,
                        "lines_added": 0,
                        "lines_removed": 0,
                    },
                ),
            ],
        )
        await _wait_for_event_appended(app, "task", 2.0)
        r2 = await client.get("/metrics")
        body2 = r2.content
        value2 = _parse_sample(body2, "omb_task_tokens_spent", labels={"task_id": task_id})
        assert value2 is None, f"expected gauge removed after task.completed, got {value2}"


# ===========================================================================
# AC7 — dispatch hooked into tail loop, crash-resilient.
# ===========================================================================


@pytest.mark.asyncio
async def test_run_subscriber_dispatches_envelopes_to_metrics_state(
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC7 — synthetic envelopes update the MetricsState via the dispatcher."""
    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)
    _write_envelopes(
        log_path,
        [
            _make_envelope("task.created", event_id_suffix="050", payload={"task_id": "t-disp"}),
        ],
    )
    await _wait_for_event_appended(app, "task", 1.0)
    # Story 10.4 P1-M2 — verify via the public /metrics HTTP exposition
    # + canonical parser rather than the private ``Counter._value.get()``.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/metrics")
        assert r.status_code == 200
        value = _parse_sample(
            r.content,
            "omb_task_lifecycle_events_total",
            labels={"event_type": "task.created"},
        )
        assert value == 1.0


@pytest.mark.asyncio
async def test_dispatch_updater_exception_does_not_crash_tail_loop(
    monkeypatch: pytest.MonkeyPatch,
    test_app_with_event_dir: tuple[FastAPI, Path],
) -> None:
    """AC7 (Story 10.3 P1-H5 lesson) — a raising updater does NOT kill the tail.

    Patch :func:`update_for` to raise on the first envelope, then verify
    the tail loop continues processing the second envelope (cursor
    advances + no crash).
    """
    from metrics_subscriber import __main__ as mainmod  # noqa: PLC0415

    app, event_dir = test_app_with_event_dir
    log_path = _today_log_path(event_dir)

    call_count = {"n": 0}

    def _raising_update_for(state: object, envelope: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic dispatcher failure")
        # Subsequent envelopes succeed — no-op.

    monkeypatch.setattr(mainmod, "update_for", _raising_update_for)
    _write_envelopes(
        log_path,
        [
            _make_envelope("task.created", event_id_suffix="060", payload={"task_id": "crash"}),
            _make_envelope(
                "task.planning.started", event_id_suffix="061", payload={"task_id": "crash"}
            ),
        ],
    )
    # The tail loop should drain BOTH envelopes despite the first
    # dispatcher raising.  Wait until both calls completed.
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        if call_count["n"] >= 2:
            break
        await asyncio.sleep(0.01)
    assert call_count["n"] >= 2, (
        f"expected ≥ 2 dispatch attempts (1 raising + 1 surviving), got {call_count['n']}"
    )
    # And the tail task is still running — not crashed.
    assert not app.state.tail_task.done(), (
        "tail task should still be running after dispatcher raised"
    )
