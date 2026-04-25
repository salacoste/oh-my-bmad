"""FastAPI application factory for registry-api (Story 2.9 AC-1).

``build_app(*, base_dir, db_url, clock) -> FastAPI`` — factory that wires:
  - Async lifespan: creates ``EventLogWriter`` + read-only SQLite engine on
    startup; tears them down on shutdown via ``AsyncExitStack`` so each
    cleanup runs independently regardless of others' exceptions; stores
    everything on ``app.state``.
  - Middleware stack: ``RequestIdMiddleware`` → ``IdempotencyKeyMiddleware``
    → ``ActorIdMiddleware`` (Architecture line 213 order).
  - Exception handlers: RFC 7807 problem+json for ``HTTPException``,
    ``RequestValidationError``, and any unhandled ``Exception``.
  - Routes: ``/v1/tasks`` (POST + GET) via ``tasks_router``.

Design notes:
  - registry-api appends events directly via ``EventLogWriter`` (NOT via
    clawhip-bridge MCP). The MCP server is for agent consumers (workers,
    orchestrator) over stdio; registry-api is a service process.
  - Recovery (``recover_all_logs``) is OWNED by the registry-state
    materializer process; this read-mostly + append-only-events service
    intentionally does NOT call it (per F27 of the Story 2.9 code review).
  - The engine is read-only (``create_engine(db_url, read_only=True)``).
    Belt-and-braces with FR26 single-writer CI gate.
  - F10: ``session_maker`` is constructed once during lifespan startup and
    reused per-request — the GET handler reads it via
    ``request.app.state.session_maker``.
  - F28: actor identity flows from ``ActorIdMiddleware`` → ``request.state``;
    we no longer mirror it onto ``app.state`` (the per-request value is the
    canonical access path).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from events.clock import Clock
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed per AC-16
    EventLogWriter,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine,
    get_session,
)
from starlette.exceptions import HTTPException

from registry_api.adapters.errors import (
    handle_http_exception,
    handle_internal_error,
    handle_validation_error,
)
from registry_api.adapters.middleware import (
    ActorIdMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
)
from registry_api.routes.tasks import router as tasks_router


def build_app(*, base_dir: Path, db_url: str, clock: Clock) -> FastAPI:
    """Build and return the wired-up FastAPI application.

    Args:
        base_dir: Root directory for JSONL event log files. ``EventLogWriter``
                  creates ``base_dir`` on construction (``mkdir(parents=True,
                  exist_ok=True)``) so a non-existent directory is fine — the
                  writer initialization handles the bootstrap.
        db_url:   SQLAlchemy async URL for the read-only SQLite store,
                  e.g. ``sqlite+aiosqlite:///path/to/state.sqlite3``.
                  Must NOT be an in-memory URL (read-only + in-memory is
                  nonsensical per Story 2.3's ``create_engine`` contract).
        clock:    Injectable clock (``SystemClock`` in production;
                  ``FrozenClock`` / ``TickingClock`` in tests).

    Returns:
        Fully configured ``FastAPI`` instance ready for ``uvicorn.run``.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Async lifespan handler — startup + shutdown resource management.

        F5+F14: Uses ``AsyncExitStack`` so each registered cleanup runs
        independently — a failure in ``writer.close()`` does not skip
        ``engine.dispose()`` and vice versa. The stack also unwinds correctly
        on partial-startup exceptions (e.g. engine creation succeeded but
        writer construction raised — engine still gets disposed).
        """
        async with AsyncExitStack() as stack:
            # Engine first — open the read-only DB before constructing the writer.
            # If create_engine raises (bad URL, etc.) we never allocate the writer.
            engine = create_engine(db_url, read_only=True)
            stack.push_async_callback(engine.dispose)

            # F10/F23: build the session_maker once via Story 2.3's get_session
            # helper and stash it on app.state for handlers to reuse — avoids
            # per-request async_sessionmaker allocation on the hot read path.
            session_maker = get_session(engine)
            app.state.engine = engine
            app.state.session_maker = session_maker
            app.state.clock = clock

            # Writer last — F13 note: EventLogWriter.__init__ calls
            # base_dir.mkdir(parents=True, exist_ok=True) so a non-existent
            # base_dir is auto-bootstrapped here (Story 2.4 AC-7).
            # F27: registry-api is a read-mostly + append-only-events service;
            # JSONL recovery is OWNED by the registry-state materializer
            # process (we do NOT call recover_all_logs here).
            writer = EventLogWriter(base_dir=base_dir, clock=clock)
            stack.push_async_callback(writer.close)
            app.state.writer = writer

            yield

    app = FastAPI(
        title="oh-my-bmad registry API",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Middlewares — Architecture line 213 order (request-id → idempotency-key
    # → actor-id). Starlette reverses add_middleware call order so we add in
    # reverse: last-added runs first.
    app.add_middleware(ActorIdMiddleware)
    app.add_middleware(IdempotencyKeyMiddleware, clock=clock)
    app.add_middleware(RequestIdMiddleware, clock=clock)

    # Exception handlers — RFC 7807 problem+json for all 4xx/5xx responses.
    # F6: handler signatures take ``exc: Exception`` and runtime-narrow, so
    # these registrations type-check cleanly under mypy --strict (no
    # ``# type: ignore`` needed).
    # F2+F3: register a generic Exception handler so unhandled errors return
    # problem+json 500 instead of FastAPI's plain text/plain default.
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_internal_error)

    # Routes — /v1 prefix applied here; handlers declare /tasks and /tasks/{id}.
    app.include_router(tasks_router, prefix="/v1")

    return app


__all__ = ["build_app"]
