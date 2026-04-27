"""Tests for the webhook endpoint (Story 3.1 AC-6 / AC-7 / AC-9 / AC-10).

Covers:

* AC-6: header secret-token verify (200 / 403 / missing-header).
* AC-7: ``<500ms`` latency budget (NFR-R3).
* AC-9: cold-start audit count — exactly 3 ``secret.accessed`` envelopes
  on boot + one webhook delivery.

AC-9 implementation note
------------------------

The cold-start audit-count test uses a recording wrapper around
``EventLogWriter.append`` to capture envelopes BEFORE canonical-JSON
serialization. The platform's ``EventEnvelope.payload`` field is a
``dict | BaseModel`` union; when the registered payload class is a
``BaseModel`` (here :class:`SecretAccessedPayload`), pydantic's union
serializer flattens to an empty dict during ``model_dump``, so
``read_log_lines`` round-trips lose the payload contents. This is a
pre-existing platform quirk (out of scope for Story 3.1). We sidestep
it by reading the in-memory envelope rather than the JSONL file.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed (mirror of test_app.py:48)
    EventLogWriter,
)
from secret_hygiene import flush_pending_emissions

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.main import build_app

_ACTOR = Actor(kind="system", id="telegram-gateway")
_BOT_TOKEN = "1234:fake-bot-token"
_WEBHOOK_SECRET = "fake-webhook-secret-1234"
_WEBHOOK_URL = "https://tunnel.example.com/v1/telegram/webhook"

# Synthetic Update payload — minimal valid shape so
# ``Update.model_validate`` accepts it. ``update_id`` is the only
# required key on the Update envelope; presence of any one inner type
# (e.g., ``message``) is enough for the dispatcher to receive it.
_SYNTHETIC_UPDATE: dict[str, Any] = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 0,
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "is_bot": False, "first_name": "Test"},
        "text": "hello",
    },
}


def _setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Populate env-vars and return the events_dir path."""
    events_dir = tmp_path / "events"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", _WEBHOOK_SECRET)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", _WEBHOOK_URL)
    monkeypatch.setenv("EVENT_LOG_DIR", str(events_dir))
    return events_dir


