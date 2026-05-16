"""Tests for RequestIdMiddleware, IdempotencyKeyMiddleware, TraceIdMiddleware (Story 3.6 / 9.2).

Coverage:
- AC-1 / Story 3.6: RequestIdMiddleware structlog bind/unbind in try/finally
  (3 tests).
- AC-2 / Story 3.6: IdempotencyKeyMiddleware origin flag + response header +
  legacy-header regression pin (6 tests).
- AC-7/AC-8 / Story 6.3: TierEnforcementMiddleware tier gate tests.
- AC1-AC6 / Story 9.2: TraceIdMiddleware validate-or-mint, structlog
  bind/unbind, malformed-header truncation, route-level propagation
  (12 tests in TestTraceIdMiddleware).
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
import structlog
import structlog.contextvars
import structlog.testing
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.ids import new_idempotency_key, new_request_id, new_uuid7
from fastapi import Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import create_engine  # noqa: IMP001 — Story 2.9 AC-16
from registry_state.schema import Base  # noqa: IMP001 — Story 2.9 AC-16

from registry_api.app import build_app

# ---------------------------------------------------------------------------
# Fixtures (inlined — no conftest per project convention)
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create ORM tables without seeding any rows."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Minimal ASGI client wired to build_app with a fresh DB."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
    app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

    # Probe endpoint that echoes request.state fields for middleware assertions.
    #
    # Story 9.2 pass-1 review C3: TEST-ONLY probe — NEVER register this in
    # ``build_app``. The endpoint surfaces ``request.state`` fields directly
    # which would be an information-disclosure hazard in production. The
    # ``/debug/`` path prefix is conventional for test-only routes in this
    # codebase but the load-bearing constraint is "never wired into
    # build_app", not the URL shape.
    @app.get("/debug/state")
    async def _state_probe(request: Request) -> JSONResponse:
        """TEST-ONLY probe — never register in ``build_app()``.

        Surfaces ``request.state`` fields + structlog contextvars so the
        middleware-assertion tests can observe binding semantics directly
        from a handler. Story 9.2 added ``trace_id`` + ``structlog_trace_id``
        for the FR58 HTTP-ingress assertions; Story 6.3 + 3.6 introduced the
        rest. Do NOT wire this into ``build_app`` — it leaks internal state.
        """
        # Story 9.2: include trace_id + the live structlog contextvars
        # snapshot so the TraceIdMiddleware tests can assert binding/
        # unbinding semantics directly from a probe handler.
        ctx_trace_id = structlog.contextvars.get_merged_contextvars(structlog.get_logger()).get(
            "trace_id"
        )
        return JSONResponse(
            {
                "request_id": getattr(request.state, "request_id", None),
                "idempotency_key": getattr(request.state, "idempotency_key", None),
                "idempotency_key_generated": getattr(
                    request.state, "idempotency_key_generated", None
                ),
                "actor_id": getattr(request.state, "actor_id", None),
                "caller_context": repr(getattr(request.state, "caller_context", None)),
                "trace_id": getattr(request.state, "trace_id", None),
                "structlog_trace_id": ctx_trace_id,
            }
        )

    # POST probe for verifying caller_context on mutating routes.
    @app.post("/debug/mutation-state")
    async def _mutation_probe(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "actor_id": getattr(request.state, "actor_id", None),
                "caller_context": repr(getattr(request.state, "caller_context", None)),
            }
        )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# RequestIdMiddleware — structlog context tests (AC-1)
# ---------------------------------------------------------------------------


class TestRequestIdMiddlewareStructlog:
    """AC-1: bind_contextvars / unbind_contextvars in try/finally."""

    @pytest.mark.asyncio
    async def test_request_id_middleware_binds_to_structlog_context_and_unbinds_on_success(
        self, app_client: AsyncClient
    ) -> None:
        """Request-id is bound into structlog during dispatch and unbound after.

        Uses structlog.testing.capture_logs() to assert the bound key is
        visible inside the handler and then verifies it is gone after the
        response completes.

        Story 3.6 L6: the dead ``captured_during`` variable removed in this
        pass; mid-request ``request_id`` visibility is exercised by the H2
        sibling test ``test_request_id_propagates_into_json_log_record`` in
        ``test_errors_envelope.py`` which captures rendered JSON.
        """
        rid = new_request_id(clock=_FROZEN_CLOCK)

        # Verify no leftover from a previous request.
        before = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "request_id" not in before

        with structlog.testing.capture_logs() as cap:
            structlog.get_logger().info("before-request")
            # The actual bind happens inside the middleware; capture_logs()
            # replaces the configured processors so we assert via
            # get_merged_contextvars directly from a custom endpoint.
            r = await app_client.get("/debug/state", headers={"X-Request-ID": rid})
        assert r.status_code == 200
        assert r.json()["request_id"] == rid

        # After the response, the contextvars must be unbound.
        after = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "request_id" not in after

        # capture_logs captures the "before-request" record; request_id must
        # not be present there (the bind had not happened yet).
        before_rec = next((r for r in cap if r.get("event") == "before-request"), None)
        if before_rec is not None:
            assert "request_id" not in before_rec

    @pytest.mark.asyncio
    async def test_request_id_middleware_unbinds_on_handler_exception(self, tmp_path: Path) -> None:
        """try/finally unbind fires even when the handler raises (AC-1).

        Uses raise_app_exceptions=False so the test observes the 500 rather
        than re-raising the RuntimeError.
        """
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

        @app.get("/debug/boom")
        async def _boom(request: Request) -> JSONResponse:
            raise RuntimeError("synthetic boom for unbind test")

        rid = new_request_id(clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):
            r = await client.get("/debug/boom", headers={"X-Request-ID": rid})

        # Handler raised; we get a 500 from the catch-all handler.
        assert r.status_code == 500

        # Critically: the unbind must have run, so no request_id leaks.
        after = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "request_id" not in after

    @pytest.mark.asyncio
    async def test_request_id_middleware_generated_on_missing_header_with_structlog_assertion(
        self, app_client: AsyncClient
    ) -> None:
        """Server generates a UUIDv7 request-id when header is absent.

        Structlog variant: the generated id is unbound after the response,
        same as the client-supplied-id path.
        """
        # No X-Request-ID header.
        r = await app_client.get("/debug/state")
        assert r.status_code == 200
        state = r.json()
        generated_rid = state["request_id"]
        assert generated_rid is not None
        assert len(generated_rid) == 36  # bare UUIDv7

        # Also present in response header.
        assert r.headers.get("X-Request-ID") == generated_rid

        # Unbound after response.
        after = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "request_id" not in after

    @pytest.mark.asyncio
    async def test_request_id_rejects_trailing_newline_payload(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Story 9.2 pass-1 review B1: ``\\A...\\Z`` anchors reject trailing-newline payloads.

        Regression pin for the Story 9.1 / 9.2 mirror-update discipline: the
        envelope-side validator switched from ``^...$`` to ``\\A...\\Z`` so a
        hostile ``X-Request-ID`` of ``<valid-uuid>\\n<garbage>`` no longer
        slips past validation. This test locks the HTTP-side behaviour:
        the middleware regenerates a fresh UUIDv7 (not the partial match) and
        emits a WARNING log.
        """
        valid = new_request_id(clock=_FROZEN_CLOCK)
        hostile = f"{valid}\nextra-garbage"
        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Request-ID": hostile})
        assert r.status_code == 200
        echoed = r.headers.get("X-Request-ID")
        assert echoed is not None
        # Middleware regenerated — echoed value is NOT the hostile input nor
        # the prefix-valid portion.
        assert echoed != hostile
        assert echoed != valid
        assert any("invalid X-Request-ID header" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# IdempotencyKeyMiddleware — origin flag + header echo (AC-2)
# ---------------------------------------------------------------------------


class TestIdempotencyKeyMiddleware:
    """AC-2: origin flag, X-Idempotency-Generated header, legacy header removal."""

    @pytest.mark.asyncio
    async def test_idempotency_middleware_marks_generated_origin_on_state(
        self, app_client: AsyncClient
    ) -> None:
        """When no Idempotency-Key header is sent, state.idempotency_key_generated is True."""
        r = await app_client.get("/debug/state")
        assert r.status_code == 200
        assert r.json()["idempotency_key_generated"] is True

    @pytest.mark.asyncio
    async def test_idempotency_middleware_marks_client_origin_on_state(
        self, app_client: AsyncClient
    ) -> None:
        """When a valid Idempotency-Key header is sent, state.idempotency_key_generated is False."""
        key = new_idempotency_key(clock=_FROZEN_CLOCK)
        r = await app_client.get("/debug/state", headers={"Idempotency-Key": key})
        assert r.status_code == 200
        body = r.json()
        assert body["idempotency_key_generated"] is False
        assert body["idempotency_key"] == key

    @pytest.mark.asyncio
    async def test_idempotency_middleware_response_header_x_idempotency_generated_true(
        self, app_client: AsyncClient
    ) -> None:
        """Missing Idempotency-Key on POST → X-Idempotency-Generated: true on response.

        Story 3.6 M5: the header is only emitted on mutation-method responses
        (POST / PUT / PATCH / DELETE) — read paths omit it because the
        ``X-Idempotency-Generated`` signal is meaningless without a mutation
        contract.
        """
        # Use the POST /v1/tasks happy-path (mutation method).
        r = await app_client.post("/v1/tasks", json={"title": "x-idem-gen-true"})
        assert r.status_code == 201
        assert r.headers.get("x-idempotency-generated") == "true"

    @pytest.mark.asyncio
    async def test_idempotency_middleware_response_header_x_idempotency_generated_false_on_client_key(  # noqa: E501
        self, app_client: AsyncClient
    ) -> None:
        """Client-supplied key on POST → X-Idempotency-Generated: false on response.

        Story 3.6 M5: gated to mutation methods (see sibling test above).
        """
        key = new_idempotency_key(clock=_FROZEN_CLOCK)
        r = await app_client.post(
            "/v1/tasks",
            json={"title": "x-idem-gen-false"},
            headers={"Idempotency-Key": key},
        )
        assert r.status_code == 201
        assert r.headers.get("x-idempotency-generated") == "false"

    @pytest.mark.asyncio
    async def test_idempotency_middleware_omits_x_idempotency_generated_on_get(
        self, app_client: AsyncClient
    ) -> None:
        """Story 3.6 M5: GET responses do NOT carry X-Idempotency-Generated.

        Read paths have no meaningful idempotency contract — the header was
        previously emitted on every response (including GETs), which
        contradicted the AC-3 mutation-method gate the team applied for the
        JSON ``extensions`` nudge. M5 fixes the inconsistency by gating the
        response header to mutation methods.
        """
        r = await app_client.get("/debug/state")
        assert r.status_code == 200
        assert "x-idempotency-generated" not in r.headers, (
            f"unexpected X-Idempotency-Generated on GET; headers: {dict(r.headers)}"
        )

    @pytest.mark.asyncio
    async def test_idempotency_middleware_no_legacy_x_idempotency_status_header(
        self, app_client: AsyncClient
    ) -> None:
        """Regression pin: the deprecated X-Idempotency-Status: not-enforced header is GONE.

        Story 2.13 migrated dedup ownership to routes/tasks.py. The middleware
        must NOT set X-Idempotency-Status on non-task routes (the route
        handler sets it on POST /v1/tasks; GET endpoints never set it).
        """
        r = await app_client.get("/debug/state")
        assert r.status_code == 200
        # The middleware must not inject the deprecated placeholder.
        assert "x-idempotency-status" not in r.headers


# ---------------------------------------------------------------------------
# TierEnforcementMiddleware — tier gate tests (Story 6.3)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def constrained_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """ASGI client with actor_kind="worker" so Tier-2+ routes are denied."""
    db_path = tmp_path / "state.sqlite3"
    db_url_str = _db_url(db_path)
    await _seed_tables(db_url_str)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
    app = build_app(
        base_dir=events_dir,
        db_url=db_url_str,
        clock=clock,
        actor_kind="worker",
    )

    @app.get("/debug/state")
    async def _state_probe(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "actor_id": getattr(request.state, "actor_id", None),
                "caller_context": repr(getattr(request.state, "caller_context", None)),
            }
        )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


