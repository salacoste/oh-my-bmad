"""Tests for /approvals command handler (Story 11.3 AC1 / FR63).

Coverage:

* ``test_approvals_command_creates_pinned_thread_when_none_exists`` —
  no existing inbox, ``create_forum_topic`` succeeds, POST to
  registry-api emits ``approval.inbox_opened``, reply confirms.
* ``test_approvals_command_returns_existing_thread_link_when_inbox_already_open``
  — existing inbox row → no new thread, reply references existing.
* ``test_approvals_command_handles_missing_can_manage_topics_permission``
  — aiogram raises ``TelegramBadRequest`` with permission error →
  actionable reply, no POST attempted.
* ``test_approvals_command_handles_registry_api_failure`` — POST 5xx
  after Forum-Topic creation → structured error reply (does NOT crash).
* ``test_approvals_command_rejects_when_inbox_owned_by_another_operator``
  — Story 11.3 review P1: operator-identity check.
* ``test_approvals_command_rejects_non_allowlisted_caller`` /
  ``test_approvals_command_silent_drops_non_allowlisted`` —
  Story 11.3 review P12: AllowlistMiddleware-gated access (AC1 + AC6).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiogram.types
import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from events import FROZEN_EPOCH

from telegram_gateway.handlers.approvals_command import handle_approvals
from telegram_gateway.handlers.registry_client import RegistryAPIClient

_FAKE_CHAT_ID = -1001234567890
_FAKE_NEW_THREAD_ID = 42
_FAKE_EVENT_ID = "e-00000000-0000-7000-8000-000000000020"


def _make_message(
    *,
    chat_id: int = _FAKE_CHAT_ID,
    user_id: int = 999,
    new_thread_id: int = _FAKE_NEW_THREAD_ID,
    create_forum_topic_raises: BaseException | None = None,
) -> MagicMock:
    """Build an aiogram Message mock with a mocked bot for /approvals tests.

    Story 11.3 review P19: the ``from_user`` mock uses
    ``spec=aiogram.types.User`` so attribute access matches the real
    surface and silent typos cannot pass.
    """
    msg = MagicMock(spec=aiogram.types.Message)
    msg.text = "/approvals"
    msg.message_id = 100
    msg.chat = MagicMock(spec=aiogram.types.Chat)
    msg.chat.id = chat_id
    msg.from_user = MagicMock(spec=aiogram.types.User)
    msg.from_user.id = user_id
    msg.from_user.username = "operator-x"
    msg.from_user.first_name = "Operator"
    msg.reply = AsyncMock(return_value=None)

    forum_topic = MagicMock()
    forum_topic.message_thread_id = new_thread_id
    forum_topic.name = "Approvals Inbox"

    msg.bot = MagicMock()
    if create_forum_topic_raises is not None:
        msg.bot.create_forum_topic = AsyncMock(side_effect=create_forum_topic_raises)
    else:
        msg.bot.create_forum_topic = AsyncMock(return_value=forum_topic)
    # Story 11.3 review P3: orphan-topic cleanup hook — tests assert this
    # is called when POST fails after create_forum_topic succeeds.
    msg.bot.delete_forum_topic = AsyncMock(return_value=None)
    return msg


def _make_registry_client_for_approvals(
    *,
    get_inbox_status: int = 404,
    get_inbox_body: dict[str, object] | None = None,
    post_inbox_status: int = 201,
    post_inbox_body: dict[str, object] | None = None,
) -> RegistryAPIClient:
    """Build a RegistryAPIClient whose transport routes by (method, path)."""
    default_post_body = {
        "operator_chat_id": _FAKE_CHAT_ID,
        "inbox_thread_id": _FAKE_NEW_THREAD_ID,
        # Story 11.3 review P34: FROZEN_EPOCH (Story 10.5 hotfix convention).
        "opened_at": FROZEN_EPOCH.isoformat(),
        "event_id": _FAKE_EVENT_ID,
        "idempotency_status": "applied",
    }

    async def _transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and "/v1/approvals/inbox/" in path:
            if get_inbox_status == 200 and get_inbox_body is not None:
                return httpx.Response(
                    status_code=200,
                    content=json.dumps(get_inbox_body).encode(),
                    headers={"content-type": "application/json"},
                    request=request,
                )
            return httpx.Response(status_code=404, content=b"{}", request=request)
        if method == "POST" and path == "/v1/approvals/inbox":
            body = post_inbox_body if post_inbox_body is not None else default_post_body
            return httpx.Response(
                status_code=post_inbox_status,
                content=json.dumps(body).encode(),
                headers={
                    "content-type": "application/json",
                    "X-Idempotency-Status": str(body.get("idempotency_status", "applied")),
                },
                request=request,
            )
        return httpx.Response(status_code=404, content=b"{}", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    return RegistryAPIClient(http_client=http_client)


@pytest.mark.asyncio
async def test_approvals_command_creates_pinned_thread_when_none_exists() -> None:
    """Story 11.3 AC1: no existing inbox → create_forum_topic + POST + reply."""
    msg = _make_message()
    registry = _make_registry_client_for_approvals(get_inbox_status=404)

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    msg.bot.create_forum_topic.assert_awaited_once()
    call_kwargs = msg.bot.create_forum_topic.call_args.kwargs
    assert call_kwargs["chat_id"] == _FAKE_CHAT_ID
    # Reply confirms the new inbox.
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "Approval inbox opened" in reply_text
    assert str(_FAKE_NEW_THREAD_ID) in reply_text


@pytest.mark.asyncio
async def test_approvals_command_returns_existing_thread_link_when_inbox_already_open() -> None:
    """Story 11.3 AC1: existing inbox row → NO new thread; reply references existing."""
    msg = _make_message()
    existing_inbox_body = {
        "operator_chat_id": _FAKE_CHAT_ID,
        "inbox_thread_id": 777,
        # Story 11.3 review P34: FROZEN_EPOCH instead of hardcoded literal.
        "opened_at": FROZEN_EPOCH.isoformat(),
        # Story 11.3 review P1: the existing-inbox test must match the
        # caller's actor_id so the owner check passes; the owner-mismatch
        # path is exercised in test_approvals_command_rejects_when_inbox_owned_by_another_operator.
        "opened_by_actor_id": "999",
    }
    registry = _make_registry_client_for_approvals(
        get_inbox_status=200,
        get_inbox_body=existing_inbox_body,
    )

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    # No new Forum-Topic created.
    msg.bot.create_forum_topic.assert_not_called()
    # Reply references the existing inbox.
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "already open" in reply_text
    assert "777" in reply_text


@pytest.mark.asyncio
async def test_approvals_command_handles_missing_can_manage_topics_permission() -> None:
    """Story 11.3 out-of-scope risk: missing permission → actionable reply, no POST.

    aiogram's ``create_forum_topic`` raises ``TelegramBadRequest`` with text
    like "Bad Request: not enough rights to manage chat topics". We catch
    and surface a graceful error.
    """
    # TelegramBadRequest has its own constructor; using model_construct via
    # subclass isn't trivially exposed — use a direct instance.
    fake_exc = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: not enough rights to manage chat topics",
    )
    msg = _make_message(create_forum_topic_raises=fake_exc)
    registry = _make_registry_client_for_approvals(get_inbox_status=404)

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    msg.bot.create_forum_topic.assert_awaited_once()
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "can_manage_topics" in reply_text
    assert "permission" in reply_text.lower()


@pytest.mark.asyncio
async def test_approvals_command_handles_registry_api_failure_after_topic_creation() -> None:
    """Forum-Topic created but POST /v1/approvals/inbox returns 500 → graceful error.

    Story 11.3 PP5 (pass-2): 5xx is an indeterminate state — the server may
    have persisted the event before failing. Do NOT delete the orphan Forum-
    Topic on 5xx (deleting when the event already landed would orphan the
    event from the topic it references). Surface "indeterminate, contact ops"
    reply instead of "retry" (which would be incorrect advice).
    """
    msg = _make_message()
    registry = _make_registry_client_for_approvals(
        get_inbox_status=404,
        post_inbox_status=500,
        post_inbox_body={"detail": "internal error"},
    )

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    msg.bot.create_forum_topic.assert_awaited_once()
    # PP5: 5xx → do NOT delete (state indeterminate).
    msg.bot.delete_forum_topic.assert_not_called()
    msg.reply.assert_awaited()
    # The handler MUST surface SOME error message (not pretend success).
    reply_text = msg.reply.call_args.args[0]
    assert "Approval inbox opened" not in reply_text
    # PP5: "indeterminate" / "contact ops" copy instead of "Retry /approvals".
    assert "indeterminate" in reply_text or "contact ops" in reply_text or "server error" in reply_text.lower()


@pytest.mark.asyncio
async def test_approvals_command_handles_registry_api_4xx_cleans_up_topic() -> None:
    """Forum-Topic created but POST returns 4xx → cleanup IS safe (server rejected).

    Story 11.3 PP5: on 4xx the server definitively rejected the request, so
    the event was NOT persisted. Deleting the orphan Forum-Topic is safe and
    correct. Reply tells operator to retry /approvals.
    """
    msg = _make_message()
    registry = _make_registry_client_for_approvals(
        get_inbox_status=404,
        post_inbox_status=422,
        post_inbox_body={"detail": "validation error"},
    )

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    msg.bot.create_forum_topic.assert_awaited_once()
    # PP5: 4xx → cleanup IS attempted.
    msg.bot.delete_forum_topic.assert_called_once_with(
        chat_id=_FAKE_CHAT_ID,
        message_thread_id=_FAKE_NEW_THREAD_ID,
    )
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "Approval inbox opened" not in reply_text


# ---------------------------------------------------------------------------
# Story 11.3 review P1 — operator-identity check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approvals_command_rejects_when_inbox_owned_by_another_operator() -> None:
    """P1: a different allowlisted operator cannot hijack the inbox via UPSERT."""
    msg = _make_message(user_id=999)
    other_owner_body = {
        "operator_chat_id": _FAKE_CHAT_ID,
        "inbox_thread_id": 777,
        "opened_at": FROZEN_EPOCH.isoformat(),
        # Different from msg.from_user.id (which is "999") → owner mismatch.
        "opened_by_actor_id": "operator-original",
    }
    registry = _make_registry_client_for_approvals(
        get_inbox_status=200,
        get_inbox_body=other_owner_body,
    )

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    # No new Forum-Topic — the caller is not the owner.
    msg.bot.create_forum_topic.assert_not_called()
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "another operator" in reply_text
    assert "operator-original" in reply_text


# ---------------------------------------------------------------------------
# Story 11.3 review P2 — from_user is None rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approvals_command_rejects_when_from_user_is_none() -> None:
    """P2: channel post / anonymous admin → reject; never proceed with 'unknown'."""
    msg = _make_message()
    msg.from_user = None
    registry = _make_registry_client_for_approvals(get_inbox_status=404)

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    msg.bot.create_forum_topic.assert_not_called()
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "operator identity" in reply_text


# ---------------------------------------------------------------------------
# Story 11.3 review P12 — AC1 / AC6 allowlist-gated tests
# ---------------------------------------------------------------------------
#
# AC1/AC6 allowlist-gate coverage lives in
# services/telegram-gateway/.../test_allowlist.py (Story 3.2 — dispatcher-
# wide middleware test). Per Story 11.3 pass-2 review PP3: previous tests
# at this location were tautological (never invoked handler/middleware,
# just asserted that mocks weren't called); deleted to avoid false-
# coverage signal.


# ---------------------------------------------------------------------------
# Story 11.3 PP1 — 410 Gone → fresh idempotency key retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approvals_command_recovers_from_410_via_fresh_key_retry() -> None:
    """PP1: 410 Gone on POST triggers ONE retry with new_idempotency_key().

    Pass-1 P17 + P4 left an unrecoverable window: deterministic
    Idempotency-Key + post-restart cache eviction = the gateway could
    never get past 410. PP1 closes the window by falling back to a
    fresh random key exactly once.
    """
    msg = _make_message()
    post_attempts: list[str] = []

    async def _transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and "/v1/approvals/inbox/" in path:
            return httpx.Response(status_code=404, content=b"{}", request=request)
        if method == "POST" and path == "/v1/approvals/inbox":
            key = request.headers.get("Idempotency-Key", "")
            post_attempts.append(key)
            # First attempt → 410 Gone; retry with fresh key → 201 Created.
            if len(post_attempts) == 1:
                return httpx.Response(
                    status_code=410,
                    content=json.dumps({"detail": "post-restart cache gone"}).encode(),
                    headers={"content-type": "application/json"},
                    request=request,
                )
            body = {
                "operator_chat_id": _FAKE_CHAT_ID,
                "inbox_thread_id": _FAKE_NEW_THREAD_ID,
                "opened_at": FROZEN_EPOCH.isoformat(),
                "event_id": _FAKE_EVENT_ID,
                "idempotency_status": "applied",
            }
            return httpx.Response(
                status_code=201,
                content=json.dumps(body).encode(),
                headers={
                    "content-type": "application/json",
                    "X-Idempotency-Status": "applied",
                },
                request=request,
            )
        return httpx.Response(status_code=404, content=b"{}", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    registry = RegistryAPIClient(http_client=http_client)

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    # Exactly two POST attempts; second key differs from first (deterministic
    # vs random) so the cache lookup misses and the event is created fresh.
    assert len(post_attempts) == 2
    assert post_attempts[0] != post_attempts[1]
    # First attempt used the deterministic ``ai-...`` SHA prefix.
    assert post_attempts[0].startswith("ai-")
    # Reply confirms success — operator unblocked.
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "Approval inbox opened" in reply_text


@pytest.mark.asyncio
async def test_approvals_command_surfaces_error_when_410_retry_also_fails() -> None:
    """PP1: if the retry attempt ALSO fails (4xx), surface error + orphan cleanup."""
    msg = _make_message()

    async def _transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and "/v1/approvals/inbox/" in path:
            return httpx.Response(status_code=404, content=b"{}", request=request)
        if method == "POST" and path == "/v1/approvals/inbox":
            return httpx.Response(
                status_code=410,
                content=json.dumps({"detail": "gone"}).encode(),
                headers={"content-type": "application/json"},
                request=request,
            )
        return httpx.Response(status_code=404, content=b"{}", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    registry = RegistryAPIClient(http_client=http_client)

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    # Orphan cleanup attempted; reply surfaces failure (not success).
    msg.bot.delete_forum_topic.assert_called_once()
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "Approval inbox opened" not in reply_text


# ---------------------------------------------------------------------------
# Story 11.3 PP4 — /approvals reopen sub-command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approvals_reopen_overwrites_existing_inbox() -> None:
    """PP4: ``/approvals reopen`` forces a fresh create_forum_topic + POST."""
    msg = _make_message(user_id=999)
    msg.text = "/approvals reopen"
    existing_inbox_body = {
        "operator_chat_id": _FAKE_CHAT_ID,
        "inbox_thread_id": 700,
        "opened_at": FROZEN_EPOCH.isoformat(),
        "opened_by_actor_id": "999",  # same operator → reopen allowed
    }
    registry = _make_registry_client_for_approvals(
        get_inbox_status=200,
        get_inbox_body=existing_inbox_body,
    )

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    # Default path would short-circuit on "already open"; reopen forces create.
    msg.bot.create_forum_topic.assert_awaited_once()
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "reopened" in reply_text


@pytest.mark.asyncio
async def test_approvals_reopen_rejected_for_non_owner() -> None:
    """PP4: reopen is owner-only — different operator cannot reopen via flag."""
    msg = _make_message(user_id=999)
    msg.text = "/approvals reopen"
    foreign_inbox_body = {
        "operator_chat_id": _FAKE_CHAT_ID,
        "inbox_thread_id": 700,
        "opened_at": FROZEN_EPOCH.isoformat(),
        "opened_by_actor_id": "operator-original",  # different owner
    }
    registry = _make_registry_client_for_approvals(
        get_inbox_status=200,
        get_inbox_body=foreign_inbox_body,
    )

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    # No new Forum-Topic — reopen-by-non-owner blocked.
    msg.bot.create_forum_topic.assert_not_called()
    msg.reply.assert_awaited()
    reply_text = msg.reply.call_args.args[0]
    assert "owned by another operator" in reply_text


@pytest.mark.asyncio
async def test_approvals_reopen_emits_fresh_inbox_opened_event() -> None:
    """PP4: reopen path issues a POST (event re-emit via UPSERT materializer)."""
    msg = _make_message(user_id=999)
    msg.text = "/approvals reopen"
    post_called = False

    async def _transport(request: httpx.Request) -> httpx.Response:
        nonlocal post_called
        path = request.url.path
        method = request.method
        if method == "GET" and "/v1/approvals/inbox/" in path:
            return httpx.Response(
                status_code=200,
                content=json.dumps(
                    {
                        "operator_chat_id": _FAKE_CHAT_ID,
                        "inbox_thread_id": 700,
                        "opened_at": FROZEN_EPOCH.isoformat(),
                        "opened_by_actor_id": "999",
                    }
                ).encode(),
                headers={"content-type": "application/json"},
                request=request,
            )
        if method == "POST" and path == "/v1/approvals/inbox":
            post_called = True
            body = {
                "operator_chat_id": _FAKE_CHAT_ID,
                "inbox_thread_id": _FAKE_NEW_THREAD_ID,
                "opened_at": FROZEN_EPOCH.isoformat(),
                "event_id": _FAKE_EVENT_ID,
                "idempotency_status": "applied",
            }
            return httpx.Response(
                status_code=201,
                content=json.dumps(body).encode(),
                headers={
                    "content-type": "application/json",
                    "X-Idempotency-Status": "applied",
                },
                request=request,
            )
        return httpx.Response(status_code=404, content=b"{}", request=request)

    http_client = httpx.AsyncClient(
        base_url="http://registry-api:8080",
        transport=httpx.MockTransport(_transport),
    )
    registry = RegistryAPIClient(http_client=http_client)

    try:
        await handle_approvals(msg, registry, trace_id=None)
    finally:
        await registry.http_client.aclose()

    assert post_called, "reopen path must POST to emit fresh event"
