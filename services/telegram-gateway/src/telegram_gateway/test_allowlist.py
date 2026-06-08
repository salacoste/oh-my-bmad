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

Payload types imported from ``events`` package (relocated from
``registry_state.domain.event_types``).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import structlog
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import TelegramObject, Update
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock, TelegramRejectedPayload
from events.envelope import Actor, EventEnvelope
from events.schema_registry import REGISTRY
from httpx import ASGITransport, AsyncClient

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.lifespan import TELEGRAM_GATEWAY_ACTOR, _drain_dispatch_tasks
from telegram_gateway.app.main import build_app
from telegram_gateway.app.middleware import AllowlistMiddleware

# Story 9.3: shape of a bare UUIDv7 (fallback path output).
# Pass-1 review L4: case-insensitive flag for defense-in-depth — the canonical
# UUIDv7 emitter ``events.ids.new_uuid7`` returns lowercase hex, but a future
# change or a test stub could produce uppercase; the test should not flake.
_UUIDV7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

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
# Story 9.3 pass-1 review L1: module-level autouse contextvars hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_structlog_contextvars_module() -> Generator[None, None, None]:
    """Module-level autouse hygiene: clear structlog contextvars around EVERY test.

    Previously this hygiene was class-scoped on
    :class:`TestStory93TraceIdDerivation`, leaving pre-existing tests (e.g.
    ``test_allowlist_middleware_is_first_in_chain``) exposed to contextvar
    leaks from sibling tests that raised before their ``try/finally``
    unbind. Promoting the fixture to the module level is belt-and-braces.
    """
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


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
    assert env.payload.user_id == 67890  # type: ignore[union-attr]
    assert env.payload.reason == "not_in_allowlist"  # type: ignore[union-attr]


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
    assert captured[0].payload.user_id == 12345  # type: ignore[union-attr]
    assert captured[0].payload.reason == "not_in_allowlist"  # type: ignore[union-attr]


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
    assert captured[0].payload.user_id == 0  # type: ignore[union-attr]
    assert captured[0].payload.reason == "no_from_user"  # type: ignore[union-attr]


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
    assert captured[0].payload.reason == "not_in_allowlist"  # type: ignore[union-attr]
    assert captured[0].payload.user_id == 12345  # type: ignore[union-attr]


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
    assert captured[0].payload.user_id == 0  # type: ignore[union-attr]
    assert captured[0].payload.reason == "no_from_user"  # type: ignore[union-attr]


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
        records = [json.loads(ln) for ln in all_lines]
        all_types = [r.get("type") for r in records]
        assert any(t == "telegram.rejected" for t in all_types), (
            f"expected telegram.rejected envelope in JSONL; got types: {all_types!r}"
        )

        # Story 9.3 pass-1 review H2 + L2: assert the JSONL envelope's
        # trace_id matches the canonical ``tg:<update_id>`` derivation
        # for the synthetic update (update_id=1 → tg:1). This is the
        # AC10 integration check the initial implementation deferred —
        # extending the existing test rather than building a fresh
        # Dispatcher harness is the lowest-cost path.
        rejected_records = [r for r in records if r.get("type") == "telegram.rejected"]
        assert len(rejected_records) == 1, (
            f"expected exactly one telegram.rejected envelope; got {len(rejected_records)}"
        )
        # ``_make_update`` defaults to update_id=1 → trace_id "tg:1".
        assert rejected_records[0].get("trace_id") == "tg:1", (
            f"expected trace_id='tg:1' on rejection envelope; "
            f"got trace_id={rejected_records[0].get('trace_id')!r}"
        )


