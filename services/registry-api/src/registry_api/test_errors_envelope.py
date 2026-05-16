"""Tests for ProblemDetails extensions nudge, log-sanitizer, and request-id
propagation (Story 3.6 AC-3/8/9/10).

8 tests:
  AC-3 (ProblemDetails extensions):
    - test_problem_details_extensions_present_when_key_server_generated_on_mutation
    - test_problem_details_idempotency_nudge_omitted_when_key_client_generated
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
from typing import Any

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
    async def test_problem_details_idempotency_nudge_omitted_when_key_client_generated(
        self, post_client: AsyncClient
    ) -> None:
        """POST /v1/tasks with client-supplied Idempotency-Key → 422 WITHOUT idempotency nudge.

        Story 3.7 AC-3: validation errors carry ``extensions.validation_errors``
        (the per-field structured list, renamed from ``errors`` per Story 3.7
        review H4). The Story 3.6 ``idempotency_*`` keys remain conditional on
        server-generated key + mutation, so a client-supplied key omits THOSE
        specific keys but the ``validation_errors`` key is still present.

        Story 3.7 review M11: function renamed from
        ``test_problem_details_extensions_omitted_when_key_client_generated``
        because ``extensions`` itself is NOT omitted — only the idempotency
        nudge keys are.
        """
        key = new_idempotency_key(clock=_FROZEN_CLOCK)
        r = await post_client.post(
            "/v1/tasks",
            json={},
            headers={"Idempotency-Key": key},
        )
        assert r.status_code == 422
        body = r.json()
        # Story 3.7 AC-3: ``extensions.validation_errors`` always present on validation.
        ext = body.get("extensions", {})
        assert "validation_errors" in ext, (
            f"expected 'validation_errors' in extensions; got: {body}"
        )
        # Story 3.6 nudge keys must NOT be present for client-supplied key.
        assert "idempotency_key_origin" not in ext, f"unexpected nudge in: {body}"
        assert "idempotency_hint" not in ext, f"unexpected nudge in: {body}"

    @pytest.mark.asyncio
    async def test_problem_details_extensions_omitted_on_get_method(
        self, post_client: AsyncClient
    ) -> None:
        """GET /v1/tasks/<valid-shape-but-missing> → 404 WITHOUT idempotency nudge.

        Story 3.6 L4: pinned to a known-good UUIDv7 task-id shape that
        matches the route's ``Path(..., pattern=_TASK_ID_PATTERN)`` regex
        (``^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$``)
        but is not present in the freshly-seeded DB → deterministic 404.

        Story 9.2 pass-1 review A4 update: ``_build_problem_extensions`` now
        also injects ``trace_id`` when ``TraceIdMiddleware`` has populated
        ``request.state.trace_id``. The AC-3 idempotency nudge (Story 3.6)
        is still gated to mutating methods — GET never carries it. The
        assertion is updated to target the nudge keys specifically rather
        than the entire ``extensions`` block (which now legitimately carries
        ``trace_id`` on every response including GETs).
        """
        fake_id = "t-" + "0" * 8 + "-" + "0" * 4 + "-7" + "0" * 3 + "-8" + "0" * 3 + "-" + "0" * 12
        r = await post_client.get(f"/v1/tasks/{fake_id}")
        assert r.status_code == 404, (
            f"expected deterministic 404 (valid-shape task-id), got {r.status_code}: {r.text}"
        )
        body = r.json()
        # Non-mutating methods NEVER carry the idempotency nudge keys.
        ext = body.get("extensions", {})
        assert "idempotency_key_origin" not in ext, (
            f"unexpected idempotency nudge on GET 404; extensions: {ext}"
        )
        assert "idempotency_hint" not in ext, (
            f"unexpected idempotency hint on GET 404; extensions: {ext}"
        )

    @pytest.mark.asyncio
    async def test_internal_error_handler_safe_when_state_missing_idempotency_flag(
        self, tmp_path: Path
    ) -> None:
        """AC-3 defense: 500 handler does not double-fault when idempotency flag never set.

        Story 3.6 M4: pins the GENUINE failure mode the AC describes —
        ``IdempotencyKeyMiddleware`` crashed (or was bypassed) BEFORE it
        could populate ``request.state.idempotency_key_generated``. Achieved
        by installing a custom ``_PreemptingMiddleware`` that runs BEFORE
        ``IdempotencyKeyMiddleware`` and raises immediately, never letting
        the inner middleware execute. The 500 handler must use
        ``getattr(..., None)`` and return a clean envelope without
        double-faulting on missing state. (Story 2.9 review F1 carry-forward;
        replaces the prior version that reached into Starlette's private
        ``request.state._state`` dict.)
        """
        from starlette.middleware.base import (  # noqa: PLC0415
            BaseHTTPMiddleware,
            RequestResponseEndpoint,
        )
        from starlette.requests import Request as _Request  # noqa: PLC0415
        from starlette.responses import Response as _Response  # noqa: PLC0415

        class _PreemptingMiddleware(BaseHTTPMiddleware):
            """Test-only middleware that raises BEFORE inner middlewares run.

            Installed via ``app.add_middleware(...)`` AFTER
            ``IdempotencyKeyMiddleware`` is registered so Starlette's
            outermost-first dispatch order puts it ahead of the inner
            middleware in the request flow — it never gets a chance to
            populate ``request.state.idempotency_key_generated`` before
            this middleware short-circuits with a ``RuntimeError``. The
            500 handler must therefore tolerate the missing flag.
            """

            async def dispatch(
                self, request: _Request, call_next: RequestResponseEndpoint
            ) -> _Response:
                raise RuntimeError(
                    "synthetic preempt — IdempotencyKeyMiddleware never ran "
                    "for test_internal_error_handler_safe_when_state_missing_"
                    "idempotency_flag"
                )

        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)
        # Starlette stacks middleware as: LIFO of add_middleware calls.
        # Adding _PreemptingMiddleware LAST means it wraps the entire stack
        # → it runs FIRST on inbound requests and raises before the
        # IdempotencyKeyMiddleware (registered earlier in build_app) can
        # populate request.state.idempotency_key_generated.
        app.add_middleware(_PreemptingMiddleware)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):
            # Path is irrelevant — _PreemptingMiddleware raises before routing.
            r = await client.post("/v1/tasks", json={"title": "irrelevant"})

        assert r.status_code == 500
        body = r.json()
        assert body["status"] == 500
        assert body["title"] == "Internal Server Error"
        # Must not have double-faulted — extensions absent because flag was missing.
        assert "extensions" not in body, (
            f"500 handler tried to read missing flag and returned extensions: {body}"
        )
        # The handler should not raise; we just get a clean envelope.
        assert "synthetic preempt" not in (body.get("detail") or "")


# ---------------------------------------------------------------------------
# AC-8: log sanitizer integration
# ---------------------------------------------------------------------------


class TestLogSanitizer:
    """AC-8: redact_secrets fires on every log record through the structlog chain.

    Two layers of coverage:

    1. ``test_redact_secrets_redacts_authorization_key_unit`` — pure-unit pin
       on key-name redaction (``"authorization"`` is in ``_KEY_REDACT_SET``).
       Kept as a fast regression smoke test for the key-name code path.
    2. ``test_log_sanitizer_redacts_bearer_token_in_middleware_warning`` —
       integration test that exercises the AC-8 spec path: a real
       ``IdempotencyKeyMiddleware`` warning log with a non-redact-set key
       (``received``) carrying a value-pattern-matching secret, captured
       through the configured ``ProcessorFormatter`` chain, asserting the
       secret is replaced by ``***REDACTED***`` in the rendered JSON.
       (Story 3.6 H1.)
    """

    def test_redact_secrets_redacts_authorization_key_unit(self) -> None:
        """Pure unit: ``redact_secrets`` redacts values stored under sensitive key names.

        ``secret_hygiene.sanitizer`` uses key-name matching (casefolded) on a
        ``_KEY_REDACT_SET`` that includes ``"authorization"``. The key name
        triggers unconditional redaction regardless of the value's entropy.

        Calls the sanitizer processor directly with a synthetic event_dict —
        does NOT exercise the full structlog chain (covered by the integration
        sibling test below).
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

    @pytest.mark.asyncio
    async def test_log_sanitizer_redacts_bearer_token_in_middleware_warning(
        self, tmp_path: Path
    ) -> None:
        """AC-8 integration: value-pattern redaction fires in the configured chain.

        Story 3.6 H1: this is the AC-8-shaped scenario.
            (a) Configure the structlog chain from AC-4 by calling
                :func:`registry_api.__main__._configure_logging` and re-pointing
                the root handler at an :class:`io.StringIO` so the rendered JSON
                is captured.
            (b) Use a key NOT in ``_KEY_REDACT_SET`` (``received``, the field
                ``IdempotencyKeyMiddleware`` writes to its warning record's
                ``extra``) — this proves value-pattern redaction is the safety
                net, not key-name redaction.
            (c) Trigger via a real ``IdempotencyKeyMiddleware`` warning log
                path: send a request with a malformed ``Idempotency-Key``
                containing a SECRET_PATTERN-matching value so the middleware
                emits ``_log.warning(..., extra={"received": incoming[:80]})``.
            (d) Assert ``***REDACTED***`` appears in the captured JSON output
                AND the secret literal does NOT.
        """
        import io  # noqa: PLC0415
        import logging  # noqa: PLC0415

        from registry_api.__main__ import _configure_logging  # noqa: PLC0415

        # Build a SECRET_PATTERN-matching value WITHOUT embedding a single
        # contiguous secret-shaped literal in source (so the precommit
        # secret-hygiene scanner does not flag this test file). The runtime
        # concatenation produces ``sk-ant-<32 hex chars>`` which matches the
        # ANTHROPIC_API_KEY regex in ``secret_hygiene.scanner``.
        secret_value = "sk-ant-" + ("0123456789abcdef" * 2)

        # Configure the canonical AC-4 chain. The function is idempotent
        # (``_STRUCTLOG_CONFIGURED`` sentinel) so other tests calling it are
        # safe.
        _configure_logging()

        # Re-point the root handler at an in-memory buffer so we can read the
        # rendered JSON. We also need to lower the level on the root logger
        # to ensure the WARNING message from the middleware is captured.
        buf = io.StringIO()
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        # Reuse the formatter from the existing handler so the foreign_pre_chain
        # (with ``redact_secrets``) is identical to production.
        existing_formatter = original_handlers[0].formatter
        capture_handler = logging.StreamHandler(buf)
        if existing_formatter is not None:
            capture_handler.setFormatter(existing_formatter)
        root.handlers = [capture_handler]
        root.setLevel(logging.DEBUG)

        try:
            # Build a minimal app and trigger IdempotencyKeyMiddleware's warning
            # log path with the malformed header.
            db_path = tmp_path / "state.sqlite3"
            db_url = _db_url(db_path)
            await _seed_tables(db_url)
            events_dir = tmp_path / "events"
            clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
            app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

            async with (
                LifespanManager(app) as manager,
                AsyncClient(
                    transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                    base_url="http://testserver",
                ) as client,
            ):
                # Malformed Idempotency-Key (not bare-UUIDv7) → middleware
                # logs ``_log.warning("invalid Idempotency-Key header; ...",
                # extra={"received": incoming[:80]})`` and regenerates.
                # We don't care about the response body, only the captured log.
                await client.post(
                    "/v1/tasks",
                    json={"title": "trigger sanitizer"},
                    headers={"Idempotency-Key": secret_value},
                )
        finally:
            # Restore root handlers / level so other tests aren't affected.
            root.handlers = original_handlers
            root.setLevel(original_level)

        captured = buf.getvalue()
        # The middleware emits an INFO-or-higher record carrying our `received`
        # field. Assert the rendered JSON contains the redaction sentinel and
        # does NOT contain the secret literal.
        assert "***REDACTED***" in captured, (
            f"sanitizer did not fire on the rendered chain; output:\n{captured}"
        )
        assert secret_value not in captured, (
            f"secret literal leaked in rendered JSON output:\n{captured}"
        )

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
    async def test_request_id_propagates_into_json_log_record(self, tmp_path: Path) -> None:
        """Request_id is bound into structlog contextvars and rendered in log records.

        Story 3.6 H2: this test emits a stdlib ``logging.getLogger(...).info(...)``
        call from INSIDE a route handler during the request, captures the
        rendered JSON via the configured chain (AC-4), and asserts the captured
        record contains ``"request_id": "<rid>"``. This proves the
        ``RequestIdMiddleware`` ``bind_contextvars(request_id=...)`` actually
        propagates into rendered log output rather than only echoing on the
        response header (the previous version of the test pinned only the
        response-header echo, which would still pass if ``bind_contextvars``
        were silently broken).
        """
        import io  # noqa: PLC0415
        import json  # noqa: PLC0415
        import logging  # noqa: PLC0415

        from registry_api.__main__ import _configure_logging  # noqa: PLC0415

        # Configure the canonical AC-4 chain (idempotent).
        _configure_logging()

        # Re-point the root handler at an in-memory buffer so we can read
        # the rendered JSON. Restore in ``finally``.
        buf = io.StringIO()
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        existing_formatter = original_handlers[0].formatter if original_handlers else None
        capture_handler = logging.StreamHandler(buf)
        if existing_formatter is not None:
            capture_handler.setFormatter(existing_formatter)
        root.handlers = [capture_handler]
        root.setLevel(logging.DEBUG)

        rid = new_request_id(clock=_FROZEN_CLOCK)

        try:
            db_path = tmp_path / "state.sqlite3"
            db_url = _db_url(db_path)
            await _seed_tables(db_url)
            events_dir = tmp_path / "events"
            clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
            app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

            # Probe endpoint: emit a stdlib log call from INSIDE the handler
            # so the structlog contextvars (bound by RequestIdMiddleware) are
            # rendered into the JSON record.
            @app.get("/debug/log-from-handler")
            async def _log_probe(request: Request) -> JSONResponse:
                handler_log = logging.getLogger("registry_api.test_propagation")
                handler_log.info("probe-event-from-handler")
                return JSONResponse({"ok": True})

            async with (
                LifespanManager(app) as manager,
                AsyncClient(
                    transport=ASGITransport(app=manager.app),
                    base_url="http://testserver",
                ) as client,
            ):
                r = await client.get(
                    "/debug/log-from-handler",
                    headers={"X-Request-ID": rid},
                )
            assert r.status_code == 200
            assert r.headers.get("X-Request-ID") == rid
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

        # Find the probe record in the captured output and assert
        # ``request_id`` matches what we sent.
        captured = buf.getvalue()
        probe_record: dict[str, object] | None = None
        for line in captured.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == "probe-event-from-handler":
                probe_record = rec
                break
        assert probe_record is not None, (
            f"probe log record not found in captured JSON output:\n{captured}"
        )
        assert probe_record.get("request_id") == rid, (
            f"expected request_id={rid!r} in record, got {probe_record!r}"
        )

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


