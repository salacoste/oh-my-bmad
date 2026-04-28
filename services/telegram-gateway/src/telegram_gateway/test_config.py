"""Tests for :class:`telegram_gateway.app.config.TelegramSettings` (Story 3.1 AC-2 / AC-11).

Fixture-string convention (per AC-12 / review-fix L2): every secret literal
here uses a ``"1234:fake-bot-token"`` shape — :mod:`secret_hygiene.scanner`'s
Telegram bot-token regex is ``\\d{6,12}:AA[A-Za-z0-9_\\-]{30,}`` so a 4-digit
prefix without the ``AA`` marker never matches. Do NOT shorten to a real-shaped
token by accident (keep the digit prefix short and omit the ``AA`` suffix).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from events.envelope import Actor
from pydantic import ValidationError

from telegram_gateway.app.config import TelegramSettings

_ACTOR = Actor(kind="system", id="telegram-gateway")

# Canonical matching URL / path used by most tests so H2 (url-path ≡ webhook_path)
# is satisfied without extra env overrides in each test.
_URL = "https://tunnel.example.com/v1/telegram/webhook"
_PATH = "/v1/telegram/webhook"  # same as the default field value


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the minimum required env-vars with a URL/path that passes H2."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _URL)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_from_env_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All required env-vars present → instance is populated and audited."""
    _base_env(monkeypatch)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))

    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)

    assert settings.bot_token.value == "1234:fake-bot-token"
    assert settings.webhook_secret_token.value == "fake-webhook-secret-1234"
    assert str(settings.webhook_url).startswith("https://tunnel.example.com")
    assert settings.webhook_path == _PATH


# ---------------------------------------------------------------------------
# webhook_url HTTPS enforcement (AC-11)
# ---------------------------------------------------------------------------


def test_webhook_url_rejects_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """architecture.md:217 — Telegram webhook MUST be https."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "http://tunnel.example.com/v1/telegram/webhook")
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))

    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert "webhook_url must be https" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_bot_token_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FAIL-CLOSED: missing TELEGRAM_BOT_TOKEN → ValidationError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))

    with pytest.raises(ValidationError):
        TelegramSettings.from_env(emit=None, actor=_ACTOR)


# ---------------------------------------------------------------------------
# webhook_url path ≡ webhook_path (review-fix H2)
# ---------------------------------------------------------------------------


def test_webhook_url_path_mismatch_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """H2: URL path that differs from webhook_path raises ValidationError."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    # URL path /v2/... but webhook_path defaults to /v1/...
    monkeypatch.setenv(
        "TELEGRAM_WEBHOOK_URL",
        "https://tunnel.example.com/v2/telegram/webhook",
    )
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))

    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert "webhook_url path must match webhook_path" in str(exc_info.value)


def test_webhook_url_path_match_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """H2: matching url path and webhook_path passes validation."""
    _base_env(monkeypatch)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert settings.webhook_path == _PATH


# ---------------------------------------------------------------------------
# Private-IP / loopback webhook_url rejection (review-fix M13)
# ---------------------------------------------------------------------------


def test_webhook_url_rejects_localhost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """M13: localhost webhook_url is rejected."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv(
        "TELEGRAM_WEBHOOK_URL",
        "https://localhost/v1/telegram/webhook",
    )
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    err = str(exc_info.value).lower()
    assert "loopback" in err or "localhost" in err or "wildcard" in err


def test_webhook_url_rejects_private_ipv4(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """M13: RFC1918 IP is rejected."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv(
        "TELEGRAM_WEBHOOK_URL",
        "https://192.168.1.10/v1/telegram/webhook",
    )
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    err = str(exc_info.value).lower()
    assert "private" in err or "192.168" in err


def test_webhook_url_public_ip_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """M13: a genuinely public (global) IP passes the private-IP check."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    # 1.1.1.1 is Cloudflare's public DNS — is_private=False, is_global=True
    monkeypatch.setenv(
        "TELEGRAM_WEBHOOK_URL",
        "https://1.1.1.1/v1/telegram/webhook",
    )
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert "1.1.1.1" in str(settings.webhook_url)


# ---------------------------------------------------------------------------
# userinfo rejection (review-fix L10)
# ---------------------------------------------------------------------------


def test_webhook_url_rejects_userinfo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """L10: userinfo (user:pass@) in webhook_url is rejected."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv(
        "TELEGRAM_WEBHOOK_URL",
        "https://user:pass@tunnel.example.com/v1/telegram/webhook",
    )
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert "userinfo" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# webhook_secret_token ASCII-printable (review-fix L8)
# ---------------------------------------------------------------------------


def test_webhook_secret_token_ascii_printable_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L8: purely ASCII-printable token passes."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "abcABC123!@_-=+")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert settings.webhook_secret_token.value == "abcABC123!@_-=+"


def test_webhook_secret_token_non_ascii_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L8: non-ASCII character in secret token raises ValidationError."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    # U+00E9 (é) is non-ASCII
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "secretévalue")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    with pytest.raises(ValidationError) as exc_info:
        TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert "ascii-printable" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# event_log_dir writability probe (review-fix M12)
# ---------------------------------------------------------------------------


def test_event_log_dir_readonly_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """M12: non-writable event_log_dir raises ValidationError at config load."""
    if sys.platform != "linux":
        pytest.skip("read-only probe requires /proc/sys (Linux only)")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _URL)
    monkeypatch.setenv("EVENT_LOG_DIR", "/proc/sys/kernel")
    with pytest.raises((ValidationError, OSError)):
        TelegramSettings.from_env(emit=None, actor=_ACTOR)


def test_event_log_dir_writable_creates_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M12: writable tmp directory passes the probe and creates the dir."""
    import os

    events_dir = tmp_path / "new_events"
    assert not events_dir.exists()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-1234")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(events_dir))
    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)
    assert settings.event_log_dir == events_dir
    assert os.path.isdir(events_dir)
