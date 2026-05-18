"""Tests for :mod:`telegram_gateway.app.lifespan` (Story 3.1 AC-4 / AC-5).

Strategy: monkeypatch :py:meth:`aiogram.Bot.set_webhook` and
:py:meth:`aiogram.client.session.base.BaseSession.close` to record
their invocations without live Telegram traffic. The lifespan is then
driven via :class:`asgi_lifespan.LifespanManager` against a real
``build_app`` so the full ``AsyncExitStack`` ordering is exercised.

Review-fix L17: actor constant imported from lifespan module rather than
magic-string repeated across files.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed (mirror of registry_api/test_app.py:48); see TODO in conftest.py
    EventLogWriter,
)
from secret_hygiene import flush_pending_emissions

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import _TELEGRAM_GATEWAY_ACTOR  # review-fix L17
from telegram_gateway.app.main import build_app

_ACTOR = Actor(kind="system", id="telegram-gateway")

_BOT_TOKEN = "1234:fake-bot-token"
_WEBHOOK_SECRET = "fake-webhook-secret-1234"
_WEBHOOK_URL = "https://tunnel.example.com/v1/telegram/webhook"


@pytest.fixture
def env_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Populate required env-vars and return shared values for assertions."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", _WEBHOOK_SECRET)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _WEBHOOK_URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))
    return {
        "events_dir": tmp_path / "events",
        "expected_url": _WEBHOOK_URL,
        "expected_secret_token": _WEBHOOK_SECRET,
    }


@pytest_asyncio.fixture
async def patched_aiogram(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Patch ``Bot.set_webhook`` + ``AiohttpSession.close`` to record calls."""
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


def _seed_settings(tmp_path: Path) -> TelegramSettings:
    """Build the placeholder TelegramSettings (env-vars must already be set)."""
    return TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )


# ---------------------------------------------------------------------------
# AC-5 / AC-4 baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_webhook_called_with_audited_url_and_drop_pending(
    env_setup: dict[str, Any], patched_aiogram: dict[str, Any], tmp_path: Path
) -> None:
    """AC-5: set_webhook called with the audited URL/token + drop_pending_updates=True."""
    settings = _seed_settings(tmp_path)
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        pass

    assert len(patched_aiogram["set_webhook_calls"]) == 1
    call = patched_aiogram["set_webhook_calls"][0]
    # Review-fix M17: assert against the expected URL directly; both sides
    # are plain strings derived from the same env-var → no trailing-slash quirk.
    assert call["url"] == env_setup["expected_url"]
    assert call["secret_token"] == env_setup["expected_secret_token"]
    assert call["drop_pending_updates"] is True


@pytest.mark.asyncio
async def test_bot_session_closed_on_shutdown(
    env_setup: dict[str, Any], patched_aiogram: dict[str, Any], tmp_path: Path
) -> None:
    """AC-3 lifespan: ``bot.session.close()`` runs on shutdown."""
    settings = _seed_settings(tmp_path)
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        assert patched_aiogram["session_close_calls"] == []
    assert len(patched_aiogram["session_close_calls"]) >= 1


