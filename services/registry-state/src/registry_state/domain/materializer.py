"""Materializer — event-log → SQLite state dispatch core (Story 2.5, AC-1/2/5).

Per-event flow inside :meth:`Materializer.apply` /
:meth:`Materializer.apply_many` (each runs in its own transaction via
``async with session.begin()``):

  1. ``SELECT 1 FROM events WHERE id = envelope.event_id`` — duplicate check.
     If a row is found we return immediately; the event was already applied
     and re-running the handler would either duplicate side effects or
     trigger spurious "row not found" errors.
  2. Dispatch to the handler registered for ``envelope.type`` (if any).
     The handler runs **before** the event-row insert because of FK ordering:
     ``events.task_id`` references ``tasks.id`` and ``events.session_id``
     references ``sessions.id``; the handler is what creates / updates those
     upstream rows (e.g. ``handle_task_created`` inserts the task row
     ``handle_task_execution_started`` references).
  3. ``INSERT INTO events ... ON CONFLICT DO NOTHING`` — belt-and-braces
     idempotency guard against the (theoretical, FR26-protected) case where
     two appliers race past the SELECT in step 1.
  4. The cursor (``MAX(events.emitted_at_monotonic_ns)``) lets the
     subscriber loop resume after restart without re-applying events that
     were already persisted.

Why SELECT-then-handler-then-INSERT (not INSERT-then-rowcount-dispatch):
``events.task_id`` is a NOT-NULL-FK-when-set column.  Inserting the event
first would fail FK validation for ``task.created`` because the matching
``tasks`` row does not exist yet.  Running the handler first creates the
task row so the subsequent event-row INSERT satisfies its FK.  The
SELECT-based duplicate check is a TOCTOU window only under concurrent
appliers; FR26 (single subscriber, enforced by
``scripts/check_single_writer.py``) eliminates that scenario in practice,
and the ``ON CONFLICT DO NOTHING`` step provides a safety net even if it
did occur.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable

from events.canonical import _default_encoder  # reuse canonical encoder
from events.envelope import EventEnvelope
from pydantic import BaseModel
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_state.domain.errors import MaterializerError
from registry_state.schema import Event

log = logging.getLogger(__name__)

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
        data: dict[str, object] = payload.model_dump()
    else:
        data = dict(payload)
    # ``data.get(...)`` returns ``object | None``; narrow via isinstance so
    # we keep the contract that ``task_id`` / ``session_id`` are ``str | None``
    # without leaning on ``Any``.
    task_id_raw: object | None = (
        data.get("task_id") if env.type.startswith(("task.", "approval.", "tier3.")) else None
    )
    task_id: str | None = task_id_raw if isinstance(task_id_raw, str) else None
    session_id_raw: object | None = (
        data.get("session_id") if env.type == "task.execution.started" else None
    )
    session_id: str | None = session_id_raw if isinstance(session_id_raw, str) else None
    return task_id, session_id


def _canonical_payload_json(env: EventEnvelope) -> str:
    """Return canonical-JSON text of the payload portion ONLY (not the full envelope).

    Matches the Story 2.1 canonical encoder: sorted keys, no whitespace,
    UTF-8, ``_default_encoder`` for datetime and other non-stdlib types.
    """
    payload = env.payload
    if isinstance(payload, BaseModel):
        data: dict[str, object] = payload.model_dump()
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

        Within a single transaction (``async with session.begin()``):
          1. ``SELECT 1 FROM events WHERE id = envelope.event_id`` —
             duplicate check.  If a row is found we return immediately,
             leaving handler side-effects un-fired.
          2. Dispatch to the registered handler (if any).  Handlers create
             / update the ``tasks`` and ``sessions`` rows that the event
             row's FK columns reference, so they MUST run before the
             event-row insert.
          3. ``INSERT INTO events ... ON CONFLICT DO NOTHING`` — a
             belt-and-braces idempotency guard.

        Commits on clean exit; rolls back on exception.  See the module
        docstring for the rationale behind the
        SELECT-then-handler-then-INSERT ordering.
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

        Resumability:
            On the first envelope that fails to apply we log the failing
            envelope's batch index, ``event_id`` and ``type``, then re-raise.
            ``MaterializerError`` propagates as-is; any other exception is
            wrapped in ``MaterializerError`` so callers see a uniform
            failure mode and can decide whether to crash-and-replay (the
            Phase-1 strategy — see ``errors.py``) or recover in-loop.
        """
        new_count = 0
        for index, envelope in enumerate(envelopes):
            try:
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
            except MaterializerError:
                # Already wrapped — log resumability metadata and propagate.
                log.error(
                    "apply_many: envelope index=%d event_id=%s type=%s failed",
                    index,
                    envelope.event_id,
                    envelope.type,
                )
                raise
            except Exception as exc:
                log.error(
                    "apply_many: envelope index=%d event_id=%s type=%s failed: %r",
                    index,
                    envelope.event_id,
                    envelope.type,
                    exc,
                )
                raise MaterializerError(
                    event_id=envelope.event_id,
                    event_type=envelope.type,
                    reason=f"unhandled exception in apply_many at index {index}: {exc}",
                ) from exc
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
