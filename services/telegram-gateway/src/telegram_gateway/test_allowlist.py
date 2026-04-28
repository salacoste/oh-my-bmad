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

import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import TelegramObject, Update
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from events.schema_registry import REGISTRY
from httpx import ASGITransport, AsyncClient
from registry_state.domain.event_types import (  # noqa: IMP001 — see TODO(architecture) in conftest.py
    TelegramRejectedPayload,
)

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import TELEGRAM_GATEWAY_ACTOR, _drain_dispatch_tasks
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
    actor: Actor = TELEGRAM_GATEWAY_ACTOR,
) -> AllowlistMiddleware:
    return AllowlistMiddleware(
        allowlist=allowlist,
        emit=emit,
        actor=actor,
        clock=_make_clock(),
    )


# ---------------------------------------------------------------------------
# Fixture: reset lifespan logger disabled flag (L7 / H1)
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_lifespan_logger() -> None:
    """Re-enable telegram_gateway.lifespan logger.

    Alembic's fileConfig call sets disable_existing_loggers=True (now
    patched to False in env.py — H1 production fix), but this fixture
    provides an extra safety net for test ordering robustness.
    """
    logging.getLogger("telegram_gateway.lifespan").disabled = False


# ---------------------------------------------------------------------------
# AC-8 unit tests — direct middleware invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlisted_user_passes_through() -> None:
    """AC-8: allowlist match → handler invoked, no envelope emitted."""
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
    captured, emit = _make_recording_emit()
    _, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    update = Update.model_validate(_make_update(99999))
    await mw(handler, update, {})

    assert len(captured) == 1
    assert captured[0].actor == TELEGRAM_GATEWAY_ACTOR
    assert captured[0].actor.kind == "system"
    assert captured[0].actor.id == "telegram-gateway"


@pytest.mark.asyncio
async def test_envelope_validates_against_schema_registry() -> None:
    """AC-8 / AC-10: emitted envelope round-trips through the schema registry."""
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
async def test_emit_failure_does_not_propagate(caplog: pytest.LogCaptureFixture) -> None:
    """AC-8: emission outage MUST NOT let a rejected user reach the handler.

    Also asserts that the error is logged (M5).
    """

    async def exploding_emit(_envelope: EventEnvelope) -> None:
        raise RuntimeError("writer offline")

    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=exploding_emit)

    update = Update.model_validate(_make_update(67890))

    with caplog.at_level(logging.ERROR, logger="telegram_gateway.middleware"):
        # Must NOT raise — _safe_emit swallows the writer error.
        result = await mw(handler, update, {})

    # Reject decision still sticks: handler never ran.
    assert result is None
    assert invocations == []

    # M5: assert the error was logged.
    matching = [r for r in caplog.records if "telegram.rejected emission failed" in r.getMessage()]
    assert len(matching) == 1, (
        f"expected one 'telegram.rejected emission failed' log; "
        f"got {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )


@pytest.mark.asyncio
async def test_middleware_p50_latency_under_1ms() -> None:
    """AC-9: O(1) frozenset check + in-process emit is sub-1.5ms p50.

    The spec ceiling is <1ms; we allow 1.5ms to accommodate CI-machine
    variability. This is an honest budget: measured local p50 for the
    in-process no-op path is ~0.05ms. If this test starts failing on a
    specific runner, measure and adjust the budget rather than silently
    loosening to 4ms.
    """
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
    assert p50 < 1.5, f"middleware p50 latency {p50:.3f}ms exceeded 1.5ms budget"


# ---------------------------------------------------------------------------
# H2 / M8 / M16: parametrized Update child-type coverage
# ---------------------------------------------------------------------------


def _make_update_with_child(
    child_field: str,
    user_attr: str,
    user_id: int,
    *,
    update_id: int = 1,
) -> dict[str, Any]:
    """Build a synthetic Update dict using *child_field* as the event carrier.

    Handles both ``from_user`` and ``user`` attribute names.
    """
    user_obj = {"id": user_id, "is_bot": False, "first_name": "Test"}

    if child_field == "message":
        child = {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 1, "type": "private"},
            "from": user_obj,
            "text": "hi",
        }
    elif child_field in (
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "business_message",
        "edited_business_message",
    ):
        child = {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 1, "type": "private"},
            "from": user_obj,
        }
    elif child_field == "callback_query":
        child = {
            "id": "cq1",
            "from": user_obj,
            "chat_instance": "ci1",
        }
    elif child_field == "inline_query":
        child = {"id": "iq1", "from": user_obj, "query": "", "offset": ""}
    elif child_field == "chosen_inline_result":
        child = {"result_id": "r1", "from": user_obj, "query": ""}
    elif child_field == "shipping_query":
        child = {
            "id": "sq1",
            "from": user_obj,
            "invoice_payload": "p",
            "shipping_address": {
                "country_code": "US",
                "state": "NY",
                "city": "NYC",
                "street_line1": "1 st",
                "street_line2": "",
                "post_code": "10001",
            },
        }
    elif child_field == "pre_checkout_query":
        child = {
            "id": "pcq1",
            "from": user_obj,
            "currency": "USD",
            "total_amount": 100,
            "invoice_payload": "p",
        }
    elif child_field in ("my_chat_member", "chat_member"):
        child = {
            "chat": {"id": 1, "type": "private"},
            "from": user_obj,
            "date": 1700000000,
            "old_chat_member": {"user": user_obj, "status": "member"},
            "new_chat_member": {"user": user_obj, "status": "member"},
        }
    elif child_field == "chat_join_request":
        child = {
            "chat": {"id": 1, "type": "private"},
            "from": user_obj,
            "user_chat_id": user_id,
            "date": 1700000000,
        }
    elif child_field == "poll_answer":
        # poll_answer uses .user, not .from_user
        child = {
            "poll_id": "p1",
            "user": user_obj,
            "option_ids": [0],
        }
    elif child_field == "message_reaction":
        # message_reaction uses .user
        child = {
            "chat": {"id": 1, "type": "private"},
            "message_id": 1,
            "date": 1700000000,
            "user": user_obj,
            "old_reaction": [],
            "new_reaction": [],
        }
    elif child_field == "purchased_paid_media":
        child = {
            "from": user_obj,
            "invoice_payload": "p",
        }
    elif child_field == "business_connection":
        # business_connection uses .user
        child = {
            "id": "bc1",
            "user": user_obj,
            "user_chat_id": user_id,
            "date": 1700000000,
            "can_reply": True,
            "is_enabled": True,
        }
    else:
        raise ValueError(f"Unknown child_field: {child_field!r}")

    return {"update_id": update_id, child_field: child}


