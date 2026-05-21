"""Integration test for NFR-R8: budget-enforcement subprocess-exit latency (Story 12.1 AC5).

End-to-end exercise of the per-task budget enforcement loop's enforcement
leg:

1. Spawn a real Python subprocess that ONLY responds to SIGKILL after
   ignoring SIGTERM is NOT used here — we use the default SIGTERM handler
   so the test exercises the happy path (SIGTERM → exit ≤5s).
2. Stand up a tmp ``EventLogWriter`` writing canonical JSONL.
3. Spawn the :func:`watch_for_budget_exceeded` supervisor as a shadow task.
4. Capture ``t0`` immediately BEFORE writing the ``task.budget_exceeded``
   envelope. Capture ``t1`` AFTER ``await`` on the supervisor task.
5. Assert ``(t1 - t0) < 5.0`` for NFR-R8 p99 ceiling.
6. Repeat 5× with different random ``task_id``s; assert ALL 5 runs pass.

Per Epic 11 retro L6 (test-fixture realism): uses real
:class:`EventEnvelope.create` + :class:`registry_state.adapters.event_log.EventLogWriter`
``.append`` to match the production wire format end-to-end. NO hand-rolled
``01HZX...`` shape envelopes.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from random import Random

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    SystemClock,
    new_event_id,
    new_task_id,
    new_uuid7,
)
from events.payloads import TaskBudgetExceededPayload
from events.schema_registry import register
from registry_state.adapters.event_log import EventLogWriter
from worker_wrapper.adapters.claude_code_runner import (
    ClaudeCodeRunner,
    _TerminationResult,
)
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.budget_supervisor import (
    _BudgetSupervisorResult,
    watch_for_budget_exceeded,
)

_ACTOR = Actor(kind="system", id="integration-test")
_DEFAULT_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None, None, None]:
    """Ensure ``task.budget_exceeded`` payload model is registered for envelope.create."""
    register("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    register("task.budget_exceeded", "1.1.0", TaskBudgetExceededPayload)
    yield


@dataclass
class _RunMeasurement:
    """One iteration's measured outcome."""

    task_id: str
    wall_clock_elapsed_s: float
    detection_latency_s: float
    termination_latency_s: float
    subprocess_returncode: int


def _settings() -> WorkerSettings:
    """Minimal WorkerSettings — the integration test only needs basic config."""
    return WorkerSettings(
        claude_command="claude",
        claude_output_format="stream-json",
        anthropic_api_key="dummy-key",
    )


def _make_budget_envelope(
    *,
    task_id: str,
    tokens_used: int,
    token_limit: int,
    mono_seed: int,
) -> EventEnvelope:
    """Build a real ``task.budget_exceeded`` envelope via the production factory."""
    rng = Random(mono_seed)
    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.1.0",
        type="task.budget_exceeded",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBudgetExceededPayload(
            task_id=task_id,
            tokens_used=tokens_used,
            token_limit=token_limit,
            step=5,
        ),
        trace_id=_DEFAULT_TRACE_ID,
        request_id=str(new_uuid7(clock=clk, rng=rng)),
    )


