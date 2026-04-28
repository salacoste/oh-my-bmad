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

``from_user`` extraction
------------------------

When registered on ``dp.update.outer_middleware``, the middleware
receives the wrapping :class:`aiogram.types.Update` object. ``Update``
itself has no ``from_user`` field, so we walk the populated child event
(``message``, ``edited_message``, ``callback_query``, etc.) and pick
the first non-None ``from_user``. Some update types (``poll``,
``poll_answer``, ``message_reaction``) carry no sender identity at
all; per AC-7 the middleware rejects defensively with the
``user_id=0, reason="no_from_user"`` sentinel. The middleware also
runs nested under inner middleware in tests (where ``event`` may
already be the unwrapped child); we accept either by trying the
unwrapped path first, then walking ``Update`` fields.

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
from typing import TYPE_CHECKING, Any, Literal

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from events.clock import Clock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id

# TODO(architecture): relocate ``TelegramRejectedPayload`` to
# ``packages/events/`` so the noqa cross-service import is no longer
# required. Tracked separately; mirror of Story 3.1 lifespan TODO.
from registry_state.domain.event_types import (  # noqa: IMP001 — telegram.rejected payload schema lives in registry-state per Story 2.14 additive-version rule; relocation to packages/events/ tracked in TODO(architecture)
    TelegramRejectedPayload,
)

if TYPE_CHECKING:
    pass

_log = logging.getLogger("telegram_gateway.middleware")

# Update child fields whose value (when populated) carries a ``from_user``
# attribute. Order matters only for documentation; at most one is set per
# Update per Telegram's contract. We iterate to find the first populated
# child that exposes ``from_user``. Excludes ``poll`` / ``poll_answer`` /
# ``message_reaction`` / ``message_reaction_count`` etc. which lack
# ``from_user`` — those flow to the ``no_from_user`` rejection branch.
_UPDATE_CHILD_FIELDS: tuple[str, ...] = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
    "business_connection",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
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
                   update slip through.
        actor:     Identity stamped onto every ``telegram.rejected``
                   envelope this middleware emits. Production: the
                   ``_TELEGRAM_GATEWAY_ACTOR`` constant from the
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
        user_id = self._extract_user_id(event)
        if user_id is None:
            await self._emit_rejection(user_id=0, reason="no_from_user")
            return None
        if user_id in self._allowlist:
            return await handler(event, data)
        await self._emit_rejection(user_id=user_id, reason="not_in_allowlist")
        return None

    @staticmethod
    def _extract_user_id(event: TelegramObject) -> int | None:
        """Pull the sender's user id off the event or its child.

        Outer middleware sees the wrapping :class:`Update`; ``Update``
        itself has no ``from_user``. Walk the populated child events
        and return the first ``from_user.id`` we find. For non-Update
        ``TelegramObject`` (e.g., direct ``Message`` in tests), check
        ``from_user`` on the event itself first.
        """
        # Direct event with from_user (covers Message / CallbackQuery
        # delivered from inner middleware or test harnesses).
        direct_from_user = getattr(event, "from_user", None)
        if direct_from_user is not None:
            uid = getattr(direct_from_user, "id", None)
            if isinstance(uid, int):
                return uid
        # Update wrapper — walk known child fields.
        if isinstance(event, Update):
            for field in _UPDATE_CHILD_FIELDS:
                child = getattr(event, field, None)
                if child is None:
                    continue
                from_user = getattr(child, "from_user", None)
                if from_user is None:
                    continue
                uid = getattr(from_user, "id", None)
                if isinstance(uid, int):
                    return uid
        return None

    async def _emit_rejection(
        self,
        *,
        user_id: int,
        reason: Literal["not_in_allowlist", "no_from_user"],
    ) -> None:
        """Build + dispatch a ``telegram.rejected`` envelope."""
        envelope = EventEnvelope.create(
            event_id=new_event_id(clock=self._clock),
            schema_version="1.0.0",
            type="telegram.rejected",
            emitted_at=self._clock.now(),
            emitted_at_monotonic_ns=self._clock.monotonic_ns(),
            actor=self._actor,
            payload=TelegramRejectedPayload(user_id=user_id, reason=reason),
            request_id=new_request_id(clock=self._clock),
        )
        await self._safe_emit(envelope)

    async def _safe_emit(self, envelope: EventEnvelope) -> None:
        """Run ``self._emit`` and swallow + log non-critical exceptions.

        Mirrors :py:meth:`secret_hygiene.AuditedSecret._safe_emit`:
        audit emission failures must NEVER prevent the middleware's
        reject decision from sticking. Critical control-flow exceptions
        (``KeyboardInterrupt`` / ``SystemExit`` / ``asyncio.CancelledError``)
        propagate naturally per the standard Python contract.
        """
        try:
            await self._emit(envelope)
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 — audit must not propagate
            _log.error(
                "telegram.rejected emission failed (event_id=%s error_type=%s): %s",
                envelope.event_id,
                type(exc).__name__,
                exc,
            )


__all__ = ["AllowlistMiddleware"]
