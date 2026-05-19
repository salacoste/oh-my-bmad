"""FastAPI application factory for the β metrics-subscriber (Story 10.3).

``build_app(*, settings) -> FastAPI`` — factory mirroring
``registry_api.app.build_app`` so the two FastAPI services share a
single startup / teardown pattern under shared uvicorn.

Wires (AC2 / AC3 / AC4):

  - **Per-app** :class:`prometheus_client.CollectorRegistry` — NOT the
    module-level ``prometheus_client.REGISTRY``.  Test isolation
    (Story 10.2 P3-L3 lesson): a fresh registry per app instance means
    cross-test metric value leak is impossible.
  - **Async lifespan** via :func:`asynccontextmanager`: spawns the
    Story 10.2 ``run_subscriber`` tail loop as a background asyncio
    task; tears it down on shutdown through :class:`AsyncExitStack`
    so each cleanup runs independently.  Lifespan-failure exceptions
    are mapped to the Story 10.2 exit-code matrix (1/2/3) via typed
    exception classes (Story 10.2 P3-H1 lesson — no substring-match).
  - **GET /metrics** returning ``generate_latest(registry)`` with
    ``Content-Type: CONTENT_TYPE_LATEST``.  Exception handler captures
    collector failures into a 500 with structured log
    ``metrics_subscriber_endpoint_failure`` — no traceback to client.
  - **GET /healthz** returning ``{"status": "ok"}`` — minimal JSON
    response that lets compose ``service_healthy`` probes (Story 10.6
    scope) gate the service without scraping the Prometheus surface.

Internal-only enforcement (P2-I5 — FR61 / NFR-O10)
---------------------------------------------------
This endpoint MUST NOT be exposed via ``ports:`` in
``docker-compose.yml``; it is reachable only through the internal
docker network.  Operator scrapes go through an SSH-tunneled
``curl http://metrics-subscriber:9090/metrics`` (FR61) or a co-
located Prometheus instance on the same docker host.  The
compose-layer enforcement lives in Story 10.6; here we additionally
log a startup WARNING heuristic
(``metrics_subscriber_bind_external_interface_suspected``) if
``settings.metrics_host`` is a concrete IP that is neither loopback
(``127.0.0.1`` / ``::1``) nor the wildcard bind-all (``0.0.0.0`` /
``::``).  ``0.0.0.0`` intentionally binds every container interface
and IS externally reachable within the docker network — the real
security boundary is compose-level network scoping, NOT this
heuristic.  The check is a sanity guard against typos (e.g. a
specific external IP like ``192.0.2.1``), not a security gate.

Cross-thread surprise (Story 10.3 risk-flag — applied)
------------------------------------------------------
``prometheus_client`` gauges and counters are thread-safe via
internal ``threading.Lock``.  Registries are NOT safe for concurrent
*registration* but ARE safe for concurrent ``.collect()``.  This
factory constructs the registry once in lifespan startup BEFORE any
HTTP request handler is attached, so the unsafe window never overlaps
with concurrent access.  Subsequent ``.inc()`` / ``.set()`` calls from
the tail-loop task and ``generate_latest()`` from a ``/metrics``
request handler can therefore interleave safely.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
import uvicorn
from events.errors import CursorSchemaVersionError, ParseSkipThresholdExceeded
from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from starlette.responses import JSONResponse, Response

from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.metrics import build_collectors
from metrics_subscriber.cursor import CursorLockUnsupportedFilesystemError

_log = structlog.get_logger(__name__)


# Story 10.2 exit-code matrix preserved: 0 graceful, 1 concurrent-start /
# unsupported-FS, 2 cursor schema_version refused, 3 corrupt-region.  The
# FastAPI lifespan path surfaces these via ``app.state.exit_code`` (read
# back in ``__main__.py`` after ``uvicorn.run`` returns) and toggles
# ``app.state.uvicorn_server.should_exit = True`` to trigger shutdown.
_EXIT_GRACEFUL = 0
_EXIT_CONCURRENT_OR_UNSUPPORTED_FS = 1
_EXIT_CURSOR_SCHEMA_VERSION = 2
_EXIT_CORRUPT_REGION = 3


def _is_external_bind_heuristic(host: str) -> bool:
    """AC9 — return True iff *host* looks like a concrete external IP.

    Heuristic only — NOT a security gate.  Real reachability is gated
    at the compose layer (P2-I5, Story 10.6).  Returns True for any
    parsable IP address that is neither loopback nor unspecified
    (``0.0.0.0`` / ``::``).  Hostnames (``metrics-subscriber``,
    ``localhost``) parse as IP-failures and are treated as non-
    external — they resolve through docker DNS.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_unspecified)


