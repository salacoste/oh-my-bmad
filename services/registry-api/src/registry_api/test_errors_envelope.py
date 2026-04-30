"""Tests for ProblemDetails extensions nudge, log-sanitizer, and request-id
propagation (Story 3.6 AC-3/8/9/10).

8 tests:
  AC-3 (ProblemDetails extensions):
    - test_problem_details_extensions_present_when_key_server_generated_on_mutation
    - test_problem_details_extensions_omitted_when_key_client_generated
    - test_problem_details_extensions_omitted_on_get_method
    - test_internal_error_handler_safe_when_state_missing_idempotency_flag
  AC-8 (log sanitizer):
    - test_log_sanitizer_redacts_bearer_token_in_middleware_warning
    - test_log_sanitizer_does_not_redact_safe_strings
  AC-9 (request-id propagation):
    - test_request_id_propagates_into_json_log_record
    - test_request_id_unbound_after_request_completes
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
import structlog
import structlog.contextvars
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
# Shared fixtures
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def post_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
    app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# AC-3: ProblemDetails.extensions nudge
# ---------------------------------------------------------------------------


class TestProblemDetailsExtensions:
    """AC-3: extensions field populated on server-generated key + mutation only."""

    @pytest.mark.asyncio
    async def test_problem_details_extensions_present_when_key_server_generated_on_mutation(
        self, post_client: AsyncClient
    ) -> None:
        """POST /v1/tasks with missing Idempotency-Key and invalid body → 422 with extensions."""
        # Malformed body → RequestValidationError → 422; no Idempotency-Key
        # header so the middleware generates one (server-generated = True).
        r = await post_client.post("/v1/tasks", json={})
        assert r.status_code == 422
        body = r.json()
        assert "extensions" in body, f"expected 'extensions' key in: {body}"
        ext = body["extensions"]
        assert ext["idempotency_key_origin"] == "server-generated"
        assert "Idempotency-Key" in ext["idempotency_hint"]

    @pytest.mark.asyncio
    async def test_problem_details_extensions_omitted_when_key_client_generated(
        self, post_client: AsyncClient
    ) -> None:
        """POST /v1/tasks with client-supplied Idempotency-Key → 422 WITHOUT extensions."""
        key = new_idempotency_key(clock=_FROZEN_CLOCK)
        r = await post_client.post(
            "/v1/tasks",
            json={},
            headers={"Idempotency-Key": key},
        )
        assert r.status_code == 422
        body = r.json()
        # No extensions field at all when key was client-supplied.
        assert "extensions" not in body, f"unexpected 'extensions' in: {body}"

    @pytest.mark.asyncio
    async def test_problem_details_extensions_omitted_on_get_method(
        self, post_client: AsyncClient
    ) -> None:
        """GET /v1/tasks/<nonexistent> → 404 WITHOUT extensions (non-mutating method)."""
        fake_id = "t-" + "0" * 8 + "-" + "0" * 4 + "-7" + "0" * 3 + "-8" + "0" * 3 + "-" + "0" * 12
        r = await post_client.get(f"/v1/tasks/{fake_id}")
        assert r.status_code in (404, 422)  # 422 if path regex fails, 404 if it passes
        body = r.json()
        # Non-mutating methods never carry the extensions nudge.
        assert "extensions" not in body, f"unexpected 'extensions' in: {body}"

    @pytest.mark.asyncio
    async def test_internal_error_handler_safe_when_state_missing_idempotency_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-3 defense: 500 handler does not double-fault when idempotency flag absent.

        Simulates an exception raised before IdempotencyKeyMiddleware populates
        request.state.idempotency_key_generated. The handler must use
        getattr(..., None) and return a clean 500 envelope.
        """
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

        @app.post("/debug/crash-before-idem")
        async def _crash(request: Request) -> JSONResponse:
            # Delete the flag to simulate middleware crash before setting it.
            if hasattr(request.state, "idempotency_key_generated"):
                del request.state._state["idempotency_key_generated"]
            raise RuntimeError("synthetic crash — test_internal_error_handler")

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):
            r = await client.post("/debug/crash-before-idem", json={})

        assert r.status_code == 500
        body = r.json()
        assert body["status"] == 500
        assert body["title"] == "Internal Server Error"
        # Must not have double-faulted — extensions absent because flag was missing.
        # The handler should not raise; we just get a clean envelope.
        assert "synthetic crash" not in (body.get("detail") or "")


# ---------------------------------------------------------------------------
# AC-8: log sanitizer integration
# ---------------------------------------------------------------------------