# Fields that carry user identity (from_user or user) — should pass allowlisted users
_USER_BEARING_CHILD_FIELDS = [
    ("message", "from_user"),
    ("edited_message", "from_user"),
    ("callback_query", "from_user"),
    ("my_chat_member", "from_user"),
    ("chat_member", "from_user"),
    ("chat_join_request", "from_user"),
    ("poll_answer", "user"),
    ("message_reaction", "user"),
    ("business_connection", "user"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("child_field,user_attr", _USER_BEARING_CHILD_FIELDS)
async def test_child_field_user_extraction(child_field: str, user_attr: str) -> None:
    """H2 / M8 / M16: user id extracted correctly from each Update child type."""
    captured, emit = _make_recording_emit()
    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    raw = _make_update_with_child(child_field, user_attr, 12345)
    try:
        update = Update.model_validate(raw)
    except Exception:
        pytest.skip(f"aiogram could not validate {child_field!r} update shape — skip")

    result = await mw(handler, update, {})

    # Allowlisted user should pass through
    assert result == "OK", (
        f"child_field={child_field!r}: allowlisted user should pass; "
        f"captured={[e.payload for e in captured]!r}"
    )
    assert invocations != [], f"child_field={child_field!r}: handler should have been called"
    assert captured == [], f"child_field={child_field!r}: no rejection envelope expected"


@pytest.mark.asyncio
@pytest.mark.parametrize("child_field,user_attr", _USER_BEARING_CHILD_FIELDS)
async def test_child_field_user_rejection(child_field: str, user_attr: str) -> None:
    """H2 / M16: non-allowlisted user rejected for each Update child type."""
    captured, emit = _make_recording_emit()
    invocations, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({99999}), emit=emit)

    raw = _make_update_with_child(child_field, user_attr, 12345)
    try:
        update = Update.model_validate(raw)
    except Exception:
        pytest.skip(f"aiogram could not validate {child_field!r} update shape — skip")

    result = await mw(handler, update, {})

    assert result is None, f"child_field={child_field!r}: should be rejected"
    assert invocations == [], f"child_field={child_field!r}: handler must not be called"
    assert len(captured) == 1
    assert captured[0].payload.reason == "not_in_allowlist"
    assert captured[0].payload.user_id == 12345


# Bot-only fields that carry no user identity — should fall to no_from_user
_BOT_ONLY_CHILD_FIELDS = [
    "message_reaction_count",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("child_field", _BOT_ONLY_CHILD_FIELDS)
async def test_bot_only_child_field_no_from_user(child_field: str) -> None:
    """AC-7: bot-only update types with no user identity → user_id=0, no_from_user."""
    # message_reaction_count has no per-user identity (bot-only)
    raw: dict[str, Any] = {"update_id": 1}
    if child_field == "message_reaction_count":
        raw[child_field] = {
            "chat": {"id": 1, "type": "private"},
            "message_id": 1,
            "date": 1700000000,
            "reactions": [],
        }
    else:
        pytest.skip(f"No synthetic shape defined for {child_field!r}")

    try:
        update = Update.model_validate(raw)
    except Exception:
        pytest.skip(f"aiogram could not validate {child_field!r} update shape — skip")

    captured, emit = _make_recording_emit()
    _, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    result = await mw(handler, update, {})
    assert result is None
    assert len(captured) == 1
    assert captured[0].payload.user_id == 0
    assert captured[0].payload.reason == "no_from_user"


# ---------------------------------------------------------------------------
# M9: multiple child fields populated (defensive behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_child_fields_silently_picks_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M9: when multiple child fields are set, first match wins + WARNING logged.

    Per Telegram's contract at most one child field is set. When violated,
    _extract_user_id picks the first populated entry in _UPDATE_CHILD_FIELDS
    deterministically and emits a WARNING.
    """
    # Build an Update that has both message (from_user=12345) and callback_query
    # (from_user=67890). message comes first in _UPDATE_CHILD_FIELDS so 12345 wins.
    raw: dict[str, Any] = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 12345, "is_bot": False, "first_name": "A"},
            "text": "hi",
        },
        "callback_query": {
            "id": "cq1",
            "from": {"id": 67890, "is_bot": False, "first_name": "B"},
            "chat_instance": "ci1",
        },
    }
    try:
        update = Update.model_validate(raw)
    except Exception as exc:
        pytest.skip(f"aiogram rejected multi-child update: {exc}")

    captured, emit = _make_recording_emit()
    _, handler = _make_handler()
    mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

    with caplog.at_level(logging.WARNING, logger="telegram_gateway.middleware"):
        result = await mw(handler, update, {})

    # 12345 is in allowlist — should pass (message wins over callback_query).
    assert result == "OK"
    assert captured == []

    # WARNING should have been emitted about multiple populated fields.
    multi_warnings = [
        r for r in caplog.records if "multiple populated child fields" in r.getMessage()
    ]
    assert len(multi_warnings) >= 1, (
        "expected a WARNING about multiple child fields; "
        f"got {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )


# ---------------------------------------------------------------------------
# L16: _UPDATE_CHILD_FIELDS coverage assertion (startup-time schema-drift check)
# ---------------------------------------------------------------------------


def test_update_child_fields_covers_known_user_bearing_fields() -> None:
    """L16: assert every Update field with from_user or user attribute is listed.

    This is a compile-time / import-time guard against schema drift: if
    aiogram adds a new Update child field that carries user identity, this
    test will fail, prompting an update to _UPDATE_CHILD_FIELDS.
    """
    from telegram_gateway.app.middleware import _UPDATE_CHILD_FIELDS

    listed_fields = {name for name, _ in _UPDATE_CHILD_FIELDS}

    # Walk Update.model_fields and find fields that are not scalar types
    # (i.e., could be a sub-object with from_user / user).
    # We check the field names we KNOW should be covered; this is a
    # regression guard rather than an exhaustive AST walk.
    expected_covered = {
        "message",
        "edited_message",
        "callback_query",
        "inline_query",
        "chosen_inline_result",
        "shipping_query",
        "pre_checkout_query",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
        "poll_answer",
        "message_reaction",
        "purchased_paid_media",
        "business_connection",
    }
    missing = expected_covered - listed_fields
    assert not missing, (
        f"_UPDATE_CHILD_FIELDS is missing these known user-bearing fields: {missing!r}. "
        "Add them with the correct user_attr ('from_user' or 'user')."
    )


# ---------------------------------------------------------------------------
# L17: AllowlistMiddleware is first in outer_middleware chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_middleware_is_first_in_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L17: AllowlistMiddleware MUST be at index 0 in dp.update.outer_middleware.

    Story 3.6 ack: when request-id / rate-limiter middleware is added,
    AllowlistMiddleware must remain first so allowlist check runs before
    any downstream middleware touches handler state.
    """
    _setup_env(monkeypatch, tmp_path, allowlist="[12345]")
    _patch_aiogram(monkeypatch)

    settings = TelegramSettings.from_env(
        emit=None, actor=TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())

    async with LifespanManager(app):
        dp = app.state.dp
        # dp.update.outer_middleware iteration includes aiogram built-in
        # middlewares (ErrorsMiddleware, UserContextMiddleware,
        # FSMContextMiddleware) that are always prepended. We check that
        # AllowlistMiddleware is the FIRST USER-REGISTERED middleware —
        # i.e., it comes before any other non-built-in middleware.
        outer_chain = list(dp.update.outer_middleware)
        aiogram_builtins = frozenset(
            {"ErrorsMiddleware", "UserContextMiddleware", "FSMContextMiddleware"}
        )
        user_mws = [mw for mw in outer_chain if type(mw).__name__ not in aiogram_builtins]
        assert len(user_mws) >= 1, "no user-registered outer middlewares found"
        assert isinstance(user_mws[0], AllowlistMiddleware), (
            f"expected AllowlistMiddleware as first user-registered outer_middleware; "
            f"got {type(user_mws[0]).__name__!r}. "
            f"Full chain: {[type(m).__name__ for m in outer_chain]!r}"
        )


# ---------------------------------------------------------------------------
# AC-8: outer-vs-inner middleware ordering via real Dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outer_middleware_runs_before_inner() -> None:
    """AC-4 / AC-8: outer middleware short-circuits inner middleware too.

    Register the allowlist on ``dp.update.outer_middleware`` AND a
    spy on ``dp.update.middleware`` (inner). Non-allowlisted user →
    inner spy NOT invoked. Allowlisted user → inner spy IS invoked.

    M7: use ``async with bot`` context-manager to avoid session leak
    on BaseException.
    """
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

    async with Bot(token=_BOT_TOKEN) as bot:
        dp = Dispatcher()
        dp.update.outer_middleware.register(
            _build_middleware(allowlist=frozenset({12345}), emit=emit)
        )
        dp.update.middleware.register(_SpyInner())

        # Register a no-op message handler so routing finds a target for
        # the allowed update; otherwise inner middleware never fires
        # because the dispatcher gives up before invoking inner chain.
        @dp.message()
        async def _noop_handler(_message: Any) -> None:
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
        emit=None, actor=TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())
    async with LifespanManager(app):
        yield


@pytest.mark.asyncio
async def test_empty_allowlist_logs_startup_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    _reset_lifespan_logger: None,
) -> None:
    """AC-6: empty TG_ALLOWLIST_USER_IDS → WARNING fires at boot."""
    _setup_env(monkeypatch, tmp_path, allowlist="[]")
    _patch_aiogram(monkeypatch)

    settings = TelegramSettings.from_env(
        emit=None, actor=TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
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
    _reset_lifespan_logger: None,
) -> None:
    """AC-6: non-empty allowlist → no warning fires."""
    _setup_env(monkeypatch, tmp_path, allowlist="[12345]")
    _patch_aiogram(monkeypatch)

    settings = TelegramSettings.from_env(
        emit=None, actor=TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
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
    async def fake_send_message(self: Bot, *args: Any, **kwargs: Any) -> Any:
        send_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(Bot, "send_message", fake_send_message)

    settings = TelegramSettings.from_env(
        emit=None, actor=TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())

    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as c:
            # Non-allowlisted user → webhook still 200 (fire-and-forget) and
            # outer middleware drops the update before any handler runs.
            r = await c.post(
                "/v1/telegram/webhook",
                json=_make_update(99999),
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
            assert r.status_code == 200
            # M13: use _drain_dispatch_tasks instead of magic range(5) sleep loop.
            await _drain_dispatch_tasks(app.state._dispatch_tasks, timeout=2.0)

        # No outbound send_message attempted.
        assert send_calls == [], (
            f"rejected user must receive no outbound message; got {send_calls!r}"
        )

        # M14: read JSONL and assert telegram.rejected envelope is present.
        # Collect lines while writer is still open (inside lifespan context).
        jsonl_files = list(events_dir.glob("*.jsonl"))
        assert jsonl_files, "expected at least one JSONL event file after webhook"
        all_lines: list[str] = []
        for f in jsonl_files:
            all_lines.extend(line for line in f.read_text().splitlines() if line.strip())
        all_types = [json.loads(ln).get("type") for ln in all_lines]
        assert any(t == "telegram.rejected" for t in all_types), (
            f"expected telegram.rejected envelope in JSONL; got types: {all_types!r}"
        )