class TestTierEnforcementMiddleware:
    """AC-7/AC-8: Tier enforcement middleware (Story 6.3)."""

    @pytest.mark.asyncio
    async def test_tier_allowed_on_matching_route(self, app_client: AsyncClient) -> None:
        """AC-8: operator (default) can POST /v1/tasks (Tier.ONE)."""
        r = await app_client.post("/v1/tasks", json={"title": "tier-ok"})
        assert r.status_code == 201

    @pytest.mark.asyncio
    async def test_read_routes_bypass_tier_check(self, app_client: AsyncClient) -> None:
        """GET routes skip tier enforcement entirely."""
        r = await app_client.get("/debug/state")
        assert r.status_code == 200
        body = r.json()
        # caller_context should be None on GET (middleware skips check).
        assert body["caller_context"] == "None"

    @pytest.mark.asyncio
    async def test_caller_context_populated_on_mutation(self, app_client: AsyncClient) -> None:
        """AC-8: request.state.caller_context is set on mutating routes."""
        from unittest.mock import patch

        from capabilities import Tier

        # Patch the tier map to include the debug probe so the middleware
        # actually processes it as a tiered route and sets caller_context.
        with patch(
            "registry_api.adapters.middleware.ROUTE_TIER_MAP",
            {"POST /v1/tasks": Tier.ONE, "POST /debug/mutation-state": Tier.ONE},
        ):
            r = await app_client.post("/debug/mutation-state")
        assert r.status_code == 200
        body = r.json()
        caller_ctx = body["caller_context"]
        assert caller_ctx != "None"
        assert "operator" in caller_ctx
        assert "http-api" in caller_ctx

    @pytest.mark.asyncio
    async def test_tier_denied_returns_403_problem_json(self, tmp_path: Path) -> None:
        """AC-7: worker-kind caller denied on a Tier-2 route returns 403."""
        from unittest.mock import patch

        from capabilities import Tier

        db_path = tmp_path / "state.sqlite3"
        db_url_str = _db_url(db_path)
        await _seed_tables(db_url_str)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

        # Temporarily elevate POST /v1/tasks to Tier.THREE so a worker gets denied
        # (worker max tier is Tier.TWO).  ROUTE_TIER_MAP is frozen, so patch
        # the module attribute with a temporary mutable dict.
        with patch(
            "registry_api.adapters.middleware.ROUTE_TIER_MAP",
            {"POST /v1/tasks": Tier.THREE},
        ):
            app = build_app(
                base_dir=events_dir,
                db_url=db_url_str,
                clock=clock,
                actor_kind="worker",
            )
            async with (
                LifespanManager(app) as manager,
                AsyncClient(
                    transport=ASGITransport(app=manager.app),
                    base_url="http://testserver",
                ) as client,
            ):
                r = await client.post("/v1/tasks", json={"title": "denied"})
                assert r.status_code == 403
                body = r.json()
                assert body["type"] == "/errors/forbidden"
                assert body["title"] == "Forbidden"
                assert body["status"] == 403
                assert "no_matching_approval" in body["detail"] or "allows Tier" in body["detail"]

    @pytest.mark.asyncio
    async def test_unmapped_mutation_route_passes_through(self, app_client: AsyncClient) -> None:
        """Unmapped mutating routes default-open (Phase 1)."""
        r = await app_client.delete("/v1/tasks/nonexistent")
        # 405 or 404 from the router — NOT 403 from tier enforcement.
        assert r.status_code in (404, 405)

    @pytest.mark.asyncio
    async def test_worker_can_access_tier_one_route(self, constrained_client: AsyncClient) -> None:
        """Worker actor_kind can still POST /v1/tasks (Tier.ONE)."""
        r = await constrained_client.post("/v1/tasks", json={"title": "worker-ok"})
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# TraceIdMiddleware — Story 9.2 (FR58 HTTP ingress) tests
# ---------------------------------------------------------------------------


