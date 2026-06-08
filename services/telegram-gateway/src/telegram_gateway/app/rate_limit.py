"""Token-bucket rate limiter for the Telegram webhook (Story 3.6 AC-5/6/7)
+ per-actor aiogram rate limiter (Story 7.5.1).

Two-layer rate-limit architecture
---------------------------------

Layer 1: FastAPI ``WebhookRateLimitMiddleware`` (this module)
    Coarse HTTP-level token-bucket scoped to ``webhook_path``. All
    requests — regardless of Telegram sender identity — share a single
    bucket (capacity=20, refill=10/s). Protects the webhook endpoint
    from unauthenticated HTTP-level floods.

Layer 2: Aiogram ``PerActorRateLimitMiddleware`` (this module)
    Fine-grained per-sender token-bucket registered as aiogram outer
    middleware AFTER the ``AllowlistMiddleware``. Only allowlisted
    updates reach this layer (non-allowlisted updates are silently
    dropped by the allowlist). Each sender gets an independent bucket
    keyed by their Telegram user id.

Why Layer 2 must run AFTER the allowlist
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If Layer 2 ran before (or alongside) the allowlist, a non-allowlisted
attacker hitting the webhook URL rapidly would consume per-actor tokens
for their own bucket. While that seems harmless (the attacker's bucket
fills independently), the HTTP-level bucket (Layer 1) is shared — the
attacker drains IT first, causing 429 responses for legitimate actors
within the same refill window. Layer 2 prevents this by only accepting
updates that have already passed the allowlist check, ensuring
non-allowlisted traffic never reaches the aiogram dispatch chain.

Request flow::

    HTTP POST → Layer 1 (FastAPI shared bucket)
        → 429 if shared bucket empty
        → webhook handler → aiogram dispatch
            → AllowlistMiddleware (outer)
                → None if not allowlisted (no further processing)
                → PerActorRateLimitMiddleware (outer, after allowlist)
                    → None if actor's bucket empty
                    → handler (inner chain → command handler)

Capacity = 20 (burst), refill = 10 tokens/s for the shared bucket —
locked by architecture.md line 215. Per-actor defaults are set at
construction time.

# Rate-limit thresholds are operator-tunable via env-vars (see
# TelegramSettings).  Defaults match the architecture.md:215 locked values.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
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

    Token consumption contract
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    The token decrement (``_tokens -= 1.0``) runs BEFORE ``call_next`` —
    this is **charge-on-attempt**, not charge-on-success. Tokens are NOT
    refunded if ``call_next`` raises (handler exception,
    ``asyncio.CancelledError`` on client disconnect). This is the correct
    DoS-protection trade-off: a failing handler must still consume a token
    so an attacker cannot bypass the limiter by triggering handler errors.
    A future maintainer should NOT move the decrement after ``call_next``,
    as that would silently disable rate-limiting for failing handlers.
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
                    headers={
                        "Retry-After": str(
                            min(3600, math.ceil((1.0 - self._tokens) / self._refill_per_second))
                        ),
                    },
                )

            # Consume + advance the refill clock together (M6 consumed-time
            # advance: ``_last_refill_ns`` only moves on a successful consume).
            # Charge-on-attempt: consume BEFORE call_next (see class docstring).
            self._tokens -= 1.0
            self._last_refill_ns = now_ns

        return await call_next(request)


_log = logging.getLogger("telegram_gateway.rate_limit")


class PerActorRateLimitMiddleware(BaseMiddleware):
    """Per-sender token-bucket rate limiter (Story 7.5.1 AC-1).

    Registered as aiogram outer middleware AFTER
    :class:`AllowlistMiddleware` so only allowlisted updates consume
    per-actor tokens. Each sender gets an independent bucket keyed by
    their Telegram user id.

    Updates without a ``from_user`` / ``user`` field (bot-only events
    like ``message_reaction_count``) pass through without rate limiting
    — they carry no per-user identity to key a bucket on.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        clock: Clock,
        max_entries: int = 1024,
    ) -> None:
        if capacity < 1:
            raise ValueError(
                f"PerActorRateLimitMiddleware: capacity must be >= 1, got {capacity!r}"
            )
        if refill_per_second <= 0:
            raise ValueError(
                f"PerActorRateLimitMiddleware: refill_per_second must be > 0,"
                f" got {refill_per_second!r}"
            )
        if max_entries < 1:
            raise ValueError(
                f"PerActorRateLimitMiddleware: max_entries must be >= 1, got {max_entries!r}"
            )
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._clock = clock
        self._max_entries = max_entries
        # OrderedDict provides LRU semantics: ``move_to_end()`` on access
        # marks an entry as most-recently-used; ``popitem(last=False)`` evicts
        # the least-recently-used when the capacity bound is reached.
        # Defense-in-depth: the allowlist is operator-controlled so the dict
        # cannot grow beyond |allowlist| entries in practice, but the bound
        # prevents unbounded growth if a misconfiguration or future code
        # change allows non-allowlisted users to reach this middleware.
        self._buckets: OrderedDict[int, tuple[float, int]] = OrderedDict()
        # user_id → (tokens, last_refill_ns)
        # Single global lock is sufficient — asyncio is single-threaded, so
        # the lock prevents inter-task reordering at await points without
        # contention from parallel threads.
        self._lock = asyncio.Lock()

    def _extract_user_id(self, event: TelegramObject) -> int | None:
        """Extract sender user id from an aiogram Update (or direct event).

        Reuses the same child-field walk as
        :meth:`AllowlistMiddleware._extract_user_id`.
        """
        direct_from_user = getattr(event, "from_user", None)
        if direct_from_user is not None:
            uid = getattr(direct_from_user, "id", None)
            if isinstance(uid, int) and not isinstance(uid, bool):
                return uid
        if isinstance(event, Update):
            child_fields = (
                ("message", "from_user"),
                ("edited_message", "from_user"),
                ("channel_post", "from_user"),
                ("edited_channel_post", "from_user"),
                ("business_message", "from_user"),
                ("edited_business_message", "from_user"),
                ("business_connection", "user"),
                ("callback_query", "from_user"),
                ("inline_query", "from_user"),
                ("chosen_inline_result", "from_user"),
                ("shipping_query", "from_user"),
                ("pre_checkout_query", "from_user"),
                ("my_chat_member", "from_user"),
                ("chat_member", "from_user"),
                ("chat_join_request", "from_user"),
                ("poll_answer", "user"),
                ("message_reaction", "user"),
                ("purchased_paid_media", "from_user"),
            )
            for field, user_attr in child_fields:
                child = getattr(event, field, None)
                if child is None:
                    continue
                user_obj = getattr(child, user_attr, None)
                if user_obj is None:
                    continue
                uid = getattr(user_obj, "id", None)
                if isinstance(uid, int) and not isinstance(uid, bool):
                    return uid
        return None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Rate-limit per sender. Returns None when the actor's bucket is empty."""
        user_id = self._extract_user_id(event)
        if user_id is None:
            return await handler(event, data)

        async with self._lock:
            now_ns = self._clock.monotonic_ns()

            if user_id in self._buckets:
                # Existing user — promote to most-recently-used.
                self._buckets.move_to_end(user_id)
                tokens, last_ns = self._buckets[user_id]
            else:
                # New user — evict LRU entry if at capacity.
                if len(self._buckets) >= self._max_entries:
                    evicted_key, _ = self._buckets.popitem(last=False)
                    _log.debug(
                        "per-actor rate limit: evicted LRU entry for user %d "
                        "(max_entries=%d reached)",
                        evicted_key,
                        self._max_entries,
                    )
                tokens = float(self._capacity)
                last_ns = now_ns

            elapsed_s = max(0.0, (now_ns - last_ns) / 1e9)
            tokens = min(float(self._capacity), tokens + elapsed_s * self._refill_per_second)

            if tokens < 1.0:
                self._buckets[user_id] = (tokens, last_ns)
                self._buckets.move_to_end(user_id)
                _log.warning(
                    "per-actor rate limit: user %d bucket empty (%.2f tokens, refill=%.1f/s)",
                    user_id,
                    tokens,
                    self._refill_per_second,
                )
                return None

            tokens -= 1.0
            self._buckets[user_id] = (tokens, now_ns)
            self._buckets.move_to_end(user_id)

        return await handler(event, data)


__all__ = ["WebhookRateLimitMiddleware", "PerActorRateLimitMiddleware"]
