"""HTTP middleware stack for registry-api (Story 2.9 AC-4 + Story 3.6 AC-1/AC-2).

Three class-based middlewares (subclassing ``BaseHTTPMiddleware``):

- ``RequestIdMiddleware``:      reads ``X-Request-ID`` header; validates against
                                the bare-UUIDv7 regex; generates via
                                ``new_request_id(clock=clock)`` if absent or
                                malformed; attaches to ``request.state.request_id``;
                                binds into structlog contextvars (Story 3.6 AC-1)
                                so downstream stdlib log records carry the
                                ``request_id`` field; unbinds in a ``try/finally``
                                so a uvicorn worker reused for a subsequent
                                request never observes the prior request's id;
                                echoes on response.
- ``IdempotencyKeyMiddleware``: reads ``Idempotency-Key`` header; generates via
                                ``new_idempotency_key(clock=clock)`` if absent
                                or malformed; attaches the key to
                                ``request.state.idempotency_key`` and an origin
                                flag to ``request.state.idempotency_key_generated``
                                (Story 3.6 AC-2). Echoes the key plus
                                ``X-Idempotency-Generated: true|false`` on every
                                response. Route-level dedup is owned by
                                ``routes/tasks.py`` via Story 2.13's
                                ``IdempotencyCacheStore.get_or_run``.
- ``ActorIdMiddleware``:        Phase 1 placeholder — hardcodes
                                ``request.state.actor_id = "http-api"``.
                                Real auth lands in Story 6.1+.

Middleware registration order in ``build_app`` (outermost → innermost in
execution order; Starlette reverses the add_middleware call order):
  app.add_middleware(ActorIdMiddleware)                      # added 1st → runs 3rd
  app.add_middleware(IdempotencyKeyMiddleware, clock=clock)  # added 2nd → runs 2nd
  app.add_middleware(RequestIdMiddleware, clock=clock)       # added 3rd → runs 1st

So incoming request flows: RequestId → IdempotencyKey → ActorId → handler.
"""

from __future__ import annotations

import logging
import re

import structlog
from events.clock import Clock
from events.ids import new_idempotency_key, new_request_id
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Bare UUIDv7 (no prefix) — matches new_request_id / new_idempotency_key output.
# Version nibble = 7, variant = 8/9/a/b. Same shape used by events.ids.
_UUIDV7_BARE_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_log = logging.getLogger("registry_api.adapters.middleware")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Read ``X-Request-ID`` header; validate UUIDv7; generate if absent/malformed.

    The generated/propagated value is echoed back as ``X-Request-ID`` on the
    response so callers can correlate requests without needing to send the header.

    F8: malformed inbound headers are rejected (with a warning log) rather than
    leaking caller-controlled strings into the event envelope's ``request_id``
    field — that field is constrained to the bare UUIDv7 regex by Story 2.1.
    """

    def __init__(self, app: ASGIApp, *, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID")
        if incoming and _UUIDV7_BARE_RE.match(incoming):
            request_id = incoming
        else:
            if incoming:
                # Malformed — log + regenerate. Truncate the received value to
                # 80 chars in the log to limit the size of malformed payloads.
                _log.warning(
                    "invalid X-Request-ID header; generating fresh",
                    extra={"received": incoming[:80]},
                )
            request_id = new_request_id(clock=self._clock)
        request.state.request_id = request_id
        # Story 3.6 AC-1: bind into structlog contextvars so any downstream log
        # record (stdlib bridge or structlog native) carries ``request_id``.
        # The unbind MUST run on every code path — even when ``call_next``
        # raises — otherwise a uvicorn worker reused for the next request
        # observes the previous request's id until its own RequestIdMiddleware
        # rebinds. The ``try/finally`` placement is load-bearing.
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        return response


class IdempotencyKeyMiddleware(BaseHTTPMiddleware):
    """Read ``Idempotency-Key`` header; generate if absent; attach to ``request.state``.

    This middleware reads/generates the key and makes it available to handlers
    via ``request.state.idempotency_key``. Story 3.6 AC-2 also records the
    *origin* of the key on ``request.state.idempotency_key_generated``: ``True``
    when the inbound header was missing or malformed (and a UUIDv7 was
    server-generated), ``False`` when the client supplied a valid one. The
    origin is echoed on every response via ``X-Idempotency-Generated``.

    Story 2.13: route-level dedup is wired in ``routes/tasks.py`` via
    ``IdempotencyCacheStore.get_or_run``. The route handler owns the
    ``X-Idempotency-Status`` header (values: ``applied`` for cache-miss,
    ``replayed`` for cache-hit). Endpoints that do NOT enforce dedup (e.g.
    GET routes) carry NO ``X-Idempotency-Status`` header — its absence is the
    "not enforced for this endpoint" signal.

    Cross-route dedup is route-scoped via Story 2.13's
    ``IdempotencyCacheStore.get_or_run``; multi-route enforcement is deferred
    to Story 6.4 (HTTP API tier middleware).
    """

    def __init__(self, app: ASGIApp, *, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Story 2.13 review M1: validate inbound Idempotency-Key against the
        # bare-UUIDv7 regex. A 10MB header would otherwise be a trivial DoS
        # vector + arbitrary client strings would land in SQLite cache PK
        # column. Mirrors RequestIdMiddleware's validation pattern.
        incoming = request.headers.get("Idempotency-Key")
        if incoming and _UUIDV7_BARE_RE.match(incoming):
            idempotency_key = incoming
            generated = False
        else:
            if incoming:
                # Truncate the received value to 80 chars in the log to limit
                # the size of malformed payloads.
                _log.warning(
                    "invalid Idempotency-Key header; generating fresh",
                    extra={"received": incoming[:80]},
                )
            idempotency_key = new_idempotency_key(clock=self._clock)
            generated = True
        request.state.idempotency_key = idempotency_key
        # Story 3.6 AC-2: origin flag for handler / exception-handler
        # consumption (errors.py uses it to populate the ProblemDetails
        # ``extensions`` nudge on mutating-method 4xx/5xx responses).
        request.state.idempotency_key_generated = generated
        response = await call_next(request)
        response.headers["Idempotency-Key"] = idempotency_key
        response.headers["X-Idempotency-Generated"] = "true" if generated else "false"
        return response


class ActorIdMiddleware(BaseHTTPMiddleware):
    """Phase 1 placeholder: hardcode ``request.state.actor_id = "http-api"``.

    Real actor identity (Telegram user ID, console operator, etc.) lands in
    Story 6.1+ when authentication / policy enforcement is added. For Phase 1
    all requests are treated as coming from the generic HTTP operator.

    TODO(Story 6.1+): replace hardcoded actor_id with real auth token parsing.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.actor_id = "http-api"
        return await call_next(request)


__all__ = [
    "ActorIdMiddleware",
    "IdempotencyKeyMiddleware",
    "RequestIdMiddleware",
]