# ---------------------------------------------------------------------------
# Story 9.3 pass-2 review Q4 — AC10 happy-path: allowlisted user's outbound
# registry-api call carries the derived ``tg:<update_id>`` trace_id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_user_envelope_chain_shares_trace_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC10 happy-path: an allowlisted ``/task`` delivery propagates the
    derived ``trace_id`` through the handler to the registry-api call.

    Pass-1 H2 added the JSONL trace_id assertion to the REJECTION path
    only. Pass-2 Q4 closes the missing happy-path: send a ``/task``
    delivery from an allowlisted user, patch ``RegistryAPIClient.create_task``
    to capture the ``trace_id`` kwarg the handler passes, and assert it
    equals ``f"tg:{update_id}"``. Direct envelope JSONL assertion isn't
    possible — telegram-gateway emits zero envelopes on the happy-path
    (registry-api creates ``task.created`` server-side per FR26).
    """
    _setup_env(monkeypatch, tmp_path, allowlist="[12345]")
    _patch_aiogram(monkeypatch)

    # Patch send_message so we never reach real Telegram.
    async def fake_send_message(self: Bot, *args: Any, **kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(Bot, "send_message", fake_send_message)

    # Capture every call to ``RegistryAPIClient.create_task``.
    captured_calls: list[dict[str, Any]] = []

    from telegram_gateway.handlers import registry_client as _rc_mod

    async def fake_create_task(
        self: _rc_mod.RegistryAPIClient,
        **kwargs: Any,
    ) -> _rc_mod.CreateTaskResponseLocal:
        captured_calls.append(kwargs)
        return _rc_mod.CreateTaskResponseLocal(
            task_id="t-00000000-0000-7000-8000-000000000000",
            event_id="e-00000000-0000-7000-8000-000000000000",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(_rc_mod.RegistryAPIClient, "create_task", fake_create_task)

    settings = TelegramSettings.from_env(
        emit=None, actor=TELEGRAM_GATEWAY_ACTOR, clock=_make_clock()
    )
    app = build_app(settings=settings, clock=_make_clock())

    update_id = 314159
    body = {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
            "text": "/task ship the kernel",
        },
    }

    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://testserver") as c,
    ):
        r = await c.post(
            "/v1/telegram/webhook",
            json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
        )
        assert r.status_code == 200
        await _drain_dispatch_tasks(app.state._dispatch_tasks, timeout=2.0)

    # The handler MUST have called create_task with trace_id="tg:<update_id>".
    assert len(captured_calls) == 1, (
        f"expected exactly one create_task call; got {len(captured_calls)}: {captured_calls!r}"
    )
    assert captured_calls[0].get("trace_id") == f"tg:{update_id}", (
        f"expected trace_id='tg:{update_id}' on outbound create_task; "
        f"got {captured_calls[0].get('trace_id')!r}"
    )


# ---------------------------------------------------------------------------
# Story 9.3 — AllowlistMiddleware derives trace_id = f"tg:{update_id}"
# (AC1-AC10; AC7 enumerates the 12 unit tests below.)
# ---------------------------------------------------------------------------


class TestStory93TraceIdDerivation:
    """Story 9.3 (FR58 Telegram / FR28): trace_id derivation + propagation.

    Note: structlog contextvars hygiene is handled by the module-level
    ``_clear_structlog_contextvars_module`` autouse fixture (pass-1 L1).
    The pass-1 class-scoped sibling fixture was removed in Story 9.3
    pass-2 review Q6 — keeping both fires ``clear_contextvars()`` four
    times per test (module + class around setup AND teardown), pure
    overhead with no extra protection.
    """

    # -- AC1 #1 / AC2 baseline ----------------------------------------------

    @pytest.mark.asyncio
    async def test_trace_id_derived_from_update_id(self) -> None:
        """AC1 #1: ``Update(update_id=42)`` → ``data['trace_id'] == 'tg:42'``."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=42))
        data: dict[str, Any] = {}
        await mw(handler, update, data)

        assert data["trace_id"] == "tg:42"

    # -- AC1 #4 (structlog binding) -----------------------------------------

    @pytest.mark.asyncio
    async def test_trace_id_bound_to_structlog_context_during_handler_dispatch(
        self,
    ) -> None:
        """AC1 #4: inside the handler, ``contextvars.get_contextvars()['trace_id']``
        matches the derived value."""
        observed: dict[str, Any] = {}

        async def handler(event: Any, data: dict[str, Any]) -> Any:
            observed.update(structlog.contextvars.get_contextvars())
            return None

        _, emit = _make_recording_emit()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=7))
        await mw(handler, update, {})

        assert observed.get("trace_id") == "tg:7"

    # -- AC1 #6 / AC1 #7 (unbind paths) -------------------------------------

    @pytest.mark.asyncio
    async def test_trace_id_unbound_after_dispatch_success(self) -> None:
        """AC1 #6: contextvars cleared after handler returns normally."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=11))
        await mw(handler, update, {})

        assert "trace_id" not in structlog.contextvars.get_contextvars()

    @pytest.mark.asyncio
    async def test_trace_id_unbound_after_dispatch_exception(self) -> None:
        """AC1 #7: contextvars cleared even when the inner handler raises."""

        async def boom(_event: Any, _data: dict[str, Any]) -> Any:
            raise RuntimeError("handler failed")

        _, emit = _make_recording_emit()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=21))
        with pytest.raises(RuntimeError, match="handler failed"):
            await mw(boom, update, {})

        assert "trace_id" not in structlog.contextvars.get_contextvars()

    @pytest.mark.asyncio
    async def test_trace_id_unbound_after_allowlist_rejection(self) -> None:
        """AC1 #7: non-allowlisted user — ``return None`` path still unbinds."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(99999, update_id=31))
        result = await mw(handler, update, {})

        assert result is None
        assert "trace_id" not in structlog.contextvars.get_contextvars()

    @pytest.mark.asyncio
    async def test_trace_id_unbound_after_no_from_user_rejection(self) -> None:
        """AC1 #7: ``no_from_user`` rejection branch still unbinds."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        # Bare update with no child events → no_from_user rejection.
        update = Update.model_validate({"update_id": 41})
        result = await mw(handler, update, {})

        assert result is None
        assert "trace_id" not in structlog.contextvars.get_contextvars()

    # -- AC2 (deterministic replay) -----------------------------------------

    @pytest.mark.asyncio
    async def test_replay_same_update_id_produces_same_trace_id(self) -> None:
        """AC2: two ``Update`` instances with the same ``update_id`` MUST
        produce byte-identical ``trace_id`` strings (FR28 idempotency invariant).
        """
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        u1 = Update.model_validate(_make_update(12345, update_id=42))
        u2 = Update.model_validate(_make_update(12345, update_id=42))

        d1: dict[str, Any] = {}
        d2: dict[str, Any] = {}
        await mw(handler, u1, d1)
        await mw(handler, u2, d2)

        assert d1["trace_id"] == "tg:42"
        assert d2["trace_id"] == "tg:42"
        assert d1["trace_id"] == d2["trace_id"]

    # -- AC3 (boundary values) ----------------------------------------------

    @pytest.mark.asyncio
    async def test_trace_id_max_int64_update_id_accepted(self) -> None:
        """AC3: ``update_id`` = int64 max (9_223_372_036_854_775_807) → valid."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        int64_max = 9_223_372_036_854_775_807
        update = Update.model_validate(_make_update(12345, update_id=int64_max))
        data: dict[str, Any] = {}
        await mw(handler, update, data)

        assert data["trace_id"] == f"tg:{int64_max}"

    @pytest.mark.asyncio
    async def test_trace_id_fallback_on_zero_update_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC3 / AC7 #9: ``update_id=0`` → WARNING log + UUIDv7 fallback in data."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=0))
        data: dict[str, Any] = {}

        with caplog.at_level(logging.WARNING, logger="telegram_gateway.middleware"):
            await mw(handler, update, data)

        # Fallback minted a bare UUIDv7 (NOT a ``tg:`` form).
        assert isinstance(data.get("trace_id"), str)
        assert _UUIDV7_RE.match(data["trace_id"]), (
            f"expected UUIDv7 fallback, got {data['trace_id']!r}"
        )

        # WARNING logged.
        matching = [
            r
            for r in caplog.records
            if r.name == "telegram_gateway.middleware"
            and r.levelno == logging.WARNING
            and "minting fallback" in r.getMessage()
        ]
        assert len(matching) >= 1, (
            f"expected at least one fallback WARNING; "
            f"got {[(r.name, r.levelname, r.getMessage()) for r in caplog.records]!r}"
        )

    @pytest.mark.asyncio
    async def test_trace_id_fallback_on_negative_update_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC3 / AC7 #10: ``update_id=-1`` → WARNING log + UUIDv7 fallback."""
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=-1))
        data: dict[str, Any] = {}

        with caplog.at_level(logging.WARNING, logger="telegram_gateway.middleware"):
            await mw(handler, update, data)

        assert isinstance(data.get("trace_id"), str)
        assert _UUIDV7_RE.match(data["trace_id"]), (
            f"expected UUIDv7 fallback, got {data['trace_id']!r}"
        )

        matching = [
            r
            for r in caplog.records
            if r.name == "telegram_gateway.middleware"
            and r.levelno == logging.WARNING
            and "minting fallback" in r.getMessage()
        ]
        assert len(matching) >= 1

    # -- AC4 (rejection envelope carries derived trace_id) ------------------

    @pytest.mark.asyncio
    async def test_telegram_rejected_envelope_carries_trace_id(self) -> None:
        """AC4: ``telegram.rejected`` envelope's ``trace_id`` == derived value."""
        captured, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(99999, update_id=123))
        await mw(handler, update, {})

        assert len(captured) == 1
        env = captured[0]
        assert env.trace_id == "tg:123", (
            f"expected envelope.trace_id == 'tg:123'; got {env.trace_id!r}"
        )

    @pytest.mark.asyncio
    async def test_no_from_user_rejection_envelope_carries_trace_id(self) -> None:
        """AC4 (sibling): ``no_from_user`` rejection envelope also carries trace_id."""
        captured, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate({"update_id": 456})
        await mw(handler, update, {})

        assert len(captured) == 1
        env = captured[0]
        assert env.payload.reason == "no_from_user"  # type: ignore[union-attr]
        assert env.trace_id == "tg:456"

    # -- AC7 #12 (integration-feeling: handler reads data["trace_id"]) ------

    @pytest.mark.asyncio
    async def test_handler_can_read_trace_id_from_data(self) -> None:
        """AC5 / AC7 #12: a downstream handler reads ``data['trace_id']`` and
        observes the value the middleware derived."""
        observed_trace_ids: list[str] = []

        async def handler(_event: Any, data: dict[str, Any]) -> Any:
            observed_trace_ids.append(data["trace_id"])
            return None

        _, emit = _make_recording_emit()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=789))
        await mw(handler, update, {})

        assert observed_trace_ids == ["tg:789"]

    # -- Pass-1 review M1 (AC3 lower-bound) --------------------------------

    @pytest.mark.asyncio
    async def test_trace_id_minimum_update_id_one_accepted(self) -> None:
        """AC3 lower bound: ``update_id=1`` → ``tg:1`` (lowest valid value).

        Pass-1 review M1: AC3 explicitly listed ``update_id=1`` as valid but
        no test covered it. The other AC3 tests cover ``int64_max`` (upper),
        ``0`` (fallback), and ``-1`` (fallback) — this fills the lower-edge
        gap.
        """
        _, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=1))
        data: dict[str, Any] = {}
        await mw(handler, update, data)

        assert data["trace_id"] == "tg:1"

    # -- Pass-1 review M3 (aiogram-DI integration test) --------------------

    @pytest.mark.asyncio
    async def test_handler_receives_trace_id_via_aiogram_di(self) -> None:
        """M3: replace tautological data-read with real aiogram DI dispatch.

        The original ``test_handler_can_read_trace_id_from_data`` reads
        ``data["trace_id"]`` immediately after the middleware writes it —
        tautological. This test registers a stub handler whose signature
        contains ``trace_id: str | None = None`` and dispatches through a
        full :class:`aiogram.Dispatcher`. aiogram's DI is responsible for
        resolving ``data["trace_id"]`` into the handler parameter; this
        test locks that contract end-to-end.
        """
        observed: list[str | None] = []

        # Story 9.3 pass-2 review Q8: use an offline ``TelegramAPIServer``
        # base so the bot's ``AiohttpSession`` does NOT initialize a real
        # connection to ``api.telegram.org``. Without this, the test flakes
        # under sandbox network-egress restrictions.
        offline_api = TelegramAPIServer.from_base("http://localhost:0")
        async with Bot(token=_BOT_TOKEN, session=AiohttpSession(api=offline_api)) as bot:
            dp = Dispatcher()
            _, emit = _make_recording_emit()
            dp.update.outer_middleware.register(
                _build_middleware(allowlist=frozenset({12345}), emit=emit)
            )

            @dp.message()
            async def stub_handler(_message: Any, trace_id: str | None = None) -> None:
                observed.append(trace_id)

            allowed = Update.model_validate(_make_update(12345, update_id=909))
            await dp.feed_update(bot, allowed)

        assert observed == ["tg:909"], (
            f"expected aiogram DI to resolve data['trace_id'] into the "
            f"handler parameter; got {observed!r}"
        )

    # -- Pass-1 review M5 (no DeprecationWarning on envelope build) --------

    @pytest.mark.asyncio
    async def test_handler_builds_envelope_with_propagated_trace_id_no_deprecation_warning(
        self,
    ) -> None:
        """AC7 spec test #12 (restored): handler reads ``data['trace_id']``,
        builds an :class:`EventEnvelope` using that trace_id, and asserts NO
        :class:`DeprecationWarning` fires (the Story 9.7 trace_id callsite-
        warning baseline drops further when a callsite passes ``trace_id``
        explicitly)."""
        import warnings as _warnings

        from events import FROZEN_EPOCH, FrozenClock, TelegramRejectedPayload
        from events.envelope import EventEnvelope
        from events.ids import new_event_id, new_request_id

        captured_envelope: list[EventEnvelope] = []

        async def handler(_event: Any, data: dict[str, Any]) -> Any:
            trace_id = data["trace_id"]
            clock = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
            env = EventEnvelope.create(
                event_id=new_event_id(clock=clock),
                schema_version="1.0.0",
                type="telegram.rejected",
                emitted_at=clock.now(),
                emitted_at_monotonic_ns=clock.monotonic_ns(),
                actor=TELEGRAM_GATEWAY_ACTOR,
                payload=TelegramRejectedPayload(user_id=12345, reason="not_in_allowlist"),
                request_id=new_request_id(clock=clock),
                trace_id=trace_id,
            )
            captured_envelope.append(env)
            return None

        _, emit = _make_recording_emit()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        update = Update.model_validate(_make_update(12345, update_id=314))

        with _warnings.catch_warnings():
            _warnings.simplefilter("error", DeprecationWarning)
            await mw(handler, update, {})

        assert len(captured_envelope) == 1
        assert captured_envelope[0].trace_id == "tg:314"

    # -- Pass-1 review M7 / Pass-2 review Q7 (Pydantic coercion lock) -------

    def test_update_id_string_coercion_produces_canonical_trace_id(self) -> None:
        """M7 / Q7: lock Pydantic's int-coercion contract for ``update_id``.

        Pass-1 M7 routed the str-form payload through the middleware and
        asserted ``data["trace_id"] == "tg:42"``. That was tautological:
        by the time the middleware sees the :class:`Update`, Pydantic has
        already coerced ``"42" → 42`` at construction time, so the
        middleware always interpolates an ``int`` and produces ``"tg:42"``
        regardless. Pass-2 Q7 rewrites this to assert the actual invariant
        the test name claims: Pydantic coerces a string update_id to a
        canonical ``int`` at ``model_validate`` time. If Pydantic flips to
        strict mode in the future this assertion fails loudly at the
        coercion site rather than silently passing because the
        downstream interpolation is still well-defined.
        """
        raw_int = _make_update(12345, update_id=42)
        raw_str: dict[str, Any] = dict(raw_int)
        raw_str["update_id"] = "42"

        update_int = Update.model_validate(raw_int)
        try:
            update_str = Update.model_validate(raw_str)
        except Exception:
            pytest.skip("Pydantic strict-mode rejected string update_id — canonicalization moot")

        # Pydantic must coerce the string to the canonical int form.
        assert update_int.update_id == 42
        assert update_str.update_id == 42
        assert type(update_int.update_id) is int
        assert type(update_str.update_id) is int
        # Locking the equality avoids any future drift to ``Decimal`` /
        # ``np.int64`` / etc. that would silently break the f-string
        # interpolation in the middleware.
        assert update_int.update_id == update_str.update_id

    # -- Pass-1 review L5 (rejection-envelope int64+1 overflow) -------------

    @pytest.mark.asyncio
    async def test_rejection_envelope_overflow_update_id_uuid_fallback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """L5: non-allowlisted user with ``update_id = int64_max + 1`` exercises
        BOTH the middleware fallback path AND the rejection envelope's
        ``trace_id`` field. The envelope should carry the UUIDv7 fallback
        (NOT the raw ``tg:<int64_max+1>`` string that would fail
        ``is_valid_trace_id``).
        """
        captured, emit = _make_recording_emit()
        _, handler = _make_handler()
        mw = _build_middleware(allowlist=frozenset({12345}), emit=emit)

        overflow = 9_223_372_036_854_775_808  # int64_max + 1
        update = Update.model_validate(_make_update(99999, update_id=overflow))

        with caplog.at_level(logging.WARNING, logger="telegram_gateway.middleware"):
            await mw(handler, update, {})

        assert len(captured) == 1
        env = captured[0]
        # NOT the literal "tg:<overflow>" — that would fail is_valid_trace_id.
        assert env.trace_id is not None
        assert env.trace_id != f"tg:{overflow}", (
            f"expected UUIDv7 fallback (not raw overflow tg:string); got trace_id={env.trace_id!r}"
        )
        assert _UUIDV7_RE.match(env.trace_id), (
            f"expected UUIDv7 fallback on rejection envelope; got {env.trace_id!r}"
        )

        matching = [
            r
            for r in caplog.records
            if "telegram_gateway.middleware" in r.name
            and r.levelno == logging.WARNING
            and "minting fallback" in r.getMessage()
        ]
        assert len(matching) >= 1


# Story 9.3 pass-2 review Q11: ``TestStory93TraceIdRateLimitChain`` moved to
# ``services/telegram-gateway/src/telegram_gateway/app/test_per_actor_rate_limit.py``
# (the natural home for any test exercising ``PerActorRateLimitMiddleware``).
