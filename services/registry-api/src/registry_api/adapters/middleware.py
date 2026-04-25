"""HTTP middleware stack for registry-api (Story 2.9 AC-4).

Three class-based middlewares (subclassing ``BaseHTTPMiddleware``):

- ``RequestIdMiddleware``:      reads ``X-Request-ID`` header; generates via
                                ``new_request_id(clock=clock)`` if absent; attaches
                                to ``request.state.request_id``; echoes on response.
- ``IdempotencyKeyMiddleware``: reads ``Idempotency-Key`` header; generates via
                                ``new_idempotency_key(clock=clock)`` if absent;
                                attaches to ``request.state.idempotency_key``. Dedup
                                NOT enforced (deferred to Story 3.6 per 2.7 AC-12).
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

from events.clock import Clock
from events.ids import new_idempotency_key, new_request_id
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Read ``X-Request-ID`` header; generate if absent; attach to ``request.state``.

    The generated/propagated value is echoed back as ``X-Request-ID`` on the
    response so callers can correlate requests without needing to send the header.
    """

    def __init__(self, app: ASGIApp, *, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming if incoming else new_request_id(clock=self._clock)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class IdempotencyKeyMiddleware(BaseHTTPMiddleware):
    """Read ``Idempotency-Key`` header; generate if absent; attach to ``request.state``.

    Dedup enforcement is explicitly deferred to Story 3.6. This middleware
    only reads/generates the key and makes it available to handlers via
    ``request.state.idempotency_key``.

    TODO(Story 3.6): wire ``IdempotencyCacheStore.get_or_run`` here to enforce
    at-most-once semantics for mutating endpoints.
    """

    def __init__(self, app: ASGIApp, *, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("Idempotency-Key")
        idempotency_key = incoming if incoming else new_idempotency_key(clock=self._clock)
        request.state.idempotency_key = idempotency_key
        return await call_next(request)


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