@pytest.mark.asyncio
async def test_flush_pending_emissions_invoked_on_shutdown(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-4: flush_pending_emissions runs on shutdown (Story 2.16 H6)."""
    flush_calls: list[float] = []
    from secret_hygiene import flush_pending_emissions as _real_flush

    from telegram_gateway.app import lifespan as lifespan_mod

    async def recording_flush(timeout: float = 1.0) -> None:
        flush_calls.append(timeout)
        await _real_flush(timeout)

    monkeypatch.setattr(lifespan_mod, "flush_pending_emissions", recording_flush)

    settings = _seed_settings(tmp_path)
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        pass

    assert len(flush_calls) == 1
    # Review-fix L4: plain == instead of pytest.approx for exact float comparison.
    assert flush_calls[0] == 2.0


# ---------------------------------------------------------------------------
# Flush-before-writer-close ordering (review-fix M14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_runs_before_writer_close(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """M14: flush_pending_emissions completes BEFORE writer.close on teardown."""
    timestamps: dict[str, float] = {}
    from secret_hygiene import flush_pending_emissions as _real_flush

    from telegram_gateway.app import lifespan as lifespan_mod

    async def recording_flush(timeout: float = 1.0) -> None:
        timestamps["flush"] = time.monotonic()
        await _real_flush(timeout)

    async def recording_writer_close(self: EventLogWriter) -> None:
        timestamps["writer_close"] = time.monotonic()

    monkeypatch.setattr(lifespan_mod, "flush_pending_emissions", recording_flush)
    monkeypatch.setattr(EventLogWriter, "close", recording_writer_close)

    settings = _seed_settings(tmp_path)
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))

    async with LifespanManager(app):
        pass

    assert "flush" in timestamps, "flush_pending_emissions was not called on teardown"
    assert "writer_close" in timestamps, "writer.close was not called on teardown"
    assert timestamps["flush"] <= timestamps["writer_close"], (
        f"flush ({timestamps['flush']:.9f}) must run before "
        f"writer.close ({timestamps['writer_close']:.9f})"
    )


# ---------------------------------------------------------------------------
# Failure-path tests (review-fixes M9, M10, M15)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_webhook_failure_unwinds_cleanly(
    env_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """M9/M15: set_webhook raises → startup propagates + bot.session.close called."""
    session_close_calls: list[int] = []

    async def failing_set_webhook(self: Bot, **kwargs: Any) -> bool:
        raise RuntimeError("Telegram API unavailable")

    async def recording_session_close(self: AiohttpSession) -> None:
        session_close_calls.append(1)

    monkeypatch.setattr(Bot, "set_webhook", failing_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", recording_session_close)

    settings = _seed_settings(tmp_path)
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))

    with pytest.raises(RuntimeError, match="Telegram API unavailable"):
        async with LifespanManager(app):
            pass  # pragma: no cover

    assert len(session_close_calls) >= 1, (
        "bot.session.close must be called even when set_webhook raises"
    )


@pytest.mark.asyncio
async def test_bot_session_close_called_when_set_webhook_raises(
    env_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """M15 explicit pin: session.close invariant on set_webhook failure."""
    session_close_calls: list[int] = []

    async def failing_set_webhook(self: Bot, **kwargs: Any) -> bool:
        raise RuntimeError("set_webhook failed")

    async def recording_session_close(self: AiohttpSession) -> None:
        session_close_calls.append(1)

    monkeypatch.setattr(Bot, "set_webhook", failing_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", recording_session_close)

    settings = _seed_settings(tmp_path)
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))

    with pytest.raises(RuntimeError):
        async with LifespanManager(app):
            pass  # pragma: no cover

    assert session_close_calls, "bot.session.close must be called on set_webhook failure"


@pytest.mark.asyncio
async def test_from_env_failure_unwinds_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """M10: from_env raises inside lifespan → writer.close still called."""
    writer_close_calls: list[int] = []

    async def recording_writer_close(self: EventLogWriter) -> None:
        writer_close_calls.append(1)

    monkeypatch.setattr(EventLogWriter, "close", recording_writer_close)

    # Seed env for settings_seed construction ...
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", _WEBHOOK_SECRET)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _WEBHOOK_URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(tmp_path / "events"))

    settings = TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))

    # Remove token so lifespan's internal from_env call fails.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(Exception):  # noqa: B017,BLE001 — ValidationError subclass varies
        async with LifespanManager(app):
            pass  # pragma: no cover

    assert writer_close_calls, "writer.close must be called even when from_env fails"


# ---------------------------------------------------------------------------
# Log-leak assertion (review-fix M7 / AC-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_line_does_not_leak_secrets(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M7/AC-5: no log record leaks bot_token or webhook_secret_token plaintext."""
    settings = _seed_settings(tmp_path)
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))

    with caplog.at_level(logging.DEBUG):
        async with LifespanManager(app):
            pass

    for record in caplog.records:
        msg = record.getMessage()
        assert _BOT_TOKEN not in msg, f"log record leaks bot_token: {record.name}: {msg!r}"
        assert _WEBHOOK_SECRET not in msg, (
            f"log record leaks webhook_secret: {record.name}: {msg!r}"
        )


