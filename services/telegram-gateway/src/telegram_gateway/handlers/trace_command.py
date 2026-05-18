"""/trace command handler for telegram-gateway (Story 9.7 / FR59a).

Causal-chain query — calls GET /v1/trace/{trace_id} on registry-api and
renders each event compactly. Paginates at 20 events per message.

Architecture §"trace_id propagation wiring" §line-1169.

Allowlist (Story 9.7 pass-1 PH-B1; pass-3 UH-1)
-----------------------------------------------
The outer ``AllowlistMiddleware`` already enforces per-user allowlist
(FR11/NFR-S4). This handler additionally enforces a REQUIRED per-chat
allowlist as defense-in-depth: `/trace` exposes full causal chains
including ``secret.accessed`` and ``tier3.action_attempted`` payloads,
so leaking even to an allowlisted user in an unintended chat (e.g.
forwarded/added to a group) is unacceptable.

Pass-3 UH-1: ``allowed_chat_ids`` has NO default — every caller MUST
pass an explicit frozenset. An empty frozenset is still legal and
denies every chat (closed-by-default surface). Removing the default
eliminates the previous bypass where bare ``handle_trace(message,
client)`` calls in the production wiring left the per-chat check
disabled.
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
# Pass-3 UL-3: hard cap on the page= argument to prevent absurd "Page
# 999999/3" renders before we know the actual page count.
_MAX_PAGE_ARG = 10_000


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


# Zero-width / BOM-style characters that copy-paste tooling can sneak in
# (anywhere — prefix, middle, suffix). Stripped before validation so operators
# don't see the cryptic "invalid trace_id shape" error without a hint about
# invisible chars (Story 9.7 pass-1 PM-E12 / pass-2 TM-B9 / pass-3 UL-1/UL-2).
# Pass-3 UL-2: renamed from _INVISIBLE_PREFIXES (old name implied prefix-only;
# translate strips from ALL positions, not just the prefix).
_INVISIBLE_CHARS = "​﻿‌‍⁠"
# Pass-3 UL-1: removed ASCII whitespace (" \t\r\n") from the table — those
# characters are already consumed by ``parts = raw_text.split(None, 2)``
# (split(None) strips + collapses whitespace). Including them was dead code.
# Defense-in-depth for invisible Unicode chars only; explicit ``.strip()``
# handles standard whitespace before any invisible-char translate is needed.
_TRACE_ID_STRIP_TABLE = {ord(c): None for c in _INVISIBLE_CHARS}


async def handle_trace(
    message: Message,
    registry_client: RegistryAPIClient,
    allowed_chat_ids: frozenset[int],
) -> None:
    """Handle the /trace <trace-id> [page=N] command.

    Parses the trace_id argument, validates it, queries /v1/trace/{trace_id}
    and renders a compact event list. Paginates at 20 events per message.

    ``allowed_chat_ids`` (Story 9.7 pass-1 PH-B1; pass-3 UH-1): REQUIRED
    per-chat allowlist (no default). Defense-in-depth on top of the
    global :class:`AllowlistMiddleware` per-user check. An empty frozenset
    denies EVERY chat — pass-3 UH-1 removed the previous default-to-empty
    bypass where missing wiring left the check disabled.

    Same allowlist-and-error contract as other handlers — ALWAYS returns
    normally so Telegram never retries the webhook delivery.
    """
    # PH-B1 / UH-1: per-chat allowlist defense-in-depth. The outer
    # middleware enforces per-user allowlist; this protects against
    # forwarding / group-add scenarios where an allowlisted user is in
    # a non-allowlisted chat. ``allowed_chat_ids`` is REQUIRED — an empty
    # frozenset denies every chat (closed-by-default).
    #
    # Story 9.7 pass-2 TH-B1: emit a structured WARNING with the
    # ``telegram.rejected`` marker so the rejection is greppable from
    # the operator's log stream. A typed envelope (matching the outer
    # ``AllowlistMiddleware`` shape) would require threading writer+actor
    # through ``make_trace_router`` — followup work tracked alongside the
    # /status wiring symmetry audit.
    chat_id = message.chat.id if message.chat is not None else None
    if chat_id is None or chat_id not in allowed_chat_ids:
        _log.warning(
            "telegram.rejected handler=/trace chat_id=%r reason=chat_not_allowed",
            chat_id,
        )
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

    # PM-E12 / Story 9.7 pass-2 TM-B9: strip ZWSP/BOM/ZWJ/ZWNJ/word-joiner
    # from ALL positions (prefix + middle + suffix) that copy-paste tooling
    # can sneak in. ``translate`` is position-agnostic; ``lstrip`` was not.
    arg_trace_id = parts[1].translate(_TRACE_ID_STRIP_TABLE)

    # Parse optional page=N argument. PM-B9: show error on bad page rather
    # than silently defaulting to 1 (silent defaulting hides operator bugs).
    # Pass-3 UL-3: cap page parsing at 10_000 so very large integers can't
    # cause misleading "Page 999999/3" replies before we even check bounds.
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
            if page > _MAX_PAGE_ARG:
                await _safe_reply(
                    message,
                    f"⚠️ Page {page} exceeds maximum of {_MAX_PAGE_ARG}.",
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

    # Pass-3 UL-3: early-return error when page > computed page count so
    # operators don't see the nonsensical "Page 999999/3" reply.
    total = len(events)
    pages = (total + _MAX_EVENTS_PER_PAGE - 1) // _MAX_EVENTS_PER_PAGE
    if page > pages:
        await _safe_reply(
            message,
            f"⚠️ Page {page} out of range (max {pages}).",
        )
        return

    reply_text = _render_trace_reply(arg_trace_id, events, page=page)
    await _safe_reply(message, reply_text)


def make_trace_router(
    *,
    allowed_chat_ids: Iterable[int],
) -> Router:
    """Factory — creates a fresh Router per dispatcher instance.

    ``allowed_chat_ids`` (Story 9.7 pass-1 PH-B1; pass-3 UH-1): REQUIRED
    per-chat allowlist (no default). The handler silently drops messages
    from chats not in the set. An empty iterable denies every chat
    (closed-by-default surface) — pass-3 UH-1 removed the previous
    default-to-empty bypass where missing wiring left the check disabled.

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