def _patch_aiogram(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Patch :py:meth:`Bot.set_webhook` + ``AiohttpSession.close``.

    Returns the list-of-ints recorder for ``feed_webhook_update`` calls so
    tests can assert dispatch did / did not happen.
    """
    dispatch_calls: list[int] = []

    async def fake_set_webhook(self: Bot, **kwargs: Any) -> bool:
        return True

    async def fake_session_close(self: AiohttpSession) -> None:
        return None

    monkeypatch.setattr(Bot, "set_webhook", fake_set_webhook)
    monkeypatch.setattr(AiohttpSession, "close", fake_session_close)

    # Patch Dispatcher.feed_webhook_update to a recorder so we don't
    # actually attempt outbound API calls when no handler matches.
    from aiogram import Dispatcher

    async def fake_feed(self: Dispatcher, bot: Bot, update: Any, **_: Any) -> None:
        dispatch_calls.append(1)
        return None

    monkeypatch.setattr(Dispatcher, "feed_webhook_update", fake_feed)
    return dispatch_calls


@pytest_asyncio.fixture
async def client_and_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[tuple[AsyncClient, Path, list[int]]]:
    events_dir = _setup_env(monkeypatch, tmp_path)
    dispatch_calls = _patch_aiogram(monkeypatch)
    settings = TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client, events_dir, dispatch_calls


@pytest.mark.asyncio
async def test_webhook_valid_secret_token_dispatches(
    client_and_state: tuple[AsyncClient, Path, list[int]],
) -> None:
    """AC-6 happy path: header matches → 200 + dispatcher invoked."""
    client, _, dispatch_calls = client_and_state
    r = await client.post(
        "/v1/telegram/webhook",
        json=_SYNTHETIC_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
    )
    assert r.status_code == 200
    assert dispatch_calls == [1]


@pytest.mark.asyncio
async def test_webhook_mismatched_secret_token_returns_403(
    client_and_state: tuple[AsyncClient, Path, list[int]],
) -> None:
    """AC-6: mismatched header → 403, NOT 401, AND dispatcher untouched."""
    client, _, dispatch_calls = client_and_state
    r = await client.post(
        "/v1/telegram/webhook",
        json=_SYNTHETIC_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-token"},
    )
    assert r.status_code == 403
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_webhook_missing_header_returns_403(
    client_and_state: tuple[AsyncClient, Path, list[int]],
) -> None:
    """AC-6: missing header → 403."""
    client, _, dispatch_calls = client_and_state
    r = await client.post("/v1/telegram/webhook", json=_SYNTHETIC_UPDATE)
    assert r.status_code == 403
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_webhook_latency_under_500ms(
    client_and_state: tuple[AsyncClient, Path, list[int]],
) -> None:
    """AC-7 / NFR-R3: end-to-end webhook handling <500ms."""
    client, _, _ = client_and_state
    start = time.perf_counter()
    r = await client.post(
        "/v1/telegram/webhook",
        json=_SYNTHETIC_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert r.status_code == 200
    assert elapsed_ms < 500, f"webhook latency {elapsed_ms:.1f}ms exceeded 500ms"


@pytest_asyncio.fixture
async def client_with_recorder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[tuple[AsyncClient, list[EventEnvelope]]]:
    """Variant of :func:`client_and_state` that captures appended envelopes.

    Wraps :py:meth:`EventLogWriter.append` so the in-memory ``EventEnvelope``
    is recorded BEFORE the canonical-JSON round-trip — see module docstring
    for why this matters.
    """
    _setup_env(monkeypatch, tmp_path)
    _patch_aiogram(monkeypatch)

    captured: list[EventEnvelope] = []
    real_append = EventLogWriter.append

    async def recording_append(self: EventLogWriter, envelope: EventEnvelope) -> None:
        captured.append(envelope)
        await real_append(self, envelope)

    monkeypatch.setattr(EventLogWriter, "append", recording_append)

    settings = TelegramSettings.from_env(
        emit=None,
        actor=_ACTOR,
        clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH),
    )
    app = build_app(settings=settings, clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH))
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client, captured


@pytest.mark.asyncio
async def test_cold_start_audit_count_is_three(
    client_with_recorder: tuple[AsyncClient, list[EventEnvelope]],
) -> None:
    """AC-9: boot + 1 webhook → exactly 3 ``secret.accessed`` envelopes.

    Pinned count: 1 for ``Bot()`` (bot_token), 1 for ``set_webhook`` (webhook
    secret), 1 for the webhook handler header-compare (webhook secret).
    Each envelope carries ``actor=Actor(kind='system', id='telegram-gateway')``
    and ``payload.secret_name`` ∈ {``telegram_bot_token``,
    ``telegram_webhook_secret_token``}.

    NOTE: ``Bot.set_webhook`` is patched to a no-op in this test, BUT
    the lifespan still reads ``audited.webhook_secret_token.value`` to
    pass it as the keyword argument — the audit fires on the property
    access, not on Telegram's side.
    """
    client, captured = client_with_recorder
    r = await client.post(
        "/v1/telegram/webhook",
        json=_SYNTHETIC_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
    )
    assert r.status_code == 200

    # Drain any in-flight emission tasks so all 3 envelopes have been
    # appended (and therefore captured) by the time we assert.
    await flush_pending_emissions(timeout=2.0)
    # Belt-and-braces: yield once so any callback chained off the flush
    # has a chance to run.
    await asyncio.sleep(0)

    def _secret_name_of(env: EventEnvelope) -> str:
        payload = env.payload
        if hasattr(payload, "secret_name"):
            return str(payload.secret_name)
        # Mapping-style payload (dict / _FrozenDict).
        return str(payload["secret_name"])  # type: ignore[index]

    secret_envelopes = [e for e in captured if e.type == "secret.accessed"]
    assert len(secret_envelopes) == 3, (
        f"expected exactly 3 secret.accessed envelopes, got "
        f"{len(secret_envelopes)}: "
        f"types={[e.type for e in captured]}"
    )

    secret_names: list[str] = []
    for env in secret_envelopes:
        # actor invariants per AC-9
        assert env.actor.kind == "system"
        assert env.actor.id == "telegram-gateway"
        secret_name = _secret_name_of(env)
        secret_names.append(secret_name)
        assert secret_name in {
            "telegram_bot_token",
            "telegram_webhook_secret_token",
        }

    # Exact composition (AC-9): 1 bot_token (Bot construction) +
    # 2 webhook_secret_token (set_webhook + webhook handler header
    # compare). Pinned on purpose — every additional ``.value`` read
    # surfaces here as a regression diff.
    assert secret_names.count("telegram_bot_token") == 1
    assert secret_names.count("telegram_webhook_secret_token") == 2