# ---------------------------------------------------------------------------
# Bot construction emits exactly 1 audit event (review-fix L12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_construction_emits_one_audit_event(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    tmp_path: Path,
) -> None:
    """L12: Bot() reads bot_token.value exactly once → 1 audit envelope."""
    captured: list[EventEnvelope] = []
    real_append = EventLogWriter.append

    async def recording_append(self: EventLogWriter, envelope: EventEnvelope) -> None:
        captured.append(envelope)
        await real_append(self, envelope)

    with patch.object(EventLogWriter, "append", recording_append):
        settings = _seed_settings(tmp_path)
        app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
        async with LifespanManager(app):
            pass

    await flush_pending_emissions(timeout=2.0)

    bot_token_envelopes = [
        e
        for e in captured
        if e.type == "secret.accessed"
        and (
            (hasattr(e.payload, "secret_name") and e.payload.secret_name == "telegram_bot_token")
            or (
                isinstance(e.payload, dict) and e.payload.get("secret_name") == "telegram_bot_token"
            )
        )
    ]
    assert len(bot_token_envelopes) == 1, (
        f"expected exactly 1 bot_token audit event, got {len(bot_token_envelopes)}: "
        f"{[e.type for e in captured]}"
    )


# ---------------------------------------------------------------------------
# Actor constant (review-fix L17)
# ---------------------------------------------------------------------------


def test_telegram_gateway_actor_identity() -> None:
    """L17: _TELEGRAM_GATEWAY_ACTOR has the canonical identity."""
    assert _TELEGRAM_GATEWAY_ACTOR.kind == "system"
    assert _TELEGRAM_GATEWAY_ACTOR.id == "telegram-gateway"


# ---------------------------------------------------------------------------
# Story 9.7 pass-3 UH-1: /trace router wiring regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_wires_trace_router_with_chat_allowlist(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """UH-1: lifespan calls ``make_trace_router`` with the audited chat allowlist.

    Pass-2 TH-B1 added ``trace_allowed_chat_ids`` plumbing but had no
    regression test asserting the wiring. Pass-3 UH-1: assert the
    ``make_trace_router`` factory is invoked AND the ``allowed_chat_ids``
    kwarg matches ``audited.trace_allowed_chat_ids`` (with the documented
    fallback to ``tg_allowlist_user_ids`` when the former is empty).
    """
    from telegram_gateway.app import lifespan as lifespan_mod

    captured_kwargs: list[dict[str, Any]] = []
    real_make_trace_router = lifespan_mod.make_trace_router

    def recording_make_trace_router(**kwargs: Any) -> Any:
        captured_kwargs.append(kwargs)
        return real_make_trace_router(**kwargs)

    monkeypatch.setattr(lifespan_mod, "make_trace_router", recording_make_trace_router)
    # Operator sets only TG_ALLOWLIST_USER_IDS — TRACE_ALLOWED_CHAT_IDS is empty
    # so the lifespan fallback path is exercised.
    monkeypatch.setenv("TG_ALLOWLIST_USER_IDS", "[12345]")

    settings = _seed_settings(tmp_path)
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        pass

    assert len(captured_kwargs) == 1, (
        f"make_trace_router must be called exactly once; got {len(captured_kwargs)}"
    )
    kwargs = captured_kwargs[0]
    assert "allowed_chat_ids" in kwargs, "make_trace_router must receive allowed_chat_ids kwarg"
    # The fallback path: trace_allowed_chat_ids empty → use tg_allowlist_user_ids.
    assert kwargs["allowed_chat_ids"] == frozenset({12345}), (
        f"expected fallback to tg_allowlist_user_ids; got {kwargs['allowed_chat_ids']!r}"
    )


@pytest.mark.asyncio
async def test_lifespan_wires_trace_router_with_explicit_trace_allowlist(
    env_setup: dict[str, Any],
    patched_aiogram: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """UH-1: explicit ``TRACE_ALLOWED_CHAT_IDS`` is used verbatim by the wiring.

    Includes a negative chat id (Telegram group) to pin the pass-3 UH-1
    validator fix — group allowlists must be possible.
    """
    from telegram_gateway.app import lifespan as lifespan_mod

    captured_kwargs: list[dict[str, Any]] = []
    real_make_trace_router = lifespan_mod.make_trace_router

    def recording_make_trace_router(**kwargs: Any) -> Any:
        captured_kwargs.append(kwargs)
        return real_make_trace_router(**kwargs)

    monkeypatch.setattr(lifespan_mod, "make_trace_router", recording_make_trace_router)
    monkeypatch.setenv("TG_ALLOWLIST_USER_IDS", "[12345]")
    # Group chat id (-100123456789) MUST be accepted — UH-1 validator fix.
    monkeypatch.setenv("TRACE_ALLOWED_CHAT_IDS", "[789,-100123456789]")

    settings = _seed_settings(tmp_path)
    clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
    app = build_app(settings=settings, clock=clock)

    async with LifespanManager(app):
        pass

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["allowed_chat_ids"] == frozenset({789, -100123456789})
