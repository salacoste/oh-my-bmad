"""Tests for RequestIdMiddleware and IdempotencyKeyMiddleware (Story 3.6 AC-1/2/10).

8 tests covering:
- AC-1: structlog bind/unbind in try/finally (2 tests)
- AC-1 variant: generated request-id + structlog assertion (1 test)
- AC-2: origin flag on request.state (2 tests)
- AC-2: X-Idempotency-Generated response header (2 tests)
- AC-2: legacy X-Idempotency-Status removal regression pin (1 test)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
import structlog
import structlog.contextvars
import structlog.testing
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.ids import new_idempotency_key, new_request_id
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
    @app.get("/debug/state")
    async def _state_probe(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "request_id": getattr(request.state, "request_id", None),
                "idempotency_key": getattr(request.state, "idempotency_key", None),
                "idempotency_key_generated": getattr(
                    request.state, "idempotency_key_generated", None
                ),
                "actor_id": getattr(request.state, "actor_id", None),
                "caller_context": repr(getattr(request.state, "caller_context", None)),
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
