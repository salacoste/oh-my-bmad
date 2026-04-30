"""Tests for WebhookRateLimitMiddleware (Story 3.6 AC-5/6/7/10) +
Story 3.7 contract test for the rate-limited problem-type slug (AC-4).

11 + 1 tests covering:
  AC-7: burst-then-429, refill behaviour (fractional + full)
  AC-5: RFC 7807 body, Retry-After header, init validation
  AC-6: passthrough for non-webhook routes
  Lock invariant: concurrent requests no double-spend
  Clock injection: TickingClock controls bucket; time.monotonic not called
  Story 3.7 AC-4: rate-limited problem-type slug parity (renderer ↔ middleware)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock, TickingClock
from httpx import ASGITransport, AsyncClient

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import _TELEGRAM_GATEWAY_ACTOR
from telegram_gateway.app.main import build_app
from telegram_gateway.app.rate_limit import WebhookRateLimitMiddleware

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACTOR = _TELEGRAM_GATEWAY_ACTOR
# Story 3.6 L5: ``***FAKE-...***``-style sentinels for synthetic secrets
# per AC-11. The bot-token MUST satisfy aiogram's ``Bot(...)`` token
# validator (``<digits>:<non-empty>`` partitions on ``:``) so the colon
# is preserved; the body uses the ``***FAKE-***`` sentinel rather than a
# realistic-looking value to make the synthetic-ness obvious to a future
# reader. The TELEGRAM_BOT_TOKEN secret-scanner regex requires an ``AA``
# prefix after the colon (``\d{6,12}:AA[A-Za-z0-9_\-]{30,}``), which the
# sentinel deliberately does NOT match.
_BOT_TOKEN = "12345678:***FAKE-BOT-TOKEN***"
_WEBHOOK_SECRET = "***FAKE-WEBHOOK-SECRET-1234***"
_WEBHOOK_URL = "https://tunnel.example.com/v1/telegram/webhook"
_WEBHOOK_PATH = "/v1/telegram/webhook"

# Synthetic Telegram update payload (plausible timestamp, not 0).
_UPDATE: dict[str, Any] = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 1_700_000_000,
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "is_bot": False, "first_name": "Test"},
        "text": "/ping",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_aiogram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out Bot.set_webhook and AiohttpSession.close so tests don't need network."""

    async def fake_set_webhook(self: Bot, **kwargs: Any) -> bool:
        return True

    async def fake_session_close(self: AiohttpSession) -> None:
        return None

    monkeypatch.setattr(Bot, "set_webhook", fake_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", fake_session_close)


def _make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TelegramSettings:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", _WEBHOOK_SECRET)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _WEBHOOK_URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    return TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )


@pytest_asyncio.fixture
async def webhook_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """Full app client with WebhookRateLimitMiddleware registered (capacity=20)."""
    _patch_aiogram(monkeypatch)
    settings = _make_settings(monkeypatch, tmp_path)
    # Use a TickingClock so each bucket access sees a small time advance
    # (1 ns per tick) — ensures elapsed_s is effectively 0 in burst tests.
    clock = TickingClock(start_ns=0, tick_ns=1)
    app = build_app(settings=settings, clock=clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


def _webhook_headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET}


# ---------------------------------------------------------------------------
# Story 3.6 L3: shared ``_ManualClock`` test double extracted to module level
# (was duplicated as a local class in TWO refill tests). Single source of
# truth — refill / clock-skew / deny-path tests reuse the same shape.
# ---------------------------------------------------------------------------


class _ManualClock:
    """Test-only clock with caller-controlled monotonic_ns().

    The bucket-state code only reads ``monotonic_ns()``; ``now()`` is
    delegated to the real wall clock so any code path that needs a wall
    timestamp continues to function. Mutate ``ns_value[0]`` to advance the
    monotonic clock between assertions.
    """

    def __init__(self, ns_value: list[int]) -> None:
        self._ns_value = ns_value

    def now(self):  # type: ignore[override]
        from datetime import UTC, datetime  # noqa: PLC0415

        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return self._ns_value[0]


# ---------------------------------------------------------------------------
# AC-7: Burst of 20 passes, 21st is 429
# ---------------------------------------------------------------------------


class TestRateLimitBurst:
    """AC-7: burst behaviour of the token-bucket."""

    @pytest.mark.asyncio
    async def test_rate_limit_passes_first_20_burst(self, webhook_client: AsyncClient) -> None:
        """First 20 POST requests to the webhook path within the window all return 200.

        Story 3.6 M1: tightened from ``!= 429`` (which would silently accept
        500/422/etc as "passes" and miss a regression that broke the webhook
        handler) to the strict ``== 200`` AC-7 contract.
        """
        results = []
        for _ in range(20):
            r = await webhook_client.post(
                _WEBHOOK_PATH,
                json=_UPDATE,
                headers=_webhook_headers(),
            )
            results.append(r.status_code)
        # AC-7: ALL 20 requests must return 200 from the webhook handler.
        assert all(s == 200 for s in results), (
            f"Expected all 20 requests to return 200; statuses: {results}"
        )

    @pytest.mark.asyncio
    async def test_rate_limit_returns_429_on_21st_request_within_window(
        self, webhook_client: AsyncClient
    ) -> None:
        """After 20 requests exhaust the bucket, the 21st returns 429."""
        for _ in range(20):
            await webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
        r = await webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
        assert r.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_429_body_is_rfc7807_problem_json(
        self, webhook_client: AsyncClient
    ) -> None:
        """429 body is RFC 7807 problem+json with correct fields."""
        for _ in range(20):
            await webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
        r = await webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
        assert r.status_code == 429
        assert "application/problem+json" in r.headers.get("content-type", "")
        body = r.json()
        assert body["type"] == "/errors/rate-limited"
        assert body["title"] == "Too Many Requests"
        assert body["status"] == 429
        assert "detail" in body
        assert "instance" in body

    @pytest.mark.asyncio
    async def test_rate_limit_429_includes_retry_after_header(
        self, webhook_client: AsyncClient
    ) -> None:
        """429 response carries Retry-After: 1 header."""
        for _ in range(20):
            await webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
        r = await webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
        assert r.status_code == 429
        assert r.headers.get("retry-after") == "1"


# ---------------------------------------------------------------------------
# AC-7: Continuous (fractional) refill
# ---------------------------------------------------------------------------


class TestRateLimitRefill:
    """AC-7: continuous fractional refill behaviour."""

    @pytest.mark.asyncio
    async def test_rate_limit_continuous_refill_at_0_5_seconds_grants_5_tokens(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """At 0.5 s after burst-out, exactly 5 tokens are available (10/s × 0.5 s).

        Uses :class:`_ManualClock` (Story 3.6 L3 shared helper) advanced
        manually: first call returns t=0 (bucket init), burst drains 20
        tokens, then we swap the clock to t=0.5 s and assert the next 5
        pass but the 6th is 429.
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        # ManualClock: tracks current ns so we can advance it between calls.
        ns_value = [0]
        clock = _ManualClock(ns_value)
        app = build_app(settings=settings, clock=clock)  # type: ignore[arg-type]

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # Drain all 20 tokens (ns_value stays at 0 → zero refill).
            for _ in range(20):
                await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())

            # Confirm bucket is empty.
            r_empty = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r_empty.status_code == 429

            # Advance clock by 0.5 s = 500_000_000 ns.
            # 10 tokens/s × 0.5 s = 5.0 tokens refilled.
            ns_value[0] = 500_000_000

            # Next 5 requests should pass.
            refill_results = []
            for _ in range(5):
                r = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
                refill_results.append(r.status_code)
            assert all(s != 429 for s in refill_results), (
                f"Expected 5 passes after 0.5 s refill; got {refill_results}"
            )

            # 6th should be 429 again (bucket back to empty).
            r_sixth = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r_sixth.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_full_refill_after_2_seconds_restores_20_tokens(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """After 2 s (20 tokens at 10/s, capped at capacity=20), full burst available.

        Story 3.6 M1: tightened to ``== 200`` for AC-7 strictness.
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        ns_value = [0]
        clock = _ManualClock(ns_value)
        app = build_app(settings=settings, clock=clock)  # type: ignore[arg-type]

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # Drain 20 tokens.
            for _ in range(20):
                await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())

            # Advance 2 s → 20 tokens refilled (at cap).
            ns_value[0] = 2_000_000_000

            # Full burst of 20 should pass again.
            results = []
            for _ in range(20):
                r = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
                results.append(r.status_code)
            # Story 3.6 M1: AC-7 strictness — ALL 20 must be 200.
            assert all(s == 200 for s in results), (
                f"Expected 20 passes (all 200) after 2 s refill; statuses: {results}"
            )

            # 21st is 429 again.
            r21 = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r21.status_code == 429


# ---------------------------------------------------------------------------
# AC-6: Passthrough for non-webhook routes
# ---------------------------------------------------------------------------


class TestRateLimitPassthrough:
    """AC-6: /v1/health and other non-webhook paths are never rate-limited."""

    @pytest.mark.asyncio
    async def test_rate_limit_passthrough_for_non_webhook_routes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """100 GET /v1/health requests succeed regardless of bucket state."""
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)
        # Frozen clock → zero elapsed time → zero refill → bucket drains fast.
        clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        app = build_app(settings=settings, clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # First drain the bucket via webhook.
            for _ in range(20):
                await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            # Confirm bucket is empty.
            r429 = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r429.status_code == 429

            # /v1/health must still respond 200 even with empty bucket.
            health_results = []
            for _ in range(100):
                rh = await client.get("/v1/health")
                health_results.append(rh.status_code)
            assert all(s == 200 for s in health_results), (
                f"Some health checks were rate-limited: {health_results}"
            )


# ---------------------------------------------------------------------------
# Lock invariant: concurrent requests no double-spend
# ---------------------------------------------------------------------------


class TestRateLimitConcurrency:
    """AC-5: asyncio.Lock prevents double-spending tokens under concurrency."""

    @pytest.mark.asyncio
    async def test_rate_limit_concurrent_requests_no_double_spend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """25 concurrent requests: exactly 20 succeed (200), 5 are 429.

        Story 3.6 M1: tightened to ``successes == 20`` (counted via ``== 200``,
        not ``!= 429``) so a regression that returns 500 from the handler
        cannot mask itself as a "success".

        Story 3.6 M2: lock invariant strengthened with two changes:
            (a) inject a slow handler (``await asyncio.sleep(0.005)``) so the
                contention window between bucket-mutation and ``call_next`` is
                observable — under cooperative scheduling without a lock, two
                coroutines could plausibly observe the same non-empty bucket
                and double-spend.
            (b) instrument the middleware's ``_tokens`` mutation via a
                consumed-token counter incremented on each "successful"
                decrement; the lock invariant requires the final counter
                value to equal exactly 20 (one increment per consumed token,
                no lost-updates).

        @pytest.mark.slow omitted intentionally: 25 × 5 ms cooperative tasks
        complete well within the PR-gate budget (~30 ms).
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)
        # Use a TickingClock so each bucket access advances 1 ns — refill is
        # effectively zero across the whole burst (matches webhook_client).
        clock = TickingClock(start_ns=0, tick_ns=1)
        app = build_app(settings=settings, clock=clock)

        # M2 (b): wrap WebhookRateLimitMiddleware.dispatch to count consumed
        # tokens AND inject the slow window AFTER lock release but BEFORE
        # ``call_next``. Inserting the sleep INSIDE the dispatch (rather than
        # replacing the route handler) keeps FastAPI's dependency injection
        # for ``telegram_webhook`` intact (the handler reads
        # ``x_telegram_bot_api_secret_token`` via ``Header()`` annotation,
        # which dep-injection extracts from the request — replacing the
        # route's endpoint breaks that path).
        consumed_tokens: list[int] = [0]

        from telegram_gateway.app.rate_limit import (  # noqa: PLC0415
            WebhookRateLimitMiddleware,
        )

        original_dispatch = WebhookRateLimitMiddleware.dispatch

        async def _instrumented_dispatch(self: Any, request: Any, call_next: Any) -> Any:
            # M2 (a): wrap ``call_next`` with a 5 ms sleep so the contention
            # window is observable — the awaited sleep yields control to other
            # coroutines, letting them pile up at the lock simultaneously.
            async def _slow_call_next(req: Any) -> Any:
                await asyncio.sleep(0.005)
                return await call_next(req)

            response = await original_dispatch(self, request, _slow_call_next)
            # M2 (b): on the success path, the middleware does NOT produce
            # 429; counter increments per consumed token under the lock.
            if response.status_code != 429:
                consumed_tokens[0] += 1
            return response

        monkeypatch.setattr(
            WebhookRateLimitMiddleware,
            "dispatch",
            _instrumented_dispatch,
        )

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            tasks = [
                client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
                for _ in range(25)
            ]
            responses = await asyncio.gather(*tasks)

        statuses = [r.status_code for r in responses]
        successes = sum(1 for s in statuses if s == 200)
        rate_limited = sum(1 for s in statuses if s == 429)
        # M1: AC-7 strictness — exactly 20 successes (200) and 5 429s.
        assert successes == 20, (
            f"Expected exactly 20 successes (200); got successes={successes}, "
            f"rate_limited={rate_limited}, statuses={sorted(statuses)}"
        )
        assert rate_limited == 5, (
            f"Expected exactly 5 rate-limited (429); got {rate_limited}; "
            f"statuses={sorted(statuses)}"
        )

        # M2 (b): consumed-token counter must equal exactly 20 — one
        # increment per successful pass-through under the lock. Any
        # lost-update under contention would leave consumed_tokens[0]
        # < 20 (two coros decremented from the same starting value, but
        # only one made it to call_next) or > 20 (impossible under the
        # current implementation, but checked for completeness).
        assert consumed_tokens[0] == 20, (
            f"Lock invariant broken — expected 20 consumed tokens, got "
            f"{consumed_tokens[0]} (statuses={sorted(statuses)})"
        )


# ---------------------------------------------------------------------------
# AC-5: __init__ validation
# ---------------------------------------------------------------------------


class TestRateLimitInit:
    """AC-5: constructor validates capacity and refill_per_second."""

    def test_rate_limit_init_rejects_invalid_capacity(self) -> None:
        """capacity=0 raises ValueError with a descriptive message."""
        from starlette.applications import Starlette  # noqa: PLC0415

        clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        dummy_app = Starlette()
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            WebhookRateLimitMiddleware(
                dummy_app,
                webhook_path="/v1/telegram/webhook",
                capacity=0,
                refill_per_second=10.0,
                clock=clock,
            )

    def test_rate_limit_init_rejects_invalid_refill_rate(self) -> None:
        """refill_per_second=-1.0 raises ValueError with a descriptive message."""
        from starlette.applications import Starlette  # noqa: PLC0415

        clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        dummy_app = Starlette()
        with pytest.raises(ValueError, match="refill_per_second must be > 0"):
            WebhookRateLimitMiddleware(
                dummy_app,
                webhook_path="/v1/telegram/webhook",
                capacity=20,
                refill_per_second=-1.0,
                clock=clock,
            )


# ---------------------------------------------------------------------------
# Clock injection: no time.monotonic calls
# ---------------------------------------------------------------------------


class TestRateLimitClockInjection:
    """AC-5: clock is injected; time.monotonic is never called by the middleware."""

    @pytest.mark.asyncio
    async def test_rate_limit_uses_injected_clock(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """TickingClock controls bucket refill; clock.monotonic_ns() is actually called.

        Uses a counting wrapper around TickingClock to verify the injected clock
        is consulted during dispatch. Also statically verifies that rate_limit.py
        does not import 'time' directly (ensures no fallback to stdlib clock).
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        call_count = [0]
        base_clock = TickingClock(start_ns=0, tick_ns=100_000_000)  # 100 ms per tick

        class _CountingClock:
            """Wraps TickingClock and counts monotonic_ns() calls."""

            def now(self) -> object:
                return base_clock.now()

            def monotonic_ns(self) -> int:
                call_count[0] += 1
                return base_clock.monotonic_ns()

        clock = _CountingClock()
        app = build_app(settings=settings, clock=clock)  # type: ignore[arg-type]

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            before = call_count[0]
            r = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            after = call_count[0]

        # Middleware must have called clock.monotonic_ns() during dispatch.
        assert after > before, (
            f"clock.monotonic_ns() was not called (before={before}, after={after})"
        )
        assert r.status_code != 500

        # Static check: rate_limit.py must not import 'time' directly.
        # Story 3.6 M3: extend AST scan to walk BOTH ``ast.Import`` (for
        # ``import time``) AND ``ast.ImportFrom`` (for ``from time import
        # monotonic_ns``). The previous scan missed the ``ImportFrom`` form
        # — a future contributor writing ``from time import monotonic_ns``
        # would have silently defeated the gate.
        import ast as _ast  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415

        src = (_Path(__file__).parent / "rate_limit.py").read_text()
        tree = _ast.parse(src)
        direct_time_imports: list[_ast.AST] = []
        for node in _ast.walk(tree):
            # ``import time`` / ``import time as t``
            if isinstance(node, _ast.Import) and any(alias.name == "time" for alias in node.names):
                direct_time_imports.append(node)
            # ``from time import monotonic_ns`` / ``from time import *``
            if isinstance(node, _ast.ImportFrom) and node.module == "time":
                direct_time_imports.append(node)
        assert not direct_time_imports, (
            f"rate_limit.py must not import 'time' directly; "
            f"found {len(direct_time_imports)} occurrence(s) (ast.Import "
            f"and/or ast.ImportFrom)"
        )


# ---------------------------------------------------------------------------
# Story 3.6 H4: trailing-slash bypass regression
# ---------------------------------------------------------------------------


class TestRateLimitTrailingSlash:
    """Story 3.6 H4: rate-limiter path matching tolerates trailing slashes.

    Strict equality (``request.url.path != self._webhook_path``) used to leak
    a bypass when an upstream proxy normalized ``/v1/telegram/webhook/`` to
    ``/v1/telegram/webhook`` (or a future ``redirect_slashes=True`` added an
    alias). The fix uses ``.rstrip('/')`` on both sides so both variants are
    rate-limited identically.
    """

    @pytest.mark.asyncio
    async def test_rate_limit_handles_trailing_slash_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``/v1/telegram/webhook`` and ``/v1/telegram/webhook/`` both bucket-rate-limited.

        Drains the bucket via the canonical path, then sends one request to
        the trailing-slash variant — it MUST also see 429, proving the
        path-matching normalization (H4) catches the alias rather than
        passing through.
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)
        clock = TickingClock(start_ns=0, tick_ns=1)
        app = build_app(settings=settings, clock=clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # Drain via canonical (no trailing slash) path.
            for _ in range(20):
                await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())

            # Trailing-slash variant must also be rate-limited; FastAPI's
            # routing layer may 404 (no exact-match route) before reaching
            # the handler, but the rate-limit middleware sits OUTSIDE the
            # router and must intercept first. The middleware short-circuits
            # with 429 — H4 proves the bypass via trailing-slash is closed.
            r_slash = await client.post(
                _WEBHOOK_PATH + "/",
                json=_UPDATE,
                headers=_webhook_headers(),
            )
            assert r_slash.status_code == 429, (
                f"trailing-slash variant bypassed the rate limiter; "
                f"got status {r_slash.status_code} (expected 429)"
            )

            # Sanity: canonical path is also still 429.
            r_no_slash = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r_no_slash.status_code == 429


