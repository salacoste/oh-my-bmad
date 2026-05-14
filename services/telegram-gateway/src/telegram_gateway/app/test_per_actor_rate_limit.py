"""Tests for PerActorRateLimitMiddleware (Story 7.5.1 AC-1/AC-2/AC-3).

Covers:
  AC-1: per-actor rate limiter after allowlist — non-allowlisted actors
        cannot consume tokens
  AC-2: layering documentation (static docstring check)
  AC-3: integration test — non-allowlisted burst does not deplete legitimate
        actor's bucket
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Update
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock, TickingClock

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import TELEGRAM_GATEWAY_ACTOR
from telegram_gateway.app.main import build_app
from telegram_gateway.app.middleware import AllowlistMiddleware
from telegram_gateway.app.rate_limit import PerActorRateLimitMiddleware

_ACTOR = TELEGRAM_GATEWAY_ACTOR
_BOT_TOKEN = "12345678:***FAKE-BOT-TOKEN***"
_WEBHOOK_SECRET = "***FAKE-WEBHOOK-SECRET-1234***"
_WEBHOOK_URL = "https://tunnel.example.com/v1/telegram/webhook"

_ALLOWLISTED_USER = 12345
_NON_ALLOWLISTED_USER = 99999


def _make_update(user_id: int, *, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": "/ping",
        },
    }


def _make_poll_answer_update(user_id: int, *, update_id: int = 1) -> dict[str, Any]:
    """Update with poll_answer (.user path) instead of .from_user."""
    return {
        "update_id": update_id,
        "poll_answer": {
            "poll_id": "123",
            "user": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "option_ids": [0],
            "option_persistent_ids": [],
        },
    }


# Type-safe recording emit helper.
EmitFunc = Callable[[Any], Awaitable[None]]


def _make_recording_emit() -> tuple[list[Any], EmitFunc]:
    captured: list[Any] = []

    async def emit(envelope: Any) -> None:
        captured.append(envelope)

    return captured, emit


def _patch_aiogram(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setenv("TG_ALLOWLIST_USER_IDS", f"[{_ALLOWLISTED_USER}]")
    return TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )


# ---------------------------------------------------------------------------
# Unit tests: direct PerActorRateLimitMiddleware invocation
# ---------------------------------------------------------------------------


class TestPerActorRateLimitUnit:
    """AC-1: unit-level per-actor rate limiting."""

    @pytest.mark.asyncio
    async def test_allowlisted_actor_consumes_own_bucket(self) -> None:
        """An allowlisted actor consuming their full bucket is rate-limited."""
        clock = TickingClock(start_ns=0, tick_ns=1)
        mw = PerActorRateLimitMiddleware(capacity=3, refill_per_second=1.0, clock=clock)

        invocations: list[Any] = []

        async def handler(event: Any, data: dict[str, Any]) -> Any:
            invocations.append(event)
            return "OK"

        update = Update.model_validate(_make_update(_ALLOWLISTED_USER))

        # First 3 should pass.
        results = []
        for _ in range(3):
            r = await mw(handler, update, {})
            results.append(r)
        assert all(r == "OK" for r in results)
        assert len(invocations) == 3

        # 4th should be dropped.
        r4 = await mw(handler, update, {})
        assert r4 is None
        assert len(invocations) == 3  # handler not called again

    @pytest.mark.asyncio
    async def test_independent_buckets_per_actor(self) -> None:
        """Two actors have independent token buckets."""
        clock = TickingClock(start_ns=0, tick_ns=1)
        mw = PerActorRateLimitMiddleware(capacity=2, refill_per_second=1.0, clock=clock)

        invocations: list[Any] = []

        async def handler(event: Any, data: dict[str, Any]) -> Any:
            invocations.append(event)
            return "OK"

        # User A exhausts their bucket.
        update_a = Update.model_validate(_make_update(111))
        for _ in range(2):
            await mw(handler, update_a, {})
        r_a_limited = await mw(handler, update_a, {})
        assert r_a_limited is None

        # User B should still have a full bucket.
        update_b = Update.model_validate(_make_update(222))
        r_b = await mw(handler, update_b, {})
        assert r_b == "OK"

    @pytest.mark.asyncio
    async def test_no_from_user_passes_through(self) -> None:
        """Updates without a user identity pass through (bot-only events)."""
        clock = TickingClock(start_ns=0, tick_ns=1)
        mw = PerActorRateLimitMiddleware(capacity=1, refill_per_second=1.0, clock=clock)

        invocations: list[Any] = []

        async def handler(event: Any, data: dict[str, Any]) -> Any:
            invocations.append(event)
            return "OK"

        # Bare update with no child events.
        update = Update.model_validate({"update_id": 1})
        r = await mw(handler, update, {})
        assert r == "OK"
        assert len(invocations) == 1

    @pytest.mark.asyncio
    async def test_token_refill_after_exhaustion(self) -> None:
        """H1: after exhausting the bucket, advancing the clock refills tokens."""
        # FrozenClock gives deterministic control over time — no implicit
        # refill from tick_ns progression (M6: avoids false-positive risk).
        clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        mw = PerActorRateLimitMiddleware(capacity=2, refill_per_second=1.0, clock=clock)

        invocations: list[Any] = []

        async def handler(event: Any, data: dict[str, Any]) -> Any:
            invocations.append(event)
            return "OK"

        update = Update.model_validate(_make_update(111))

        # Exhaust the bucket (capacity=2).
        await mw(handler, update, {})
        await mw(handler, update, {})
        r_limited = await mw(handler, update, {})
        assert r_limited is None

        # Advance clock by 1s → 1 token refilled (refill_per_second=1.0).
        clock._mono = 1_000_000_000  # 1 second in nanoseconds
        r_refilled = await mw(handler, update, {})
        assert r_refilled == "OK"

    @pytest.mark.asyncio
    async def test_deny_path_does_not_advance_last_refill_ns(self) -> None:
        """M3: denied requests must NOT advance last_refill_ns (M6 invariant).

        Under sustained overload, if the deny path updated last_refill_ns,
        sub-ULP elapsed_s increments could silently lose accumulated refill
        time. The deny path preserves the timestamp so the next successful
        consumption refills from the last consumed time.
        """
        clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        mw = PerActorRateLimitMiddleware(capacity=1, refill_per_second=1.0, clock=clock)

        async def handler(event: Any, data: dict[str, Any]) -> Any:
            return "OK"

        update = Update.model_validate(_make_update(111))

        # Consume the only token at t=0.
        r1 = await mw(handler, update, {})
        assert r1 == "OK"

        # Advance to t=0.5s — not enough for a refill (need 1.0s).
        clock._mono = 500_000_000
        r2 = await mw(handler, update, {})
        assert r2 is None

        # last_refill_ns should still be 0 (the consume timestamp),
        # not 500_000_000. Advance to t=1.0s — now we should have
        # 1 full refill from t=0 to t=1.0s, NOT from t=0.5 to t=1.0.
        clock._mono = 1_000_000_000
        r3 = await mw(handler, update, {})
        assert r3 == "OK"  # would fail if deny path advanced last_refill_ns

    @pytest.mark.asyncio
    async def test_extract_user_id_via_user_attribute(self) -> None:
        """M5: _extract_user_id finds user via .user path (e.g. poll_answer)."""
        mw = PerActorRateLimitMiddleware(
            capacity=5,
            refill_per_second=1.0,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
        )

        update = Update.model_validate(_make_poll_answer_update(42))
        uid = mw._extract_user_id(update)
        assert uid == 42

    def test_constructor_rejects_zero_capacity(self) -> None:
        """M4: capacity=0 raises ValueError."""
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            PerActorRateLimitMiddleware(
                capacity=0,
                refill_per_second=1.0,
                clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            )

    def test_constructor_rejects_negative_refill(self) -> None:
        """M4: refill_per_second <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="refill_per_second must be > 0"):
            PerActorRateLimitMiddleware(
                capacity=5,
                refill_per_second=0.0,
                clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
            )


