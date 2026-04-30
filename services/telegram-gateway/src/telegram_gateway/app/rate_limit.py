"""Token-bucket rate limiter for the Telegram webhook (Story 3.6 AC-5/6/7).

Capacity = 20 (burst), refill = 10 tokens/s — locked by architecture.md
line 215. Continuous (fractional) refill: at 0.5 s after a full burst-out
the bucket holds 5 tokens (10 req/s × 0.5 s = 5).

Scoped to ``settings.webhook_path`` only; all other routes (notably
``/v1/health``) pass through. Internal HTTP API stays un-rate-limited per
NFR-S7.

# TODO(Phase 2): operator-tunable thresholds via env-vars (e.g.
# TG_WEBHOOK_RATE_LIMIT_CAPACITY) when the platform supports multiple
# webhook endpoints / multi-channel sinks.
"""

from __future__ import annotations

import asyncio

from events.clock import Clock
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class WebhookRateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket rate limiter scoped to ``webhook_path``.

    All requests to any path other than ``webhook_path`` pass through
    immediately — health checks and any future internal routes are never
    rate-limited (AC-6 / NFR-S7).

    Bucket state (``_tokens``, ``_last_refill_ns``) is protected by an
    ``asyncio.Lock`` so concurrent webhook deliveries cannot double-spend the
    same token (AC-5 lock invariant).

    ``clock`` is injected rather than calling ``time.monotonic()`` directly so
    tests can control bucket behaviour deterministically via ``TickingClock``
    (architecture.md line 215 / Story 3.1 H4 cache-once pattern).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        webhook_path: str,
        capacity: int,
        refill_per_second: float,
        clock: Clock,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"WebhookRateLimitMiddleware: capacity must be >= 1, got {capacity!r}")
        if refill_per_second <= 0:
            raise ValueError(
                f"WebhookRateLimitMiddleware: refill_per_second must be > 0,"
                f" got {refill_per_second!r}"
            )
        super().__init__(app)
        self._webhook_path = webhook_path
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._clock = clock
        self._tokens: float = float(capacity)
        self._last_refill_ns: int = clock.monotonic_ns()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # AC-6: pass through any route that is NOT the webhook path.
        # Story 3.6 H4: normalize trailing slashes on both sides — strict
        # equality leaks a bypass when an upstream proxy normalizes
        # ``/v1/telegram/webhook/`` → ``/v1/telegram/webhook`` (or a future
        # FastAPI ``redirect_slashes=True`` adds an alias). Match by stripped
        # path so both variants are rate-limited consistently.
        if request.url.path.rstrip("/") != self._webhook_path.rstrip("/"):
            return await call_next(request)

        async with self._lock:
            now_ns = self._clock.monotonic_ns()
            # Story 3.6 M6: clamp to >= 0.0 so a non-monotonic test clock or
            # backward-skewing wall-clock cannot silently consume tokens via
            # negative ``elapsed_s``.
            elapsed_s = max(0.0, (now_ns - self._last_refill_ns) / 1e9)
            self._tokens = min(
                float(self._capacity),
                self._tokens + elapsed_s * self._refill_per_second,
            )

            if self._tokens < 1.0:
                # AC-5: RFC 7807 problem+json 429. Body is constructed inline —
                # no cross-service import of registry_api.ProblemDetails (AC-11).
                # Story 3.6 L2: ``instance`` is the path ONLY (no query string)
                # so any caller-supplied query params (debug flags, callback
                # data, secrets-by-mistake) are NOT echoed back in the
                # public-facing problem-json.
                # Story 3.6 M6 (deny path): do NOT advance ``_last_refill_ns``
                # here — under sustained overload, repeatedly probing the
                # bucket while the bucket value < 1.0 would otherwise accrue
                # ``elapsed_s × refill_rate`` in sub-ULP increments that round
                # to 0.0 and silently lose accumulated time. By leaving
                # ``_last_refill_ns`` untouched on the deny path, the next
                # successful consumption refills from the LAST consumed time
                # rather than the last probe time.
                body = {
                    "type": "/errors/rate-limited",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": "Webhook rate limit exceeded; retry after refill.",
                    "instance": request.url.path,
                }
                return JSONResponse(
                    content=body,
                    status_code=429,
                    media_type="application/problem+json",
                    headers={"Retry-After": "1"},
                )

            # Consume + advance the refill clock together (M6 consumed-time
            # advance: ``_last_refill_ns`` only moves on a successful consume).
            self._tokens -= 1.0
            self._last_refill_ns = now_ns

        return await call_next(request)


__all__ = ["WebhookRateLimitMiddleware"]