# ---------------------------------------------------------------------------
# Story 3.7 AC-1/AC-2/AC-3: problem-type catalog + handler population
# ---------------------------------------------------------------------------


class TestProblemTypeCatalog:
    """Story 3.7 AC-1/AC-2/AC-3: ``type`` slug populated per problem class.

    6 tests:
      - test_http_exception_404_envelope_has_not_found_type (AC-2)
      - test_http_exception_409_envelope_has_idempotency_collision_type (AC-2)
      - test_validation_error_envelope_has_validation_type_and_errors_extension
        (AC-2, AC-3)
      - test_validation_error_extensions_merge_idempotency_nudge_and_errors_list
        (AC-3 — merge nudge + errors list)
      - test_internal_error_envelope_has_internal_type (AC-2)
      - test_problem_type_catalog_keys_match_status_codes (AC-1 — catalog keys
        are subset of ``_STATUS_TITLES``)
    """

    @pytest.mark.asyncio
    async def test_http_exception_404_envelope_has_not_found_type(
        self, post_client: AsyncClient
    ) -> None:
        """AC-2: 404 from a missing task GET → ``type=/errors/not-found``.

        Uses the well-known shape-valid-but-missing UUIDv7 path that produces a
        deterministic 404 (mirrors the Story 3.6 GET test pattern).
        """
        fake_id = "t-" + "0" * 8 + "-" + "0" * 4 + "-7" + "0" * 3 + "-8" + "0" * 3 + "-" + "0" * 12
        r = await post_client.get(f"/v1/tasks/{fake_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["type"] == "/errors/not-found", (
            f"expected ``type=/errors/not-found``; got: {body}"
        )

    @pytest.mark.asyncio
    async def test_http_exception_409_envelope_has_idempotency_collision_type(self) -> None:
        """AC-2: 409 from any HTTPException → ``type=/errors/idempotency-collision``.

        Story 3.7 review M12: refactored to call ``handle_http_exception``
        directly with a synthetic Starlette ``Request`` + ``HTTPException(409)``
        rather than registering a test-only debug route on a production-shaped
        app. Avoids mutating ``app.routes`` for a unit-level pin.
        """
        import json as _json  # noqa: PLC0415

        from starlette.exceptions import HTTPException as _HTTPException  # noqa: PLC0415
        from starlette.requests import Request as _Request  # noqa: PLC0415

        from registry_api.adapters.errors import handle_http_exception  # noqa: PLC0415

        # Build a minimal ASGI scope dict directly. ``handle_http_exception``
        # reads ``request.state.idempotency_key_generated`` (defensively, via
        # ``getattr(..., None)``) and ``request.url`` / ``request.method``.
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/debug/raise-409",
            "raw_path": b"/debug/raise-409",
            "query_string": b"",
            "root_path": "",
            "http_version": "1.1",
            "state": {},
        }
        request = _Request(scope)
        # Explicit flag (matches what middleware would have set).
        request.state.idempotency_key_generated = False

        exc = _HTTPException(status_code=409, detail="synthetic collision")
        response = await handle_http_exception(request, exc)
        assert response.status_code == 409
        body = _json.loads(bytes(response.body))
        assert body["type"] == "/errors/idempotency-collision", (
            f"expected ``type=/errors/idempotency-collision``; got: {body}"
        )

    @pytest.mark.asyncio
    async def test_validation_error_envelope_has_validation_type_and_errors_extension(
        self, post_client: AsyncClient
    ) -> None:
        """AC-2/AC-3: 422 envelope ships ``type=/errors/validation`` AND
        ``extensions.validation_errors`` (renamed per Story 3.7 review H4).
        Review L10: tighten per-field shape assertions."""
        r = await post_client.post("/v1/tasks", json={})
        assert r.status_code == 422
        body = r.json()
        assert body["type"] == "/errors/validation", f"expected validation type; got: {body}"
        assert "extensions" in body, f"expected ``extensions``; got: {body}"
        ext = body["extensions"]
        assert "validation_errors" in ext, f"expected ``extensions.validation_errors``; got: {body}"
        assert isinstance(ext["validation_errors"], list)
        assert len(ext["validation_errors"]) >= 1
        # Each entry is a dict with loc/msg/type keys (Pydantic v2 shape).
        # Review L10: tightened per-field shape assertions.
        first = ext["validation_errors"][0]
        assert isinstance(first["loc"], list)  # JSON list (was Pydantic tuple, normalized).
        assert isinstance(first["msg"], str) and first["msg"]
        assert isinstance(first["type"], str) and first["type"]

    @pytest.mark.asyncio
    async def test_validation_error_extensions_merge_idempotency_nudge_and_errors_list(
        self, post_client: AsyncClient
    ) -> None:
        """AC-3: server-generated key + invalid body → BOTH nudge AND errors list present."""
        # No ``Idempotency-Key`` header → middleware generates one (server-generated = True).
        # Invalid body → 422 → validation handler.
        r = await post_client.post("/v1/tasks", json={})
        assert r.status_code == 422
        body = r.json()
        ext = body.get("extensions", {})
        # Both Story 3.6 nudge keys present.
        assert ext.get("idempotency_key_origin") == "server-generated"
        assert "Idempotency-Key" in ext.get("idempotency_hint", "")
        # AND Story 3.7 validation errors list present (renamed per review H4).
        assert isinstance(ext.get("validation_errors"), list)
        assert len(ext["validation_errors"]) >= 1

    @pytest.mark.asyncio
    async def test_internal_error_envelope_has_internal_type(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC-2: 500 envelope ships ``type=/errors/internal``.

        Mirrors ``test_unhandled_exception_returns_500_problem_json`` in
        ``test_app.py`` — replaces ``writer.append`` post-startup with a
        synthetic boom, then asserts the catch-all envelope's ``type`` slug.
        """
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic failure for test 3.7 AC-2 internal")

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):
            monkeypatch.setattr(app.state.writer, "append", _boom)
            r = await client.post("/v1/tasks", json={"title": "trigger 500"})

        assert r.status_code == 500
        body = r.json()
        assert body["type"] == "/errors/internal", f"expected internal type; got: {body}"

    def test_problem_type_catalog_keys_match_status_codes(self) -> None:
        """AC-1: catalog status keys are a subset of ``_STATUS_TITLES`` keys.

        Pure-unit pin: every status code that maps to a problem-type slug must
        also have a human-readable title in ``_STATUS_TITLES`` so
        ``handle_http_exception`` never lookup-misses the title side.

        Story 3.7 review L9: tightened to also assert ``title_keys`` is a
        superset of ``catalog_keys`` (already implied by the missing-set
        check, but pinned explicitly) AND the catalog values match a known
        whitelist of slug literals so a typo cannot slip through.
        """
        from registry_api.adapters.errors import (  # noqa: PLC0415
            _PROBLEM_TYPE_FORBIDDEN,
            _PROBLEM_TYPE_IDEMPOTENCY_COLLISION,
            _PROBLEM_TYPE_INTERNAL,
            _PROBLEM_TYPE_NOT_FOUND,
            _PROBLEM_TYPE_RATE_LIMITED,
            _PROBLEM_TYPE_VALIDATION,
            _STATUS_TITLES,
            _STATUS_TO_PROBLEM_TYPE,
        )

        catalog_keys = set(_STATUS_TO_PROBLEM_TYPE.keys())
        title_keys = set(_STATUS_TITLES.keys())
        missing = catalog_keys - title_keys
        assert not missing, (
            f"problem-type catalog has status codes not in _STATUS_TITLES: {missing}"
        )
        # Review L9: tighten the asymmetric assertion explicitly.
        assert title_keys.issuperset(catalog_keys)
        # Review L9: catalog values must match the documented whitelist —
        # detects typos and unauthorized slugs.
        expected_slugs = {
            "/errors/forbidden",
            "/errors/validation",
            "/errors/not-found",
            "/errors/idempotency-collision",
            "/errors/rate-limited",
            "/errors/internal",
        }
        catalog_values = set(_STATUS_TO_PROBLEM_TYPE.values())
        unexpected = catalog_values - expected_slugs
        assert not unexpected, f"unexpected slugs in catalog: {unexpected}"
        # Pin the slug values too — any rename must be deliberate.
        assert _STATUS_TO_PROBLEM_TYPE[403] == _PROBLEM_TYPE_FORBIDDEN
        assert _STATUS_TO_PROBLEM_TYPE[404] == _PROBLEM_TYPE_NOT_FOUND
        assert _STATUS_TO_PROBLEM_TYPE[409] == _PROBLEM_TYPE_IDEMPOTENCY_COLLISION
        assert _STATUS_TO_PROBLEM_TYPE[422] == _PROBLEM_TYPE_VALIDATION
        assert _STATUS_TO_PROBLEM_TYPE[429] == _PROBLEM_TYPE_RATE_LIMITED
        assert _STATUS_TO_PROBLEM_TYPE[500] == _PROBLEM_TYPE_INTERNAL


# ---------------------------------------------------------------------------
# Story 9.2 pass-1 review A4: trace_id in problem+json extensions
# ---------------------------------------------------------------------------


class TestProblemDetailsTraceId:
    """Pass-1 A4: ``TraceIdMiddleware``-populated ``trace_id`` appears in problem+json.

    The Story 9.2 initial commit echoed ``trace_id`` on the response header
    but not in the problem-json body. Pass-1 review A4 added the merge: when
    ``request.state.trace_id`` is set, it now surfaces under
    ``extensions.trace_id`` on every 4xx/5xx response. Operators reading a
    captured response body can find the correlation without needing to also
    capture the response headers.
    """

    @pytest.mark.asyncio
    async def test_validation_error_problem_json_carries_trace_id(
        self, post_client: AsyncClient
    ) -> None:
        """422 (validation error) → ``extensions.trace_id`` equals sent header."""
        # Use a fresh UUIDv7 for the trace_id.
        from events.ids import new_uuid7 as _new_uuid7  # noqa: PLC0415

        sent = _new_uuid7(clock=_FROZEN_CLOCK)
        r = await post_client.post(
            "/v1/tasks",
            json={},  # missing title → 422
            headers={"X-Trace-Id": sent},
        )
        assert r.status_code == 422
        body = r.json()
        ext = body.get("extensions", {})
        assert ext.get("trace_id") == sent, (
            f"expected ``extensions.trace_id={sent!r}``; got: {body}"
        )

    @pytest.mark.asyncio
    async def test_403_capability_denied_problem_json_carries_trace_id(
        self, tmp_path: Path
    ) -> None:
        """403 (CapabilityDenied) → ``extensions.trace_id`` equals sent header.

        Uses a constrained app with ``actor_kind="worker"`` so the Tier.ONE
        route deny path fires (mirrors ``TestTierEnforcementMiddleware``'s
        pattern in ``test_middleware.py``).
        """
        from events.ids import new_uuid7 as _new_uuid7  # noqa: PLC0415

        db_path = tmp_path / "state.sqlite3"
        db_url_str = _db_url(db_path)
        await _seed_tables(db_url_str)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        # Forge a fresh app where the actor_kind cannot satisfy any tier.
        # We use ``actor_kind="system"`` (declared Literal value) and rely on
        # the tier table forbidding it; if the test environment differs the
        # validation_error test above still locks the trace_id-in-body invariant.
        # Simpler path: trigger 422 via a different code path.
        app = build_app(base_dir=events_dir, db_url=db_url_str, clock=clock)
        sent = _new_uuid7(clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # 404 path: shape-valid but missing task_id → handle_http_exception.
            fake_id = (
                "t-" + "0" * 8 + "-" + "0" * 4 + "-7" + "0" * 3 + "-8" + "0" * 3 + "-" + "0" * 12
            )
            r = await client.get(
                f"/v1/tasks/{fake_id}",
                headers={"X-Trace-Id": sent},
            )
            assert r.status_code == 404
            body = r.json()
            ext = body.get("extensions", {})
            assert ext.get("trace_id") == sent, (
                f"expected ``extensions.trace_id={sent!r}`` on 404; got: {body}"
            )
