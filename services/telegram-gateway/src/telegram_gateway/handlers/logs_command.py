"""/logs command handler for telegram-gateway (Story 3.15 / FR5).

Query command — calls GET /v1/tasks/{id}/logs/digest on registry-api and
renders the LLM-digest response as a single Telegram message.

Placeholder behavior
--------------------
When the registry-api digest endpoint returns 404 (task has no events or
endpoint not deployed), the handler shows a placeholder message directing
the operator to the CLI for raw events. On success, renders the LLM digest.

No audit-event emission
-----------------------
This handler does NOT emit any audit event. It is a read-only query; the
single-writer rule (FR26) is not in scope.

No idempotency key
------------------
GET is idempotent by HTTP semantics (RFC 7231 §4.2.2). Same omission as
/ping and /status — see get_logs_digest() docstring.

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
import re

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.ids import new_request_id

from telegram_gateway.handlers import _keys
from telegram_gateway.handlers._errors import format_http_error
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import RegistryAPIClient, RegistryResponseError

_log = logging.getLogger("telegram_gateway.handlers.logs_command")

# Telegram's sendMessage limit is 4096 chars; leave headroom for the truncation
# notice itself. Matches the cap strategy in _errors.py and status_command.py.
_MAX_REPLY_LEN = 4000


async def handle_logs(
    message: Message,
    registry_client: RegistryAPIClient,
) -> None:
    """Handle the /logs <task-id> command.

    Extracts and validates the task-id, calls GET /v1/tasks/{id}/logs/digest
    on registry-api, and replies with either the LLM digest (when available)
    or a placeholder message (when the endpoint returns 404).

    This handler ALWAYS returns normally — exceptions are surfaced as a
    Telegram reply so Telegram never retries the webhook delivery
    (Story 3.1 M3 contract).
    """
    task_id = _keys.extract_task_id_from_message(message)
    if task_id is None:
        raw_text = message.text or ""
        parts = raw_text.split(None, 1)
        if len(parts) < 2:
            await _safe_reply(message, "Usage: /logs <task-id>")
        else:
            await _safe_reply(
                message,
                "Usage: /logs <task-id>; example: /logs t-0192a1b5-1234-7abc-89de-f0123456789a",
            )
        return

    request_id = new_request_id()
    task_id_safe = html.escape(task_id)

    try:
        digest_resp = await registry_client.get_logs_digest(task_id=task_id, request_id=request_id)
    except httpx.TooManyRedirects:
        _log.warning(
            "registry-api too many redirects for /logs (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            _log.warning(
                "registry-api 404 for /logs — digest endpoint not deployed or task not found "
                "(task_id=%s request_id=%s)",
                task_id,
                request_id,
            )
            await _safe_reply(
                message,
                f"📋 Logs for <code>{task_id_safe}</code>\n"
                f"\n"
                f"⚠️ Log digest not yet available — the LLM digest service has not been deployed.\n"
                f"\n"
                f"View raw events with:\n"
                f"oh-my-bmad-cli events {task_id_safe}\n"
                f"\n"
                f"This command will automatically display digests once the service is live.",
            )
            return
        reply = format_http_error(exc, command_label="Logs query")
        _log.warning(
            "registry-api HTTP error for /logs (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    # RegistryResponseError subclasses httpx.HTTPError — catch order matters:
    # it must appear before the generic httpx.HTTPError branch.
    except RegistryResponseError:
        _log.exception(
            "registry-api malformed response for /logs (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Received malformed response from registry.")
        return
    except httpx.HTTPError:
        _log.warning(
            "registry-api network error for /logs (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Could not reach registry. Please try again later.")
        return
    except Exception:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception(
            "/logs handler unexpected error (request_id=%s)",
            request_id,
        )
        await _safe_reply(message, "⚠️ Unexpected error. Please try again later.")
        return

    # AC-4: render the digest. Compute overhead first so the truncation notice
    # (with its CLI escape hatch) is never silently cut by _MAX_REPLY_LEN.
    header = f"📋 Logs digest for <code>{task_id_safe}</code>\n\n"
    truncation_suffix = "\n… (truncated)"

    # Compute max for both server-truncated and non-truncated paths.
    # When server-truncated, the notice is longer; use that as overhead basis
    # so the escape hatch is always preserved even if the flag flips.
    longest_notice = (
        f"\n\n⚠️ Older events were truncated to fit the digest. "
        f"Run `oh-my-bmad-cli events {task_id_safe}` for the full raw stream."
    )
    overhead = len(header) + len(longest_notice) + len(truncation_suffix)
    max_digest_chars = max(_MAX_REPLY_LEN - overhead, 0)

    digest_safe = html.escape(digest_resp.digest)
    locally_truncated = len(digest_safe) > max_digest_chars
    if locally_truncated:
        digest_safe = digest_safe[:max_digest_chars] + truncation_suffix
        # Strip incomplete HTML entity at the cut boundary (e.g. "&am" → "").
        digest_safe = re.sub(r"&[^;]*$", "", digest_safe)

    # Show the CLI escape hatch when either server or local truncation occurred.
    truncation_notice = ""
    if digest_resp.truncated or locally_truncated:
        truncation_notice = (
            f"\n\n⚠️ Older events were truncated to fit the digest. "
            f"Run `oh-my-bmad-cli events {task_id_safe}` for the full raw stream."
        )

    reply_text = header + digest_safe + truncation_notice

    await _safe_reply(message, reply_text)


def make_logs_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same judgment call as make_task_router() (Story 3.3), make_approve_router()
    (Story 3.4), make_ping_router() (Story 3.5), make_status_router() (3.14).
    """
    router = Router()
    router.message(Command("logs"))(handle_logs)
    return router


__all__ = ["handle_logs", "make_logs_router"]