# ---------------------------------------------------------------------------
# AC-3: Integration test — non-allowlisted burst does not drain legitimate
# actor's per-actor bucket (tested at aiogram dispatcher level)
# ---------------------------------------------------------------------------


class TestPerActorRateLimitIntegration:
    """AC-3: non-allowlisted burst cannot drain a legitimate actor's bucket."""

    @pytest.mark.asyncio
    async def test_non_allowlisted_burst_does_not_deplete_legitimate_actor(
        self,
    ) -> None:
        """Non-allowlisted burst does NOT drain legitimate actor's per-actor bucket.

        Tests at the aiogram dispatcher level (bypassing the HTTP bucket)
        so the test isolates the per-actor limiter from Layer 1.

        H2: explicitly asserts that the non-allowlisted user never appears
        in the per-actor bucket dict — proving they consumed zero tokens.
        """
        clock = TickingClock(start_ns=0, tick_ns=1)
        allowlist = frozenset({_ALLOWLISTED_USER})
        captured, emit = _make_recording_emit()

        allowlist_mw = AllowlistMiddleware(
            allowlist=allowlist,
            emit=emit,
            actor=_ACTOR,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
        )
        per_actor_mw = PerActorRateLimitMiddleware(
            capacity=3,
            refill_per_second=1.0,
            clock=clock,
        )

        handler_invocations: list[Any] = []

        async with Bot(token="1234:fake-bot-token") as bot:
            dp = Dispatcher()
            # Register in order: allowlist first, per-actor second.
            dp.update.outer_middleware.register(allowlist_mw)
            dp.update.outer_middleware.register(per_actor_mw)

            @dp.message()
            async def _handler(message: Any) -> None:
                handler_invocations.append(message.from_user.id)

            # Phase 1: burst from non-allowlisted actor.
            # AllowlistMiddleware drops all of these; PerActorRateLimitMiddleware
            # never sees them.
            for i in range(50):
                update = Update.model_validate(
                    _make_update(_NON_ALLOWLISTED_USER, update_id=i + 1)
                )
                await dp.feed_update(bot, update)

            # All 50 were rejected by allowlist — handler never ran.
            assert len(handler_invocations) == 0
            assert len(captured) == 50  # all emitted telegram.rejected

            # H2: non-allowlisted user must NOT have a bucket entry.
            assert _NON_ALLOWLISTED_USER not in per_actor_mw._buckets, (
                "non-allowlisted user consumed per-actor tokens"
            )

            # Phase 2: legitimate allowlisted actor sends requests.
            # Per-actor bucket should be FULL (untouched by non-allowlisted burst).
            for i in range(3):
                update = Update.model_validate(
                    _make_update(_ALLOWLISTED_USER, update_id=100 + i)
                )
                await dp.feed_update(bot, update)

            # All 3 should have passed through — per-actor bucket was not drained.
            assert len(handler_invocations) == 3
            assert all(uid == _ALLOWLISTED_USER for uid in handler_invocations)

    @pytest.mark.asyncio
    async def test_per_actor_limit_enforced_after_allowlist(self) -> None:
        """AC-1: an allowlisted actor exceeding their per-actor bucket is rate-limited."""
        clock = TickingClock(start_ns=0, tick_ns=1)
        allowlist = frozenset({_ALLOWLISTED_USER})
        captured, emit = _make_recording_emit()

        allowlist_mw = AllowlistMiddleware(
            allowlist=allowlist,
            emit=emit,
            actor=_ACTOR,
            clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
        )
        per_actor_mw = PerActorRateLimitMiddleware(
            capacity=2,
            refill_per_second=1.0,
            clock=clock,
        )

        handler_invocations: list[Any] = []

        async with Bot(token="1234:fake-bot-token") as bot:
            dp = Dispatcher()
            dp.update.outer_middleware.register(allowlist_mw)
            dp.update.outer_middleware.register(per_actor_mw)

            @dp.message()
            async def _handler(message: Any) -> None:
                handler_invocations.append(message.from_user.id)

            # Allowlisted user sends 4 requests with capacity=2.
            for i in range(4):
                update = Update.model_validate(
                    _make_update(_ALLOWLISTED_USER, update_id=i + 1)
                )
                await dp.feed_update(bot, update)

            # Only first 2 should reach the handler; last 2 dropped by per-actor limiter.
            assert len(handler_invocations) == 2
            # No allowlist rejections (user is allowlisted).
            assert len(captured) == 0


