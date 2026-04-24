"""Materializer — event-log → SQLite state dispatch core (Story 2.5, AC-1/2/5).

The materializer is the single mutation entry point for all event-sourced
state. It:
  1. Inserts every event into the ``events`` table via ``INSERT ... ON CONFLICT
     DO NOTHING`` (idempotent by PK ``event_id``).
  2. Dispatches to a registered handler when the insert was *new* (rowcount=1).
     Already-applied events (rowcount=0) skip handler dispatch — this is the
     idempotency contract.
  3. Maintains a cursor (``MAX(emitted_at_monotonic_ns)``) that the subscriber
     loop uses to resume after restart without re-applying known events.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from events.canonical import _default_encoder  # reuse canonical encoder
from events.envelope import EventEnvelope
from pydantic import BaseModel
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_state.domain.errors import MaterializerError
from registry_state.schema import Event

# Handler callable: (session, envelope) → None  (async)
Handler = Callable[[AsyncSession, EventEnvelope], Awaitable[None]]


def _extract_ids(env: EventEnvelope) -> tuple[str | None, str | None]:
    """Return ``(task_id, session_id)`` from the envelope payload.

    Intentionally permissive: unknown event types with no ``task_id`` in the
    payload produce ``(None, None)`` — matches schema nullability. Future
    stories can extend per-type extraction without breaking this baseline.
    """
    payload = env.payload
    if isinstance(payload, BaseModel):
        data: dict[str, Any] = payload.model_dump()
    else:
        data = dict(payload)
    task_id: str | None = data.get("task_id") if env.type.startswith("task.") else None
    is_exec = env.type == "task.execution.started"
    session_id: str | None = data.get("session_id") if is_exec else None
    return task_id, session_id


def _canonical_payload_json(env: EventEnvelope) -> str:
    """Return canonical-JSON text of the payload portion ONLY (not the full envelope).

    Matches the Story 2.1 canonical encoder: sorted keys, no whitespace,
    UTF-8, ``_default_encoder`` for datetime and other non-stdlib types.
    """
    payload = env.payload
    if isinstance(payload, BaseModel):
        data: dict[str, Any] = payload.model_dump()
    else:
        data = dict(payload)
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default_encoder,
    )


class Materializer:
    """Dispatch core: apply events to SQLite state via registered handlers.

    Usage::

        materializer = Materializer(session_maker=get_session(engine))
        materializer.register_handler("task.created", handle_task_created)
        await materializer.apply(envelope)
        count = await materializer.apply_many(envelopes)
    """

    def __init__(self, *, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker
        self._handlers: dict[str, Handler] = {}

    def register_handler(self, event_type: str, handler: Handler) -> None:
        """Register *handler* for *event_type*.

        Only one handler per type is supported at Phase-1 scale. Registering a
        second handler for the same type silently replaces the first — future
        stories can add fan-out if needed.
        """
        self._handlers[event_type] = handler

    async def apply(self, envelope: EventEnvelope) -> None:
        """Apply a single event envelope to the SQLite state.

        Opens a session, dispatches to the registered handler (which creates
        the tasks/sessions rows that events.task_id/session_id FK-reference),
        then inserts the ``Event`` row (idempotent by PK via ON CONFLICT DO
        NOTHING). Handler runs first so that FK constraints are satisfied when
        the event row is inserted. If the event row already exists (rowcount=0),
        the whole operation was already applied — no handler dispatch.
        Commits on clean exit; rolls back on exception.
        """
        async with self._session_maker() as session, session.begin():
            task_id, session_id = _extract_ids(envelope)
            event_values = dict(
                id=envelope.event_id,
                type=envelope.type,
                schema_version=envelope.schema_version,
                emitted_at=envelope.emitted_at,
                emitted_at_monotonic_ns=envelope.emitted_at_monotonic_ns,
                actor_kind=envelope.actor.kind,
                actor_id=envelope.actor.id,
                task_id=task_id,
                session_id=session_id,
                parent_event_id=envelope.parent_event_id,
                request_id=envelope.request_id,
                payload_json=_canonical_payload_json(envelope),
            )
            # Check for duplicate BEFORE running handler (avoid side-effects
            # on already-applied events). Use a SELECT for existence check.
            dup_stmt = select(literal(1)).where(Event.id == envelope.event_id)
            dup_result = await session.execute(dup_stmt)
            if dup_result.scalar() is not None:
                return  # already applied — skip handler
            # Handler runs first so it creates the referenced tasks/sessions
            # rows before the event row's FK constraints are checked.
            handler = self._handlers.get(envelope.type)
            if handler is not None:
                await handler(session, envelope)
            event_stmt = (
                sqlite_insert(Event)
                .values(**event_values)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await session.execute(event_stmt)

    async def apply_many(self, envelopes: Iterable[EventEnvelope]) -> int:
        """Apply multiple envelopes; return count of *new* events applied.

        Duplicates (events already in ``events`` table) are skipped silently.
        Used by startup replay to process batches of historical events.
        Handler runs before event-row insert to satisfy FK constraints.
        """
        new_count = 0
        for envelope in envelopes:
            async with self._session_maker() as session, session.begin():
                # Duplicate check — avoid running the handler twice.
                dup_stmt = select(literal(1)).where(Event.id == envelope.event_id)
                dup_result = await session.execute(dup_stmt)
                if dup_result.scalar() is not None:
                    continue  # already applied — skip
                # Handler first (creates FK-referenced rows).
                handler = self._handlers.get(envelope.type)
                if handler is not None:
                    await handler(session, envelope)
                # Event row insert after handler so FK is satisfied.
                task_id, session_id = _extract_ids(envelope)
                event_stmt = (
                    sqlite_insert(Event)
                    .values(
                        id=envelope.event_id,
                        type=envelope.type,
                        schema_version=envelope.schema_version,
                        emitted_at=envelope.emitted_at,
                        emitted_at_monotonic_ns=envelope.emitted_at_monotonic_ns,
                        actor_kind=envelope.actor.kind,
                        actor_id=envelope.actor.id,
                        task_id=task_id,
                        session_id=session_id,
                        parent_event_id=envelope.parent_event_id,
                        request_id=envelope.request_id,
                        payload_json=_canonical_payload_json(envelope),
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                await session.execute(event_stmt)
                new_count += 1
        return new_count

    async def cursor(self, session: AsyncSession) -> int:
        """Return ``MAX(emitted_at_monotonic_ns)`` from the events table, or 0 if empty.

        Used by the subscriber loop to derive a resumable position: events
        whose ``emitted_at_monotonic_ns <= cursor`` were already applied and
        can be filtered out before replay.
        """
        stmt = select(func.max(Event.emitted_at_monotonic_ns))
        result = await session.execute(stmt)
        value: int | None = result.scalar()
        return value if value is not None else 0


__all__ = ["Handler", "Materializer", "MaterializerError"]
