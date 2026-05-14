"""/status command handler for telegram-gateway (Story 3.14 / FR4 / FR12).

Query command — calls GET /v1/tasks/{id} on registry-api and renders the
response as a single human-friendly Telegram message.

This handler composes patterns from two existing handlers:

- From /approve (Story 3.4): task-id argument extraction via
  ``_keys.extract_task_id_from_message()``, usage reply pattern.
- From /ping (Story 3.5): GET request pattern (no Idempotency-Key header),
  ``request_id`` generation, error cascade.

No audit-event emission
-----------------------
This handler does NOT emit any audit event. It is a read-only query; the
single-writer rule (FR26) is not in scope.

No idempotency key
------------------
GET is idempotent by HTTP semantics (RFC 7231 §4.2.2). A /status invocation
fetches a read-only snapshot; repeating it does not create a second resource.
Same omission as /ping — see get_task() docstring.

Error handling (Story 3.1 M3 contract)
---------------------------------------
ALL exceptions are caught and surfaced as a Telegram reply. The handler
ALWAYS returns normally (never raises). Telegram receives a 200 ACK from
the webhook endpoint regardless of what happens inside.

HTML parse mode (AC-6 / Story 3.1 M5)
--------------------------------------
Reply messages use HTML markup. ``DefaultBotProperties(parse_mode=ParseMode.HTML)``
is set globally in lifespan.py so all ``message.reply(...)`` calls inherit HTML
mode without explicit kwarg. All externally sourced values are HTML-escaped.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers import _keys
from telegram_gateway.handlers._errors import format_http_error
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import (
    RegistryAPIClient,
    RegistryResponseError,
    TaskResponseLocal,
)

_log = logging.getLogger("telegram_gateway.handlers.status_command")

# Telegram's sendMessage limit is 4096 chars; leave headroom for the truncation
# notice itself. Matches the cap strategy in _errors.py.
_MAX_REPLY_LEN = 4000


def _render_status_reply(task: TaskResponseLocal) -> str:
    """Render a state-aware status reply for Telegram.

    Produces compact, context-dependent formatting:
    - blocked/executing/idle: operational view — since HH:MM, step, event,
      agent, lock, commands (blocked omits title per Journey 6 spec)
    - completed/stopped: terminal view — title, timestamps, actor
    - pending/planning/plan_ready: early-stage — title, actor, commands
    """
    tid = html.escape(task.task_id)
    status = html.escape(task.status)
    lines: list[str] = [f"📋 Task <code>{tid}</code>"]

    if task.status in ("completed", "stopped"):
        lines.append(f"Status: {status}")
        if task.title is not None:
            lines.append(f"Title: {html.escape(task.title)}")
        lines.append(f"Created: {task.created_at.isoformat(timespec='seconds')}")
        lines.append(f"Updated: {task.updated_at.isoformat(timespec='seconds')}")
        actor = f"{html.escape(task.actor.kind)}/{html.escape(task.actor.id)}"
        lines.append(f"Actor: {actor}")
    elif task.status in ("blocked", "executing", "idle"):
        if task.state_since is not None:
            hhmm = task.state_since.astimezone(UTC).strftime("%H:%M")
            lines.append(f"Status: {status} (since {hhmm})")
        else:
            lines.append(f"Status: {status}")

        if task.status != "blocked" and task.title is not None:
            lines.append(f"Title: {html.escape(task.title)}")

        if task.current_step is not None and task.total_steps is not None:
            lines.append(f"Step: {task.current_step}/{task.total_steps}")

        if task.last_event is not None:
            ev = task.last_event
            ev_line = html.escape(ev.type)
            if ev.summary:
                ev_line += f" — {html.escape(ev.summary[:200])}"
            lines.append(f"Last event: {ev_line}")

        if task.last_agent_action:
            lines.append(f"Last agent: {html.escape(task.last_agent_action[:80])}")

        if task.worktree_lock is not None:
            lines.append(
                "Worktree: held" if task.worktree_lock.held else "Worktree: not held"
            )

        # prefer available_commands; fall back to next_commands (deprecated)
        commands = task.available_commands or task.next_commands
        if commands:
            cmds = ", ".join(f"/{html.escape(cmd)}" for cmd in commands)
            lines.append(f"Available: {cmds}")
    else:
        lines.append(f"Status: {status}")
        if task.title is not None:
            lines.append(f"Title: {html.escape(task.title)}")
        actor = f"{html.escape(task.actor.kind)}/{html.escape(task.actor.id)}"
        lines.append(f"Actor: {actor}")

        commands = task.available_commands or task.next_commands
        if commands:
            cmds = ", ".join(f"/{html.escape(cmd)}" for cmd in commands)
            lines.append(f"Available: {cmds}")

    reply = "\n".join(lines)

    if len(reply) > _MAX_REPLY_LEN:
        cut = reply.rfind("\n", 0, _MAX_REPLY_LEN)
        if cut == -1:
            cut = _MAX_REPLY_LEN
        reply = reply[:cut] + "\n… (truncated)"

    return reply


async def handle_status(
    message: Message,
    registry_client: RegistryAPIClient,
) -> None:
    """Handle the /status <task-id> command.

    Extracts and validates the task-id, calls GET /v1/tasks/{id} on
    registry-api, and replies with a human-friendly rendering of every field.

    ``registry_client`` is injected via ``dp.workflow_data["registry_client"]``
    set in lifespan.py (shared with /task, /approve, /ping handlers).

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    task_id = _keys.extract_task_id_from_message(message)
    if task_id is None:
        raw_text = message.text or ""
        parts = raw_text.split(None, 1)
        if len(parts) < 2:
            await _safe_reply(message, "Usage: /status <task-id>")
        else:
            await _safe_reply(
                message,
                "Usage: /status <task-id>; example: /status t-0192a1b5-1234-7abc-89de-f0123456789a",
            )
        return

    request_id = new_request_id()

    try:
        task = await registry_client.get_task(task_id=task_id, request_id=request_id)
    except httpx.TooManyRedirects:
        _log.warning(
            "registry-api too many redirects for /status (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            _log.warning(
                "registry-api 404 for /status (task_id=%s request_id=%s)",
                task_id,
                request_id,
            )
            task_id_safe = html.escape(task_id)
            await _safe_reply(
                message,
                f"⚠️ Task not found: <code>{task_id_safe}</code>",
            )
            return
        reply = format_http_error(exc, command_label="Status query")
        _log.warning(
            "registry-api HTTP error for /status (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except RegistryResponseError:
        _log.exception(
            "registry-api malformed response for /status (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Received malformed response from registry.")
        return
    except httpx.HTTPError:
        _log.warning(
            "registry-api network error for /status (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Could not reach registry. Please try again later.")
        return
    except Exception:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception(
            "/status handler unexpected error (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Unexpected error. Please try again later.")
        return

    reply_text = _render_status_reply(task)

    await _safe_reply(message, reply_text)


def make_status_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as make_task_router() (Story 3.3), make_approve_router()
    (Story 3.4), and make_ping_router() (Story 3.5).

    handle_status is a standalone module-level coroutine so tests can import
    it directly without brittle router-introspection.
    """
    router = Router()
    router.message(Command("status"))(handle_status)
    return router


__all__ = ["handle_status", "make_status_router"]