async def _drain_tail_task(task: asyncio.Task[int], stop_event: asyncio.Event) -> None:
    """Lifespan teardown: signal stop, await the tail task to drain.

    Wrapped in :func:`contextlib.suppress` for :class:`asyncio.CancelledError`
    so a SIGTERM-during-shutdown does not raise out of the lifespan
    finalisation path (the cursor is already persisted by the tail
    loop's own ``finally`` block; the task's exit code is informational
    for the caller in ``__main__.py``).
    """
    stop_event.set()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def build_app(*, settings: MetricsSubscriberSettings) -> FastAPI:
    """Build and return the wired-up FastAPI application.

    The factory does not start uvicorn — that is the caller's job in
    ``__main__.py``.  Tests construct the app directly and exercise
    the lifespan via :class:`asgi_lifespan.LifespanManager` + an
    in-process ``httpx.AsyncClient`` with ``ASGITransport``.

    Args:
        settings: Validated :class:`MetricsSubscriberSettings` from env.
            Read once at factory time; the same instance is shared
            with ``run_subscriber`` inside lifespan startup.

    Returns:
        Fully configured :class:`FastAPI` instance ready for
        ``uvicorn.run(app, host=..., port=..., log_config=None,
        access_log=False)`` per the registry-api pattern.
    """

    # Per-app registry constructed once at factory time.  This MUST
    # NOT be the module-level ``prometheus_client.REGISTRY``: a global
    # would silently accumulate collectors across test runs and break
    # ``test_metrics_endpoint_returns_valid_exposition`` deterministically
    # on the second invocation (Duplicated metric registration error).
    registry = CollectorRegistry()
    metrics_state = build_collectors(registry)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Spawn the Story 10.2 tail loop; tear down via AsyncExitStack."""
        # AC9 bind-warning heuristic.  Logged once at startup — operator
        # can react before the service accepts its first scrape.
        if _is_external_bind_heuristic(settings.metrics_host):
            _log.warning(
                "metrics_subscriber_bind_external_interface_suspected",
                host=settings.metrics_host,
                note=(
                    "metrics_host parses as a concrete external IP "
                    "(neither loopback nor unspecified).  P2-I5 forbids "
                    "exposing /metrics on a public ingress; verify the "
                    "compose-layer network scoping (Story 10.6) before "
                    "starting in production."
                ),
            )

        # Per-app state — stored on ``app.state`` so route handlers
        # observe the same registry instance the tail loop mutates.
        app.state.settings = settings
        app.state.registry = registry
        app.state.metrics = metrics_state
        app.state.exit_code = _EXIT_GRACEFUL
        # ``uvicorn_server`` is populated by ``__main__.py`` BEFORE
        # ``server.serve()`` so the lifespan-failure path can flip
        # ``should_exit``.  In test contexts (``LifespanManager`` +
        # ``httpx.AsyncClient``) it remains ``None`` because there is
        # no uvicorn Server to signal.
        app.state.uvicorn_server = getattr(app.state, "uvicorn_server", None)

        stop_event = asyncio.Event()
        # Import inside lifespan to avoid circular import at module
        # load time: ``run_subscriber`` lives in
        # ``metrics_subscriber.__main__`` which is imported by tests
        # that don't need the FastAPI surface.
        from metrics_subscriber.__main__ import (  # noqa: PLC0415 — circular-import guard
            run_subscriber,
        )

        tail_task: asyncio.Task[int] = asyncio.create_task(
            run_subscriber(settings, stop_event=stop_event, metrics_state=metrics_state),
            name="metrics_subscriber_tail",
        )
        # Story 10.3 pass-1 P1-H4: expose the task on app.state so the
        # ``/healthz`` handler can probe ``task.done() / task.exception()``
        # to discriminate "tail running" vs "tail crashed but exit code
        # not yet propagated".
        app.state.tail_task = tail_task

        # Wire the tail task's exit code → app.state + uvicorn shutdown.
        # ``isinstance`` on the typed exception is the canonical path
        # (Story 10.2 P3-H1 lesson — no substring-match).  Non-zero
        # exit codes from ``run_subscriber`` itself (returned via
        # ``return 1/2/3``) are mirrored onto ``app.state.exit_code``
        # so ``__main__.py`` can surface them to the process exit
        # value after ``uvicorn.run`` returns.
        def _on_tail_done(task: asyncio.Task[int]) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                rc = task.result()
                if rc != _EXIT_GRACEFUL:
                    _log.error(
                        "metrics_subscriber_tail_task_exited_nonzero",
                        exit_code=rc,
                    )
                    app.state.exit_code = rc
                    _request_uvicorn_shutdown(app)
                return
            # Lifespan exception path: typed-exception discrimination.
            if isinstance(exc, CursorSchemaVersionError):
                app.state.exit_code = _EXIT_CURSOR_SCHEMA_VERSION
                _log.critical(
                    "metrics_subscriber_lifespan_failure",
                    error_type=type(exc).__name__,
                    exit_code=_EXIT_CURSOR_SCHEMA_VERSION,
                )
            elif isinstance(exc, ParseSkipThresholdExceeded):
                app.state.exit_code = _EXIT_CORRUPT_REGION
                _log.critical(
                    "metrics_subscriber_lifespan_failure",
                    error_type=type(exc).__name__,
                    exit_code=_EXIT_CORRUPT_REGION,
                )
            elif isinstance(exc, CursorLockUnsupportedFilesystemError | BlockingIOError):
                app.state.exit_code = _EXIT_CONCURRENT_OR_UNSUPPORTED_FS
                _log.critical(
                    "metrics_subscriber_lifespan_failure",
                    error_type=type(exc).__name__,
                    exit_code=_EXIT_CONCURRENT_OR_UNSUPPORTED_FS,
                )
            else:
                # Unclassified — log the typed exception and re-raise
                # the contract: any non-typed lifespan failure indicates
                # a defect in the subscriber code path.  Exit code 1 is
                # the conservative default; operators can correlate
                # against the structured log to diagnose.
                app.state.exit_code = _EXIT_CONCURRENT_OR_UNSUPPORTED_FS
                _log.critical(
                    "metrics_subscriber_lifespan_failure_unclassified",
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    exit_code=_EXIT_CONCURRENT_OR_UNSUPPORTED_FS,
                )
            _request_uvicorn_shutdown(app)

        async with AsyncExitStack() as stack:
            # Each callback unwinds independently (the registry-api
            # F5+F14 pattern): a failure in the tail-drain does not
            # leak the registry / metrics state.
            stack.push_async_callback(_drain_tail_task, tail_task, stop_event)
            # Story 10.3 pass-1 P1-M4: register the done-callback INSIDE
            # the AsyncExitStack scope (post-stack-entry) so the
            # callback can only fire while the lifespan is fully wired
            # up.  If ``tail_task`` completes between ``create_task`` and
            # this line (e.g. zero-length event log + immediate
            # stop_event), the callback registers on the already-done
            # task and asyncio schedules it on the next loop pass with
            # the AsyncExitStack still active — eliminating the
            # ms-level race window where ``_on_tail_done`` would have
            # called ``server.should_exit = True`` before lifespan
            # finished setting up.
            tail_task.add_done_callback(_on_tail_done)
            yield

    app = FastAPI(
        title="metrics-subscriber",
        version="0.1.0",
        lifespan=_lifespan,
    )

    @app.get("/metrics", response_class=Response)
    async def metrics(request: Request) -> Response:
        """AC4 — Prometheus text-exposition over the per-app registry.

        Reads the registry instance stashed on ``app.state`` (the same
        instance the tail loop mutates) so HTTP scrapes always see the
        most recent gauge / counter values.
        """
        registry_ = request.app.state.registry
        body = generate_latest(registry_)
        return Response(content=body, media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        """AC2 — JSON health probe for compose ``service_healthy``.

        Wired by Story 10.6 in docker-compose.yml; here we just expose
        the route so probes have a non-exposition surface to hit
        without scraping ``/metrics`` (a hot path under Prometheus).

        Story 10.3 pass-1 P1-H4: the probe is NOT hollow.  If the
        background tail task has crashed (``_on_tail_done`` already
        fired and set a non-zero ``app.state.exit_code``) OR the task
        finished with an exception, the probe returns
        ``503 {"status": "degraded", "reason": "tail_task_failed",
        "exit_code": <code>}`` so the compose ``service_healthy``
        probe fails fast instead of passing while the subscriber is
        already dead-walking toward uvicorn shutdown.
        """
        exit_code = getattr(request.app.state, "exit_code", 0)
        tail_task: asyncio.Task[int] | None = getattr(request.app.state, "tail_task", None)
        tail_task_crashed = (
            tail_task is not None and tail_task.done() and tail_task.exception() is not None
        )
        if exit_code != 0 or tail_task_crashed:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "reason": "tail_task_failed",
                    "exit_code": int(exit_code) if isinstance(exit_code, int) else 1,
                },
            )
        return JSONResponse(status_code=200, content={"status": "ok"})

    @app.exception_handler(Exception)
    async def _on_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """AC4 — collector failures surface as 500 with structured log.

        FastAPI's default ``Exception`` handler returns plain text +
        traceback.  Per AC4 we instead emit a JSON envelope + structured
        ``metrics_subscriber_endpoint_failure`` log so dashboards can
        alert on scrape failure without exposing internals to the
        scraping client.
        """
        _log.error(
            "metrics_subscriber_endpoint_failure",
            path=request.url.path,
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error"},
        )

    return app


def _request_uvicorn_shutdown(app: FastAPI) -> None:
    """Toggle ``uvicorn.Server.should_exit`` when a server is attached.

    The ``__main__.py`` entrypoint stashes the ``uvicorn.Server``
    instance on ``app.state.uvicorn_server`` before calling
    ``server.serve()``.  Test contexts (LifespanManager) leave the
    attribute ``None`` and this becomes a no-op — the test owns its
    own teardown via the async-with block.
    """
    server: uvicorn.Server | None = getattr(app.state, "uvicorn_server", None)
    if server is not None:
        server.should_exit = True


__all__ = ["build_app"]

# Story 10.3 pass-1 P1-L2: the ``_P2_I5_INTERNAL_ONLY`` dict that
# previously lived below ``__all__`` was dead code — the P2-I5 grep
# anchor is satisfied by the module-level docstring (lines 27-43).
# Deleted to remove a maintenance landmine for future contributors.
# Grep ``"P2-I5" services/metrics-subscriber/src/metrics_subscriber/app/main.py``
# still finds the docstring header.
