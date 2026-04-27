"""Tests for :mod:`telegram_gateway.app.lifespan` (Story 3.1 AC-4 / AC-5).

Strategy: monkeypatch :py:meth:`aiogram.Bot.set_webhook` and
:py:meth:`aiogram.client.session.base.BaseSession.close` to record
their invocations without live Telegram traffic. The lifespan is then
driven via :class:`asgi_lifespan.LifespanManager` against a real
``build_app`` so the full ``AsyncExitStack`` ordering is exercised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.main import build_app

_ACTOR = Actor(kind="system", id="telegram-gateway")


@pytest.fixture
def env_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Populate required env-vars and return shared values for assertions."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://tunnel.example.com/v1/telegram/webhook")
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    return {
        "events_dir": tmp_path / "events",
        "expected_url": "https://tunnel.example.com/v1/telegram/webhook",
        "expected_secret_token": "fake-webhook-secret-1234",
    }


@pytest_asyncio.fixture
async def patched_aiogram(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Patch ``Bot.set_webhook`` + ``AiohttpSession.close`` to record calls.

    Returns a dict of recorders that tests can introspect.
    """
    set_webhook_calls: list[dict[str, Any]] = []
    session_close_calls: list[int] = []

    async def fake_set_webhook(self: Bot, **kwargs: Any) -> bool:
        set_webhook_calls.append(kwargs)
        return True

    async def fake_session_close(self: AiohttpSession) -> None:
        session_close_calls.append(1)

    monkeypatch.setattr(Bot, "set_webhook", fake_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", fake_session_close)

    return {
        "set_webhook_calls": set_webhook_calls,
        "session_close_calls": session_close_calls,
    }


def _seed_settings() -> TelegramSettings:
    """Build the placeholder TelegramSettings (env-vars must already be set)."""
    return TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )


@pytest.mark.asyncio
async def test_set_webhook_called_with_audited_url_and_drop_pending(
    env_setup: dict[str, Any], patched_aiogram: dict[str, Any]
) -> None:
    """AC-5: set_webhook called with the audited URL/token + drop_pending_updates=True."""
    settings = _seed_settings()
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        pass  # startup-only assertions

    assert len(patched_aiogram["set_webhook_calls"]) == 1
    call = patched_aiogram["set_webhook_calls"][0]
    assert call["url"] == env_setup["expected_url"]
    assert call["secret_token"] == env_setup["expected_secret_token"]
    assert call["drop_pending_updates"] is True


@pytest.mark.asyncio
async def test_bot_session_closed_on_shutdown(
    env_setup: dict[str, Any], patched_aiogram: dict[str, Any]
) -> None:
    """AC-3 lifespan: ``bot.session.close()`` runs on shutdown."""
    settings = _seed_settings()
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        # Session not closed during startup.
        assert patched_aiogram["session_close_calls"] == []
    # After context exit, session.close has been awaited at least once.
    assert len(patched_aiogram["session_close_calls"]) >= 1


@pytest.mark.asyncio
async def test_flush_pending_emissions_invoked_on_shutdown(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4: flush_pending_emissions runs on shutdown (Story 2.16 H6).

    We patch the symbol imported into :mod:`telegram_gateway.app.lifespan`
    to record invocations without altering its behavior — the real
    helper is still safe to call, but recording lets us assert the
    drain happened on the teardown path.
    """
    flush_calls: list[float] = []
    from secret_hygiene import flush_pending_emissions as _real_flush

    from telegram_gateway.app import lifespan as lifespan_mod

    async def recording_flush(timeout: float = 1.0) -> None:
        flush_calls.append(timeout)
        await _real_flush(timeout)

    monkeypatch.setattr(lifespan_mod, "flush_pending_emissions", recording_flush)

    settings = _seed_settings()
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        pass

    assert len(flush_calls) == 1
    # Story 3.1 lifespan pins timeout=2.0s (matches registry-api precedent).
    assert flush_calls[0] == pytest.approx(2.0)
