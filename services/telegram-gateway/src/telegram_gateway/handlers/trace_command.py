"""/trace command handler for telegram-gateway (Story 9.7 / FR59a).

Causal-chain query — calls GET /v1/trace/{trace_id} on registry-api and
renders each event compactly. Paginates at 20 events per message.

Architecture §"trace_id propagation wiring" §line-1169.
"""

from __future__ import annotations

import contextlib
import html
import logging

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.envelope import is_valid_trace_id  # noqa: IMP001
from events.ids import new_request_id  # noqa: IMP001

from telegram_gateway.handlers._errors import format_http_error, log_missing_trace_id
from telegram_gateway.handlers._safe_reply import safe_reply as _safe_reply
from telegram_gateway.handlers.registry_client import (
    RegistryAPIClient,
    RegistryResponseError,
)

_log = logging.getLogger("telegram_gateway.handlers.trace_command")

# Telegram message size limit with headroom for the truncation notice.
_MAX_REPLY_LEN = 4000
# Maximum events to show per message before pagination notice.
_MAX_EVENTS_PER_PAGE = 20


def _render_event_line(event: dict[str, object]) -> str:
    """Render a single event as a compact Telegram-safe HTML line."""
    event_type = html.escape(str(event.get("type", "unknown")))
    event_id = html.escape(str(event.get("event_id", "?")))
    emitted_at = html.escape(str(event.get("emitted_at", "?")))
    # Show only the time portion for compactness (strip date + timezone suffix).
    time_part = emitted_at[11:23] if len(emitted_at) > 23 else emitted_at
    return f"<code>{time_part}</code> <b>{event_type}</b> <code>{event_id[:16]}…</code>"


def _render_trace_reply(
    trace_id: str,
    events: list[dict[str, object]],
    page: int = 1,
) -> str:
    """Render paginated event chain for Telegram."""
    start = (page - 1) * _MAX_EVENTS_PER_PAGE
    page_events = events[start : start + _MAX_EVENTS_PER_PAGE]
    total = len(events)
    pages = (total + _MAX_EVENTS_PER_PAGE - 1) // _MAX_EVENTS_PER_PAGE

    tid = html.escape(trace_id)
    lines: list[str] = [f"🔍 Trace <code>{tid}</code> — {total} event(s)"]

    if pages > 1:
        lines.append(f"Page {page}/{pages} — use /trace {trace_id} page={page + 1} for more")

    lines.append("")
    for event in page_events:
        lines.append(_render_event_line(event))

    reply = "\n".join(lines)
    if len(reply) > _MAX_REPLY_LEN:
        cut = reply.rfind("\n", 0, _MAX_REPLY_LEN)
        if cut == -1:
            cut = _MAX_REPLY_LEN
        reply = reply[:cut] + "\n… (truncated)"
    return reply


async def handle_trace(
    message: Message,
    registry_client: RegistryAPIClient,
    trace_id: str | None = None,
) -> None:
    """Handle the /trace <trace-id> [page=N] command.

    Parses the trace_id argument, validates it, queries /v1/trace/{trace_id}
    and renders a compact event list. Paginates at 20 events per message.

    Same allowlist-and-error contract as other handlers — ALWAYS returns
    normally so Telegram never retries the webhook delivery.
    """
    if trace_id is None:
        log_missing_trace_id(_log, "/trace")

    raw_text = message.text or ""
    parts = raw_text.split(None, 2)

    # parts[0] = "/trace", parts[1] = trace_id arg, parts[2] = optional "page=N"
    if len(parts) < 2:
        await _safe_reply(
            message,
            "Usage: /trace <trace-id>\nExample: /trace 01917e5c-a7d1-7000-8abc-000000000001",
        )
        return

    arg_trace_id = parts[1]

    # Parse optional page=N argument.
    page = 1
    if len(parts) >= 3:
        page_arg = parts[2].strip()
        if page_arg.startswith("page="):
            with contextlib.suppress(ValueError):
                page = max(1, int(page_arg[5:]))

    if not is_valid_trace_id(arg_trace_id):
        await _safe_reply(
            message,
            f"⚠️ Invalid trace_id: <code>{html.escape(arg_trace_id)}</code>\n"
            "Must be a bare UUIDv7 or 'tg:&lt;update_id&gt;'.",
        )
        return

    request_id = new_request_id()

    try:
        events = await registry_client.get_trace(
            trace_id=arg_trace_id,
            request_id=request_id,
        )
    except httpx.TooManyRedirects:
        _log.warning("registry-api too many redirects for /trace (request_id=%s)", request_id)
        await _safe_reply(message, "⚠️ Registry unreachable. Try again in a moment.")
        return
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            await _safe_reply(
                message,
                f"⚠️ Invalid trace_id rejected by registry: "
                f"<code>{html.escape(arg_trace_id)}</code>",
            )
            return
        reply = format_http_error(exc, command_label="Trace query")
        _log.warning(
            "registry-api HTTP error for /trace (status=%s request_id=%s): %s",
            exc.response.status_code,
            request_id,
            exc,
        )
        await _safe_reply(message, reply)
        return
    except RegistryResponseError:
        _log.exception("registry-api malformed response for /trace (request_id=%s)", request_id)
        await _safe_reply(message, "⚠️ Received malformed response from registry.")
        return
    except httpx.HTTPError:
        _log.warning("registry-api network error for /trace (request_id=%s)", request_id)
        await _safe_reply(message, "⚠️ Could not reach registry. Please try again later.")
        return
    except Exception:  # noqa: BLE001 — backstop: never propagate to webhook
        _log.exception("/trace handler unexpected error (request_id=%s)", request_id)
        await _safe_reply(message, "⚠️ Unexpected error. Please try again later.")
        return

    if not events:
        await _safe_reply(
            message,
            f"No events found for trace_id=<code>{html.escape(arg_trace_id)}</code>.",
        )
        return

    reply_text = _render_trace_reply(arg_trace_id, events, page=page)
    await _safe_reply(message, reply_text)


def make_trace_router() -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same pattern as other command routers.
    """
    router = Router()
    router.message(Command("trace"))(handle_trace)
    return router


__all__ = ["handle_trace", "make_trace_router"]