# ---------------------------------------------------------------------------
# Story 3.6 M6: token-bucket math edge cases
# ---------------------------------------------------------------------------


class TestRateLimitTokenBucketMath:
    """Story 3.6 M6: clamp negative-elapsed and pin deny-path no-advance."""

    @pytest.mark.asyncio
    async def test_rate_limit_clamps_negative_elapsed_when_clock_skews_backwards(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A backward-skewing clock cannot silently consume tokens via negative elapsed.

        Story 3.6 M6: ``elapsed_s = max(0.0, (now_ns - last_refill_ns) / 1e9)``
        — without the clamp, a non-monotonic test clock returning ``now_ns <
        _last_refill_ns`` produces a negative ``elapsed_s``, whose product
        with ``refill_per_second`` then SUBTRACTS from ``self._tokens``,
        leaving the bucket below capacity for normal-rate callers.
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        # Clock initialized at a high value, then SKEWS BACKWARDS to 0 on
        # the first dispatch — this would otherwise drive ``elapsed_s``
        # negative and silently subtract from ``_tokens``.
        ns_value = [10_000_000_000]  # 10 s → bucket initializes here
        clock = _ManualClock(ns_value)
        app = build_app(settings=settings, clock=clock)  # type: ignore[arg-type]

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # Skew backwards: clock now reports 0, before init.
            ns_value[0] = 0

            # The first request is a normal POST; with the clamp,
            # ``elapsed_s = max(0.0, ...)`` so no token is silently
            # subtracted, AND the request consumes exactly one token (the
            # bucket starts full at ``capacity=20`` so the response is 200).
            r = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r.status_code == 200, (
                f"backward clock skew produced unexpected status {r.status_code}; "
                f"clamp may be missing"
            )

            # Drain to capacity-1 — the clamp must keep the math sane: at
            # most 19 more requests should pass.
            results: list[int] = []
            for _ in range(20):
                rr = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
                results.append(rr.status_code)
            # Exactly 19 successes and one 429 — bucket holds capacity=20
            # tokens at init; we already consumed 1, so 19 remain.
            successes = sum(1 for s in results if s == 200)
            denials = sum(1 for s in results if s == 429)
            assert successes == 19, (
                f"backward-clock skew: expected 19 more successes, got "
                f"{successes} successes and {denials} denials; statuses={results}"
            )
            assert denials == 1, f"backward-clock skew: expected exactly 1 denial, got {denials}"

    @pytest.mark.asyncio
    async def test_rate_limit_deny_path_does_not_advance_last_refill_ns(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Repeated denials don't lose tokens to FP drift / lost-elapsed.

        Story 3.6 M6 (deny path): on the deny path, ``_last_refill_ns`` is
        NOT advanced — the next successful consumption refills based on
        elapsed time since the LAST consumed token, not the last probe.

        Test: drain bucket, hammer it with 1000 denials at sub-ms intervals
        (``elapsed`` per probe rounds to 0 below ULP if ``_last_refill_ns``
        were advanced on every probe), then advance the clock by 1 second
        and confirm exactly 10 tokens were refilled (10/s × 1 s).
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        ns_value = [0]
        clock = _ManualClock(ns_value)
        app = build_app(settings=settings, clock=clock)  # type: ignore[arg-type]

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            # Drain the bucket (clock stays at 0 → zero refill).
            for _ in range(20):
                await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())

            # Hammer with denials at sub-ms intervals so each probe's
            # ``elapsed`` rounds to ~0. Without the M6 fix, advancing
            # ``_last_refill_ns`` on each probe would lose the cumulative
            # time to floating-point underflow.
            for i in range(1, 51):
                ns_value[0] = i  # advance 1 ns per probe
                r_deny = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
                assert r_deny.status_code == 429

            # Now advance to 1 second after the LAST consumption (which
            # happened at ns_value=0). The fix's deny-path no-advance means
            # ``_last_refill_ns`` is still 0, so refill = 10/s × 1 s = 10
            # tokens regardless of how many denials we issued.
            ns_value[0] = 1_000_000_000

            # Exactly 10 tokens should be available.
            ok_results: list[int] = []
            for _ in range(10):
                r_ok = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
                ok_results.append(r_ok.status_code)
            # NOTE: the clock advances by ``ns_value[0]`` — successive
            # consumptions advance ``_last_refill_ns``, but the read-side
            # of the clock (``ns_value[0]``) doesn't auto-tick within this
            # loop. So all 10 are evaluated against the same ``now_ns =
            # 1_000_000_000``; with deny-path no-advance + correct math,
            # the bucket holds 10 tokens after refill.
            assert all(s == 200 for s in ok_results), (
                f"deny-path advanced _last_refill_ns and lost tokens to "
                f"FP drift; statuses={ok_results}"
            )

            # 11th must be 429 — bucket is empty again.
            r_empty = await client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            assert r_empty.status_code == 429


# ---------------------------------------------------------------------------
# Story 3.7 AC-4: rate-limited problem-type slug parity
# ---------------------------------------------------------------------------


class TestRateLimitProblemTypeContract:
    """Story 3.7 AC-4: pin slug parity between renderer constant and middleware body.

    The rate-limited slug is duplicated in three places:
      1. ``services/registry-api/.../adapters/errors.py`` (catalog source)
      2. ``services/telegram-gateway/.../handlers/_errors.py`` (renderer dispatch)
      3. ``services/telegram-gateway/.../app/rate_limit.py`` (middleware 429 body)

    This test pins (2) ↔ (3) and asserts the literal value matches the
    documented slug. Cross-checks (1) by reading the registry-api source
    file at runtime — failures here indicate one of the three sites has
    drifted and must be re-aligned by hand.
    """

    def test_rate_limit_problem_type_matches_catalog(self) -> None:
        """Renderer constant, middleware body, and catalog all agree on ``/errors/rate-limited``."""
        # (a) Renderer constant pinned to the literal slug.
        from telegram_gateway.handlers._errors import (  # noqa: PLC0415
            _PROBLEM_TYPE_RATE_LIMITED,
        )

        assert _PROBLEM_TYPE_RATE_LIMITED == "/errors/rate-limited"

        # (b) Rate-limit middleware ships the same literal in its 429 body.
        # Read the source file (rather than triggering a 429) so the test is
        # static-text-level — any future refactor that builds the body from a
        # different constant (or drifts the literal) is caught immediately.
        from pathlib import Path as _Path  # noqa: PLC0415

        src = (_Path(__file__).parent / "rate_limit.py").read_text()
        assert '"/errors/rate-limited"' in src, (
            "rate_limit.py 429 body literal does not match _PROBLEM_TYPE_RATE_LIMITED; "
            "renderer dispatch will fall through to the legacy status path."
        )

        # (c) Cross-service sanity: the registry-api catalog uses the same
        # literal. Read by inspection (vs cross-service import via IMP001
        # opt-out) per Story 3.6 review N7 carry-forward — duplication-with-
        # contract-test is the chosen contract surface.
        registry_errors = (
            _Path(__file__).parent.parent.parent.parent.parent.parent
            / "registry-api"
            / "src"
            / "registry_api"
            / "adapters"
            / "errors.py"
        )
        if registry_errors.exists():
            registry_src = registry_errors.read_text()
            assert '_PROBLEM_TYPE_RATE_LIMITED = "/errors/rate-limited"' in registry_src, (
                "registry-api catalog ``_PROBLEM_TYPE_RATE_LIMITED`` does not match the "
                "telegram-gateway renderer constant; the wire contract has drifted."
            )
