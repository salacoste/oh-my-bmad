"""HTTP middleware stack for registry-api (Stories 2.9 / 3.6 / 6.3 / 9.2).


Five class-based middlewares (subclassing ``BaseHTTPMiddleware``):

- ``TraceIdMiddleware``:        reads ``X-Trace-Id`` header (Story 9.2 / FR58);
                                validates against the Story 9.1 contract
                                (bare UUIDv7 OR ``\\Atg:[1-9][0-9]{0,18}\\Z``
                                with int64 ceiling); generates via
                                ``new_uuid7(clock=clock)`` if absent or
                                malformed; attaches to
                                ``request.state.trace_id``; binds into
                                structlog contextvars so every downstream log
                                record (including the inner ``request_id``
                                bind) carries ``trace_id``; unbinds in a
                                ``try/finally`` so a uvicorn worker reused
                                for the next request never observes the
                                prior request's trace; echoes on response.
                                This is the HTTP ingress for Epic 9's α
                                ``trace_id`` propagation kernel.
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
  app.add_middleware(TierEnforcementMiddleware, ...)        # runs 5th (innermost)
  app.add_middleware(ActorIdMiddleware)                      # runs 4th
  app.add_middleware(IdempotencyKeyMiddleware, clock=clock)  # runs 3rd
  app.add_middleware(RequestIdMiddleware, clock=clock)       # runs 2nd
  app.add_middleware(TraceIdMiddleware, clock=clock)         # runs 1st (outermost)

So incoming request flows:
  TraceId → RequestId → IdempotencyKey → ActorId → TierEnforcement → handler.

``TraceIdMiddleware`` runs OUTERMOST so the ``trace_id`` structlog binding is
established before any inner middleware (including ``RequestIdMiddleware``)
emits a log record — every log line and every emitted ``EventEnvelope``
carries the parent ``trace_id`` correlation alongside the per-request
``request_id``.
"""

from __future__ import annotations

import logging
import re
from types import MappingProxyType

import structlog
from capabilities import CallerContext, Tier, check_tier  # noqa: IMP001 — services→packages allowed
from events.clock import Clock

# Story 9.2 pass-1 review A1: import the public ``is_valid_trace_id`` helper
# from ``events.envelope`` so HTTP-side validation stays symmetric with the
# envelope-side ``_trace_id_shape`` validator. Single source of truth for the
# Story 9.1 trace_id shape contract (UUIDv7 OR ``tg:<update_id>``).
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — services→packages allowed
from events.errors import CapabilityDenied
from events.ids import new_idempotency_key, new_request_id, new_uuid7
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Story 3.6 M5: single source of truth for the mutation-method set, shared
# with ``adapters/errors.py``. Importing keeps the constant in one place
# rather than duplicating the literal in two files.
from registry_api.adapters.errors import _MUTATING_METHODS as _MUTATING_METHODS
from registry_api.adapters.errors import _build_capability_denied_response

# Bare UUIDv7 (no prefix) — matches new_request_id / new_idempotency_key output.
# Version nibble = 7, variant = 8/9/a/b. Same shape used by events.ids.
#
# Story 9.2 carry-fix (Epic 8.7 L2 mirror-update discipline): tightened the
# anchors from ``^...$`` to ``\A...\Z`` to match the envelope-side validator
# in ``packages/events/src/events/envelope.py:134``. ``^/$`` match at line
# boundaries — a hostile ``X-Request-ID`` of ``<valid-uuid>\n<garbage>`` would
# previously slip past ``^...$`` validation. ``\A/\Z`` anchor only to the
# absolute start/end of the input.
_UUIDV7_BARE_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)

# Story 9.2 pass-1 review A1: the local ``_TRACE_ID_TELEGRAM_RE`` / ``_INT64_MAX``
# constants and ``_is_valid_trace_id`` helper were promoted to public symbols
# in ``events.envelope`` (``TRACE_ID_TELEGRAM_RE`` / ``INT64_MAX_UPDATE_ID`` /
# ``is_valid_trace_id``). The HTTP middleware now imports the helper directly
# so the Story 9.1 shape contract has a single source of truth (mirror-update
# discipline; Epic 9 retro candidate). Telegram-gateway / console / MCP /
# worker ingresses (Stories 9.3-9.6) will reuse the same helper.