# ---------------------------------------------------------------------------
# AC-2: documentation check (static)
# ---------------------------------------------------------------------------


class TestPerActorRateLimitDocs:
    """AC-2: verify the two-layer architecture is documented."""

    def test_rate_limit_module_has_two_layer_docstring(self) -> None:
        """L5: rate_limit.py module docstring documents both layers specifically."""
        from telegram_gateway.app import rate_limit as mod

        doc = mod.__doc__
        assert doc is not None
        doc_lower = doc.lower()
        # Must mention both layers and their purposes.
        assert "layer 1" in doc_lower, "docstring must describe Layer 1"
        assert "layer 2" in doc_lower, "docstring must describe Layer 2"
        assert "per-actor" in doc_lower or "per_actor" in doc_lower
        assert "allowlist" in doc_lower, (
            "docstring must explain relationship to allowlist"
        )
        # Must include the request flow diagram.
        assert "allowlistmiddleware" in doc_lower, (
            "docstring must show AllowlistMiddleware in flow"
        )

    @pytest.mark.asyncio
    async def test_per_actor_middleware_registered_after_allowlist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """PerActorRateLimitMiddleware is registered AFTER AllowlistMiddleware in outer chain."""
        _patch_aiogram(monkeypatch)
        settings = _make_settings(monkeypatch, tmp_path)
        app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))

        async with LifespanManager(app):
            dp = app.state.dp
            outer_chain = list(dp.update.outer_middleware)
            aiogram_builtins = frozenset(
                {"ErrorsMiddleware", "UserContextMiddleware", "FSMContextMiddleware"}
            )
            user_mws = [mw for mw in outer_chain if type(mw).__name__ not in aiogram_builtins]

            # At least AllowlistMiddleware + PerActorRateLimitMiddleware
            assert len(user_mws) >= 2, (
                f"expected >= 2 user-registered outer middlewares; "
                f"got {[type(m).__name__ for m in user_mws]!r}"
            )

            # AllowlistMiddleware must be first.
            assert isinstance(user_mws[0], AllowlistMiddleware), (
                f"expected AllowlistMiddleware first; got {type(user_mws[0]).__name__!r}"
            )

            # PerActorRateLimitMiddleware must be second.
            assert isinstance(user_mws[1], PerActorRateLimitMiddleware), (
                f"expected PerActorRateLimitMiddleware second; got {type(user_mws[1]).__name__!r}"
            )