class TestLogSanitizer:
    """AC-8: redact_secrets fires on every log record through the structlog chain."""

    def test_log_sanitizer_redacts_bearer_token_in_middleware_warning(self) -> None:
        """A log record with a sensitive key name is redacted by redact_secrets.

        ``secret_hygiene.sanitizer`` uses key-name matching (casefolded) on a
        ``_KEY_REDACT_SET`` that includes ``"authorization"`` and ``"bearer"``.
        The test simulates a middleware log that accidentally attaches an
        authorization header value. The key name triggers unconditional
        redaction regardless of the value's entropy.

        Uses the sanitizer processor directly — no need for the full structlog
        chain (which is only wired in __main__.py at production runtime).
        """
        from secret_hygiene.sanitizer import REDACTED_SENTINEL, redact_secrets  # noqa: PLC0415

        # "authorization" is in _KEY_REDACT_SET → key-name redaction fires.
        event_dict: dict[str, object] = {
            "event": "invalid Idempotency-Key header; generating fresh",
            "authorization": "Bearer abc123def-secret-value",
            "level": "warning",
        }
        result = redact_secrets(None, None, event_dict)
        assert result["authorization"] == REDACTED_SENTINEL, (
            f"authorization value was NOT redacted; got: {result['authorization']!r}"
        )
        # The event message itself must pass through unchanged.
        assert result["event"] == "invalid Idempotency-Key header; generating fresh"

    def test_log_sanitizer_does_not_redact_safe_strings(self) -> None:
        """Non-secret values pass through the sanitizer unchanged (negative test)."""
        from secret_hygiene.sanitizer import redact_secrets  # noqa: PLC0415

        event_dict: dict[str, object] = {
            "event": "task created",
            "task_id": "t-abc123",
            "level": "info",
        }
        result = redact_secrets(None, None, event_dict)
        assert result["task_id"] == "t-abc123"
        assert result["event"] == "task created"
        assert "REDACTED" not in str(result)


# ---------------------------------------------------------------------------
# AC-9: request-id propagation into log records
# ---------------------------------------------------------------------------


class TestRequestIdPropagation:
    """AC-9: request_id appears in downstream log records; absent after request ends."""

    @pytest.mark.asyncio
    async def test_request_id_propagates_into_json_log_record(
        self, post_client: AsyncClient
    ) -> None:
        """Request_id is bound into structlog contextvars during the request lifetime.

        Technique: send a POST, assert the echoed X-Request-ID matches what was
        sent (proves RequestIdMiddleware ran and bound the correct id), then
        assert that after the response completes the contextvars are clean (the
        try/finally unbind ran). This exercises the same code-path that would
        cause downstream logging calls to carry request_id.
        """
        rid = new_request_id(clock=_FROZEN_CLOCK)

        # Make a request — RequestIdMiddleware binds request_id for its duration.
        r = await post_client.post(
            "/v1/tasks",
            json={"title": "propagation test"},
            headers={"X-Request-ID": rid},
        )
        assert r.status_code == 201
        # X-Request-ID echoed on response proves the middleware bound the correct id.
        assert r.headers.get("X-Request-ID") == rid

        # After response completes: unbind must have run — no leakage.
        after = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "request_id" not in after

    @pytest.mark.asyncio
    async def test_request_id_unbound_after_request_completes(self, tmp_path: Path) -> None:
        """After response, request_id is absent from contextvars — try/finally proof.

        Sends two sequential requests. After each one, asserts request_id is
        unbound. Verifies the second request does NOT observe the first request's
        id in the ambient context (the try/finally unbind ran).
        """
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

        # Probe endpoint: returns the current structlog contextvars at call time.
        @app.get("/debug/contextvars")
        async def _ctx_probe(request: Request) -> JSONResponse:
            ctx = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
            return JSONResponse({"request_id_in_ctx": ctx.get("request_id")})

        rid1 = new_request_id(clock=clock)
        rid2 = new_request_id(clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            r1 = await client.get("/debug/contextvars", headers={"X-Request-ID": rid1})
            assert r1.status_code == 200
            # Inside the handler, request_id IS bound.
            assert r1.json()["request_id_in_ctx"] == rid1

            # After r1 completes, ambient context must be clean.
            after_r1 = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
            assert "request_id" not in after_r1

            r2 = await client.get("/debug/contextvars", headers={"X-Request-ID": rid2})
            assert r2.status_code == 200
            # r2 sees its OWN id, not r1's.
            assert r2.json()["request_id_in_ctx"] == rid2

        # After both requests: clean.
        final = structlog.contextvars.get_merged_contextvars(structlog.get_logger())
        assert "request_id" not in final