# Story 9.2 pass-1 review B4 + C2 + pass-2 review N12: shared WARNING log
# message constant. Pulled out so the middleware emit-site and
# ``test_middleware.py`` caplog assertions stay in lock-step. The hint leads
# with the constraint "lowercase-hex UUIDv7 (a-f, 0-9 only)" so operators
# reading the log see the rule first — pass-1 wording buried "lowercase"
# mid-sentence and operators reported scanning past it. The Telegram
# ``tg:<update_id>`` alternate form is also surfaced explicitly.
#
# Story 9.3 pass-2 review Q10: renamed from ``_INVALID_TRACE_LOG_MSG`` —
# leading-underscore is the Python convention for module-private symbols,
# but ``test_middleware.py`` imports it across the package boundary. The
# private-name contract is violated either way; expose it explicitly to
# stop pretending. Added to ``__all__`` so the public surface is documented.
INVALID_TRACE_LOG_MSG = (
    "invalid X-Trace-Id header; generating fresh "
    "(expected lowercase-hex UUIDv7 (a-f, 0-9 only) or 'tg:<update_id>')"
)

_log = logging.getLogger("registry_api.adapters.middleware")


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Read ``X-Trace-Id`` header; validate per Story 9.1 contract; generate if absent/malformed.

    The validated/minted value is attached to ``request.state.trace_id`` AND
    bound into structlog ``contextvars`` for the duration of the request, so
    every log record emitted from inside the request (including the inner
    ``RequestIdMiddleware`` and every route handler) carries ``trace_id``.
    The value is echoed back as ``X-Trace-Id`` on the response.

    This is the HTTP ingress for Epic 9's α ``trace_id`` propagation kernel
    (FR58). Stories 9.3-9.6 implement the Telegram / console / MCP / worker
    ingresses; Story 9.7 makes ``trace_id`` mandatory at the envelope level.

    Order rationale: this middleware runs OUTERMOST in execution flow (added
    LAST in ``build_app`` since Starlette reverses ``add_middleware`` order).
    That guarantees the structlog ``trace_id`` bind is established before
    ``RequestIdMiddleware`` runs — log records emitted from inside any inner
    middleware or handler carry BOTH ``trace_id`` (parent correlation) AND
    ``request_id`` (per-request).

    Security note (Story 9.2 pass-1 review B5):
        ``trace_id`` is purely correlational — it carries NO authentication or
        authorisation signal. A caller can submit any well-formed ``X-Trace-Id``
        value and the middleware will echo it. Operators correlating events
        across services MUST combine ``trace_id`` with ``actor.kind`` /
        ``actor.id`` (set by ``ActorIdMiddleware`` + future Story 6.1+ auth)
        to attribute origin. Specifically, a hostile caller submitting
        ``X-Trace-Id: tg:42`` from the HTTP surface will produce a Telegram-
        shaped trace_id that is otherwise indistinguishable from one minted by
        a real telegram-gateway ingress; only ``actor.kind == "operator"`` (the
        HTTP-surface signal) lets a downstream correlator tell the two apart.
    """

    def __init__(self, app: ASGIApp, *, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Story 9.2 pass-1 review A1: ``is_valid_trace_id`` lives in
        # ``events.envelope`` so HTTP-side validation stays symmetric with the
        # envelope-side validator. Single source of truth for the Story 9.1
        # contract.
        #
        # Story 9.2 pass-2 review N9: distinguish "header absent" (silent mint)
        # from "header present but malformed" (WARNING + mint). Empty string
        # (``X-Trace-Id: ``) falls into the malformed branch because it is
        # almost always a misconfigured proxy stripping the value — silently
        # accepting it would hide that bug class.
        #
        # Story 9.3 pass-1 review L7: defense-in-depth against proxy duplication
        # (some Cloudflare / nginx configs inject ``X-Trace-Id`` twice). Starlette
        # exposes both values via ``Headers.getlist``; ``.get(...)`` returns only
        # the first per RFC 7230. We log a WARNING when we observe multi-value
        # input so operators can spot the misconfigured proxy, then use the
        # first value (the same behaviour as ``.get(...)``).
        header_values = request.headers.getlist("X-Trace-Id")
        if len(header_values) > 1:
            _log.warning(
                "multi-value X-Trace-Id received (proxy duplication?); using first",
                extra={"received_count": len(header_values)},
            )
        incoming = request.headers.get("X-Trace-Id")
        if incoming is None:
            trace_id = new_uuid7(clock=self._clock)
            client_supplied_trace_id = False
        elif is_valid_trace_id(incoming):
            trace_id = incoming
            client_supplied_trace_id = True
        else:
            # Present but malformed (includes empty string). Truncate the
            # received value to 80 chars in the log to bound the size of
            # malformed payloads (mirrors RequestIdMiddleware's truncation
            # discipline). Story 9.2 pass-1 review B4 + C2: WARNING message
            # lives in the ``INVALID_TRACE_LOG_MSG`` module constant so test
            # ``caplog`` assertions and the emit-site stay in lock-step
            # (renamed from ``_INVALID_TRACE_LOG_MSG`` in Story 9.3 pass-2
            # review Q10 — see comment at the constant's definition site).
            _log.warning(
                INVALID_TRACE_LOG_MSG,
                extra={"received": incoming[:80]},
            )
            trace_id = new_uuid7(clock=self._clock)
            client_supplied_trace_id = False
        request.state.trace_id = trace_id
        # Bind into structlog contextvars so every downstream log record
        # carries ``trace_id``. The unbind in the ``try/finally`` is
        # load-bearing: without it, a uvicorn worker reused for the next
        # request would observe the prior request's trace_id until its own
        # TraceIdMiddleware rebound it. Mirrors RequestIdMiddleware:115-119.
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("trace_id")
        response.headers["X-Trace-Id"] = trace_id
        # Story 9.2 pass-1 review C4 + pass-2 review N2: append ``X-Trace-Id``
        # to the ``Vary`` response header so cache layers (Varnish / CDNs /
        # browser HTTP cache) do not serve a response with the wrong echoed
        # trace_id to a request that carried a different ``X-Trace-Id``.
        #
        # Three pass-2 fixes vs the pass-1 implementation:
        #   1. Case-insensitive dedup — HTTP header tokens are case-insensitive
        #      per RFC 7230 §3.2 so ``vary: x-trace-id`` already covers us.
        #   2. ``Vary: *`` is a TERMINATOR per RFC 7231 §7.1.4 — never append
        #      tokens to it; the star already disables caching downstream.
        #   3. Gate on ``client_supplied_trace_id``: when the middleware MINTED
        #      a fresh UUID, every request has a unique value so ``Vary`` would
        #      effectively disable caching of any cacheable downstream endpoint.
        #      We only need ``Vary`` when a client actively varied the header.
        if client_supplied_trace_id:
            existing_vary = response.headers.get("Vary", "").strip()
            if existing_vary == "*":
                # ``Vary: *`` is a RFC 7231 §7.1.4 terminator — leave untouched.
                pass
            else:
                existing_tokens = [p.strip() for p in existing_vary.split(",") if p.strip()]
                existing_lower = {t.lower() for t in existing_tokens}
                if "x-trace-id" not in existing_lower:
                    existing_tokens.append("X-Trace-Id")
                    response.headers["Vary"] = ", ".join(existing_tokens)
        return response


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
ROUTE_TIER_MAP: MappingProxyType[str, Tier] = MappingProxyType(
    {
        "POST /v1/tasks": Tier.ONE,
    }
)


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
            return _build_capability_denied_response(request, exc)

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
    "INVALID_TRACE_LOG_MSG",
    "IdempotencyKeyMiddleware",
    "RequestIdMiddleware",
    "ROUTE_TIER_MAP",
    "TierEnforcementMiddleware",
    "TraceIdMiddleware",
]
