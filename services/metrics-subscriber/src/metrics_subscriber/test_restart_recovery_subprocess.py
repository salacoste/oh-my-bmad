"""Subprocess-based restart-recovery tests (Story 10.2 pass-2 P2-H9 + P2-L1).

Pass-1's in-process restart-recovery test (test_restart_recovery.py)
does not exercise the real ``python -m metrics_subscriber`` +
``proc.send_signal(SIGTERM)`` path.  Pass-2 (P2-H9) adds the missing
coverage: spawn the actual entrypoint, send SIGTERM, restart, and
assert exactly-once across the boundary.

P2-L1 also adds a cross-process flock test exercising the production
concurrent-start refusal semantics (the in-process version validates
OFD contention only).

Both are marked ``@pytest.mark.slow`` so they can be excluded from
fast inner-loop runs but execute in CI.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    new_event_id,
    new_uuid7,
    to_canonical_json,
)
from events.clock import FrozenClock
from events.schema_registry import register
from pydantic import BaseModel


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _register_test_event_type() -> None:
    """Idempotently register the event type used by these envelopes.

    The subscriber subprocess will also call ``register()`` at import
    time via the metrics-subscriber package; we register here so the
    file-writing helpers in this test module can construct envelopes.
    """
    register("test.restart_recovery_subproc.envelope", "1.0.0", _SimplePayload)


_ACTOR = Actor(kind="system", id="subproc-test")
_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


def _make_envelope(value: str, mono_seed: int) -> EventEnvelope:
    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    rng = Random(mono_seed)
    return EventEnvelope(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.0.0",
        type="test.restart_recovery_subproc.envelope",  # noqa: EVT001 test-only fixture envelope for P2-H9 subprocess restart-recovery test
        emitted_at=FROZEN_EPOCH,
        emitted_at_monotonic_ns=mono_seed,
        actor=_ACTOR,
        payload={"value": value},
        trace_id=_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _write_envelopes(path: Path, envs: list[EventEnvelope]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for env in envs:
            f.write(to_canonical_json(env) + b"\n")


def _subscriber_env(tmp_path: Path, events_dir: Path, cursor_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OMB_METRICS_EVENT_LOG_DIR"] = str(events_dir)
    env["OMB_METRICS_CURSOR_PATH"] = str(cursor_path)
    env["OMB_METRICS_POLL_INTERVAL_S"] = "0.05"
    env["OMB_METRICS_PERSIST_EVERY_N_EVENTS"] = "100"
    env["OMB_METRICS_LOG_LEVEL"] = "INFO"
    # Story 10.3 AC8 — dispatch to the standalone tail-loop entry path.
    # The subprocess tests exercise tail semantics (signal handling,
    # cursor-lock contention, SIGTERM drain) directly; they do NOT need
    # the FastAPI uvicorn surface.  Without this env var the default
    # ``server`` mode spins up uvicorn and SIGTERM exits with rc=-15
    # because the uvicorn server (not the tail loop) owns signal
    # handling and does not propagate stop_event drain semantics.
    env["OMB_METRICS_RUN_MODE"] = "tail"
    return env


@pytest.mark.slow
@pytest.mark.integration
def test_subprocess_sigterm_persists_cursor_and_resumes_exactly_once(tmp_path: Path) -> None:
    """P2-H9 — spawn ``python -m metrics_subscriber``, SIGTERM, restart, assert offset > 0.

    Verifies the real SIGTERM-handler → ``stop_event.set()`` →
    ``persist_now`` shutdown path that the in-process test cannot
    exercise.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)

    # Write a moderately sized log so the subscriber has work to do.
    today = datetime.now(UTC)
    log_path = events_dir / f"{today.date().isoformat()}.jsonl"
    envs = [_make_envelope(f"v{i}", mono_seed=i) for i in range(2000)]
    _write_envelopes(log_path, envs)

    # --- Run 1: spawn, let it process some envelopes, SIGTERM ---
    env = _subscriber_env(tmp_path, events_dir, cursor_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "metrics_subscriber"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait until cursor.json appears + offset > 0 (subscriber has
        # processed at least one persist-window) OR 8s timeout.
        deadline = time.monotonic() + 8.0
        offset_after_run1 = 0
        while time.monotonic() < deadline:
            if cursor_path.exists():
                try:
                    body = json.loads(cursor_path.read_text())
                    if body.get("offset", 0) > 0:
                        offset_after_run1 = body["offset"]
                        break
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(0.05)
        assert offset_after_run1 > 0, "subscriber never persisted a non-zero offset"
        # Send SIGTERM; the signal handler should set stop_event and
        # drain the cursor before exit.
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)
    # P3-H6: tightened from ``rc in (0, -SIGTERM)`` to ``rc == 0``.
    # SIGKILL fallback (``proc.kill()`` after timeout) is no longer
    # acceptable — the subscriber must drain cleanly under SIGTERM
    # within 5s.  Pre-pass-3 the looser assertion masked the
    # missing-stop-check-after-drain bug uncovered by P3-H6.
    assert rc == 0, f"clean SIGTERM exit required; got rc={rc}"

    # Cursor must be persisted with at least the previously-observed offset.
    body = json.loads(cursor_path.read_text())
    assert body["offset"] >= offset_after_run1

    # --- Run 2: fresh subprocess, same env → should resume + drain ---
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "metrics_subscriber"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 8.0
        target_size = log_path.stat().st_size
        while time.monotonic() < deadline:
            try:
                body = json.loads(cursor_path.read_text())
                if body.get("offset", 0) >= target_size:
                    break
            except (OSError, json.JSONDecodeError):
                pass
            time.sleep(0.05)
        # P3-H6: small additional dwell so the subscriber is firmly
        # past ``asyncio.run`` bootstrap (signal handler installed)
        # before SIGTERM arrives.  Without this, a SIGTERM delivered
        # during bootstrap kills the process with -15 (Python's
        # default SIGTERM handler) which the tightened
        # ``rc2 == 0`` assertion (no SIGKILL/SIGTERM fallback)
        # correctly flags as a regression.
        time.sleep(0.5)
        proc2.send_signal(signal.SIGTERM)
        # P3-H6: tightened from ``rc2 in (0, -SIGTERM)`` to
        # ``rc2 == 0``.  Clean exit only — no SIGKILL/SIGTERM
        # default-handler fallback acceptable.
        rc2 = proc2.wait(timeout=5.0)
    finally:
        if proc2.poll() is None:
            proc2.kill()
            proc2.wait(timeout=2.0)
    assert rc2 == 0, f"clean SIGTERM exit required for proc2; got rc2={rc2}"
    body_final = json.loads(cursor_path.read_text())
    assert body_final["offset"] == log_path.stat().st_size


@pytest.mark.slow
@pytest.mark.integration
def test_subprocess_second_instance_refuses_with_concurrent_start_event(tmp_path: Path) -> None:
    """P2-L1 — two subscriber subprocesses against the same cursor.

    The second exits non-zero (1) and emits
    ``metrics_subscriber_concurrent_start_refused`` to stderr.
    Cross-process flock semantics — the in-process test only exercised
    OFD contention.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    # Write at least one envelope so the first subscriber doesn't exit
    # immediately on empty-log no-events.
    today = datetime.now(UTC)
    log_path = events_dir / f"{today.date().isoformat()}.jsonl"
    envs = [_make_envelope(f"v{i}", mono_seed=i) for i in range(10)]
    _write_envelopes(log_path, envs)

    env = _subscriber_env(tmp_path, events_dir, cursor_path)
    # P3-H3 — set the lock-acquired sentinel env var.  Polling for
    # ``<cursor>.lock`` itself races on the open-but-not-flocked
    # window (``os.open(O_CREAT)`` creates the file BEFORE
    # ``fcntl.flock`` is called).  The sentinel exists ONLY after
    # ``cursor.lock()`` has returned successfully, eliminating the
    # race.
    lock_sentinel = tmp_path / "proc1_lock_acquired.sentinel"
    env["OMB_METRICS_LOCK_ACQUIRED_SENTINEL"] = str(lock_sentinel)
    proc1 = subprocess.Popen(
        [sys.executable, "-m", "metrics_subscriber"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # P3-H3: wait for the sentinel (proof flock is held), not the
        # lockfile itself.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if lock_sentinel.exists():
                break
            time.sleep(0.05)
        assert lock_sentinel.exists(), (
            "proc1 did not write the lock-acquired sentinel; flock may not be held"
        )
        # Spawn the second; it should exit 1 quickly.
        proc2 = subprocess.run(
            [sys.executable, "-m", "metrics_subscriber"],
            env=env,
            capture_output=True,
            timeout=10.0,
            check=False,
        )
        assert proc2.returncode == 1, (
            f"second subscriber should exit 1 on lock contention; got {proc2.returncode}"
        )
        # The structured event should appear in stderr (logging.basicConfig
        # routes structlog/stdlib to stderr by default in our basic-config
        # setup).
        combined = (proc2.stderr or b"").decode() + (proc2.stdout or b"").decode()
        assert "metrics_subscriber_concurrent_start_refused" in combined, (
            f"expected concurrent-start event in stderr/stdout; got:\n{combined}"
        )
    finally:
        proc1.send_signal(signal.SIGTERM)
        proc1.wait(timeout=5.0)
        if proc1.poll() is None:
            proc1.kill()
            proc1.wait(timeout=2.0)


@pytest.mark.slow
@pytest.mark.integration
def test_subprocess_sigterm_during_quiescence_drains_cleanly(tmp_path: Path) -> None:
    """P3-H6 — SIGTERM during the rollover-quiescence wait exits 0 cleanly.

    Scenario: yesterday's JSONL exists with stable size (no further
    growth), no today file (so quiescence-wait engages — the
    fast-path requires today's file to exist with non-zero size).
    The subscriber spends the wait window polling.  SIGTERM
    delivered during the wait must result in a clean ``rc == 0``
    exit + cursor reflecting all drained events.

    Pre-pass-3 the post-drain stop-event check was missing — a
    SIGTERM that arrived after ``_drain_chunk`` returned but before
    the rollover branch / next sleep would not be observed until
    the next poll cycle.  Combined with the looser ``rc in (0,
    -SIGTERM)`` assertion, a missing-clean-exit regression slipped
    through.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)

    # Write yesterday's JSONL with stable size; do NOT create today
    # so the rollover quiescence-wait engages.
    yesterday = datetime.now(UTC).date()
    # Force the path to be a day BEFORE today so the reader sees
    # rollover-pending.  Subscriber's current_day_path uses real
    # ``datetime.now(UTC).date()`` so write the file with that name
    # and the reader will treat it as "today" until midnight or
    # until restart sees a new today.
    # Simpler: write the file using yesterday's date string and let
    # the cursor reference it via the cursor.json path field.
    yesterday_str = yesterday.isoformat()  # e.g., 2026-05-19
    log_path = events_dir / f"{yesterday_str}.jsonl"
    envs = [_make_envelope(f"v{i}", mono_seed=i) for i in range(50)]
    _write_envelopes(log_path, envs)

    env = _subscriber_env(tmp_path, events_dir, cursor_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "metrics_subscriber"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait until the subscriber has drained the file (offset
        # reflects EOF) — that is when it enters the rollover-
        # quiescence wait.
        target_size = log_path.stat().st_size
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if cursor_path.exists():
                try:
                    body = json.loads(cursor_path.read_text())
                    if body.get("offset", 0) >= target_size:
                        break
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(0.05)
        # Deliver SIGTERM while the subscriber is in the
        # quiescence wait (file fully drained, no today file).
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)
    # P3-H6: clean exit required — no SIGKILL fallback.
    assert rc == 0, f"clean SIGTERM exit required during quiescence; got rc={rc}"
    # Cursor must reflect the full drain.
    body_final = json.loads(cursor_path.read_text())
    assert body_final["offset"] == target_size, (
        f"cursor must reflect drained EOF; got {body_final['offset']}, expected {target_size}"
    )
