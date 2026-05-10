"""HTTP middleware stack for registry-api (Story 2.9 AC-4 + Story 3.6 AC-1/AC-2 + Story 6.3).


Four class-based middlewares (subclassing ``BaseHTTPMiddleware``):

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
- ``TierEnforcementMiddleware``: enforces capability tiers on mutating routes
                                using ``check_tier`` from the capabilities
                                package (Story 6.3). Builds a ``CallerContext``
                                from the configured ``actor_kind`` and the
                                ``request.state.actor_id`` set by
                                ``ActorIdMiddleware``; attaches it to
                                ``request.state.caller_context``; short-circuits
                                with 403 on ``CapabilityDenied``.

Middleware registration order in ``build_app`` (outermost → innermost in
execution order; Starlette reverses the add_middleware call order):
  app.add_middleware(TierEnforcementMiddleware, ...)        # runs 4th (innermost)
  app.add_middleware(ActorIdMiddleware)                      # runs 3rd
  app.add_middleware(IdempotencyKeyMiddleware, clock=clock)  # runs 2nd
  app.add_middleware(RequestIdMiddleware, clock=clock)       # runs 1st (outermost)

So incoming request flows: RequestId → IdempotencyKey → ActorId → TierEnforcement → handler.
"""

from __future__ import annotations

import logging
import re
from types import MappingProxyType

import structlog
from capabilities import CallerContext, Tier, check_tier  # noqa: IMP001 — services→packages allowed
from events.clock import Clock
from events.envelope import ActorKind  # noqa: IMP001 — services→packages allowed
from events.errors import CapabilityDenied
from events.ids import new_idempotency_key, new_request_id
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# Story 3.6 M5: single source of truth for the mutation-method set, shared
# with ``adapters/errors.py``. Importing keeps the constant in one place
# rather than duplicating the literal in two files.
from registry_api.adapters.errors import _MUTATING_METHODS as _MUTATING_METHODS
from registry_api.adapters.errors import _PROBLEM_MEDIA_TYPE as _PROBLEM_MEDIA_TYPE
from registry_api.adapters.errors import _build_idempotency_extensions

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
        # Story 3.6 M5: only emit the ``X-Idempotency-Generated`` header on
        # mutation-method responses. On read paths (GET / HEAD / OPTIONS)
        # the header is meaningless and contradicted the AC-3 mutation-method
        # gate the team applied for the JSON ``extensions`` nudge — drop it
        # from non-mutating responses for consistency.
        if request.method.upper() in _MUTATING_METHODS:
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


# Phase 1 route-to-tier mapping (Story 6.3). Story 6.4 adds Tier-2 entries
# when the /decisions endpoint lands. Keys are ``"METHOD /path/prefix"`` —
# matched via startswith so ``"POST /v1/tasks"`` covers both
# ``/v1/tasks`` and ``/v1/tasks/{id}``.  Frozen via MappingProxyType so
# tests cannot accidentally leak mutations between runs.
ROUTE_TIER_MAP: MappingProxyType[str, Tier] = MappingProxyType({
    "POST /v1/tasks": Tier.ONE,
})


class TierEnforcementMiddleware(BaseHTTPMiddleware):
    """Enforce capability tiers on mutating HTTP routes (Story 6.3).

    For each incoming request:
      1. Skip tier check on read methods (GET/HEAD/OPTIONS).
      2. For mutating methods, look up the route in ``ROUTE_TIER_MAP``.
      3. If a tier is required, build a ``CallerContext`` from the configured
         ``actor_kind`` and ``request.state.actor_id``, then call
         ``check_tier``.
      4. On success, attach the ``CallerContext`` to
         ``request.state.caller_context`` and delegate to the handler.
      5. On ``CapabilityDenied``, short-circuit with RFC 7807 403.

    Must run AFTER ``ActorIdMiddleware`` in the execution order so
    ``request.state.actor_id`` is populated.
    """

    def __init__(self, app: ASGIApp, *, actor_kind: ActorKind) -> None:
        super().__init__(app)
        self._actor_kind = actor_kind

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        method = request.method.upper()

        # Read-only methods bypass tier enforcement entirely.
        if method not in _MUTATING_METHODS:
            return await call_next(request)

        # Look up required tier by route key (method + path prefix).
        route_key = f"{method} {request.url.path}"
        required_tier = self._resolve_tier(route_key)
        if required_tier is None:
            # Unmapped mutating route — allow through (Phase 1 default-open).
            return await call_next(request)

        actor_id = getattr(request.state, "actor_id", "unknown")
        caller = CallerContext(
            actor_kind=self._actor_kind,
            actor_id=actor_id,
        )
        request.state.caller_context = caller

        try:
            check_tier(route_key, caller, required_tier)
        except CapabilityDenied as exc:
            _log.warning(
                "tier_enforcement_denied",
                extra={"route": route_key, "actor_id": actor_id, "reason": exc.reason},
            )
            return JSONResponse(
                content={
                    "type": "/errors/forbidden",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": exc.reason,
                    "instance": str(request.url),
                    "extensions": _build_idempotency_extensions(request),
                },
                status_code=403,
                media_type=_PROBLEM_MEDIA_TYPE,
            )

        return await call_next(request)

    @staticmethod
    def _resolve_tier(route_key: str) -> Tier | None:
        """Match *route_key* against ``ROUTE_TIER_MAP`` by longest prefix."""
        for prefix, tier in sorted(ROUTE_TIER_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
            if route_key == prefix or route_key.startswith(prefix + "/"):
                return tier
        return None


__all__ = [
    "ActorIdMiddleware",
    "IdempotencyKeyMiddleware",
    "RequestIdMiddleware",
    "ROUTE_TIER_MAP",
    "TierEnforcementMiddleware",
]
