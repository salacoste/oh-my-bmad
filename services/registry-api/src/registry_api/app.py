"""FastAPI application factory for registry-api (Story 2.9 AC-1).

``build_app(*, base_dir, db_url, clock) -> FastAPI`` — factory that wires:
  - Async lifespan: creates ``EventLogWriter`` + read-only SQLite engine on
    startup; tears them down on shutdown; stores on ``app.state``.
  - Middleware stack: ``RequestIdMiddleware`` → ``IdempotencyKeyMiddleware``
    → ``ActorIdMiddleware`` (Architecture line 213 order).
  - Exception handlers: RFC 7807 problem+json for ``HTTPException`` and
    ``RequestValidationError``.
  - Routes: ``/v1/tasks`` (POST + GET) via ``tasks_router``.

Design notes:
  - registry-api appends events directly via ``EventLogWriter`` (NOT via
    clawhip-bridge MCP). The MCP server is for agent consumers (workers,
    orchestrator) over stdio; registry-api is a service process.
  - ``recover_all_logs`` runs on startup as a defensive idempotent step; it
    does not conflict with the materializer's own recovery.
  - The engine is read-only (``create_engine(db_url, read_only=True)``).
    Belt-and-braces with FR26 single-writer CI gate.
  - ``app.state.actor_id`` is set to ``"http-api"`` here so the POST handler
    can read it via ``app.state.actor_id``; the ActorIdMiddleware sets
    ``request.state.actor_id`` per-request which is the canonical access path
    inside handlers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from events.clock import Clock
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed per AC-16
    EventLogWriter,
    recover_all_logs,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine,
)
from starlette.exceptions import HTTPException

from registry_api.adapters.errors import handle_http_exception, handle_validation_error
from registry_api.adapters.middleware import (
    ActorIdMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
)
from registry_api.routes.tasks import router as tasks_router


def build_app(*, base_dir: Path, db_url: str, clock: Clock) -> FastAPI:
    """Build and return the wired-up FastAPI application.

    Args:
        base_dir: Root directory for JSONL event log files.
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
        """Async lifespan handler — startup + shutdown resource management."""
        # ------ Startup ------
        writer = EventLogWriter(base_dir=base_dir, clock=clock)
        # Defensive recovery: trim partial tails left by a previous crash.
        # Idempotent — safe to run even if the materializer also recovers.
        await recover_all_logs(base_dir)
        engine = create_engine(db_url, read_only=True)

        app.state.writer = writer
        app.state.engine = engine
        app.state.clock = clock
        # Phase 1 actor identity — accessible from handler via app.state.
        # ActorIdMiddleware sets request.state.actor_id per-request; this
        # mirrors it on app.state for convenience in the POST handler.
        app.state.actor_id = "http-api"

        try:
            yield
        finally:
            # ------ Shutdown ------
            await writer.close()
            await engine.dispose()

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
    app.add_exception_handler(HTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]

    # Routes — /v1 prefix applied here; handlers declare /tasks and /tasks/{id}.
    app.include_router(tasks_router, prefix="/v1")

    return app


__all__ = ["build_app"]
