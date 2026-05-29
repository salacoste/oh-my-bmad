"""FastAPI application factory for registry-api (Story 2.9 AC-1).

``build_app(*, base_dir, db_url, clock) -> FastAPI`` — factory that wires:
  - Async lifespan: creates ``EventLogWriter`` + read-only SQLite engine on
    startup; tears them down on shutdown via ``AsyncExitStack`` so each
    cleanup runs independently regardless of others' exceptions; stores
    everything on ``app.state``.
  - Middleware stack: ``TraceIdMiddleware`` → ``RequestIdMiddleware`` →
    ``IdempotencyKeyMiddleware`` → ``ActorIdMiddleware`` →
    ``TierEnforcementMiddleware`` (Architecture line 213 order extended by
    Story 9.2 — ``TraceIdMiddleware`` is outermost so ``trace_id`` binds to
    the structlog context before any inner middleware runs).
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

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import anthropic
import cachetools
from events.clock import Clock
from events.envelope import ActorKind  # noqa: IMP001 — services→packages allowed
from events.errors import CapabilityDenied
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from idempotency import IdempotencyCacheStore
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed per AC-16
    EventLogWriter,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine,
    get_session,
)
from starlette.exceptions import HTTPException

from registry_api.adapters.errors import (
    handle_capability_denied,
    handle_http_exception,
    handle_internal_error,
    handle_validation_error,
)
from registry_api.adapters.key_rotation import detect_and_emit_key_rotation
from registry_api.adapters.middleware import (
    ActorIdMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
    TierEnforcementMiddleware,
    TraceIdMiddleware,
)
from registry_api.routes.approvals import (
    router as approvals_router,
)
from registry_api.routes.decisions import (
    router as decisions_router,
)
from registry_api.routes.digest import (
    router as digest_router,
)
from registry_api.routes.events import (
    router as events_router,
)
from registry_api.routes.tasks import (
    ResponseSlot,
    ResponseSlotCache,
)
from registry_api.routes.tasks import (
    router as tasks_router,
)
from registry_api.routes.trace import (
    router as trace_router,
)
from registry_api.settings import ApprovalSigningSettings

# Idempotency-cache TTL — 7 days per FR28 (Architecture line 205). The cache is
# created by the registry-state schema (``IdempotencyCache`` ORM model) and
# written to by ``IdempotencyCacheStore`` from this service. The cache is the
# ONLY SQLite write surface registry-api owns; tasks/events/sessions remain
# materialized exclusively by the registry-state subscriber (FR26).
_IDEMPOTENCY_TTL_SECONDS = 604800

# Bound on the in-process side-channel response cache. Mirrors
# ``IdempotencyCacheStore`` defaults (``max_in_process=100_000``) so an attacker
# cannot OOM the process by submitting 10M unique idempotency-keys. Eviction is
# TTL-driven; entries past 7d (the FR28 cache TTL) are dropped lazily.
#
# ``ResponseSlot`` and ``ResponseSlotCache`` are defined in ``routes/tasks.py``
# and re-exported here so callers can import them from ``registry_api.app``;
# we put them in ``routes`` to avoid a circular import (this module imports
# the tasks router).
_RESPONSE_CACHE_MAX = 100_000


def build_app(
    *,
    base_dir: Path,
    db_url: str,
    clock: Clock,
    actor_kind: ActorKind = "operator",
    signing_settings: ApprovalSigningSettings | None = None,
) -> FastAPI:
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
        actor_kind: Actor kind for tier enforcement (Story 6.3). Phase 1
                  defaults to ``"operator"`` — the HTTP API is operator-facing.
        signing_settings: Optional :class:`ApprovalSigningSettings` carrying
                  the operator HMAC key (Story 11.1 / FR64). When ``None``,
                  the factory constructs one via ``.from_env()`` (reads
                  ``OPERATOR_HMAC_KEY``). Tests inject explicit instances
                  to avoid env-var coupling. Hot-reload is NOT supported —
                  the key is read once here and survives until process
                  restart (Story 11.5 will add rotation).

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

        Story 2.13: also constructs an ``IdempotencyCacheStore`` backed by a
        SEPARATE writable engine pointing at the same SQLite file. The cache
        owns its own table (``idempotency_cache``) per FR28 / Architecture
        line 205; the read-only engine above continues to gate
        tasks/events/sessions reads.

        Architectural risk (review M8 — DOCUMENTED, follow-up flagged):
            registry-state owns the writable engine for tasks/events/sessions
            (FR26 single-writer). Story 2.13 introduces a SECOND writable
            engine in registry-api targeting the same SQLite file. SQLite WAL
            permits multiple readers AND a single writer at any given moment
            — at the database level, NOT per table. Under sustained write
            contention (e.g. high-RPS dedup hits + concurrent materializer
            commits) the system will surface ``OperationalError: database is
            locked``.

            This is acceptable for Phase 1 (low RPS, idempotency cache
            writes are sub-millisecond) but a follow-up story should
            separate the idempotency cache into its own SQLite file. Until
            then operators monitoring registry-api error rates should treat
            ``database is locked`` as a load-shedding signal.
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

            # Story 2.13: writable engine for the idempotency cache. The cache
            # writes to its own ``idempotency_cache`` table; tasks/events/
            # sessions are still materialized solely by registry-state (FR26).
            # ``check_single_writer`` already excludes ``packages/idempotency/``
            # from its scan, so this writable surface does not violate FR26.
            cache_engine = create_engine(db_url, read_only=False)
            stack.push_async_callback(cache_engine.dispose)
            cache_session_maker = get_session(cache_engine)
            idempotency_cache = IdempotencyCacheStore(
                session_maker=cache_session_maker,
                clock=clock,
                ttl_seconds=_IDEMPOTENCY_TTL_SECONDS,
            )
            app.state.idempotency_cache = idempotency_cache
            # In-memory side-channel: maps idempotency_key → ``ResponseSlot``
            # (canonical JSON body + task_id). ``IdempotencyCacheStore.get_or_run``
            # only stores the ``result_event_id`` per Story 2.7 AC-1; the
            # response body is captured here so byte-identity holds across
            # replays without re-serializing the Pydantic model (which could
            # introduce key-order differences).
            #
            # Bounding (review C3): ``cachetools.TTLCache`` with maxsize +
            # 7-day TTL mirroring ``IdempotencyCacheStore`` so a sustained
            # stream of unique idempotency-keys cannot OOM the process. Both
            # caches share the same eviction policy; a key dropped from one
            # but not the other is acceptable (the route handles each branch).
            #
            # Population safety (review C1): the slot is written INSIDE the
            # factory closure in routes/tasks.py — that closure runs under
            # ``IdempotencyCacheStore.get_or_run``'s per-key lock, so loser
            # callers (concurrent same-key requests) cannot observe a
            # half-populated cache. See routes/tasks.py post_tasks docstring.
            response_body_cache: ResponseSlotCache = cachetools.TTLCache(
                maxsize=_RESPONSE_CACHE_MAX,
                ttl=_IDEMPOTENCY_TTL_SECONDS,
            )
            app.state.idempotency_response_cache = response_body_cache

            # Story 11.1 (FR64 / NFR-S10): load approval-signing settings.
            # If the caller passed an explicit instance (tests do this), use
            # it directly; otherwise read OPERATOR_HMAC_KEY from the env.
            # Hot-reload is NOT supported — operators must restart to pick
            # up a rotated key (Story 11.5 will formalize the rotation flow
            # with a key.rotated audit event).
            resolved_signing = (
                signing_settings
                if signing_settings is not None
                else ApprovalSigningSettings.from_env()
            )
            app.state.signing_settings = resolved_signing

            # Writer last — F13 note: EventLogWriter.__init__ calls
            # base_dir.mkdir(parents=True, exist_ok=True) so a non-existent
            # base_dir is auto-bootstrapped here (Story 2.4 AC-7).
            # F27: registry-api is a read-mostly + append-only-events service;
            # JSONL recovery is OWNED by the registry-state materializer
            # process (we do NOT call recover_all_logs here).
            writer = EventLogWriter(base_dir=base_dir, clock=clock)
            stack.push_async_callback(writer.close)
            app.state.writer = writer

            # Story 7.3: Anthropic client for LLM-powered event digests (FR5).
            # Graceful degradation: if no key, client is None and the digest
            # endpoint returns a raw-event fallback instead of calling the LLM.
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if api_key:
                llm_client = anthropic.AsyncAnthropic(api_key=api_key)
                stack.push_async_callback(llm_client.close)
                app.state.anthropic_client = llm_client
            else:
                app.state.anthropic_client = None

            # Story 11.5 (FR65a) — detect HMAC key rotation BEFORE serving
            # requests. Synchronous + fail-loud (D3): if event emission
            # fails (storage I/O, EventLogWriter poisoned), this raises and
            # registry-api startup aborts. The audit invariant supersedes
            # uptime — operators address the storage problem before any
            # approval traffic flows. Idempotent: if the key fingerprint
            # matches the last-known value in registry-state, this is a
            # no-op (no event emitted).
            await detect_and_emit_key_rotation(
                current_key=resolved_signing.operator_hmac_key,
                session_maker=session_maker,
                event_log_writer=writer,
                clock=clock,
            )

            yield

    app = FastAPI(
        title="oh-my-bmad registry API",
        version="0.3.0",
        lifespan=lifespan,
    )

    # Middlewares — Architecture line 213 order extended by Story 9.2:
    # trace-id → request-id → idempotency-key → actor-id → tier-enforcement.
    # Starlette reverses add_middleware call order so we add in reverse:
    # last-added runs FIRST in execution flow (outermost). ``TraceIdMiddleware``
    # is OUTERMOST so the structlog ``trace_id`` bind is established before
    # ``RequestIdMiddleware`` runs — every inner log record + every emitted
    # ``EventEnvelope`` then carries the parent ``trace_id`` correlation
    # alongside the per-request ``request_id`` (FR58 HTTP ingress).
    app.add_middleware(TierEnforcementMiddleware, actor_kind=actor_kind)
    app.add_middleware(ActorIdMiddleware)
    app.add_middleware(IdempotencyKeyMiddleware, clock=clock)
    app.add_middleware(RequestIdMiddleware, clock=clock)
    app.add_middleware(TraceIdMiddleware, clock=clock)

    # Exception handlers — RFC 7807 problem+json for all 4xx/5xx responses.
    # Story 6.3: CapabilityDenied → 403 Forbidden.
    app.add_exception_handler(CapabilityDenied, handle_capability_denied)
    # F6: handler signatures take ``exc: Exception`` and runtime-narrow, so
    # these registrations type-check cleanly under mypy --strict (no
    # ``# type: ignore`` needed).
    # F2+F3: register a generic Exception handler so unhandled errors return
    # problem+json 500 instead of FastAPI's plain text/plain default.
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_internal_error)

    # Story 11.3.7 (AC5 / FR17 minimal) — /v1/health liveness probe.
    # The S-4 separability test (and the telegram-gateway registry_client's
    # TODO(story-TBD)) reference this endpoint; previously absent on the
    # server side (only telegram-gateway exposed its own /v1/health). FR17
    # eventually expands this to registry status / worker status / queue
    # depth / platform version — for now we return a stable liveness shape
    # so external probes (S-4 harness, future ping commands) see 200 OK.
    # Declared inline (no router) because the handler needs no DB access and
    # no per-route dependencies — just confirms the FastAPI app is serving.
    # NOTE: this is a LIVENESS probe only — the four middleware registered
    # above (TraceId/RequestId/IdempotencyKey/ActorId) DO apply to this route
    # since they're mounted via ``app.add_middleware``; they only set
    # ``request.state`` + response headers so they do not gate the 200. The
    # endpoint does NOT exercise registry-state SQLite, EventLogWriter, or
    # any downstream dependency, so a green response only proves "the HTTP
    # server is up", not "the spine is healthy". FR17 may add a separate
    # ``/v1/ready`` readiness probe later for kubernetes-style coupling.
    @app.get("/v1/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — returns 200 when registry-api is serving."""
        return {"status": "ok", "service": "registry-api"}

    # Routes — /v1 prefix applied here; handlers declare /tasks and /tasks/{id}.
    app.include_router(tasks_router, prefix="/v1")
    # Story 7.3 — LLM digest endpoint for task events (FR5).
    app.include_router(digest_router, prefix="/v1")
    # Story 7.5 — raw event tail for debugging (FR6).
    app.include_router(events_router, prefix="/v1")
    # Story 6.4 — decisions sub-resource on tasks.
    app.include_router(decisions_router, prefix="/v1")
    # Story 9.7 / FR59a — /trace/{trace_id} operator query.
    app.include_router(trace_router, prefix="/v1")
    # Story 11.3 / FR63 — /approvals/inbox endpoints for operator-facing
    # pinned-thread routing (telegram-gateway emits via POST; clawhip-daemon
    # reads via GET to decide whether to route ``task.approval_requested``
    # to the operator's pinned Forum-Topic inbox).
    app.include_router(approvals_router, prefix="/v1")

    return app


__all__ = ["ResponseSlot", "ResponseSlotCache", "build_app"]