_TRACE_UUID_RE = r"\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"


class TestTraceIdMiddleware:
    """Story 9.2 AC1-AC6 + AC10: TraceIdMiddleware validate-or-mint + propagation."""

    @pytest.fixture(autouse=True)
    def _clear_structlog_contextvars(self) -> Generator[None, None, None]:
        """Story 9.2 pass-1 review B2: ensure no prior test left ``trace_id`` bound.

        structlog ``contextvars`` are process-global. Without this autouse
        fixture, a parallel async test that raised before its ``try/finally``
        unbind could leak state into this test class — making the
        ``unbound after request`` assertions flaky. Belt-and-braces: clear
        before AND after each test in this class.
        """
        structlog.contextvars.clear_contextvars()
        yield
        structlog.contextvars.clear_contextvars()

    @pytest.mark.asyncio
    async def test_trace_id_minted_on_missing_header(self, app_client: AsyncClient) -> None:
        """AC1 #4 + AC6 #1: no X-Trace-Id → server mints a bare UUIDv7."""
        r = await app_client.get("/debug/state")
        assert r.status_code == 200
        trace_id = r.headers.get("X-Trace-Id")
        assert trace_id is not None
        assert re.match(_TRACE_UUID_RE, trace_id), (
            f"minted X-Trace-Id does not match UUIDv7 shape: {trace_id!r}"
        )

    @pytest.mark.asyncio
    async def test_trace_id_preserved_on_valid_uuidv7_header(self, app_client: AsyncClient) -> None:
        """AC1 #2 + AC6 #2: valid bare UUIDv7 header echoes unchanged."""
        sent = new_uuid7(clock=_FROZEN_CLOCK)
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": sent})
        assert r.status_code == 200
        assert r.headers.get("X-Trace-Id") == sent
        assert r.json()["trace_id"] == sent

    @pytest.mark.asyncio
    async def test_trace_id_preserved_on_valid_telegram_form_header(
        self, app_client: AsyncClient
    ) -> None:
        """AC2 + AC6 #3: ``tg:<update_id>`` form accepted (per Story 9.1 contract)."""
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": "tg:42"})
        assert r.status_code == 200
        assert r.headers.get("X-Trace-Id") == "tg:42"
        assert r.json()["trace_id"] == "tg:42"

    @pytest.mark.asyncio
    async def test_trace_id_regenerated_on_malformed_header(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC1 #3 + AC6 #4: malformed header → WARNING + fresh UUIDv7 (not the bad value)."""
        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Trace-Id": "bad-value"})
        assert r.status_code == 200
        echoed = r.headers.get("X-Trace-Id")
        assert echoed is not None
        assert echoed != "bad-value"
        assert re.match(_TRACE_UUID_RE, echoed)
        # Warning log fired with the (truncated) received payload.
        warnings = [
            rec for rec in caplog.records if "invalid X-Trace-Id header" in rec.getMessage()
        ]
        assert warnings, "expected a WARNING log for the malformed X-Trace-Id header"

    @pytest.mark.asyncio
    async def test_trace_id_regenerated_on_tg_zero_header(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC6 #5: ``tg:0`` is rejected (Story 9.1 leading-zero / zero-update_id rule)."""
        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Trace-Id": "tg:0"})
        assert r.status_code == 200
        echoed = r.headers.get("X-Trace-Id")
        assert echoed is not None
        assert echoed != "tg:0"
        assert re.match(_TRACE_UUID_RE, echoed)
        assert any("invalid X-Trace-Id header" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_trace_id_regenerated_on_int64_overflow_header(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC6 #6: ``tg:<n>`` above int64-max is rejected even though it matches the regex."""
        overflow = "tg:9999999999999999999"  # 19 digits > int64 max
        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Trace-Id": overflow})
        assert r.status_code == 200
        echoed = r.headers.get("X-Trace-Id")
        assert echoed is not None
        assert echoed != overflow
        assert re.match(_TRACE_UUID_RE, echoed)
        assert any("invalid X-Trace-Id header" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_trace_id_attached_to_request_state(self, app_client: AsyncClient) -> None:
        """AC1 #5 + AC6 #7: handler observes ``request.state.trace_id`` == response header."""
        sent = new_uuid7(clock=_FROZEN_CLOCK)
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": sent})
        assert r.status_code == 200
        body = r.json()
        assert body["trace_id"] == sent
        assert r.headers["X-Trace-Id"] == sent

    @pytest.mark.asyncio
    async def test_trace_id_bound_to_structlog_context_during_request(
        self, app_client: AsyncClient
    ) -> None:
        """AC1 #6 + AC6 #8: ``structlog.contextvars`` carries trace_id inside the handler."""
        sent = new_uuid7(clock=_FROZEN_CLOCK)
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": sent})
        assert r.status_code == 200
        # The probe captures get_merged_contextvars() FROM INSIDE the handler.
        assert r.json()["structlog_trace_id"] == sent

    @pytest.mark.asyncio
    async def test_trace_id_unbound_from_structlog_context_after_request(
        self, app_client: AsyncClient
    ) -> None:
        """AC1 #7 + AC6 #9: ``try/finally`` unbind protects worker reuse (success path)."""
        # Verify clean slate first.
        before = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "trace_id" not in before

        sent = new_uuid7(clock=_FROZEN_CLOCK)
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": sent})
        assert r.status_code == 200

        # After the response, the contextvars must be unbound. Worker reuse
        # would otherwise leak the prior trace_id into the next request until
        # the next TraceIdMiddleware rebinds.
        after = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "trace_id" not in after

    @pytest.mark.asyncio
    async def test_trace_id_unbound_even_when_handler_raises(self, tmp_path: Path) -> None:
        """AC1 #7 + AC6 #10: ``try/finally`` unbind fires even when the handler raises."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

        @app.get("/debug/boom-trace")
        async def _boom(request: Request) -> JSONResponse:
            raise RuntimeError("synthetic boom for trace_id unbind test")

        sent = new_uuid7(clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):
            r = await client.get("/debug/boom-trace", headers={"X-Trace-Id": sent})

        assert r.status_code == 500
        # The unbind must have run even though the handler raised.
        after = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "trace_id" not in after

    @pytest.mark.asyncio
    async def test_trace_id_truncated_in_log_for_malformed_header(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC6 #11: malformed-header WARNING log truncates ``received`` to ≤80 chars."""
        overlong = "Z" * 500  # 500 chars of garbage; matches no shape
        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Trace-Id": overlong})
        assert r.status_code == 200

        # Find the warning record and assert the ``received`` extra is ≤80 chars.
        warnings = [
            rec for rec in caplog.records if "invalid X-Trace-Id header" in rec.getMessage()
        ]
        assert warnings, "expected a WARNING log for the malformed X-Trace-Id header"
        rec = warnings[0]
        received = getattr(rec, "received", None)
        assert received is not None, "warning record missing 'received' field"
        assert len(received) <= 80, f"received not truncated to ≤80 chars: len={len(received)}"

    @pytest.mark.asyncio
    async def test_response_carries_both_x_request_id_and_x_trace_id(
        self, app_client: AsyncClient
    ) -> None:
        """AC7: response carries both X-Request-ID and X-Trace-Id."""
        sent_trace = new_uuid7(clock=_FROZEN_CLOCK)
        sent_request = new_request_id(clock=_FROZEN_CLOCK)
        r = await app_client.get(
            "/debug/state",
            headers={"X-Trace-Id": sent_trace, "X-Request-ID": sent_request},
        )
        assert r.status_code == 200
        assert r.headers.get("X-Trace-Id") == sent_trace
        assert r.headers.get("X-Request-ID") == sent_request
        body = r.json()
        assert body["trace_id"] == sent_trace
        assert body["request_id"] == sent_request

    # ------------------------------------------------------------------
    # Story 9.2 pass-1 review additions (A3, B4, B8, C1, C4)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_trace_id_not_echoed_on_raw_500_exception_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass-1 A3 (corrected): documents that raw 500 crashes do NOT echo X-Trace-Id.

        Starlette's ``BaseHTTPMiddleware.call_next`` re-raises any unhandled
        exception from the inner ASGI app up through the outer middleware's
        dispatch function. This means the echo line AFTER ``await call_next``
        never executes when a ``RuntimeError`` escapes the route handler — the
        exception propagates straight past it.

        The ``X-Trace-Id`` echo on 422/404/403 responses DOES work because
        those code paths return a ``JSONResponse`` (not a raw exception) through
        the normal response flow. See ``test_trace_id_echoed_on_422_validation_error_response``
        for the working case.

        This test pins the ACTUAL behaviour (no header on raw 500) so future
        contributors have a clear reference. Story 9.7 / a Starlette upgrade
        may change this — the test will catch the regression.
        """
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

        sent = new_uuid7(clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):

            async def _boom(*_args: object, **_kwargs: object) -> None:
                raise RuntimeError("synthetic failure for 500 path documentation test")

            monkeypatch.setattr(app.state.writer, "append", _boom)

            r = await client.post(
                "/v1/tasks",
                json={"title": "boom-for-500-doc"},
                headers={"X-Trace-Id": sent},
            )

        assert r.status_code == 500
        # Document the actual behaviour: BaseHTTPMiddleware re-raises through
        # call_next so the echo line never runs on the raw exception path.
        # The ``trace_id`` IS still available via ``extensions.trace_id`` in the
        # problem+json body (A4 wired it into ``handle_internal_error`` via
        # ``_build_problem_extensions``), so correlation is not completely lost.
        assert r.headers.get("X-Trace-Id") is None, (
            "Starlette behaviour changed: X-Trace-Id is now echoed on raw 500 path "
            "— update this test and the A3 docstring."
        )

    @pytest.mark.asyncio
    async def test_trace_id_echoed_on_422_validation_error_response(
        self, app_client: AsyncClient
    ) -> None:
        """Pass-1 A3: ``X-Trace-Id`` is echoed on 422 validation errors too.

        FastAPI's ``RequestValidationError`` flows through the registered
        exception handler (``handle_validation_error``) which returns a
        ``JSONResponse`` — the outer middleware's echo line must still run.
        """
        sent = new_uuid7(clock=_FROZEN_CLOCK)
        # Missing required ``title`` → RequestValidationError → 422.
        r = await app_client.post("/v1/tasks", json={}, headers={"X-Trace-Id": sent})
        assert r.status_code == 422
        assert r.headers.get("X-Trace-Id") == sent

    @pytest.mark.asyncio
    async def test_trace_id_uppercase_uuid_rejected_with_lowercase_hint(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Pass-1 B4: uppercase-hex UUIDv7 is rejected; WARNING surfaces the lowercase hint.

        The Story 9.1 shape contract requires lowercase hex; an uppercase
        UUIDv7 is the most common operator mistake when copy-pasting from
        Python's ``uuid.UUID(...).__str__()`` after a ``.upper()`` call.
        The improved WARNING message documents the constraint so the operator
        does not need to read the regex.
        """
        # Construct an uppercase variant of a valid UUIDv7.
        lowercase = new_uuid7(clock=_FROZEN_CLOCK)
        uppercase = lowercase.upper()
        assert uppercase != lowercase  # sanity

        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Trace-Id": uppercase})
        assert r.status_code == 200
        echoed = r.headers.get("X-Trace-Id")
        assert echoed is not None
        # Middleware regenerated a fresh lowercase UUIDv7.
        assert echoed != uppercase
        assert re.match(_TRACE_UUID_RE, echoed)
        # WARNING fired AND carries the lowercase hint.
        warnings = [
            rec for rec in caplog.records if "invalid X-Trace-Id header" in rec.getMessage()
        ]
        assert warnings, "expected a WARNING log for the uppercase X-Trace-Id"
        assert any(
            "lowercase UUIDv7" in rec.getMessage() and "tg:<update_id>" in rec.getMessage()
            for rec in warnings
        ), "WARNING message should hint at the lowercase + tg: shape constraints"

    @pytest.mark.asyncio
    async def test_trace_id_and_request_id_both_bound_during_handler(
        self, app_client: AsyncClient
    ) -> None:
        """Pass-1 B8: both ``trace_id`` AND ``request_id`` visible inside the handler.

        Locks the OUTERMOST-first execution order: ``TraceIdMiddleware`` binds
        ``trace_id`` BEFORE ``RequestIdMiddleware`` binds ``request_id``, and
        both are still visible to the inner handler. A future refactor that
        reordered middleware registration would break this test before any
        production logger lost correlation.
        """
        sent_trace = new_uuid7(clock=_FROZEN_CLOCK)
        sent_request = new_request_id(clock=_FROZEN_CLOCK)
        r = await app_client.get(
            "/debug/state",
            headers={"X-Trace-Id": sent_trace, "X-Request-ID": sent_request},
        )
        assert r.status_code == 200
        body = r.json()
        # Handler observed BOTH bindings during dispatch.
        assert body["structlog_trace_id"] == sent_trace
        # ``request_id`` is bound by RequestIdMiddleware — verified indirectly
        # via the response header echo (RequestIdMiddleware structlog binding
        # is already covered by TestRequestIdMiddlewareStructlog above; this
        # test pins that the binding ordering preserves both keys).
        assert r.headers.get("X-Request-ID") == sent_request
        assert r.headers.get("X-Trace-Id") == sent_trace
        # And the state fields agree with the headers.
        assert body["trace_id"] == sent_trace
        assert body["request_id"] == sent_request

    @pytest.mark.asyncio
    async def test_trace_id_tg_int64_max_accepted_at_middleware(
        self, app_client: AsyncClient
    ) -> None:
        """Pass-1 C1: ``tg:<int64-max>`` is accepted at the middleware boundary.

        Boundary test mirroring the envelope-side coverage. The 19-digit
        regex cap admits up to ~9.99e18; the post-match int check enforces
        the int64 ceiling at exactly ``9_223_372_036_854_775_807``.
        """
        int64_max = "tg:9223372036854775807"
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": int64_max})
        assert r.status_code == 200
        assert r.headers.get("X-Trace-Id") == int64_max
        assert r.json()["trace_id"] == int64_max

    @pytest.mark.asyncio
    async def test_trace_id_tg_int64_plus_one_regenerated_at_middleware(
        self, app_client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Pass-1 C1: ``tg:<int64-max + 1>`` is rejected at the middleware boundary.

        Sibling boundary test. The value matches the 19-digit regex but
        exceeds int64 max — the post-match numeric check must reject it and
        trigger remint + WARNING.
        """
        overflow = "tg:9223372036854775808"  # int64 max + 1
        with caplog.at_level(logging.WARNING, logger="registry_api.adapters.middleware"):
            r = await app_client.get("/debug/state", headers={"X-Trace-Id": overflow})
        assert r.status_code == 200
        echoed = r.headers.get("X-Trace-Id")
        assert echoed is not None
        assert echoed != overflow
        assert re.match(_TRACE_UUID_RE, echoed)
        assert any("invalid X-Trace-Id header" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_trace_id_response_appends_vary_x_trace_id(self, app_client: AsyncClient) -> None:
        """Pass-1 C4: response carries ``Vary: X-Trace-Id`` so caches do not serve stale traces.

        Without ``Vary``, an upstream cache could serve a response with the
        wrong echoed trace_id to a request that carried a different
        ``X-Trace-Id`` header — silently corrupting correlation. The
        middleware appends the token (or no-ops if already present).
        """
        sent = new_uuid7(clock=_FROZEN_CLOCK)
        r = await app_client.get("/debug/state", headers={"X-Trace-Id": sent})
        assert r.status_code == 200
        vary_header = r.headers.get("Vary", "")
        vary_tokens = [p.strip() for p in vary_header.split(",") if p.strip()]
        assert "X-Trace-Id" in vary_tokens, (
            f"expected 'X-Trace-Id' token in Vary header, got: {vary_header!r}"
        )