async def _run_one_iteration(
    *,
    event_log_dir: Path,
    iteration: int,
) -> _RunMeasurement:
    """Run one budget-enforcement E2E exercise.

    1. Spawn a long-sleep Python subprocess as the stand-in for ``claude``.
    2. Attach to a fresh ``ClaudeCodeRunner`` (we only call
       ``.terminate_with_grace`` on it — no ``.run()``).
    3. Spawn the budget supervisor as a shadow task.
    4. Write the ``task.budget_exceeded`` envelope via ``EventLogWriter.append``.
    5. Await the supervisor; assert it fired.
    6. Return the measured latencies.
    """
    runner = ClaudeCodeRunner(_settings())
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(300)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    runner._process = proc

    async def _terminate() -> _TerminationResult:
        return await runner.terminate_with_grace(grace_period_s=5.0)

    cancel = asyncio.Event()
    task_id = new_task_id(clock=SystemClock(), rng=Random(iteration))
    writer = EventLogWriter(base_dir=event_log_dir, clock=SystemClock())
    try:
        supervisor_task = asyncio.create_task(
            watch_for_budget_exceeded(
                task_id=task_id,
                event_log_dir=event_log_dir,
                terminate_callback=_terminate,
                clock=SystemClock(),
                cancel_event=cancel,
                poll_interval_s=0.1,
            ),
            name=f"budget-supervisor-{task_id}-iter{iteration}",
        )

        envelope = _make_budget_envelope(
            task_id=task_id,
            tokens_used=2000 + iteration * 100,
            token_limit=1000,
            mono_seed=iteration + 1,
        )

        # Give the supervisor a moment to start polling so we measure
        # detection latency (not "supervisor not yet ready" latency).
        await asyncio.sleep(0.05)

        # CAPTURE t0 IMMEDIATELY before writing the envelope.
        t0 = time.monotonic()
        await writer.append(envelope)

        # Await the supervisor — it will fire the callback when the new
        # envelope is observed. Bound to 10s as a paranoia ceiling; the
        # actual NFR-R8 assertion is 5s below.
        result: _BudgetSupervisorResult = await asyncio.wait_for(
            supervisor_task,
            timeout=10.0,
        )
        t1 = time.monotonic()
    finally:
        await writer.close()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    wall_elapsed = t1 - t0
    assert result.triggered is True, f"iter {iteration}: supervisor did not fire"
    assert proc.returncode is not None, f"iter {iteration}: subprocess did not exit"
    assert result.detection_latency_s is not None
    assert result.termination_latency_s is not None

    return _RunMeasurement(
        task_id=task_id,
        wall_clock_elapsed_s=wall_elapsed,
        detection_latency_s=result.detection_latency_s,
        termination_latency_s=result.termination_latency_s,
        subprocess_returncode=proc.returncode,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_enforced_subprocess_exits_within_5s_e2e(tmp_path: Path) -> None:
    """NFR-R8: 5× repetitions — every run completes within 5s wall-clock.

    Per Story 12.1 AC5: this is the latency p99 enforcement test. The five
    independent runs each spawn a fresh subprocess + supervisor + writer +
    envelope; the wall-clock delta from envelope-append → supervisor-return
    must be under 5s every time.
    """
    iterations = 5
    measurements: list[_RunMeasurement] = []
    for i in range(iterations):
        # Each iteration uses its own subdirectory so the writer's daily-
        # rotation cache + scan-offset cursor are fresh.
        per_iter_dir = tmp_path / f"iter-{i}"
        per_iter_dir.mkdir(parents=True, exist_ok=True)
        m = await _run_one_iteration(event_log_dir=per_iter_dir, iteration=i)
        measurements.append(m)

    # AC5 — every iteration MUST satisfy the 5s ceiling.
    for m in measurements:
        assert m.wall_clock_elapsed_s < 5.0, (
            f"NFR-R8 violated: task_id={m.task_id} took "
            f"{m.wall_clock_elapsed_s:.3f}s (detection="
            f"{m.detection_latency_s:.3f}s, termination="
            f"{m.termination_latency_s:.3f}s)"
        )
        # Sanity — sum of measured legs is in the ballpark of wall clock.
        # We use a loose tolerance because the wall-clock window includes
        # supervisor-side overhead (await scheduling, asyncio.wait_for)
        # that the injected-clock latencies don't capture.
        leg_sum = m.detection_latency_s + m.termination_latency_s
        assert leg_sum <= m.wall_clock_elapsed_s + 0.5, (
            f"Latency leg sum {leg_sum:.3f}s exceeds wall clock "
            f"{m.wall_clock_elapsed_s:.3f}s by more than 0.5s"
        )
        assert m.subprocess_returncode != 0, (
            f"Expected non-zero exit (SIGTERM-driven); got {m.subprocess_returncode}"
        )
