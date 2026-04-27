"""Tests for :class:`telegram_gateway.app.config.TelegramSettings` (Story 3.1 AC-2 / AC-11).

Fixture-string convention (per AC-12): every secret literal here uses a
non-Telegram-shaped string like ``"1234:fake-bot-token"`` —
:mod:`secret_hygiene.scanner`'s Telegram bot-token regex is
``\\d+:[A-Za-z0-9_-]{35}`` so a 4-digit suffix without a colon never
matches. Do NOT shorten the comment to a real-shaped token by accident
(no colon, no 35-char suffix).
"""

from __future__ import annotations

import pytest
from events.envelope import Actor
from pydantic import ValidationError

from telegram_gateway.app.config import TelegramSettings

_ACTOR = Actor(kind="system", id="telegram-gateway")


def test_from_env_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """All required env-vars present → instance is populated and audited."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://tunnel.example.com/v1")

    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)

    # AuditedSecret.value reads the plaintext but emit=None so no audit fires.
    assert settings.bot_token.value == "1234:fake-bot-token"
    assert settings.webhook_secret_token.value == "fake-webhook-secret-1234"
    # HttpUrl normalizes — the parsed string round-trips.
    assert str(settings.webhook_url).startswith("https://tunnel.example.com")
    assert settings.webhook_path == "/v1/telegram/webhook"


def test_webhook_url_rejects_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """architecture.md:217 — Telegram webhook MUST be https."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "http://tunnel.example.com/v1")

    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert "webhook_url must be https" in str(exc_info.value)


def test_missing_bot_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAIL-CLOSED: missing TELEGRAM_BOT_TOKEN → ValidationError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://tunnel.example.com/v1")

    with pytest.raises(ValidationError):
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
