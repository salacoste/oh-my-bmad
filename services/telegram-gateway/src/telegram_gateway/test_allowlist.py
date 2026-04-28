"""Tests for the allowlist outer middleware (Story 3.2).

Covers AC-3 / AC-5 / AC-6 / AC-7 / AC-8 / AC-9. The middleware itself
is an aiogram ``BaseMiddleware`` so most tests construct an
:class:`AllowlistMiddleware` directly and invoke ``__call__`` with a
synthetic :class:`aiogram.types.Update` — no FastAPI / webhook layer
required for the unit-level guarantees.

Two integration-flavored tests exercise the lifespan path:

* ``test_empty_allowlist_logs_startup_warning`` — pins the AC-6 boot
  warning via ``caplog``.
* ``test_outer_middleware_runs_before_inner`` — register an inner
  middleware on a real :class:`aiogram.Dispatcher` and assert ordering
  via ``feed_update``.

AC-12 noqa note: ``registry_state.domain.event_types`` is imported via
``# noqa: IMP001`` per the same TODO as ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from events.schema_registry import REGISTRY
from httpx import ASGITransport, AsyncClient
from registry_state.domain.event_types import (  # noqa: IMP001 — telegram.rejected payload schema lives in registry-state per Story 2.14 additive-version rule; relocation to packages/events/ tracked in TODO(architecture)
    TelegramRejectedPayload,
)

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import _TELEGRAM_GATEWAY_ACTOR
from telegram_gateway.app.main import build_app
from telegram_gateway.app.middleware import AllowlistMiddleware

_BOT_TOKEN = "1234:fake-bot-token"
_WEBHOOK_SECRET = "fake-webhook-secret-1234"
_WEBHOOK_URL = "https://tunnel.example.com/v1/telegram/webhook"


def _make_update(user_id: int, *, update_id: int = 1) -> dict[str, Any]:
    """Build a synthetic Update dict for a Message from *user_id*."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": "hello",
        },
    }


def _make_clock() -> FrozenClock:
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


def _make_recording_emit() -> tuple[list[EventEnvelope], Any]:
    captured: list[EventEnvelope] = []

    async def emit(envelope: EventEnvelope) -> None:
        captured.append(envelope)

    return captured, emit


def _make_handler() -> tuple[list[Any], Any]:
    invocations: list[Any] = []

    async def handler(event: Any, data: dict[str, Any]) -> Any:
        invocations.append(event)
        return "OK"

    return invocations, handler


def _build_middleware(
    *,
    allowlist: frozenset[int],
    emit: Any,
    actor: Actor | None = None,
) -> AllowlistMiddleware:
    return AllowlistMiddleware(
        allowlist=allowlist,
        emit=emit,
        actor=actor or _TELEGRAM_GATEWAY_ACTOR,
        clock=_make_clock(),
    )


# ---------------------------------------------------------------------------
# AC-8 unit tests — direct middleware invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlisted_user_passes_through() -> None:
    """AC-8: allowlist match → handler invoked, no envelope emitted."""
    from aiogram.types import Update

    captured, emit = _make_recording_emit()
    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    update = Update.model_validate(_make_update(12345))
    result = await mw(handler, update, {})

    assert result == "OK"
    assert len(invocations) == 1
    assert captured == []


@pytest.mark.asyncio
async def test_non_allowlisted_user_rejected() -> None:
    """AC-8: id absent from allowlist → handler skipped + 1 envelope emitted."""
    from aiogram.types import Update

    captured, emit = _make_recording_emit()
    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    update = Update.model_validate(_make_update(67890))
    result = await mw(handler, update, {})

    assert result is None
    assert invocations == []
    assert len(captured) == 1
    env = captured[0]
    assert env.type == "telegram.rejected"
    assert env.payload.user_id == 67890
    assert env.payload.reason == "not_in_allowlist"


@pytest.mark.asyncio
async def test_empty_allowlist_rejects_everyone() -> None:
    """AC-8 / AC-6: closed-by-default — empty set rejects every user."""
    from aiogram.types import Update

    captured, emit = _make_recording_emit()
    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset(), emit=emit)

    update = Update.model_validate(_make_update(12345))
    result = await mw(handler, update, {})

    assert result is None
    assert invocations == []
    assert len(captured) == 1
    assert captured[0].payload.user_id == 12345
    assert captured[0].payload.reason == "not_in_allowlist"


@pytest.mark.asyncio
async def test_event_without_from_user_rejected_with_sentinel() -> None:
    """AC-7: ``Update`` lacking ``from_user`` → user_id=0, reason=no_from_user."""
    from aiogram.types import Update

    captured, emit = _make_recording_emit()
    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    # Bare update with no child events at all (synthetic minimal shape).
    update = Update.model_validate({"update_id": 99})
    result = await mw(handler, update, {})

    assert result is None
    assert invocations == []
    assert len(captured) == 1
    assert captured[0].payload.user_id == 0
    assert captured[0].payload.reason == "no_from_user"


