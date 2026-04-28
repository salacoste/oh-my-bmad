"""Test for ``GET /v1/health`` (Story 3.1 AC-8)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient

from telegram_gateway import __version__
from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import _TELEGRAM_GATEWAY_ACTOR  # review-fix L17
from telegram_gateway.app.main import build_app

_ACTOR = _TELEGRAM_GATEWAY_ACTOR  # review-fix L17: canonical import, not magic string


@pytest_asyncio.fixture
async def client(  # review-fix L3: renamed from health_client → client
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv(
        "TELEGRAM_WEBHOOK_URL",
        "https://tunnel.example.com/v1/telegram/webhook",
    )
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))

    async def fake_set_webhook(self: Bot, **kwargs: Any) -> bool:
        return True

    async def fake_session_close(self: AiohttpSession) -> None:
        return None

    monkeypatch.setattr(Bot, "set_webhook", fake_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", fake_session_close)

    settings = TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://testserver") as c,
    ):
        yield c


@pytest.mark.asyncio
async def test_health_returns_envelope(client: AsyncClient) -> None:
    """AC-8: ``GET /v1/health`` returns ``{status, service, version}`` JSON."""
    r = await client.get("/v1/health")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body == {
        "status": "ok",
        "service": "telegram-gateway",
        "version": __version__,
    }
