"""Tests for WebhookRateLimitMiddleware (Story 3.6 AC-5/6/7/10).

11 tests covering:
  AC-7: burst-then-429, refill behaviour (fractional + full)
  AC-5: RFC 7807 body, Retry-After header, init validation
  AC-6: passthrough for non-webhook routes
  Lock invariant: concurrent requests no double-spend
  Clock injection: TickingClock controls bucket; time.monotonic not called
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
_BOT_TOKEN = "1234:fake-bot-token"
_WEBHOOK_SECRET = "fake-webhook-secret-1234"
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
# AC-7: Burst of 20 passes, 21st is 429
# ---------------------------------------------------------------------------


class TestRateLimitBurst:
    """AC-7: burst behaviour of the token-bucket."""

    @pytest.mark.asyncio
    async def test_rate_limit_passes_first_20_burst(self, webhook_client: AsyncClient) -> None:
        """First 20 POST requests to the webhook path within the window all return 200."""
        results = []
        for _ in range(20):
            r = await webhook_client.post(
                _WEBHOOK_PATH,
                json=_UPDATE,
                headers=_webhook_headers(),
            )
            results.append(r.status_code)
        # The webhook handler returns 200 for valid updates.
        non_rate_limited = [s for s in results if s != 429]
        assert len(non_rate_limited) == 20, f"Expected all 20 requests to pass; statuses: {results}"

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

        Uses a FrozenClock advanced manually: first call returns t=0 (bucket
        init), burst drains 20 tokens, then we swap the clock to t=0.5 s and
        assert the next 5 pass but the 6th is 429.
        """
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        # ManualClock: tracks current ns so we can advance it between calls.
        ns_value = [0]

        class _ManualClock:
            def now(self):  # type: ignore[override]
                from datetime import UTC, datetime  # noqa: PLC0415

                return datetime.now(UTC)

            def monotonic_ns(self) -> int:
                return ns_value[0]

        clock = _ManualClock()
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
        """After 2 s (20 tokens at 10/s, capped at capacity=20), full burst available."""
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)

        ns_value = [0]

        class _ManualClock:
            def now(self):  # type: ignore[override]
                from datetime import UTC, datetime  # noqa: PLC0415

                return datetime.now(UTC)

            def monotonic_ns(self) -> int:
                return ns_value[0]

        clock = _ManualClock()
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
            non_429 = [s for s in results if s != 429]
            assert len(non_429) == 20, f"Expected 20 passes after 2 s refill; statuses: {results}"

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
        self, webhook_client: AsyncClient
    ) -> None:
        """25 concurrent requests: exactly 20 succeed, 5 are 429.

        asyncio.gather fires all coroutines concurrently. The lock ensures
        tokens are consumed atomically — no two coroutines can observe the same
        non-empty bucket simultaneously.

        @pytest.mark.slow omitted intentionally: 25 in-process async tasks
        complete in <100 ms, well within the PR-gate budget.
        """
        tasks = [
            webhook_client.post(_WEBHOOK_PATH, json=_UPDATE, headers=_webhook_headers())
            for _ in range(25)
        ]
        responses = await asyncio.gather(*tasks)
        statuses = [r.status_code for r in responses]
        successes = sum(1 for s in statuses if s != 429)
        rate_limited = sum(1 for s in statuses if s == 429)
        assert successes == 20, (
            f"Expected exactly 20 successes; got successes={successes}, "
            f"rate_limited={rate_limited}, statuses={sorted(statuses)}"
        )
        assert rate_limited == 5, f"Expected exactly 5 rate-limited; got {rate_limited}"


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
        import ast as _ast  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415

        src = (_Path(__file__).parent / "rate_limit.py").read_text()
        tree = _ast.parse(src)
        direct_time_imports = [
            node
            for node in _ast.walk(tree)
            if isinstance(node, _ast.Import) and any(alias.name == "time" for alias in node.names)
        ]
        assert not direct_time_imports, (
            f"rate_limit.py must not import 'time' directly; found: {direct_time_imports}"
        )