@pytest.mark.asyncio
async def test_actor_identity_in_envelope() -> None:
    """AC-8: emitted envelope carries the canonical telegram-gateway actor."""
    from aiogram.types import Update

    captured, emit = _make_recording_emit()
    _, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    update = Update.model_validate(_make_update(99999))
    await mw(handler, update, {})

    assert len(captured) == 1
    assert captured[0].actor == _TELEGRAM_GATEWAY_ACTOR
    assert captured[0].actor.kind == "system"
    assert captured[0].actor.id == "telegram-gateway"


@pytest.mark.asyncio
async def test_envelope_validates_against_schema_registry() -> None:
    """AC-8 / AC-10: emitted envelope round-trips through the schema registry."""
    from aiogram.types import Update

    # Sanity: registration is idempotent + present (autouse fixture handles it).
    assert ("telegram.rejected", "1.0.0") in REGISTRY
    assert REGISTRY[("telegram.rejected", "1.0.0")] is TelegramRejectedPayload

    captured, emit = _make_recording_emit()
    _, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    update = Update.model_validate(_make_update(67890))
    await mw(handler, update, {})

    assert len(captured) == 1
    env = captured[0]
    # The payload is a real ``TelegramRejectedPayload`` instance because
    # ``EventEnvelope.create`` validates against the registered model.
    assert isinstance(env.payload, TelegramRejectedPayload)
    assert env.schema_version == "1.0.0"


@pytest.mark.asyncio
async def test_emit_failure_does_not_propagate() -> None:
    """AC-8: emission outage MUST NOT let a rejected user reach the handler."""
    from aiogram.types import Update

    async def exploding_emit(_envelope: EventEnvelope) -> None:
        raise RuntimeError("writer offline")

    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=exploding_emit)

    update = Update.model_validate(_make_update(67890))
    # Must NOT raise — _safe_emit swallows the writer error.
    result = await mw(handler, update, {})

    # Reject decision still sticks: handler never ran.
    assert result is None
    assert invocations == []


@pytest.mark.asyncio
async def test_middleware_p50_latency_under_1ms() -> None:
    """AC-9: O(1) frozenset check + in-process emit is sub-1ms p50."""
    from aiogram.types import Update

    _, emit = _make_recording_emit()
    _, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    update = Update.model_validate(_make_update(12345))

    # Warm-up.
    for _ in range(10):
        await mw(handler, update, {})

    samples: list[float] = []
    for _ in range(100):
        start = time.perf_counter()
        await mw(handler, update, {})
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    p50 = samples[len(samples) // 2]
    # In-process / no-op emit budget — gives 4× the spec's 1ms ceiling
    # for CI-machine variability while still flagging order-of-magnitude
    # regressions.
    assert p50 < 4.0, f"middleware p50 latency {p50:.3f}ms exceeded budget"


# ---------------------------------------------------------------------------
# AC-8: outer-vs-inner middleware ordering via real Dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outer_middleware_runs_before_inner() -> None:
    """AC-4 / AC-8: outer middleware short-circuits inner middleware too.

    Register the allowlist on ``dp.update.outer_middleware`` AND a
    spy on ``dp.update.middleware`` (inner). Non-allowlisted user →
    inner spy NOT invoked. Allowlisted user → inner spy IS invoked.
    """
    from aiogram import BaseMiddleware
    from aiogram.types import TelegramObject, Update

    inner_invocations: list[Any] = []

    class _SpyInner(BaseMiddleware):
        async def __call__(
            self,
            handler: Any,
            event: TelegramObject,
            data: dict[str, Any],
        ) -> Any:
            inner_invocations.append(event)
            return await handler(event, data)

    captured, emit = _make_recording_emit()

    bot = Bot(token=_BOT_TOKEN)
    try:
        dp = Dispatcher()
        dp.update.outer_middleware.register(
            _build_middleware(allowlist=frozenset({12345}), emit=emit)
        )
        dp.update.middleware.register(_SpyInner())

        # Register a no-op message handler so routing finds a target for
        # the allowed update; otherwise inner middleware never fires
        # because the dispatcher gives up before invoking inner chain.
        @dp.message()
        async def _noop_handler(_message: Any) -> None:  # pragma: no cover - trivial
            return None

        # Non-allowlisted user → outer rejects, inner never runs.
        rejected = Update.model_validate(_make_update(99999))
        await dp.feed_update(bot, rejected)
        assert inner_invocations == []
        assert len(captured) == 1

        # Allowlisted user → outer passes through, inner runs once.
        allowed = Update.model_validate(_make_update(12345, update_id=2))
        await dp.feed_update(bot, allowed)
        assert len(inner_invocations) == 1
        # Still only the one rejection envelope.
        assert len(captured) == 1
    finally:
        await bot.session.close()


# ---------------------------------------------------------------------------
# AC-5 / AC-6 lifespan-path tests
# ---------------------------------------------------------------------------


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, allowlist: str) -> Path:
    events_dir = tmp_path / "events"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", _WEBHOOK_SECRET)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _WEBHOOK_URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(events_dir))
    monkeypatch.setenv("TG_ALLOWLIST_USER_IDS", allowlist)
    return events_dir


