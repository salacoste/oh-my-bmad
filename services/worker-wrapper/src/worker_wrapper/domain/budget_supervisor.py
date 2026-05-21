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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from events import current_day_path, read_log_lines
from events.clock import Clock

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _BudgetSupervisorResult:
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
    - ``detection_latency_s``: clock-based seconds from supervisor start to
      first matching envelope detection (bounded by ``poll_interval_s +
      fdatasync delay``). ``None`` on cancel-clean path.
    - ``termination_latency_s``: clock-based seconds for the
      ``terminate_callback`` to return (covers the SIGTERM→SIGKILL window).
      ``None`` on cancel-clean path.

    All latency fields use the injected ``Clock`` so tests can drive them
    deterministically via :class:`events.clock.TickingClock` rather than
    wall-clock waits.
    """

    triggered: bool
    event_id: str | None = None
    tokens_used: int | None = None
    token_limit: int | None = None
    detection_latency_s: float | None = None
    termination_latency_s: float | None = None


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
) -> _BudgetSupervisorResult:
    """Tail the JSONL event log for ``task.budget_exceeded`` events matching ``task_id``.

    On the FIRST matching envelope:

    1. Capture ``detection_latency_s`` (start → match) via the injected ``clock``.
    2. ``await terminate_callback()`` — caller wraps the SIGTERM → wait ≤5s
       → SIGKILL escalation (typically :meth:`ClaudeCodeRunner.terminate_with_grace`).
    3. Capture ``termination_latency_s`` (callback start → callback return)
       and return :class:`_BudgetSupervisorResult` with ``triggered=True``.

    If ``cancel_event`` is set before any matching envelope arrives, return
    ``_BudgetSupervisorResult(triggered=False)`` cleanly — this is the normal
    task-completion path (runner exited, lifespan signals the shadow to exit).

    Args:
        task_id: The task whose budget event we're watching for. Match is
            case-sensitive exact equality against
            ``payload["task_id"]`` on each envelope.
        event_log_dir: Base directory containing daily JSONL event log files.
            Same convention as :class:`worker_wrapper.adapters.approval_waiter.ApprovalWaiter`.
        terminate_callback: Coroutine factory invoked on first match. The
            callback's return value is ignored — supervisor only cares about
            the call completing (or raising).
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
        ``_BudgetSupervisorResult`` describing the outcome.

    Notes:
        - Pure async — no thread spawn, no blocking I/O on the event loop.
        - JSONL parse errors and missing-file conditions are logged + retried
          on the next poll; the supervisor never crashes the task on its own
          errors (Epic 11 retro L1 fail-loud isolation).
        - Cross-day rotation: re-resolves ``current_day_path`` each poll
          (matches :class:`ApprovalWaiter` precedent).
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
            return _BudgetSupervisorResult(triggered=False)

        path = current_day_path(event_log_dir, clock.now())
        if path != last_path:
            scan_offset = 0
            last_path = path

        match = _scan_for_match(
            path=path,
            task_id=task_id,
            scan_offset=scan_offset,
            log=log,
        )
        if match is None:
            # No match yet — advance offset to skip already-scanned envelopes
            # on the next poll, then sleep.
            scan_offset = _last_scanned_idx(path, scan_offset, log)
        else:
            detection_ns = clock.monotonic_ns() - start_ns
            detection_latency_s = detection_ns / _NS_PER_S
            log.info(
                "budget_supervisor_match",
                task_id=task_id,
                event_id=match.event_id,
                tokens_used=match.tokens_used,
                token_limit=match.token_limit,
                detection_latency_s=detection_latency_s,
            )
            term_start_ns = clock.monotonic_ns()
            try:
                await terminate_callback()
            finally:
                term_ns = clock.monotonic_ns() - term_start_ns
                termination_latency_s = term_ns / _NS_PER_S
                log.info(
                    "budget_supervisor_terminated",
                    task_id=task_id,
                    event_id=match.event_id,
                    termination_latency_s=termination_latency_s,
                )
            return _BudgetSupervisorResult(
                triggered=True,
                event_id=match.event_id,
                tokens_used=match.tokens_used,
                token_limit=match.token_limit,
                detection_latency_s=detection_latency_s,
                termination_latency_s=termination_latency_s,
            )

        # Sleep with cancel-responsiveness: race the sleep against the cancel
        # event so we don't burn up to poll_interval_s after a cancel fires.
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=poll_interval_s)
        except TimeoutError:
            continue
        else:
            # Cancel fired during the sleep — handle on next loop iteration.
            continue


@dataclass(frozen=True)
class _Match:
    """Internal — fields lifted from a matching envelope."""

    event_id: str
    tokens_used: int
    token_limit: int


def _scan_for_match(
    *,
    path: Path,
    task_id: str,
    scan_offset: int,
    log: structlog.stdlib.BoundLogger,
) -> _Match | None:
    """Scan JSONL past ``scan_offset`` for the first matching budget event.

    Tolerates :class:`FileNotFoundError` (log file not yet created — the
    producer may not have emitted any event for today), JSON decode errors
    (corrupted line — Story 11.4 PP14 reader already skips trailing partial
    lines), and missing/malformed payload fields (logged + skipped).

    Returns the first match or ``None``.
    """
    try:
        envelopes_iter = read_log_lines(path)
    except FileNotFoundError:
        return None

    idx = 0
    try:
        for envelope in envelopes_iter:
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
            return _Match(
                event_id=getattr(envelope, "event_id", ""),
                tokens_used=tokens_used,
                token_limit=token_limit,
            )
    except (OSError, ValueError) as exc:
        # Decision D5 / Epic 11 retro L1 — log and continue; the supervisor
        # must not regress task reliability for its own I/O bugs. Pydantic
        # ValidationError inherits from ValueError, so corrupted records on
        # disk are absorbed here and we'll retry on the next poll.
        log.warning(
            "budget_supervisor_scan_error",
            task_id=task_id,
            path=str(path),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None
    return None


def _last_scanned_idx(path: Path, prior_offset: int, log: structlog.stdlib.BoundLogger) -> int:
    """Count total envelopes currently in ``path`` to advance the scan offset.

    Returns the prior offset on any I/O error so the next poll re-scans from
    the same point (no data loss).
    """
    try:
        envelopes_iter = read_log_lines(path)
    except FileNotFoundError:
        return prior_offset
    try:
        return sum(1 for _ in envelopes_iter)
    except (OSError, ValueError) as exc:
        log.warning(
            "budget_supervisor_offset_count_error",
            path=str(path),
            error=str(exc),
        )
        return prior_offset


def _safe_payload(envelope: Any) -> dict[str, Any] | None:
    """Extract payload as a plain ``dict``, tolerating BaseModel + missing fields.

    Mirrors :func:`worker_wrapper.adapters.approval_waiter._safe_payload` but
    additionally unwraps Pydantic ``BaseModel`` payloads via ``model_dump`` —
    :func:`events.log_reader.read_log_lines` returns validated envelopes whose
    payload is a typed model, not a raw dict, after Story 9.7's payload
    backfill landed.
    """
    payload = getattr(envelope, "payload", None)
    if isinstance(payload, dict):
        return payload
    # Pydantic BaseModel — round-trip via model_dump so downstream callers
    # see the same dict shape as the raw JSONL.
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:  # noqa: BLE001 — defensive; never crash the supervisor
            return None
        if isinstance(dumped, dict):
            return dumped
    return None


__all__ = [
    "_BudgetSupervisorResult",
    "watch_for_budget_exceeded",
]
