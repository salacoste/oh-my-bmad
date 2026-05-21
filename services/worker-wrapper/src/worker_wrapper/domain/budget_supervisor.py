"""Budget supervisor — JSONL tail subscriber for ``task.budget_exceeded`` events.

Story 12.1 (FR66 + NFR-R8 + FR26) — the **enforcement leg** of Epic 12's
per-task budget loop. Subscribes to the JSONL event log via the established
:func:`events.log_reader.read_log_lines` + :func:`events.log_reader.current_day_path`
polling pattern (already in use by :mod:`worker_wrapper.adapters.approval_waiter`).
On the FIRST matching ``task.budget_exceeded`` envelope for the active
``task_id``: invokes a caller-supplied :paramref:`terminate_callback` that
wraps :meth:`ClaudeCodeRunner.terminate_with_grace` (SIGTERM → wait ≤5s →
SIGKILL escalation).

NFR-R8 budget: event-emit to subprocess-exit < 5s p99. The supervisor owns the
detect-and-dispatch leg; the runner adapter owns the SIGTERM/SIGKILL leg.
Separately-measured ``detection_latency_s`` and ``termination_latency_s``
let the operator tune each leg independently (Decision D5).

FR26 single-writer: the supervisor READS JSONL and NEVER writes events.
Subprocess SIGTERM/SIGKILL is process control, not state mutation; the audit
event ``task.budget_enforcement_triggered`` is emitted by Story 12.2 AFTER
this supervisor's termination dispatch.

Architectural placement: ``domain/`` because the supervisor is pure
orchestration (no I/O boundaries of its own — it composes injected
``event_log_dir`` reads + injected ``terminate_callback``). Subprocess
control lives in ``adapters/`` per Decision D2.

Cross-day rotation (PP7 — known limitation): the supervisor scans the
*current* day's JSONL file each poll. An envelope written at 23:59:59 may be
missed if the supervisor's first poll lands at 00:00:00.05 (UTC) — the
scan then targets the new day's file. This matches the
:class:`worker_wrapper.adapters.approval_waiter.ApprovalWaiter` Phase-1
precedent. Production task duration assumption is <12 hours; rare cross-day
breaches are an audit-but-not-block scenario. Story 12.1.1 follow-up tracks
proper cross-day handling.

Lesson reuse:

- Story 11.4 PP14 — streaming JSONL reader with blank-line + decode-error
  tolerance. :func:`events.log_reader.read_log_lines` already implements
  the trailing-partial-line skip; we layer payload-shape tolerance on top.
- Story 9.7 pass-2 TH-E1 — pre-1.1.0 back-fill is built into
  ``read_log_lines``; supervisor inherits it for free.
- D5 fail-loud isolation (Epic 11 retro L1) — supervisor does NOT halt the
  task on its own errors. Corrupted file / unreadable directory is logged
  and retried on the next poll; the supervisor's role is shadow-monitor.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import structlog
from events import EventEnvelope, current_day_path, read_log_lines
from events.clock import Clock

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BudgetSupervisorResult:
    """Outcome of a :func:`watch_for_budget_exceeded` invocation (AC1).

    Fields:

    - ``triggered``: ``True`` if a matching ``task.budget_exceeded`` envelope
      was observed and the termination callback fired; ``False`` if the
      supervisor exited cleanly via ``cancel_event``.
    - ``event_id``: ``event_id`` of the matching envelope; ``None`` on
      cancel-clean path.
    - ``tokens_used`` / ``token_limit``: copied from the matching
      :class:`events.payloads.TaskBudgetExceededPayload`; ``None`` on
      cancel-clean path.
    - ``step`` (PP16): step counter from the matching payload (Story 12.2
      will use it for the ``task.budget_enforcement_triggered`` audit event).
    - ``detection_latency_s``: clock-based seconds from supervisor start to
      first matching envelope detection (bounded by ``poll_interval_s +
      fdatasync delay``). ``None`` on cancel-clean path.
    - ``termination_latency_s``: clock-based seconds for the
      ``terminate_callback`` to return (covers the SIGTERM→SIGKILL window).
      ``None`` on cancel-clean path.
    - ``termination_method`` (PP21): which signal-path the termination took
      (``"noop"`` / ``"sigterm"`` / ``"sigkill"``) — propagated from the
      callback's :class:`worker_wrapper.adapters.claude_code_runner.TerminationResult`
      when the callback returns one. ``None`` if the callback is opaque or
      the cancel-clean path was taken.

    All latency fields use the injected ``Clock`` so tests can drive them
    deterministically via :class:`events.clock.TickingClock` rather than
    wall-clock waits.

    PP18 — public dataclass; the leading-underscore name
    (``_BudgetSupervisorResult``) was a Story 12.1 pass-1 review finding
    (underscore-public-export contradiction). Renamed; the underscore alias
    below preserves the original import path for the lifespan integration
    and integration test.
    """

    triggered: bool
    event_id: str | None = None
    tokens_used: int | None = None
    token_limit: int | None = None
    step: int | None = None
    detection_latency_s: float | None = None
    termination_latency_s: float | None = None
    termination_method: Literal["noop", "sigterm", "sigkill"] | None = None


# PP18 — backwards-compat alias; callers (lifespan, integration test) keep
# importing the underscore-prefixed name.
_BudgetSupervisorResult = BudgetSupervisorResult


_BUDGET_EXCEEDED_TYPE: str = "task.budget_exceeded"
_NS_PER_S: float = 1e9


async def watch_for_budget_exceeded(
    *,
    task_id: str,
    event_log_dir: Path,
    terminate_callback: Callable[[], Awaitable[Any]],
    clock: Clock,
    cancel_event: asyncio.Event,
    poll_interval_s: float = 0.5,
) -> BudgetSupervisorResult:
    """Tail the JSONL event log for ``task.budget_exceeded`` events matching ``task_id``.

    On the FIRST matching envelope:

    1. Capture ``detection_latency_s`` (start → match) via the injected ``clock``.
    2. ``await terminate_callback()`` — caller wraps the SIGTERM → wait ≤5s
       → SIGKILL escalation (typically :meth:`ClaudeCodeRunner.terminate_with_grace`).
    3. Capture ``termination_latency_s`` (callback start → callback return)
       and return :class:`BudgetSupervisorResult` with ``triggered=True``.

    If ``cancel_event`` is set before any matching envelope arrives, return
    ``BudgetSupervisorResult(triggered=False)`` cleanly — this is the normal
    task-completion path (runner exited, lifespan signals the shadow to exit).

    Args:
        task_id: The task whose budget event we're watching for. Match is
            case-sensitive exact equality against
            ``payload["task_id"]`` on each envelope.
        event_log_dir: Base directory containing daily JSONL event log files.
            Same convention as :class:`worker_wrapper.adapters.approval_waiter.ApprovalWaiter`.
        terminate_callback: Coroutine factory invoked on first match. The
            callback's return value is captured and, if it is a
            :class:`worker_wrapper.adapters.claude_code_runner.TerminationResult`
            (PP21), its ``method`` field is propagated to the supervisor
            result so operators can read SIGTERM-vs-SIGKILL from one log line.
            The callback raising is logged + isolated (PP9) so a transient
            adapter error does not clobber the runner's exception path.
        clock: Injected :class:`events.clock.Clock`. Use
            :class:`events.clock.SystemClock` in production; ``TickingClock``
            in unit tests.
        cancel_event: :class:`asyncio.Event` that, when set, causes the
            supervisor to return cleanly without firing the callback. Used by
            lifespan integration to retire the shadow task after the runner
            completes naturally.
        poll_interval_s: Seconds between JSONL polls. Default 0.5s — 10
            retries inside the 5s NFR-R8 budget leaves ≥4.5s for the
            termination leg (Decision D1).

    Returns:
        :class:`BudgetSupervisorResult` describing the outcome.

    Notes:
        - Pure async — JSONL reads are off-loaded to a worker thread via
          :func:`asyncio.to_thread` (PP2) so a multi-MB log on a busy disk
          can't starve the runner's stdout reader on the event loop.
        - JSONL parse errors and missing-file conditions are logged + retried
          on the next poll; the supervisor never crashes the task on its own
          errors (Epic 11 retro L1 fail-loud isolation).
        - Cross-day rotation: scans today's path only — see the module
          docstring (PP7) for the rationale and follow-up tracking.
    """
    log = structlog.get_logger(__name__)
    log.info(
        "budget_supervisor_started",
        task_id=task_id,
        poll_interval_s=poll_interval_s,
        event_log_dir=str(event_log_dir),
    )
    start_ns = clock.monotonic_ns()
    scan_offset = 0
    last_path: Path | None = None

    while True:
        if cancel_event.is_set():
            log.info("budget_supervisor_cancelled", task_id=task_id)
            return BudgetSupervisorResult(triggered=False)

        path = current_day_path(event_log_dir, clock.now())
        if path != last_path:
            scan_offset = 0
            last_path = path

        # PP2 — JSONL scan runs in a worker thread so the event loop is not
        # blocked by file I/O on large logs.
        # PP3 — single-pass scan returns (match, envelopes_scanned). The
        # caller advances ``scan_offset`` regardless of match outcome,
        # eliminating the prior O(2N) double-iteration and the fdatasync
        # dual-counting race (PP10 subsumed).
        match, envelopes_scanned = await asyncio.to_thread(
            _scan_for_match,
            path=path,
            task_id=task_id,
            scan_offset=scan_offset,
            log=log,
        )
        scan_offset = envelopes_scanned
        if match is not None:
            detection_ns = clock.monotonic_ns() - start_ns
            detection_latency_s = detection_ns / _NS_PER_S
            log.info(
                "budget_supervisor_match",
                task_id=task_id,
                event_id=match.event_id,
                tokens_used=match.tokens_used,
                token_limit=match.token_limit,
                step=match.step,
                detection_latency_s=detection_latency_s,
            )
            term_start_ns = clock.monotonic_ns()
            termination_method: Literal["noop", "sigterm", "sigkill"] | None = None
            try:
                callback_value = await terminate_callback()
            except Exception as exc:  # noqa: BLE001 — PP9 isolation
                # PP9 — supervisor must not clobber the runner's exception
                # path. Log loudly + emit a structured warning; the lifespan
                # still sees triggered=True so the FSM transitions and the
                # task isn't left in IN_PROGRESS.
                log.error(
                    "budget_supervisor_callback_raised",
                    task_id=task_id,
                    event_id=match.event_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                callback_value = None
            else:
                # PP21 — propagate the runner's TerminationResult.method
                # without importing the adapter here (avoid domain→adapter
                # dependency). Duck-type on ``.method``.
                method_attr = getattr(callback_value, "method", None)
                if method_attr in {"noop", "sigterm", "sigkill"}:
                    termination_method = method_attr
            term_ns = clock.monotonic_ns() - term_start_ns
            termination_latency_s = term_ns / _NS_PER_S
            log.info(
                "budget_supervisor_terminated",
                task_id=task_id,
                event_id=match.event_id,
                termination_latency_s=termination_latency_s,
                termination_method=termination_method,
            )
            return BudgetSupervisorResult(
                triggered=True,
                event_id=match.event_id,
                tokens_used=match.tokens_used,
                token_limit=match.token_limit,
                step=match.step,
                detection_latency_s=detection_latency_s,
                termination_latency_s=termination_latency_s,
                termination_method=termination_method,
            )

        # Sleep with cancel-responsiveness: race the sleep against the cancel
        # event so we don't burn up to poll_interval_s after a cancel fires.
        # PP11 — when cancel fires during the sleep, exit IMMEDIATELY.
        # Looping back would do one more scan that could spuriously match a
        # late-arriving event and transition a successfully-completed task
        # to TASK_FAILED.
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=poll_interval_s)
        except TimeoutError:
            continue
        log.info("budget_supervisor_cancelled_during_sleep", task_id=task_id)
        return BudgetSupervisorResult(triggered=False)


@dataclass(frozen=True)
class _Match:
    """Internal — fields lifted from a matching envelope."""

    event_id: str
    tokens_used: int
    token_limit: int
    step: int | None


def _scan_for_match(
    *,
    path: Path,
    task_id: str,
    scan_offset: int,
    log: structlog.stdlib.BoundLogger,
) -> tuple[_Match | None, int]:
    """Scan JSONL past ``scan_offset`` for the first matching budget event.

    Returns ``(match | None, envelopes_scanned)`` so the caller can advance
    its offset cursor regardless of match outcome — PP3 single-pass refactor
    eliminating the prior O(2N) double-iteration plus the fdatasync race
    (PP10 subsumed). When a match is found, ``envelopes_scanned`` is the
    cumulative index INCLUDING the matched line so subsequent polls skip
    past it (matches :class:`ApprovalWaiter._scan_today` semantics).

    Tolerates :class:`FileNotFoundError` (log file not yet created — the
    producer may not have emitted any event for today, returns
    ``(None, scan_offset)`` so the cursor doesn't regress), JSON decode
    errors (corrupted line — Story 11.4 PP14 reader already skips trailing
    partial lines), and missing/malformed payload fields (logged + skipped).

    PP22 — wraps the read iterator in :func:`contextlib.closing` so the
    underlying file handle is released eagerly on early return or
    exception, not via GC.
    """
    try:
        envelopes_iter = read_log_lines(path)
    except FileNotFoundError:
        return None, scan_offset

    # PP22 — :func:`events.log_reader.read_log_lines` returns a generator (see
    # ``_read_log_lines_gen``), but its declared return type is the abstract
    # ``Iterator[EventEnvelope]`` which lacks ``.close()``. Cast to a concrete
    # ``Generator`` so :func:`contextlib.closing` accepts the runtime object
    # and releases the underlying file handle eagerly on early return or
    # exception (rather than relying on GC).
    closeable_iter = cast(
        "Generator[EventEnvelope, None, None]",
        envelopes_iter,
    )
    idx = 0
    try:
        with contextlib.closing(closeable_iter):
            for envelope in closeable_iter:
                idx += 1
                if idx <= scan_offset:
                    continue
                if getattr(envelope, "type", "") != _BUDGET_EXCEEDED_TYPE:
                    continue
                payload = _safe_payload(envelope)
                if payload is None:
                    continue
                if payload.get("task_id") != task_id:
                    continue
                tokens_used = payload.get("tokens_used")
                token_limit = payload.get("token_limit")
                if not isinstance(tokens_used, int) or not isinstance(token_limit, int):
                    log.warning(
                        "budget_supervisor_malformed_payload",
                        task_id=task_id,
                        event_id=getattr(envelope, "event_id", ""),
                        tokens_used_type=type(tokens_used).__name__,
                        token_limit_type=type(token_limit).__name__,
                    )
                    continue
                step_value = payload.get("step")
                step = step_value if isinstance(step_value, int) else None
                return (
                    _Match(
                        event_id=getattr(envelope, "event_id", ""),
                        tokens_used=tokens_used,
                        token_limit=token_limit,
                        step=step,
                    ),
                    idx,
                )
    except (OSError, ValueError) as exc:
        # Decision D5 / Epic 11 retro L1 — log and continue; the supervisor
        # must not regress task reliability for its own I/O bugs. Pydantic
        # ValidationError inherits from ValueError, so corrupted records on
        # disk are absorbed here and we'll retry on the next poll. PP3 — on
        # error we conservatively return the PRIOR ``scan_offset`` so the
        # next poll re-scans the same region rather than skipping past it.
        log.warning(
            "budget_supervisor_scan_error",
            task_id=task_id,
            path=str(path),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None, scan_offset
    return None, idx


def _safe_payload(envelope: Any) -> dict[str, Any] | None:
    """Extract payload as a plain ``dict``.

    PP17 — empirical investigation confirmed that
    :func:`events.log_reader.read_log_lines` produces envelopes via
    :func:`events.canonical.from_canonical_json` →
    :meth:`pydantic.BaseModel.model_validate_json`, which deserializes the
    JSON payload object into a ``dict`` (then frozen by
    :class:`events.envelope.EventEnvelope`'s ``_freeze_payload_dict``
    validator). The supervisor's earlier ``BaseModel.model_dump`` branch
    was dead code. Sibling :func:`worker_wrapper.adapters.approval_waiter._safe_payload`
    is correct on this point; the supervisor now mirrors it precisely.
    """
    payload = getattr(envelope, "payload", None)
    if isinstance(payload, dict):
        return payload
    return None


__all__ = [
    # PP18 — public name; underscore-prefixed alias kept for in-tree callers.
    "BudgetSupervisorResult",
    "_BudgetSupervisorResult",
    "watch_for_budget_exceeded",
]
