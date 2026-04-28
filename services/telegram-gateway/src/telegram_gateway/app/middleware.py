"""Allowlist outer middleware for telegram-gateway (Story 3.2 / FR11 / NFR-S4).

aiogram v3 ``BaseMiddleware`` registered on ``dp.update.outer_middleware``
that intercepts EVERY inbound :class:`aiogram.types.Update` BEFORE the
dispatcher resolves a handler. Non-allowlisted senders short-circuit
(``return None``) so the handler chain never runs; rejection is logged
to the audit trail as a typed ``telegram.rejected`` envelope with the
minimal PII surface ``{user_id, reason}`` (no message content, no
username, no chat metadata).

Why outer_middleware (not inner)
--------------------------------

Inner middleware fires only AFTER routing finds a handler match. Outer
middleware fires BEFORE — even for update types with no registered
handler (``my_chat_member``, ``poll``, etc.). The allowlist must run
before routing so non-allowlisted users sending unhandled update types
are ALSO rejected + audited (defense-in-depth + NFR-S4 wording: "no
response" is independent of whether a handler would have matched).

No-response semantics (AC-5)
----------------------------

Story 3.1's webhook handler returns ``200`` BEFORE ``feed_webhook_update``
(fire-and-forget dispatch via ``asyncio.create_task``). Returning
``None`` from this middleware suppresses handler invocation but never
sends an outbound Telegram message — Telegram observes a successful
``200`` ACK and never receives a ``sendMessage`` for rejected users.

``from_user`` / ``user`` extraction
------------------------------------

When registered on ``dp.update.outer_middleware``, the middleware
receives the wrapping :class:`aiogram.types.Update` object. ``Update``
itself has no ``from_user`` field, so we walk the populated child event
and pick the first non-None ``from_user`` or ``user``. aiogram v3 child
fields and their user-attribute names:

- ``message``, ``edited_message``, ``channel_post``, ``edited_channel_post``,
  ``business_message``, ``edited_business_message``: ``from_user``
- ``callback_query``, ``inline_query``, ``chosen_inline_result``,
  ``shipping_query``, ``pre_checkout_query``: ``from_user``
- ``my_chat_member``, ``chat_member``, ``chat_join_request``: ``from_user``
- ``poll_answer``, ``message_reaction``, ``business_connection``: ``user``
- ``purchased_paid_media``: ``from_user``
- ``message_reaction_count``, ``chat_boost``, ``removed_chat_boost``,
  ``deleted_business_messages``: no user identity — falls through to
  ``no_from_user`` defensive rejection (bot-only events, AC-7).

``request_id`` correlation (M6)
--------------------------------

The middleware accepts an optional ``request_id`` from the aiogram
``data`` dict (injected by Story 3.6's request-id middleware). If
present, it is forwarded to the rejection envelope so the log entry
correlates with the inbound webhook request. Falls back to a freshly
minted ``new_request_id()`` until 3.6 lands.

AllowlistMiddleware MUST be the first registered outer_middleware on
``dp.update`` — Story 3.6 ack. See ``lifespan.py`` registration site.

Fire-and-forget audit emission
------------------------------

Mirrors Story 2.16's ``AuditedSecret._safe_emit`` shape: ``emit`` is
awaited inside a try/except that swallows + logs everything except
control-flow exceptions (``KeyboardInterrupt``, ``SystemExit``,
``CancelledError``). The middleware decision (``return None`` for
rejected, ``return await handler(...)`` for allowed) is taken BEFORE
the await on emit, so an emission failure cannot let a rejected
update slip through to the handler. The emit is awaited inline (not
``create_task``-scheduled) so test assertions on envelope counts are
synchronous; production callers run in an asyncio loop where the
inline await adds ``< 1ms`` for the in-process EventLogWriter (AC-9).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from events.clock import Clock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id

# TODO(architecture): relocate ``TelegramRejectedPayload`` to
# ``packages/events/`` so the noqa cross-service import is no longer
# required. Tracked separately; mirror of Story 3.1 lifespan TODO.
from registry_state.domain.event_types import (  # noqa: IMP001 — see TODO above
    TELEGRAM_REJECTED_SCHEMA_VERSION,
    TelegramRejectedPayload,
)

# Re-entrant emission guard — same ContextVar used by AuditedSecret._safe_emit.
# Importing it here lets AllowlistMiddleware's _safe_emit skip nested emissions
# if a future emit callable itself reads an AuditedSecret. If the ContextVar
# is not publicly exported from secret_hygiene, this import will fail; document
# the divergence below and fall back to the unguarded form.
try:
    from secret_hygiene.audited_secret import (  # noqa: PLC2701 — intentional private-name for re-entrant guard
        _in_emission,
    )

    _HAS_IN_EMISSION = True
except ImportError:
    # TODO(architecture): expose _in_emission as a public API from secret_hygiene
    # so AllowlistMiddleware can participate in the re-entrancy guard without
    # importing a private name. Until then we operate without the guard.
    _in_emission = None  # type: ignore[assignment]
    _HAS_IN_EMISSION = False

_log = logging.getLogger("telegram_gateway.middleware")

# Update child fields and their user-identity attribute name.
# "First populated field wins" — per Telegram's contract at most one is set
# per Update, but if multiple are somehow set (contract violation) we pick
# the first match deterministically.
#
# Format: (field_name, user_attr)
#   user_attr="from_user"  — standard aiogram v3 fields
#   user_attr="user"       — poll_answer, message_reaction, business_connection
#   user_attr=None         — bot-only events; no per-user identity
_UPDATE_CHILD_FIELDS: tuple[tuple[str, str | None], ...] = (
    ("message", "from_user"),
    ("edited_message", "from_user"),
    ("channel_post", "from_user"),
    ("edited_channel_post", "from_user"),
    ("business_message", "from_user"),
    ("edited_business_message", "from_user"),
    ("business_connection", "user"),  # uses .user, not .from_user
    ("callback_query", "from_user"),
    ("inline_query", "from_user"),
    ("chosen_inline_result", "from_user"),
    ("shipping_query", "from_user"),
    ("pre_checkout_query", "from_user"),
    ("my_chat_member", "from_user"),
    ("chat_member", "from_user"),
    ("chat_join_request", "from_user"),
    ("poll_answer", "user"),  # uses .user, not .from_user
    ("message_reaction", "user"),  # uses .user, not .from_user
    ("purchased_paid_media", "from_user"),
    # bot-only / no per-user identity — falls through to no_from_user:
    ("message_reaction_count", None),
    ("chat_boost", None),
    ("removed_chat_boost", None),
    ("deleted_business_messages", None),
)

EmitCallable = Callable[[EventEnvelope], Awaitable[None]]


class AllowlistMiddleware(BaseMiddleware):
    """Outer middleware enforcing the operator allowlist (FR11 / NFR-S4).

    Args:
        allowlist: ``frozenset[int]`` of allowed Telegram user ids. An
                   empty set rejects every inbound update (closed-by-
                   default). The lifespan emits a startup WARNING when
                   the operator passes an empty set so the configuration
                   gap surfaces at boot.
        emit:      Async callable that persists an :class:`EventEnvelope`
                   (typically ``EventLogWriter.append``). Failures are
                   swallowed + logged via ``_safe_emit`` — the middleware's
                   reject/accept decision is taken BEFORE the await on
                   emit, so an emission outage cannot let a rejected
                   update slip through. Must NOT be ``None``.
        actor:     Identity stamped onto every ``telegram.rejected``
                   envelope this middleware emits. Production: the
                   ``TELEGRAM_GATEWAY_ACTOR`` constant from the
                   lifespan module (``Actor(kind="system",
                   id="telegram-gateway")``).
        clock:     Injectable :class:`events.clock.Clock` for
                   deterministic envelope IDs + timestamps in tests.
    """

    def __init__(
        self,
        *,
        allowlist: frozenset[int],
        emit: EmitCallable,
        actor: Actor,
        clock: Clock,
    ) -> None:
        if emit is None:  # type: ignore[comparison-overlap]
            raise ValueError("AllowlistMiddleware requires a non-None emit callable")
        self._allowlist = allowlist
        self._emit = emit
        self._actor = actor
        self._clock = clock

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Reject non-allowlisted senders BEFORE handler dispatch.

        Returning ``None`` short-circuits the dispatcher (handler never
        runs). Allowed users delegate to ``handler(event, data)``
        unchanged.
        """
        # Carry forward the inbound request_id when Story 3.6's request-id
        # middleware has already injected it into ``data``. Falls back to a
        # fresh ID until 3.6 lands (M6 — documented in module docstring).
        inbound_request_id = data.get("request_id") if isinstance(data, dict) else None

        user_id = self._extract_user_id(event)
        if user_id is None:
            await self._emit_rejection(
                user_id=0, reason="no_from_user", request_id=inbound_request_id
            )
            return None
        if user_id in self._allowlist:
            return await handler(event, data)
        await self._emit_rejection(
            user_id=user_id, reason="not_in_allowlist", request_id=inbound_request_id
        )
        return None

    @staticmethod
    def _extract_user_id(event: TelegramObject) -> int | None:
        """Pull the sender's user id off the event or its child.

        Outer middleware sees the wrapping :class:`Update`; ``Update``
        itself has no ``from_user``. Walk the populated child events
        and return the first user id we find. For non-Update
        ``TelegramObject`` (e.g., direct ``Message`` in tests), check
        ``from_user`` on the event itself first.

        When multiple child fields are populated (Telegram contract
        violation) a WARNING is emitted and the first match wins
        deterministically.
        """
        # Direct event with from_user (covers Message / CallbackQuery
        # delivered from inner middleware or test harnesses).
        direct_from_user = getattr(event, "from_user", None)
        if direct_from_user is not None:
            uid = getattr(direct_from_user, "id", None)
            if isinstance(uid, int) and not isinstance(uid, bool):
                return uid
        # Update wrapper — walk known child fields.
        if isinstance(event, Update):
            populated_fields: list[str] = []
            for field, _uattr in _UPDATE_CHILD_FIELDS:
                child = getattr(event, field, None)
                if child is not None:
                    populated_fields.append(field)
            if len(populated_fields) > 1:
                _log.warning("Update has multiple populated child fields: %r", populated_fields)
            for field, user_attr in _UPDATE_CHILD_FIELDS:
                child = getattr(event, field, None)
                if child is None:
                    continue
                if user_attr is None:
                    # Bot-only event; no user identity — skip, falls to no_from_user.
                    continue
                user_obj = getattr(child, user_attr, None)
                if user_obj is None:
                    continue
                uid = getattr(user_obj, "id", None)
                if isinstance(uid, int) and not isinstance(uid, bool):
                    return uid
        return None

    async def _emit_rejection(
        self,
        *,
        user_id: int,
        reason: Literal["not_in_allowlist", "no_from_user"],
        request_id: str | None = None,
    ) -> None:
        """Build + dispatch a ``telegram.rejected`` envelope."""
        resolved_request_id = (
            request_id if request_id is not None else new_request_id(clock=self._clock)
        )
        envelope = EventEnvelope.create(
            event_id=new_event_id(clock=self._clock),
            schema_version=TELEGRAM_REJECTED_SCHEMA_VERSION,
            type="telegram.rejected",
            emitted_at=self._clock.now(),
            emitted_at_monotonic_ns=self._clock.monotonic_ns(),
            actor=self._actor,
            payload=TelegramRejectedPayload(user_id=user_id, reason=reason),
            request_id=resolved_request_id,
        )
        await self._safe_emit(envelope)

    async def _safe_emit(self, envelope: EventEnvelope) -> None:
        """Run ``self._emit`` and swallow + log non-critical exceptions.

        Mirrors :py:meth:`secret_hygiene.AuditedSecret._safe_emit`:
        audit emission failures must NEVER prevent the middleware's
        reject decision from sticking. Critical control-flow exceptions
        (``KeyboardInterrupt`` / ``SystemExit`` / ``asyncio.CancelledError``)
        propagate naturally per the standard Python contract.

        Sets the ``_in_emission`` ContextVar (from secret_hygiene) when
        available, preventing re-entrant AuditedSecret reads inside the
        emit callable from scheduling additional audit tasks.
        """
        if _HAS_IN_EMISSION and _in_emission is not None:
            token = _in_emission.set(True)
            try:
                try:
                    await self._emit(envelope)
                except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                    raise
                except Exception as exc:  # noqa: BLE001 — audit must not propagate
                    _log.exception(
                        "telegram.rejected emission failed (event_id=%s error_type=%s): %s",
                        envelope.event_id,
                        type(exc).__name__,
                        exc,
                    )
            finally:
                _in_emission.reset(token)
        else:
            try:
                await self._emit(envelope)
            except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 — audit must not propagate
                _log.exception(
                    "telegram.rejected emission failed (event_id=%s error_type=%s): %s",
                    envelope.event_id,
                    type(exc).__name__,
                    exc,
                )


__all__ = ["AllowlistMiddleware"]
