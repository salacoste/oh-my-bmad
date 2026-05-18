"""/trace command handler for telegram-gateway (Story 9.7 / FR59a).

Causal-chain query — calls GET /v1/trace/{trace_id} on registry-api and
renders each event compactly. Paginates at 20 events per message.

Architecture §"trace_id propagation wiring" §line-1169.

Allowlist (Story 9.7 pass-1 PH-B1)
----------------------------------
The outer ``AllowlistMiddleware`` already enforces per-user allowlist
(FR11/NFR-S4). This handler additionally enforces an OPTIONAL per-chat
allowlist as defense-in-depth: `/trace` exposes full causal chains
including ``secret.accessed`` and ``tier3.action_attempted`` payloads,
so leaking even to an allowlisted user in an unintended chat (e.g.
forwarded/added to a group) is unacceptable. When ``allowed_chat_ids``
is empty the per-chat check is bypassed (back-compat default).
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable
from functools import partial

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from events.envelope import is_valid_trace_id  # noqa: IMP001
from events.ids import new_request_id  # noqa: IMP001

from telegram_gateway.handlers._errors import format_http_error
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


# Zero-width / BOM-style characters that copy-paste tooling can prepend to
# pasted trace_ids. Stripped before validation so operators don't see the
# cryptic "invalid trace_id shape" error without a hint about invisible chars
# (Story 9.7 pass-1 PM-E12).
_INVISIBLE_PREFIXES = "​﻿‌‍⁠"


async def handle_trace(
    message: Message,
    registry_client: RegistryAPIClient,
    allowed_chat_ids: frozenset[int] = frozenset(),
) -> None:
    """Handle the /trace <trace-id> [page=N] command.

    Parses the trace_id argument, validates it, queries /v1/trace/{trace_id}
    and renders a compact event list. Paginates at 20 events per message.

    ``allowed_chat_ids`` (Story 9.7 pass-1 PH-B1): optional per-chat
    allowlist. Empty set bypasses the per-chat check; non-empty sets log
    + drop messages from non-allowed chats. Defense-in-depth on top of
    the global :class:`AllowlistMiddleware` per-user check.

    Same allowlist-and-error contract as other handlers — ALWAYS returns
    normally so Telegram never retries the webhook delivery.
    """
    # PH-B1: per-chat allowlist defense-in-depth. The outer middleware
    # enforces per-user allowlist; this protects against forwarding/
    # group-add scenarios where an allowlisted user is in a non-allowlisted
    # chat. Empty set = bypass (back-compat default).
    if allowed_chat_ids:
        chat_id = message.chat.id if message.chat is not None else None
        if chat_id is None or chat_id not in allowed_chat_ids:
            _log.warning("/trace rejected: chat_id=%r not in allowed_chat_ids", chat_id)
            # Do NOT reply — silent drop avoids confirming bot presence to
            # unauthorized chats. Mirrors AllowlistMiddleware behavior.
            return

    raw_text = message.text or ""
    parts = raw_text.split(None, 2)

    # parts[0] = "/trace", parts[1] = trace_id arg, parts[2] = optional "page=N"
    if len(parts) < 2:
        await _safe_reply(
            message,
            "Usage: /trace <trace-id>\nExample: /trace 01917e5c-a7d1-7000-8abc-000000000001",
        )
        return

    # PM-E12: strip ZWSP/BOM/ZWJ/ZWNJ/word-joiner prefixes that copy-paste
    # tooling can sneak in. Then standard whitespace strip.
    arg_trace_id = parts[1].strip().lstrip(_INVISIBLE_PREFIXES)

    # Parse optional page=N argument. PM-B9: show error on bad page rather
    # than silently defaulting to 1 (silent defaulting hides operator bugs).
    page = 1
    if len(parts) >= 3:
        page_arg = parts[2].strip()
        if page_arg.startswith("page="):
            try:
                page = int(page_arg[5:])
            except ValueError:
                await _safe_reply(
                    message,
                    f"⚠️ Invalid page argument: <code>{html.escape(page_arg)}</code>\n"
                    "Expected integer ≥ 1 (e.g. page=2).",
                )
                return
            if page < 1:
                await _safe_reply(
                    message,
                    f"⚠️ Invalid page: must be ≥ 1, got {page}.",
                )
                return

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
    except httpx.ConnectError:
        # PM-B13: transport-error breakdown — mirror console-cli's friendlier
        # categorization rather than collapsing every transport fault into
        # the generic HTTPError branch.
        _log.warning("registry-api connect error for /trace (request_id=%s)", request_id)
        await _safe_reply(message, "⚠️ Could not connect to registry. Please try again later.")
        return
    except httpx.TimeoutException:
        _log.warning("registry-api timeout for /trace (request_id=%s)", request_id)
        await _safe_reply(message, "⚠️ Registry timed out. Please try again later.")
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


def make_trace_router(
    *,
    allowed_chat_ids: Iterable[int] = (),
) -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    ``allowed_chat_ids`` (Story 9.7 pass-1 PH-B1): optional per-chat
    allowlist. Empty iterable disables the per-chat check (back-compat
    default). Non-empty: the handler silently drops messages from chats
    not in the set.

    Avoids "Router already attached" RuntimeError across test lifespans.
    Same pattern as other command routers.
    """
    allowed = frozenset(allowed_chat_ids)
    router = Router()
    # Bind the allowed-chat-ids snapshot to the handler so the router
    # captures the runtime configuration once at construction.
    router.message(Command("trace"))(partial(handle_trace, allowed_chat_ids=allowed))
    return router


__all__ = ["handle_trace", "make_trace_router"]
