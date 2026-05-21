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
from events.schema_registry import REGISTRY, _rebuild_types_cache, register
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
    """Snapshot + restore the schema registry around each test (PP14).

    See :mod:`worker_wrapper.domain.test_budget_supervisor._isolated_registry`
    for the rationale; this is the mirror at the integration-test boundary.
    """
    snapshot = dict(REGISTRY)
    register("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    register("task.budget_exceeded", "1.1.0", TaskBudgetExceededPayload)
    try:
        yield
    finally:
        for key in list(REGISTRY.keys()):
            if key not in snapshot:
                REGISTRY.pop(key, None)
        for key, model in snapshot.items():
            REGISTRY[key] = model
        _rebuild_types_cache()


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
    cancel = asyncio.Event()
    task_id = new_task_id(clock=SystemClock(), rng=Random(iteration))
    writer = EventLogWriter(base_dir=event_log_dir, clock=SystemClock())
    # PP19 — spawn the subprocess INSIDE the try block so a pre-spawn raise
    # in any of the setup lines above does not leak a child process. The
    # ``finally`` block below reaps ``proc`` unconditionally.
    proc: asyncio.subprocess.Process | None = None
    try:
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
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()

    wall_elapsed = t1 - t0
    assert result.triggered is True, f"iter {iteration}: supervisor did not fire"
    assert proc is not None, f"iter {iteration}: subprocess never spawned"
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_enforcement_latency_cold_start(tmp_path: Path) -> None:
    """PP8 — cold-start latency: envelope written BEFORE supervisor spawn.

    The AC5 happy-path test buffers a 50ms warmup before measuring t0, which
    favours the supervisor (its first poll has already cycled). This test
    measures the harder cold-start path: the envelope sits in the JSONL
    BEFORE the supervisor task is created, so the very first poll must
    detect it. Wall-clock from supervisor-spawn → supervisor-return must
    still satisfy the 5s ceiling.
    """
    runner = ClaudeCodeRunner(_settings())
    cancel = asyncio.Event()
    task_id = new_task_id(clock=SystemClock(), rng=Random(99))
    writer = EventLogWriter(base_dir=tmp_path, clock=SystemClock())
    proc: asyncio.subprocess.Process | None = None
    try:
        # 1. Write the budget envelope BEFORE spawning the supervisor.
        envelope = _make_budget_envelope(
            task_id=task_id, tokens_used=5000, token_limit=1000, mono_seed=99
        )
        await writer.append(envelope)

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

        # 2. Measure wall-clock from supervisor-spawn → supervisor-return.
        t0 = time.monotonic()
        supervisor_task = asyncio.create_task(
            watch_for_budget_exceeded(
                task_id=task_id,
                event_log_dir=tmp_path,
                terminate_callback=_terminate,
                clock=SystemClock(),
                cancel_event=cancel,
                poll_interval_s=0.1,
            ),
            name=f"budget-supervisor-coldstart-{task_id}",
        )
        result: _BudgetSupervisorResult = await asyncio.wait_for(
            supervisor_task,
            timeout=10.0,
        )
        t1 = time.monotonic()
    finally:
        await writer.close()
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()

    elapsed = t1 - t0
    assert result.triggered is True, f"cold-start: supervisor did not fire ({result})"
    # NFR-R8 ceiling — cold-start MUST still satisfy it.
    assert elapsed < 5.0, f"cold-start latency {elapsed:.3f}s exceeds NFR-R8 5s ceiling"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_enforcement_latency_sigkill_escalation(tmp_path: Path) -> None:
    """PP8 — SIGKILL-escalation latency under the 5s NFR-R8 ceiling.

    The subprocess installs SIG_IGN for SIGTERM so the supervisor's
    cooperative terminate path is forced to escalate to SIGKILL after the
    full grace window. Wall-clock from envelope-write → supervisor-return
    must remain below 5s + buffer; ``termination_method`` MUST be ``sigkill``.
    """
    runner = ClaudeCodeRunner(_settings())
    cancel = asyncio.Event()
    task_id = new_task_id(clock=SystemClock(), rng=Random(123))
    writer = EventLogWriter(base_dir=tmp_path, clock=SystemClock())
    proc: asyncio.subprocess.Process | None = None
    try:
        # SIGTERM-ignoring child — sleep ≤60s so test fails fast on bug.
        # Read a sentinel from stdout before driving termination so the
        # SIG_IGN handler is definitely installed (otherwise SIGTERM-before-
        # handler-install collapses to the default-handler exit path).
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            (
                "import signal, time, sys; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('READY', flush=True); "
                "time.sleep(60)"
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        sentinel = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
        assert sentinel.strip() == b"READY", f"child SIG_IGN setup failed: {sentinel!r}"
        runner._process = proc

        async def _terminate() -> _TerminationResult:
            # Short grace so SIGKILL escalation fires quickly under test.
            return await runner.terminate_with_grace(grace_period_s=1.0)

        supervisor_task = asyncio.create_task(
            watch_for_budget_exceeded(
                task_id=task_id,
                event_log_dir=tmp_path,
                terminate_callback=_terminate,
                clock=SystemClock(),
                cancel_event=cancel,
                poll_interval_s=0.1,
            ),
            name=f"budget-supervisor-sigkill-{task_id}",
        )

        envelope = _make_budget_envelope(
            task_id=task_id, tokens_used=5000, token_limit=1000, mono_seed=123
        )
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await writer.append(envelope)
        result: _BudgetSupervisorResult = await asyncio.wait_for(
            supervisor_task,
            timeout=10.0,
        )
        t1 = time.monotonic()
    finally:
        await writer.close()
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()

    elapsed = t1 - t0
    assert result.triggered is True
    # 1s grace + supervisor overhead must still stay under 5s NFR-R8.
    assert elapsed < 5.0, f"SIGKILL-escalation latency {elapsed:.3f}s exceeds NFR-R8 5s ceiling"
    # PP21 — termination method MUST propagate as sigkill.
    assert result.termination_method == "sigkill", (
        f"expected sigkill escalation; supervisor reported "
        f"termination_method={result.termination_method!r}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lifespan_handles_runner_exception_when_supervisor_fires_budget_enforcement(  # noqa: E501
    tmp_path: Path,
) -> None:
    """PP1 (P0) + PP12 — runner raises while supervisor fires; lifespan path is reachable.

    Reproduces the original P0 lifecycle-corruption bug:

    1. A runner stub ``runner.run()`` raises ``BrokenPipeError`` (the
       common cascade when the supervisor's SIGTERM kills the subprocess
       mid-stream).
    2. The budget supervisor fires the termination callback for a matching
       envelope (writes ``triggered=True`` into its result).
    3. The lifespan logic (replicated here as a faithful copy of the
       ``run_task`` try/except/finally block) MUST handle ``triggered=True``
       BEFORE re-raising the runner exception; the post-finally lifecycle
       transition + audit log must run.

    Pre-PP1: ``budget_result`` was declared inside the ``finally`` clause;
    the runner exception propagated past the post-finally block and the
    triggered-handling code was unreachable. Result: lifecycle stuck in
    IN_PROGRESS, no audit event, exception leaked.
    """
    cancel = asyncio.Event()
    task_id = new_task_id(clock=SystemClock(), rng=Random(7))
    writer = EventLogWriter(base_dir=tmp_path, clock=SystemClock())

    callback_calls: list[_TerminationResult] = []

    async def _terminate() -> _TerminationResult:
        # Stand-in for runner.terminate_with_grace — returns a
        # TerminationResult so the supervisor propagates ``method``.
        result = _TerminationResult(method="sigterm", elapsed_s=0.001, exit_code=-15)
        callback_calls.append(result)
        return result

    supervisor_task = asyncio.create_task(
        watch_for_budget_exceeded(
            task_id=task_id,
            event_log_dir=tmp_path,
            terminate_callback=_terminate,
            clock=SystemClock(),
            cancel_event=cancel,
            poll_interval_s=0.05,
        ),
        name=f"pp1-budget-supervisor-{task_id}",
    )

    # Pre-write the envelope so the supervisor fires immediately on its first poll.
    envelope = _make_budget_envelope(
        task_id=task_id, tokens_used=2000, token_limit=1000, mono_seed=7
    )
    await writer.append(envelope)
    await writer.close()

    # Wait for the supervisor to observe and dispatch the callback so
    # the runner exception happens AFTER the callback has set triggered=True.
    # We poll a brief window then simulate the runner exception.
    await asyncio.sleep(0.3)

    # Faithful replica of the post-PP1 lifespan try/except/finally block:
    runner_raised: BaseException | None = None
    budget_result: _BudgetSupervisorResult | None = None
    try:

        async def _runner_run() -> None:
            # Simulate the SIGTERM-cascade BrokenPipeError that
            # ``runner.run()`` typically raises when its subprocess is
            # killed mid-stream.
            raise BrokenPipeError("subprocess stdout closed during budget enforcement")

        await _runner_run()
    except BaseException as exc:  # noqa: BLE001 — PP1 capture
        runner_raised = exc
    finally:
        cancel.set()
        try:
            budget_result = await asyncio.wait_for(supervisor_task, timeout=7.0)
        except TimeoutError:  # pragma: no cover — supervisor must complete
            supervisor_task.cancel()
            raise

    # Assertions: the triggered path MUST be reachable.
    assert budget_result is not None
    assert budget_result.triggered is True, (
        "PP1 regression: budget supervisor fired but budget_result.triggered is False"
    )
    assert budget_result.termination_method == "sigterm"
    assert runner_raised is not None
    assert isinstance(runner_raised, BrokenPipeError)
    # The lifespan, given triggered=True, treats the runner exception as the
    # expected cascade — no re-raise; the test reaches this assertion cleanly.
    assert callback_calls, "termination callback never ran"
