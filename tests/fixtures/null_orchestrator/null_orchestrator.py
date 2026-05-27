"""Pass-through null orchestrator — test fixture for the S-3 separability test.

Story 2.15 / FR35 / NFR-M5: this script proves that the platform's
``orchestrator-adapter`` layer is swappable. It runs in place of the real
orchestrator inside the docker-compose stack via the ``ORCHESTRATOR_IMAGE``
env-var override; it tails the JSONL event log, detects each
``task.created`` envelope, and emits the canonical 4-event task lifecycle:

  task.planning.started → task.plan.ready → task.execution.started → task.completed

The fixture deliberately fakes the entire lifecycle without launching any
real worker — that is the "pass-through null" interpretation of FR35.

Design choices (documented in the Story 2.15 Spec Amendments):

* **JSONL tailing, not MCP subscription.** The null orchestrator polls the
  current-day ``YYYY-MM-DD.jsonl`` file at 100ms intervals using the same
  ``_read_new_envelopes_since`` primitive registry-state's subscriber uses.
  An MCP-subscriber path would add significant plumbing for a test fixture;
  JSONL tailing is the pragmatic minimum.

* **4 events emitted, not 3.** The spec lists 3 (planning.started,
  plan.ready, execution.requested) but says "task completes via
  task.completed". Without a worker no one else emits ``task.completed``,
  so the null orchestrator emits it too. This is the simplest interpretation
  of "pass-through" — the orchestrator pretends to orchestrate by emitting
  the canonical lifecycle events without doing any real work.

* **``task.execution.started``, not ``task.execution.requested``.** The
  spec's ``task.execution.requested`` is a typo — only
  ``task.execution.started`` is registered with the schema registry
  (Stories 2.5 + 2.10 registered all 12 task types). Using the actual
  registered type keeps payload validation green.

* **In-memory dedupe under restart (AC-7).** On startup the orchestrator
  scans existing ``*.jsonl`` files in ``EVENT_LOG_DIR`` and builds a set
  of ``task_id``s that already have ANY follow-up lifecycle event
  (``task.planning.started`` / ``task.plan.ready`` /
  ``task.execution.started`` / ``task.completed``) in the log; tasks
  present in that set are NEVER re-processed. During tailing each
  newly-detected ``task.created`` is checked against the set, then
  added AFTER the lifecycle has been successfully appended (mark-after-
  emit). The mark-after-emit ordering closes the previous
  crash-between-mark-and-first-emit window: a crash mid-lifecycle leaves
  the partial lifecycle in the log, the next run's startup scan picks
  it up via the present-but-incomplete lifecycle types, and the task
  is suppressed from re-emit. A crash BEFORE the first append leaves
  the task with ONLY ``task.created`` in the log; the next run emits
  the full lifecycle — the desired exactly-once-from-the-orchestrator
  semantics. This is a Phase-1 best-effort behavior — production
  orchestrators (Story 5.10+) will use proper task-state queries instead
  of log-scanning.

* **``Actor(kind="orchestrator", id="null-orchestrator")``.** All emitted
  envelopes carry this fixed actor identity. ``parent_event_id`` chains
  to the originating ``task.created`` envelope's id for audit traceability.

* **``request_id`` is regenerated per emit (NOT shared across the
  lifecycle).** Each of the 4 follow-up envelopes calls
  ``new_request_id()`` independently. Rationale: ``request_id`` in the
  envelope schema correlates events sharing the same originating
  request — there is no concept of "this orchestrator caused 4 events
  in the same request"; each emit is its own logical operation and
  carries its own correlation id. Audit traceability across the
  lifecycle is preserved via ``parent_event_id`` (all 4 chain to the
  ``task.created`` envelope's ``event_id``), not via request_id.

* **``/tmp/ready`` healthcheck signal.** Touched after the first poll
  iteration so docker-compose's ``test -f /tmp/ready`` healthcheck flips
  to ``healthy``. Mirrors the registry-state pattern.

Multi-writer JSONL caveat (M10, deferred to Story 5.10): registry-api
AND null-orchestrator both append to the same ``YYYY-MM-DD.jsonl``
file in this test stack. Story 2.4's ``EventLogWriter`` documents
"use only one instance per process" as the production contract.
Phase-1 envelope sizes (4 lifecycle types: ~600-900 bytes each
including base64 payloads) are well below ``PIPE_BUF`` (4096 bytes
on Linux) so ``write(2)`` calls remain atomic — no torn-line risk
under current event types. The proper architectural fix (have the
null-orchestrator emit via the clawhip-bridge MCP rather than the
JSONL writer directly) is deferred to Story 5.10's real
orchestrator-adapter.

Environment variables:

* ``EVENT_LOG_DIR`` — directory containing ``YYYY-MM-DD.jsonl`` files.
  Default ``/var/lib/oh-my-bmad/registry/events`` (production-aligned).
* ``NULL_ORCHESTRATOR_POLL_INTERVAL_S`` — poll-loop sleep between scans
  (float seconds; default ``0.1`` = 100ms). Used by tests to speed up
  the loop if needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

from events import (  # side-effect: register() calls
    Actor,
    EventEnvelope,
    SystemClock,
    TaskCompletedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    new_request_id,
    new_session_id,
)
from events.canonical import from_canonical_json
from events.clock import Clock
from events.ids import new_event_id
from registry_state.adapters.event_log import (
    EventLogWriter,
    read_log_lines,
)

log = logging.getLogger("null-orchestrator")

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_POLL_INTERVAL_S = 0.1

# Fixed actor identity for every event the null orchestrator emits — see
# AC-6. ``orchestrator`` is one of the registered ``ActorKind`` literals
# (``operator | orchestrator | worker | system | clawhip``).
_ACTOR: Actor = Actor(kind="orchestrator", id="null-orchestrator")

# Healthcheck readiness file path. ``docker-compose.yml`` uses
# ``test -f /tmp/ready`` for the healthcheck — the orchestrator touches
# this file after the first poll iteration to flip the health to ``healthy``.
_READY_FILE = Path("/tmp/ready")  # noqa: S108 — healthcheck signal, not data store

# Set of envelope types the null-orchestrator itself emits. Presence of ANY
# of these in the log for a given task_id means the orchestrator has already
# at least started processing — the startup scan keys on this set so a crash
# between detecting ``task.created`` and emitting the first lifecycle event
# is correctly handled (crash-victims pre-emit re-emit on restart; tasks
# with ANY orchestrator-emitted event are skipped).
ORCHESTRATOR_EMITTED_TYPES: frozenset[str] = frozenset(
    {
        "task.planning.started",
        "task.plan.ready",
        "task.execution.started",
        "task.completed",
    }
)


def _scan_processed_task_ids(base_dir: Path) -> set[str]:
    """Return task_ids that have ≥1 orchestrator-emitted lifecycle event in the log.

    Restart contract (AC-7): on restart, tasks where ANY orchestrator-emitted
    event already landed are skipped. Tasks where only ``task.created`` is
    present (crash-victims pre-emit) ARE re-processed — their lifecycle is
    re-emitted from scratch. This means crash-victims may produce
    duplicate-by-task_id lifecycle events across runs; production
    orchestrators (Story 5.10+) use proper task-state queries to avoid this.

    Story 2.15 code-review fix M5 (round 2): uses the module-level
    :data:`ORCHESTRATOR_EMITTED_TYPES` set so the witness types stay in
    sync with what :func:`_emit_lifecycle_for_task` actually appends.

    rglob is used (Mn10 hardening borrowed from Story 2.13's reviewer)
    so future operators who organize event logs into subdirectories
    (e.g., ``YYYY/MM/DD.jsonl``) still get correct dedupe seeding.

    Memory note (Mn12): at 1M tasks the seen-set is ~80MB and the full
    rescan is O(N) on every restart. Acceptable for a Phase-1 test
    fixture; production orchestrators (Story 5.10+) will use proper
    task-state queries against registry-state's SQLite projection
    instead of log-scanning.
    """
    seen: set[str] = set()
    if not base_dir.exists():
        return seen
    for path in sorted(base_dir.rglob("*.jsonl")):
        try:
            for env in read_log_lines(path):
                if env.type not in ORCHESTRATOR_EMITTED_TYPES:
                    continue
                payload = env.payload
                if isinstance(payload, dict):
                    task_id = payload.get("task_id")
                else:
                    task_id = getattr(payload, "task_id", None)
                if isinstance(task_id, str):
                    seen.add(task_id)
        except FileNotFoundError:
            # TOCTOU: file disappeared between glob() and open(). Skip.
            continue
    return seen


def _read_new_envelopes_since(path: Path, offset: int) -> tuple[int, list[EventEnvelope]]:
    """Tail-read complete envelopes from *path* starting at *offset*.

    Inlined from ``registry_state.adapters.event_log._read_new_envelopes_since``
    per Story 2.15 code-review fix M9 — the upstream symbol is private
    (underscore-prefixed) and importing across the spine boundary would
    technically count as "spine source touched" by the test fixture's
    coupling; story principle is "no spine source changes". Inline copy
    keeps the fixture self-contained without promoting the spine API.

    Behavior matches the upstream implementation:

    * Missing path → ``(offset, [])``.
    * Offset beyond EOF → ``(offset, [])`` (no auto-reset).
    * Trailing partial lines (no terminating ``\\n``) are NOT consumed;
      offset stops at the last complete newline.
    * CRLF tolerance: trailing ``\\r`` is stripped before JSON parse.

    Note (Mn3 documentation): callers are expected to invoke this via
    ``asyncio.to_thread`` so the blocking syscalls don't stall the event
    loop. The polling caller in :func:`run_null_orchestrator` does this.
    """
    if not path.exists():
        return offset, []
    envelopes: list[EventEnvelope] = []
    new_offset = offset
    with open(path, "rb") as f:
        f.seek(offset)
        last_complete_end = offset
        while True:
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # Trailing partial line — leave for the next poll.
                break
            envelopes.append(from_canonical_json(raw.rstrip(b"\r\n")))
            last_complete_end += len(raw)
        new_offset = last_complete_end
    return new_offset, envelopes


async def _emit_lifecycle_for_task(
    *,
    writer: EventLogWriter,
    task_created_env: EventEnvelope,
    clock: Clock,
) -> None:
    """Emit the canonical 4-event lifecycle for one ``task.created`` envelope.

    The events chain causally: every emitted envelope's ``parent_event_id``
    points at the originating ``task.created`` event's ``event_id`` (per
    AC-6). ``request_id`` is freshly generated per call so each emit has
    its own correlation id.

    Args:
        writer: ``EventLogWriter`` to ``await writer.append(env)`` against.
        task_created_env: The originating ``task.created`` envelope.
        clock: Injected clock supplying ``now()`` and ``monotonic_ns()``.

    The four emitted events (in order):

    1. ``task.planning.started(task_id)``
    2. ``task.plan.ready(task_id, plan_summary="null-orchestrator pass-through")``
    3. ``task.execution.started(task_id, session_id=<new>)``
    4. ``task.completed(task_id, summary="null-orchestrator completion", pr_url=None)``
    """
    payload = task_created_env.payload
    if isinstance(payload, dict):
        task_id_obj = payload.get("task_id")
    else:
        task_id_obj = getattr(payload, "task_id", None)
    if not isinstance(task_id_obj, str):
        log.warning(
            "task.created event %s has non-string task_id payload; skipping",
            task_created_env.event_id,
        )
        return
    task_id: str = task_id_obj
    parent_event_id = task_created_env.event_id

    # ---- 1. task.planning.started ------------------------------------
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.0.0",
        type="task.planning.started",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanningStartedPayload(task_id=task_id),
        parent_event_id=parent_event_id,
        request_id=new_request_id(clock=clock),
        trace_id=task_created_env.trace_id,
    )
    await writer.append(env)
    log.info("emitted task.planning.started for %s (event_id=%s)", task_id, env.event_id)

    # ---- 2. task.plan.ready ------------------------------------------
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.0.0",
        type="task.plan.ready",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskPlanReadyPayload(
            task_id=task_id,
            plan_summary="null-orchestrator pass-through",
        ),
        parent_event_id=parent_event_id,
        request_id=new_request_id(clock=clock),
        trace_id=task_created_env.trace_id,
    )
    await writer.append(env)
    log.info("emitted task.plan.ready for %s (event_id=%s)", task_id, env.event_id)

    # ---- 3. task.execution.started -----------------------------------
    session_id = new_session_id(clock=clock)
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.0.0",
        type="task.execution.started",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskExecutionStartedPayload(task_id=task_id, session_id=session_id),
        parent_event_id=parent_event_id,
        request_id=new_request_id(clock=clock),
        trace_id=task_created_env.trace_id,
    )
    await writer.append(env)
    log.info(
        "emitted task.execution.started for %s session=%s (event_id=%s)",
        task_id,
        session_id,
        env.event_id,
    )

    # ---- 4. task.completed -------------------------------------------
    env = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(
            task_id=task_id,
            summary="null-orchestrator completion",
            pr_url=None,
        ),
        parent_event_id=parent_event_id,
        request_id=new_request_id(clock=clock),
        trace_id=task_created_env.trace_id,
    )
    await writer.append(env)
    log.info("emitted task.completed for %s (event_id=%s)", task_id, env.event_id)


async def run_null_orchestrator(
    *,
    base_dir: Path,
    clock: Clock,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Main async loop: tail JSONL → emit lifecycle on each new ``task.created``.

    Args:
        base_dir: Root directory containing ``YYYY-MM-DD.jsonl`` files.
        clock: Injected clock for emitted envelope timestamps.
        poll_interval_s: Sleep between tail-loop iterations (default 100ms).
        stop_event: Optional ``asyncio.Event`` for clean shutdown. If
            ``None``, a local event is created (useful in tests).
    """
    stop = stop_event if stop_event is not None else asyncio.Event()

    # AC-7: build the dedupe set BEFORE the writer starts emitting. Any
    # task that already has a ``task.planning.started`` event in the log
    # is treated as already-processed and silently skipped. This handles
    # the mid-task-restart case described in the story: the orchestrator
    # restarts after emitting some lifecycle events for task X — on the
    # restart, X is in the seen set and the lifecycle is NOT re-emitted.
    processed: set[str] = _scan_processed_task_ids(base_dir)
    log.info("startup: %d task_id(s) already processed (will be skipped)", len(processed))

    writer = EventLogWriter(base_dir=base_dir, clock=clock)
    # Trim any trailing partial line left by a prior crash before we
    # start appending. Same contract registry-state's subscriber follows.
    await writer.recover()

    # Per-file byte offsets, mirroring the registry-state subscriber
    # pattern. Empty dict means every file starts at offset 0 — but the
    # AC-7 dedupe set ensures the startup-replay phase does NOT re-emit
    # lifecycle events for tasks already seen.
    #
    # Mn9 (deferred): the offsets dict accumulates entries for every
    # ``YYYY-MM-DD.jsonl`` file the orchestrator has ever observed. At
    # one entry per UTC day this leaks at ~365 entries/year — negligible
    # for a test fixture. A long-running production orchestrator would
    # want a TTL/LRU bound; defer to Story 5.10's real implementation.
    offsets: dict[str, int] = {}
    first_iteration_done = False

    try:
        while not stop.is_set():
            envelopes_seen: list[EventEnvelope] = []
            # Mn10 hardening: rglob (not glob) so future operators who
            # nest event logs by date subdirectory still get tailed
            # correctly. Polling cost note: this re-reads only the bytes
            # appended since the last offset (O(delta)), not the entire
            # file — the per-poll cost stays bounded as the log grows.
            for path in sorted(base_dir.rglob("*.jsonl")):
                # Use the path RELATIVE to base_dir as the offsets key
                # (was: ``path.name``) so identically-named files in
                # nested subdirectories don't collide on the same key.
                offset_key = str(path.relative_to(base_dir))
                prior = offsets.get(offset_key, 0)
                new_offset, envelopes = await asyncio.to_thread(
                    _read_new_envelopes_since, path, prior
                )
                offsets[offset_key] = new_offset
                envelopes_seen.extend(envelopes)

            for env in envelopes_seen:
                if env.type != "task.created":
                    continue
                payload = env.payload
                if isinstance(payload, dict):
                    task_id_obj = payload.get("task_id")
                else:
                    task_id_obj = getattr(payload, "task_id", None)
                if not isinstance(task_id_obj, str):
                    log.warning(
                        "task.created event %s has non-string task_id; skipping",
                        env.event_id,
                    )
                    continue
                task_id: str = task_id_obj
                if task_id in processed:
                    # AC-7: already processed earlier in this run, OR a
                    # follow-up lifecycle event for this task is already
                    # in the log (the startup scan picks up any of the 4
                    # lifecycle types — see _scan_processed_task_ids).
                    continue
                # Story 2.15 code-review fix M5: mark AFTER successful
                # emit. The prior implementation marked BEFORE emit which
                # left a crash-window between mark and first append where
                # the next run would treat the task as fresh and re-emit.
                # Now: a crash mid-emit means the next run's startup scan
                # sees the partial lifecycle (any of the 4 types) and
                # adds the task_id to the dedupe set, suppressing re-emit.
                # A crash BEFORE the first append leaves the task ONLY
                # with task.created in the log; the next run treats it
                # as fresh and emits the full lifecycle — the desired
                # exactly-once-from-the-orchestrator semantics.
                await _emit_lifecycle_for_task(writer=writer, task_created_env=env, clock=clock)
                processed.add(task_id)

            # AC-2 step 4: touch /tmp/ready after the first poll iteration
            # so the docker-compose healthcheck flips to ``healthy``.
            if not first_iteration_done:
                first_iteration_done = True
                try:
                    _READY_FILE.touch()
                except OSError as exc:
                    log.warning("failed to touch %s healthcheck signal: %s", _READY_FILE, exc)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
    finally:
        # Mirror registry-state's behaviour: clear /tmp/ready on shutdown
        # so any subsequent ``compose start`` re-establishes readiness via
        # a fresh first-poll touch (rather than reading a stale-from-prior-run
        # signal in the same writable layer).
        try:
            _READY_FILE.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("failed to delete %s on shutdown: %s", _READY_FILE, exc)
        await writer.close()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Register SIGTERM/SIGINT handlers that set *stop_event*."""
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue


async def _async_main() -> None:
    """Async entrypoint — wired by :func:`main`."""
    base_dir = Path(os.environ.get("EVENT_LOG_DIR", _DEFAULT_LOG_DIR))
    poll_interval_s = float(
        os.environ.get("NULL_ORCHESTRATOR_POLL_INTERVAL_S", str(_DEFAULT_POLL_INTERVAL_S))
    )
    log.info(
        "null-orchestrator starting — log_dir=%s poll_interval_s=%s",
        base_dir,
        poll_interval_s,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, stop_event)

    base_dir.mkdir(parents=True, exist_ok=True)
    await run_null_orchestrator(
        base_dir=base_dir,
        clock=SystemClock(),
        poll_interval_s=poll_interval_s,
        stop_event=stop_event,
    )


def main() -> None:
    """Sync entrypoint for ``python -m null_orchestrator``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_async_main())


if __name__ == "__main__":
    main()