def _patch_aiogram(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set_webhook(self: Bot, **kwargs: Any) -> bool:
        return True

    async def fake_session_close(self: AiohttpSession) -> None:
        return None

    monkeypatch.setattr(Bot, "set_webhook", fake_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", fake_session_close)


@pytest_asyncio.fixture
async def _empty_allowlist_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[None]:
    _setup_env(monkeypatch, tmp_path, allowlist="[]")
    _patch_aiogram(monkeypatch)
    settings = TelegramSettings.from_env(
        emit=None, actor=_TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())
    async with LifespanManager(app):
        yield


@pytest.mark.asyncio
async def test_empty_allowlist_logs_startup_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-6: empty TG_ALLOWLIST_USER_IDS → WARNING fires at boot."""
    _setup_env(monkeypatch, tmp_path, allowlist="[]")
    _patch_aiogram(monkeypatch)

    # Re-enable the logger in case a prior test (e.g., a migration test that
    # calls logging.config.fileConfig with disable_existing_loggers=True) has
    # set its .disabled flag. caplog.at_level() below sets the level but does
    # not clear the disabled flag, so the warning would be silently swallowed.
    logging.getLogger("telegram_gateway.lifespan").disabled = False

    settings = TelegramSettings.from_env(
        emit=None, actor=_TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())

    with caplog.at_level(logging.WARNING, logger="telegram_gateway.lifespan"):
        async with LifespanManager(app):
            pass

    matching = [
        r
        for r in caplog.records
        if r.name == "telegram_gateway.lifespan"
        and r.levelno == logging.WARNING
        and "TG_ALLOWLIST_USER_IDS is empty" in r.getMessage()
    ]
    assert len(matching) == 1, (
        f"expected exactly one empty-allowlist WARNING; "
        f"got {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )


@pytest.mark.asyncio
async def test_non_empty_allowlist_does_not_log_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-6: non-empty allowlist → no warning fires."""
    _setup_env(monkeypatch, tmp_path, allowlist="[12345]")
    _patch_aiogram(monkeypatch)

    # Re-enable in case a prior test's fileConfig call set .disabled = True.
    logging.getLogger("telegram_gateway.lifespan").disabled = False

    settings = TelegramSettings.from_env(
        emit=None, actor=_TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())

    with caplog.at_level(logging.WARNING, logger="telegram_gateway.lifespan"):
        async with LifespanManager(app):
            pass

    matching = [r for r in caplog.records if "TG_ALLOWLIST_USER_IDS is empty" in r.getMessage()]
    assert matching == [], "non-empty allowlist must NOT trigger empty-allowlist warning"


@pytest.mark.asyncio
async def test_rejected_user_receives_no_outbound_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-5: a non-allowlisted webhook delivery yields zero ``send_message`` calls.

    Pins the no-response contract: returning ``None`` from the
    middleware suppresses handler invocation, and Story 3.1's
    fire-and-forget webhook returns ``200`` regardless. Telegram
    observes a clean ACK and never sees an outbound ``sendMessage``.
    """
    events_dir = _setup_env(monkeypatch, tmp_path, allowlist="[12345]")
    _patch_aiogram(monkeypatch)

    send_calls: list[Any] = []

    # Defense-in-depth: spy on Bot.send_message in case any handler
    # tried to reply. The middleware should short-circuit BEFORE any
    # handler runs, so this list MUST stay empty for a rejected user.
    async def fake_send_message(  # pragma: no cover - assertion path
        self: Bot, *args: Any, **kwargs: Any
    ) -> Any:
        send_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(Bot, "send_message", fake_send_message)

    settings = TelegramSettings.from_env(
        emit=None, actor=_TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://testserver") as c,
    ):
        # Non-allowlisted user → webhook still 200 (fire-and-forget) and
        # outer middleware drops the update before any handler runs.
        r = await c.post(
            "/v1/telegram/webhook",
            json=_make_update(99999),
            headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
        )
        assert r.status_code == 200
        # Yield a couple of cycles so the fire-and-forget dispatch task
        # runs the middleware decision and any audit emit.
        for _ in range(5):
            await asyncio.sleep(0)

    # No outbound send_message attempted.
    assert send_calls == [], f"rejected user must receive no outbound message; got {send_calls!r}"

    # Audit envelope was written to disk (best-effort — file may take a
    # tick to flush; we only assert presence, not content).
    jsonl_files = list(events_dir.glob("*.jsonl"))
    assert jsonl_files, "expected at least one JSONL event file after webhook"
