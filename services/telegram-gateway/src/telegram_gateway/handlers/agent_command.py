"""/agent command handler for telegram-gateway (Story 3.19 / FR17a).

Read-only query — calls GET /v1/tasks/{id} on registry-api to verify the
task exists, then renders runtime ownership info.  Phase 1 returns a
static ``runtime=claude-code`` placeholder; real worker/session data ships
with Epic 5 (Story 5.9 session-registry MCP server).

No audit-event emission
-----------------------
Read-only query; the single-writer rule (FR26) is not in scope.

No idempotency key
------------------
GET is idempotent by HTTP semantics (RFC 7231 §4.2.2).

Error handling (Story 3.1 M3 contract)
---------------------------------------
ALL exceptions are caught and surfaced as a Telegram reply. The handler
ALWAYS returns normally (never raises).
"""

from __future__ import annotations

import html
import logging

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers import _keys
from telegram_gateway.handlers._errors import format_http_error
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = logging.getLogger("telegram_gateway.handlers.agent_command")

# Phase 1 placeholder — Story 5.9 replaces with real runtime lookup.
_DEFAULT_RUNTIME = "claude-code"


async def handle_agent(
    message: Message,
    registry_client: RegistryAPIClient,
    trace_id: str | None = None,
) -> None:
    """Handle the /agent <task-id> command.

    Read-only query — calls ``get_task`` to verify the task exists, then
    renders Phase 1 static ``runtime=claude-code`` info.  Unlike
    ``/approve``, ``/stop``, ``/reject``, ``/retry``, this handler does
    NOT call ``submit_decision``.
    """
    task_id = _keys.extract_task_id_from_message(message)
    if task_id is None:
        raw_text = message.text or ""
        parts = raw_text.split(None, 1)
        if len(parts) < 2:
            await _safe_reply(message, "Usage: /agent <task-id>")
        else:
            await _safe_reply(
                message,
                "Usage: /agent <task-id>; example: /agent t-0192a1b5-1234-7abc-89de-f0123456789a",
            )
        return

    # Derive operator handle for the reply (logging-only for the API call;
    # get_task has no operator_actor_id parameter).
    if message.from_user is not None:
        operator_handle = html.escape(
            message.from_user.username or message.from_user.first_name or "operator"
        )
    else:
        _log.warning(
            "/agent from_user is None (message_id=%s chat_id=%s)",
            message.message_id,
            message.chat.id if message.chat else "?",
        )
        operator_handle = "operator"

    request_id = new_request_id()

    try:
        await registry_client.get_task(task_id=task_id, request_id=request_id, trace_id=trace_id)
    except httpx.TooManyRedirects as exc:
        _log.warning(
            "registry-api too many redirects for /agent (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Registry misconfigured: too many redirects.")
        return
    except httpx.HTTPStatusError as exc:
        reply = format_http_error(exc, command_label="Agent command")
        _log.warning(
            "registry-api HTTP error for /agent (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except RegistryResponseError as exc:
        _log.exception(
            "registry-api malformed response for /agent (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(
            message,
            "⚠️ Registry returned an unexpected response. Logs captured.",
        )
        return
    except httpx.HTTPError as exc:
        _log.warning(
            "registry-api network error for /agent (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(
            message,
            f"⚠️ Could not reach registry: {type(exc).__name__}.",
        )
        return
    except Exception as exc:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception(
            "/agent handler unexpected error (request_id=%s): %s",
            request_id,
            exc,
        )
        await _safe_reply(message, "⚠️ Internal error. Logs captured.")
        return

    # AC-2: Phase 1 static response.
    task_id_safe = html.escape(task_id)
    await _safe_reply(
        message,
        f"🤖 Task {task_id_safe}: runtime={_DEFAULT_RUNTIME} @{operator_handle}",
    )


def make_agent_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as make_task_router() (Story 3.3).
    """
    router = Router()
    router.message(Command("agent"))(handle_agent)
    return router


__all__ = ["handle_agent", "make_agent_router"]
