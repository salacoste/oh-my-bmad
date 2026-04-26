"""Event synthesis helpers for the Story 2.11 crash-injection harness.

These helpers build canonical ``EventEnvelope`` objects, serialize them via
``to_canonical_json`` (matching :class:`EventLogWriter`'s on-disk format), and
append them directly to a per-day ``YYYY-MM-DD.jsonl`` file under the
host-side bind-mount the harness exposes via :meth:`CrashHarness.event_log_dir`.

The harness writes JSONL **only** — never SQLite. This preserves the
``check_single_writer`` discipline (the harness is a JSONL-emit "external"
caller; the registry-state container is the sole SQLite writer) and lets
us prove materialization end-to-end via post-restart DB queries.

``Phase`` enumerates the 4 lifecycle phases the harness drives. Phase event
sequences are **additive**: ``EXECUTING`` includes the ``PLANNING`` events
plus its own; ``AWAITING_APPROVAL`` includes both prior phases plus
``task.approval_requested``; etc.

Phase 1 mapping note (per Story 2.11 spec): there is no typed ``verifying``
status in the materializer yet. ``task.summary_emitted`` is the closest
existing post-execution observability event. Real ``verifying`` lifecycle
status lands with Epic 5 worker-lifecycle stories — a TODO marker is
placed below to make the proxy mapping easy to revisit.

Note on durability: ``append_envelope`` does not call ``fsync()`` on the
written file. This matches ``EventLogWriter``'s default behaviour. For the
crash-injection harness this is safe because the kill targets the
``registry-state`` *container*, not the harness process — the harness's
own ``open(..., "ab")`` writes flush via the kernel's page cache before
the kill subprocess returns, and the bind-mount makes those bytes
visible inside the container's view of the same path.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING

import aiosqlite
from events import (
    Actor,
    EventEnvelope,
    TickingClock,
    new_event_id,
    new_request_id,
    new_session_id,
    to_canonical_json,
)
from pydantic import BaseModel
from registry_state.domain.event_types import (
    TaskApprovalRequestedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskSummaryEmittedPayload,
)

if TYPE_CHECKING:
    # ``tests/crash-injection`` has a hyphen in its directory name so it is
    # NOT a Python package — pytest discovers tests via importlib mode. The
    # _crash_compose module sits next to this file; the type-only reference
    # uses the same module-path the test file (``test_restart_recovery.py``)
    # uses at runtime via direct ``from _crash_compose import CrashHarness``.
    from _crash_compose import CrashHarness  # pragma: no cover

    # Clock is type-only here — keeping it inside TYPE_CHECKING avoids a
    # circular-import risk and keeps the runtime import surface narrow.
    from events.clock import Clock


# Synthesized events are emitted by a deterministic test actor — keeping the
# Story 2.10 discipline (``Actor(kind="system", id=...)`` for non-operator
# system-driven events).
HARNESS_ACTOR: Actor = Actor(kind="system", id="crash-harness")


class Phase(StrEnum):
    """Lifecycle phases the crash-injection harness drives.

    Each phase is an **independent** crash-recovery scenario: a fresh
    ``task_id`` is synthesized for each phase test, and the JSONL log
    accumulates 4 separate task lifecycles across the session. The word
    "additive" in the spec refers to the *event sequence within a single
    phase* — i.e. EXECUTING includes PLANNING's events plus its own —
    NOT to one task progressing across multiple phase tests.

    See :func:`drive_task_through_phase` for the canonical per-phase
    event sequence.
    """

    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_APPROVAL = "awaiting_approval"
    # TODO Story 5.x — the real `verifying` lifecycle status lands with
    # Epic 5 worker-lifecycle stories. `task.summary_emitted` is the
    # closest existing post-execution observability event today.
    VERIFYING = "verifying"


def synthesize_envelope(
    *,
    event_type: str,
    schema_version: str,
    task_id: str,
    payload: BaseModel,
    clock: Clock,
    rng: Random,
    parent_event_id: str | None = None,
) -> EventEnvelope:
    """Build a canonical EventEnvelope with sane crash-harness defaults.

    Wraps :meth:`EventEnvelope.create` (which validates against the
    schema-registry) so the harness benefits from the same payload-model
    enforcement production code does. The actor is fixed to
    :data:`HARNESS_ACTOR`; the request_id is freshly generated per call.

    Args:
        event_type: Event type string (e.g. ``"task.created"``). Must already
            be registered with :func:`events.schema_registry.register`.
        schema_version: ``MAJOR.MINOR.PATCH`` semver string.
        task_id: Owning task id; included as a kwarg for traceability and
            to gate against accidentally-mismatched payload.task_id values.
        payload: Pydantic payload model matching the registered type.
        clock: Strictly-increasing clock — the materializer's cursor
            advancement requires monotonic ``emitted_at_monotonic_ns``.
            Use :class:`TickingClock` (default 1ms tick) or pass a
            shared instance across multiple synthesize calls.
        rng: Seeded ``random.Random`` for deterministic UUIDv7s.
        parent_event_id: Optional ``e-<uuidv7>`` parent linkage.
    """
    # Defensive: the lifecycle event payloads all carry ``task_id``;
    # surface a wiring bug if a caller threads a mismatched id through.
    payload_task_id = getattr(payload, "task_id", None)
    if payload_task_id is not None and payload_task_id != task_id:
        raise ValueError(
            f"synthesize_envelope: task_id kwarg {task_id!r} does not match "
            f"payload.task_id {payload_task_id!r}"
        )
    return EventEnvelope.create(
        event_id=new_event_id(clock=clock, rng=rng),
        schema_version=schema_version,
        type=event_type,
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=HARNESS_ACTOR,
        payload=payload,
        parent_event_id=parent_event_id,
        request_id=new_request_id(clock=clock, rng=rng),
    )


def append_envelope(
    log_dir: Path,
    env: EventEnvelope,
    *,
    day: date | None = None,
) -> Path:
    """Synchronously append *env* to the per-day JSONL file under *log_dir*.

    Mirrors :class:`EventLogWriter`'s on-disk format (canonical JSON +
    ``\\n``). No fsync is issued — matches the writer's default behaviour
    when ``REGISTRY_STATE_LOG_FSYNC`` is not set. The harness's writes
    are not interrupted by the kill (the kill targets the
    ``registry-state`` container, not the harness process), so kernel
    page-cache propagation is sufficient.

    Args:
        log_dir: The host-side bind-mount path (e.g.
            ``CrashHarness.event_log_dir()``).
        env: Validated :class:`EventEnvelope` to serialize.
        day: Optional UTC date for filename selection; defaults to
            ``env.emitted_at.date()``.

    Returns:
        The path that was appended to (``log_dir / "YYYY-MM-DD.jsonl"``).
    """
    if day is None:
        day = env.emitted_at.astimezone(UTC).date()
    target = log_dir / f"{day.isoformat()}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    # ``to_canonical_json`` calls ``model_dump(mode="python")`` on the outer
    # envelope which returns ``{}`` for nested BaseModel payloads (Pydantic v2
    # strict+union serialization edge-case: the payload union type is
    # ``dict[str, Any] | BaseModel`` and Pydantic doesn't recurse into the
    # foreign BaseModel's fields during outer-model serialization). Rebuild a
    # dict-payload envelope before serializing so the JSONL line contains the
    # correct field values (matching ``EventLogWriter``'s format).
    payload_dict: dict[str, object]
    if isinstance(env.payload, BaseModel):
        payload_dict = env.payload.model_dump(mode="python")
    else:
        # Already a mapping (e.g. _FrozenDict) — copy via spread so both
        # plain dicts and frozen mappings are handled without depending
        # on the constructor accepting a non-Mapping iterable.
        payload_dict = {**env.payload}
    # Rebuild envelope with a dict payload so to_canonical_json serializes it.
    dict_env = EventEnvelope(
        event_id=env.event_id,
        schema_version=env.schema_version,
        type=env.type,
        emitted_at=env.emitted_at,
        emitted_at_monotonic_ns=env.emitted_at_monotonic_ns,
        actor=env.actor,
        payload=payload_dict,
        parent_event_id=env.parent_event_id,
        trace_id=env.trace_id,
        request_id=env.request_id,
    )
    line = to_canonical_json(dict_env) + b"\n"
    # `xb` would error if the file already exists; we want append.
    # ``open(..., "ab")`` is sync; ``EventLogWriter`` offloads to a thread
    # for asyncio responsiveness, but the harness is a sync test driver
    # so we don't need that machinery.
    with target.open("ab") as fh:
        fh.write(line)
    return target


def drive_task_through_phase(
    harness: CrashHarness,
    *,
    task_id: str,
    phase: Phase,
    clock: Clock,
    rng: Random,
) -> list[EventEnvelope]:
    """Append the canonical event sequence for *phase* to the harness log.

    Sequences are **additive** — each phase reuses the prior phase's
    events and appends new ones:

    * ``PLANNING``         → ``task.created`` → ``task.planning.started``
    * ``EXECUTING``        → ... + ``task.plan.ready`` → ``task.execution.started``
    * ``AWAITING_APPROVAL``→ ... + ``task.approval_requested``
    * ``VERIFYING``        → ... + ``task.summary_emitted``

    Args:
        harness: The :class:`CrashHarness` whose event_log_dir() to write to.
        task_id: The synthesized task ID (use ``new_task_id`` upstream).
        phase: The :class:`Phase` to drive to.
        clock: Strictly-increasing clock (see :func:`synthesize_envelope`).
        rng: Seeded ``random.Random`` for deterministic UUIDv7s.

    Returns:
        The list of envelopes appended to disk, in append order.
    """
    log_dir = harness.event_log_dir()
    envelopes: list[EventEnvelope] = []

    # ---- PLANNING ----------------------------------------------------
    env = synthesize_envelope(
        event_type="task.created",
        schema_version="1.0.0",
        task_id=task_id,
        payload=TaskCreatedPayload(task_id=task_id, title="crash-harness task"),
        clock=clock,
        rng=rng,
    )
    append_envelope(log_dir, env)
    envelopes.append(env)

    env = synthesize_envelope(
        event_type="task.planning.started",
        schema_version="1.0.0",
        task_id=task_id,
        payload=TaskPlanningStartedPayload(task_id=task_id),
        clock=clock,
        rng=rng,
    )
    append_envelope(log_dir, env)
    envelopes.append(env)

    if phase is Phase.PLANNING:
        return envelopes

    # ---- EXECUTING ---------------------------------------------------
    env = synthesize_envelope(
        event_type="task.plan.ready",
        schema_version="1.0.0",
        task_id=task_id,
        payload=TaskPlanReadyPayload(task_id=task_id, plan_summary="synthesized plan"),
        clock=clock,
        rng=rng,
    )
    append_envelope(log_dir, env)
    envelopes.append(env)

    session_id = new_session_id(clock=clock, rng=rng)
    env = synthesize_envelope(
        event_type="task.execution.started",
        schema_version="1.0.0",
        task_id=task_id,
        payload=TaskExecutionStartedPayload(task_id=task_id, session_id=session_id),
        clock=clock,
        rng=rng,
    )
    append_envelope(log_dir, env)
    envelopes.append(env)

    if phase is Phase.EXECUTING:
        return envelopes

    # ---- AWAITING_APPROVAL -------------------------------------------
    env = synthesize_envelope(
        event_type="task.approval_requested",
        schema_version="1.0.0",
        task_id=task_id,
        payload=TaskApprovalRequestedPayload(
            task_id=task_id,
            action="merge",
            justification="synthesized approval gate",
        ),
        clock=clock,
        rng=rng,
    )
    append_envelope(log_dir, env)
    envelopes.append(env)

    if phase is Phase.AWAITING_APPROVAL:
        return envelopes

    # ---- VERIFYING (Phase 1 proxy: task.summary_emitted) -------------
    env = synthesize_envelope(
        event_type="task.summary_emitted",
        schema_version="1.0.0",
        task_id=task_id,
        payload=TaskSummaryEmittedPayload(
            task_id=task_id,
            summary="synthesized summary (Phase 1 verifying-proxy)",
        ),
        clock=clock,
        rng=rng,
    )
    append_envelope(log_dir, env)
    envelopes.append(env)

    return envelopes


async def wait_for_materialization(
    db_path: Path,
    *,
    last_event_id: str,
    timeout_s: float = 30.0,
    poll_interval_s: float = 1.0,
) -> None:
    """Poll the read-only DB until *last_event_id* appears in events table.

    Opens the SQLite file in read-only URI mode (``mode=ro``) so the
    harness honours the single-writer discipline: only the registry-state
    container ever writes the DB.

    Args:
        db_path: Host-side path to ``state.sqlite3``.
        last_event_id: ``e-<uuidv7>`` to wait for.
        timeout_s: Total budget; raises :class:`TimeoutError` on expiry.
        poll_interval_s: Sleep between SELECT attempts.

    Raises:
        TimeoutError: If the row never appears within ``timeout_s``.

    Only :class:`aiosqlite.OperationalError` is swallowed during polling
    (DB file not yet created, file locked, schema not yet applied —
    transient conditions during start-up). Programming errors,
    schema-mismatch errors, and unexpected exceptions propagate so they
    surface as test failures rather than as a misleading TimeoutError.
    """
    deadline = time.monotonic() + timeout_s
    uri = f"file:{db_path}?mode=ro"
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            async with aiosqlite.connect(uri, uri=True) as conn:
                cursor = await conn.execute("SELECT 1 FROM events WHERE id = ?", (last_event_id,))
                row = await cursor.fetchone()
                await cursor.close()
                if row is not None:
                    return
        except aiosqlite.OperationalError as exc:
            # DB may not yet exist, or schema not yet created, or file
            # locked by the writer — all transient during start-up.
            last_err = repr(exc)
        await asyncio.sleep(poll_interval_s)
    raise TimeoutError(
        f"event {last_event_id!r} did not materialize within {timeout_s}s (last error={last_err!r})"
    )


def make_clock_and_rng(*, seed: int = 42) -> tuple[TickingClock, Random]:
    """Construct a deterministic clock + RNG pair for a phase test.

    The clock starts at the current UTC wall-clock instant (so emitted_at
    timestamps line up with the day's JSONL file the registry-state
    subscriber will scan post-restart) and ticks 1ms per call. The RNG
    is seeded so the synthesized event_ids are reproducible.

    ``start_ns`` is derived from the host's real ``time.monotonic_ns()``
    so that each successive phase call produces strictly increasing
    ``emitted_at_monotonic_ns`` values. This is required because the
    harness shares a single DB across all 4 phase tests (the session-scoped
    fixture boots the container once and restarts it between phases). After
    each restart the materializer's startup cursor equals the DB's
    MAX(emitted_at_monotonic_ns) from the previous phase. If a new phase
    starts a fresh TickingClock at ``start_ns=1_000_000`` its first events
    have mono_ns ≤ the prior cursor and are silently skipped — causing
    "task not found" failures for later events in the same phase sequence.

    Using ``time.monotonic_ns()`` as the anchor guarantees that events
    written by any phase are always > any cursor left by prior phases
    (which is bounded above by the ``time.monotonic_ns()`` value at the
    time the prior phase's events were written).

    The ``+1_000_000`` offset is a 1ms breathing-room buffer: it prevents
    a theoretical collision where ``time.monotonic_ns()`` returns the
    same nanosecond value as the prior phase's last-applied event cursor.
    ``TickingClock`` returns ``start_ns`` on the FIRST call (not
    ``start_ns + tick``), so without the offset the very first synthesized
    event of this phase would have ``emitted_at_monotonic_ns == anchor``.
    If the prior phase's last event had the same anchor value, the
    materializer's ``> cursor_ns`` filter would skip it. The +1ms offset
    ensures strict ``>``.
    """
    now = datetime.now(UTC)
    # Anchor at the current host monotonic clock + 1ms breathing room.
    # See docstring rationale above for why both the anchor and the offset
    # are necessary.
    start_ns = time.monotonic_ns() + 1_000_000
    return TickingClock(start_now=now, start_ns=start_ns), Random(seed)


__all__ = [
    "HARNESS_ACTOR",
    "Phase",
    "append_envelope",
    "drive_task_through_phase",
    "make_clock_and_rng",
    "synthesize_envelope",
    "wait_for_materialization",
]
