"""Route-level tests for ``GET /v1/health`` (Story 11.3.9 / AC4 + AC5).

Two test classes:

* ``TestHealthRouteFailureModes`` (AC5) — the route NEVER returns 5xx for
  any backend degradation; every failure path returns 200 with the
  appropriate ``"degraded"`` / ``"unknown"`` / ``0`` fallback.
* ``TestHealthRouteBudgetDiscipline`` (AC4) — the route respects the
  per-probe ``asyncio.wait_for`` budgets so a single hung probe does
  not blow the total response wall-clock.

Uses FastAPI's :class:`TestClient` with a minimal app that mounts only
the health router. ``app.state.session_maker`` is injected via the
factory fixture so each test can choose its failure pattern (no
session_maker → engine-disposed → per-probe timeout → etc.).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

from registry_api.routes.health import HealthResponse
from registry_api.routes.health import router as health_router

_AppFactory = Callable[[object | None], FastAPI]


@pytest.fixture
def make_app() -> _AppFactory:
    """Build a FastAPI app with the health router + injectable session_maker.

    Test passes ``None`` to skip session_maker assignment (route falls
    into the "standalone test mount" branch — degraded everything),
    or a callable returning an async context-manager yielding a session.
    """

    def _factory(session_maker: object | None) -> FastAPI:
        app = FastAPI()
        app.include_router(health_router, prefix="/v1")
        if session_maker is not None:
            app.state.session_maker = session_maker
        return app

    return _factory


# ─── AC5 — failure-mode discipline (NEVER 5xx) ───────────────────────────────


class TestHealthRouteFailureModes:
    """AC5: route NEVER returns 5xx for any backend degradation."""

    def test_returns_200_when_no_session_maker_wired(self, make_app: _AppFactory) -> None:
        """Standalone test mount (no app.state.session_maker) → 200 degraded.

        In production this branch never fires (``build_app`` always wires
        session_maker per ``app.py:197``). The test pins the safety
        fallback: even a misconfigured app must return a 200 instead of
        an AttributeError-induced 500.
        """
        app = make_app(None)
        client = TestClient(app)
        response = client.get("/v1/health")

        assert response.status_code == 200, (
            f"AC5 violation: standalone mount returned {response.status_code} "
            f"(must be 200); body={response.text!r}"
        )
        body = HealthResponse.model_validate(response.json())
        assert body.registry_status == "degraded"
        assert body.worker_status == "unknown"
        assert body.clawhip_queue_depth == 0
        assert body.version  # non-empty

    def test_returns_200_when_session_maker_raises(self, make_app: _AppFactory) -> None:
        """``session_maker()`` raises (pool exhausted, engine disposed) → 200 degraded.

        Mirrors a post-shutdown / mid-disposal failure: the route handler
        is called but ``async with session_maker()`` raises immediately.
        Per AC5 the route catches it and returns 200 with the degraded
        fallback for ALL 3 fields.
        """

        class _ExplodingSessionMaker:
            def __call__(self) -> object:
                raise RuntimeError("simulated session_maker failure")

        app = make_app(_ExplodingSessionMaker())
        client = TestClient(app)
        response = client.get("/v1/health")

        assert response.status_code == 200, (
            f"AC5 violation: session_maker() crash returned {response.status_code}"
        )
        body = HealthResponse.model_validate(response.json())
        assert body.registry_status == "degraded"
        assert body.worker_status == "unknown"
        assert body.clawhip_queue_depth == 0

    def test_returns_200_when_session_execute_raises(self, make_app: _AppFactory) -> None:
        """Session opens but every ``execute`` raises → 200 with mixed signals.

        ``probe_registry_reachable`` returns False → registry_status=degraded.
        ``probe_worker_recently_active`` returns False → worker_status=unknown
        (because registry is degraded; "we can't tell" not "idle").
        ``probe_queue_depth`` returns 0 → clawhip_queue_depth=0 (paired
        with degraded so 0 is not misread as "queue empty").
        """
        from sqlalchemy.exc import OperationalError

        class _ExplodingSession:
            async def __aenter__(self) -> _ExplodingSession:
                return self

            async def __aexit__(self, *_a: object) -> None:
                return None

            async def execute(self, *_a: object, **_kw: object) -> object:
                raise OperationalError("any SQL", {}, Exception("simulated"))

        class _SessionMaker:
            def __call__(self) -> _ExplodingSession:
                return _ExplodingSession()

        app = make_app(_SessionMaker())
        client = TestClient(app)
        response = client.get("/v1/health")

        assert response.status_code == 200, (
            f"AC5 violation: SELECT crash returned {response.status_code}"
        )
        body = HealthResponse.model_validate(response.json())
        assert body.registry_status == "degraded"
        assert body.worker_status == "unknown"
        assert body.clawhip_queue_depth == 0


# ─── AC4 — budget discipline ─────────────────────────────────────────────────


class TestHealthRouteBudgetDiscipline:
    """AC4: per-probe budgets honored; total wall time < 500ms.

    Critical invariant: a SINGLE hung probe must NOT blow the total
    response wall-clock. The 3 probes run in parallel via
    ``asyncio.gather`` and each one is wrapped in ``asyncio.wait_for``.
    A 10s-hung probe should still complete the response in ~200ms
    (the per-probe budget for the registry probe).
    """

    def test_total_wall_time_under_500ms_when_all_probes_hang(self, make_app: _AppFactory) -> None:
        """All 3 probes hang for 10s → response returns in ~200ms (max budget).

        Verifies that ``asyncio.wait_for`` actually cancels each hung
        probe at its per-probe budget. If the wait_for wrapper were
        missing, this test would wait 10s and the assert would catch it
        (with generous slack: 500ms — well below 10s).

        Hung-probe simulation: session.execute() awaits a 10s sleep.
        All 3 probe coroutines hit that and would block forever; the
        wait_for(timeout=0.200) for registry probe (and 0.150 for the
        others) must time them out and let the route return.
        """

        class _HangingSession:
            async def __aenter__(self) -> _HangingSession:
                return self

            async def __aexit__(self, *_a: object) -> None:
                return None

            async def execute(self, *_a: object, **_kw: object) -> object:
                await asyncio.sleep(10.0)
                raise AssertionError("wait_for didn't cancel the hung probe")

        class _SessionMaker:
            def __call__(self) -> _HangingSession:
                return _HangingSession()

        app = make_app(_SessionMaker())
        client = TestClient(app)

        start = time.monotonic()
        response = client.get("/v1/health")
        elapsed = time.monotonic() - start

        assert response.status_code == 200, (
            f"AC4 violation: route did not respect wait_for budget; status={response.status_code}"
        )
        assert elapsed < 0.500, (
            f"AC4 violation: total wall time {elapsed:.3f}s exceeded 500ms p95 budget "
            f"(probes should each cancel at 200/150/150ms via asyncio.wait_for; gather "
            f"makes total = max ≈ 200ms; this test's 500ms slack covers TestClient overhead)"
        )

        body = HealthResponse.model_validate(response.json())
        # All probes timed out → degraded everything.
        assert body.registry_status == "degraded"
        assert body.worker_status == "unknown"
        assert body.clawhip_queue_depth == 0


# ─── Happy-path mapping (the non-degraded branch) ────────────────────────────


class TestHealthRouteHappyPath:
    """The route returns REAL computed signals when probes succeed.

    Regression guard for the copy-paste class of bug where the final
    ``return HealthResponse(...)`` echoes the hardcoded degraded fallback
    instead of the computed ``registry_status`` / ``worker_status`` /
    ``clawhip_queue_depth``. The AC5 failure-mode tests all assert the
    degraded shape, so without this test a hardcoded-degraded return
    would pass the whole suite. Uses a REAL in-memory SQLite engine
    seeded with the canonical schema so all 3 probes run real SQL.
    """

    def _seeded_session_maker(
        self,
    ) -> tuple[AsyncEngine, object]:
        """Build (engine, session_maker) against a seeded in-memory DB.

        Returns the engine too so the caller can dispose it. The DB has:
        a worker event 10s ago (→ worker_status='ok') + 2 pending tasks
        created 30s ago (→ clawhip_queue_depth=2).
        """
        import asyncio
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from registry_state.schema import (  # noqa: IMP001 — services→services per AC-16
            Base,
            Event,
            Task,
        )
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        now = datetime.now(UTC)

        async def _seed() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as s:
                s.add(
                    Event(
                        id=f"01{uuid4().hex[:36]}",
                        type="task.execution.started",
                        schema_version="1.0.0",
                        emitted_at=now - timedelta(seconds=10),
                        emitted_at_monotonic_ns=0,
                        actor_kind="operator",
                        actor_id="happy-path-test",
                        task_id=None,
                        session_id=None,
                        payload_json="{}",
                        request_id=str(uuid4()),
                    )
                )
                for _ in range(2):
                    s.add(
                        Task(
                            id=f"01{uuid4().hex[:36]}",
                            status="pending",
                            created_at=now - timedelta(seconds=30),
                            updated_at=now - timedelta(seconds=30),
                            actor_kind="operator",
                            actor_id="happy-path-test",
                            title=None,
                        )
                    )
                await s.commit()

        # asyncio.run (not get_event_loop().run_until_complete) — a fresh loop
        # per call is immune to a prior async test having closed the shared
        # loop ("RuntimeError: Event loop is closed" under full-suite ordering).
        asyncio.run(_seed())
        return engine, async_sessionmaker(engine, expire_on_commit=False)

    def test_returns_real_signals_when_all_probes_succeed(self, make_app: _AppFactory) -> None:
        """All 3 probes succeed → ok / ok / 2 — NOT the degraded fallback.

        This is the test the code-review flagged as missing: every other
        route test asserts a degraded shape, so a regression that hardcodes
        the degraded return would otherwise slip through green.
        """
        import asyncio

        engine, session_maker = self._seeded_session_maker()
        try:
            app = make_app(session_maker)
            client = TestClient(app)
            response = client.get("/v1/health")

            assert response.status_code == 200
            body = HealthResponse.model_validate(response.json())
            assert body.registry_status == "ok", (
                f"registry SELECT 1 should succeed → 'ok'; got {body.registry_status!r}"
            )
            assert body.worker_status == "ok", (
                f"worker event 10s ago is in the 60s window → 'ok'; got {body.worker_status!r}"
            )
            assert body.clawhip_queue_depth == 2, (
                f"2 pending tasks in window → depth 2; got {body.clawhip_queue_depth}"
            )
            assert body.version  # non-empty
        finally:
            asyncio.run(engine.dispose())
