#!/usr/bin/env python3
"""Deterministic v1.0.0 fixture generator for the migrator integration test.

Produces ``scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl`` containing
30 tasks with a mixed lifecycle:

* All 30 tasks emit the 4-event happy path:
  ``task.created`` → ``task.planning.started`` → ``task.plan.ready`` →
  ``task.completed``.
* The first 15 tasks ALSO emit a ``task.execution.started`` event between
  ``plan.ready`` and ``completed`` (so they exercise the materializer's
  session-row creation path).

Total: 30 × 4 + 15 × 1 = **135 events** across the v1.0.0 fixture.
Updated from the original 25 × 4 = 100-event fixture per Story 2.14
code-review fix M3 — the original lifecycle skipped
``task.execution.started`` so no ``sessions`` rows materialized, making
the AC-7 sessions-equivalence assertion vacuously true. This fixture
ensures sessions-equivalence has actual signal.

Each event line is a canonical-JSON object with **sorted keys** and the
``(",", ":")`` separator pair — byte-identical to what the platform's
:func:`events.to_canonical_json` would emit for an envelope carrying a
plain-dict payload. We deliberately emit dict payloads (not BaseModel
payloads) to match the on-disk shape the migrator + materializer parse
back through :func:`events.from_canonical_json`. v1.0.0 envelopes
intentionally OMIT the new ``extensions`` field — the migrator under
test is what adds it.

Determinism (within-run) is guaranteed by:

* :class:`events.FrozenClock` / :func:`events.new_uuid7` with
  :class:`random.Random(42)` — given the fixed seed + clock + call
  ordering below, every UUIDv7 is reproducible across runs.
* A 1ms tick on both real-time (``emitted_at``) and monotonic
  (``emitted_at_monotonic_ns``) clocks, anchored at
  ``2026-04-22T00:00:00Z`` / ``1_000_000`` ns.

Per code-review fix Mn10: determinism is "given fixed seed + clock + call
ordering" — within-call UUIDv7 ordering is monotonic by clock; between
calls is determined by the call sequence below, not by any inherent UUIDv7
property. SHA-256 of the resulting fixture is pinned in
``test_migrator_integration.py::test_gen_fixture_is_byte_deterministic``.

Re-run as a script to rebuild the fixture (e.g., after adding event types).
The committed fixture is the reproducibility ground-truth; the script is
the rebuild aid. After regeneration, update the SHA constant in the
determinism test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

from events import FrozenClock, new_event_id, new_session_id, new_task_id, new_uuid7

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_v1.0.0.jsonl"
_FIXTURE_START = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
_FIXTURE_MONO_START_NS = 1_000_000  # 1ms; matches Story 2.10 idiom.
_FIXTURE_TICK_NS = 1_000_000  # 1ms per event.
_TASK_COUNT = 30
_TASKS_WITH_EXECUTION = 15  # First half emits task.execution.started.
_ACTOR: dict[str, str] = {"kind": "operator", "id": "r2d2"}


def _build_clock(event_index: int) -> FrozenClock:
    """Per-event clock: real-time advances by 1ms; monotonic by 1ms (in ns)."""
    return FrozenClock(
        mono_ns=_FIXTURE_MONO_START_NS + event_index * _FIXTURE_TICK_NS,
        now=_FIXTURE_START + timedelta(milliseconds=event_index),
    )


def _emitted_at(clk: FrozenClock) -> str:
    """Match :func:`events.canonical._datetime_to_iso_z`: ms-precision UTC ``Z``."""
    dt = clk.now()
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _envelope(
    *,
    clk: FrozenClock,
    rng: Random,
    type: str,
    payload: dict[str, Any],
    parent_event_id: str | None,
) -> dict[str, Any]:
    """Build a v1.0.0 canonical-JSON envelope dict (no ``extensions`` field)."""
    return {
        "actor": {"id": _ACTOR["id"], "kind": _ACTOR["kind"]},
        "emitted_at": _emitted_at(clk),
        "emitted_at_monotonic_ns": clk.monotonic_ns(),
        "event_id": new_event_id(clock=clk, rng=rng),
        "parent_event_id": parent_event_id,
        "payload": payload,
        "request_id": new_uuid7(clock=clk, rng=rng),
        "schema_version": "1.0.0",
        "trace_id": None,
        "type": type,
    }


def main() -> None:
    rng = Random(42)
    out_lines: list[str] = []
    event_idx = 0  # Global monotonic counter across all tasks for clock anchoring.
    for task_idx in range(_TASK_COUNT):
        emits_execution = task_idx < _TASKS_WITH_EXECUTION
        # Mint task_id from the FIRST event's clock so the UUIDv7 timestamp
        # aligns with the task's birth.
        task_clock = _build_clock(event_idx)
        task_id = new_task_id(clock=task_clock, rng=rng)

        # Event 1: task.created
        e0 = _envelope(
            clk=_build_clock(event_idx),
            rng=rng,
            type="task.created",
            payload={
                "task_id": task_id,
                "title": f"Fixture task {task_idx:02d}",
                "repo": f"github.com/r2d2/fixture-{task_idx:02d}",
                "hint": "seed-fixture",
            },
            parent_event_id=None,
        )
        event_idx += 1
        # Event 2: task.planning.started
        e1 = _envelope(
            clk=_build_clock(event_idx),
            rng=rng,
            type="task.planning.started",
            payload={"task_id": task_id},
            parent_event_id=e0["event_id"],
        )
        event_idx += 1
        # Event 3: task.plan.ready
        e2 = _envelope(
            clk=_build_clock(event_idx),
            rng=rng,
            type="task.plan.ready",
            payload={"task_id": task_id, "plan_summary": f"Plan {task_idx:02d}: 3 steps"},
            parent_event_id=e1["event_id"],
        )
        event_idx += 1
        if emits_execution:
            # Mint session_id from this event's clock; the task moves to
            # ``executing`` and a Session row materializes downstream.
            exec_clock = _build_clock(event_idx)
            session_id = new_session_id(clock=exec_clock, rng=rng)
            e_exec = _envelope(
                clk=exec_clock,
                rng=rng,
                type="task.execution.started",
                payload={"task_id": task_id, "session_id": session_id},
                parent_event_id=e2["event_id"],
            )
            event_idx += 1
            parent_for_completed = e_exec["event_id"]
        else:
            e_exec = None
            parent_for_completed = e2["event_id"]
        # Final event: task.completed
        e3 = _envelope(
            clk=_build_clock(event_idx),
            rng=rng,
            type="task.completed",
            payload={
                "task_id": task_id,
                "summary": f"Task {task_idx:02d} done",
                "pr_url": None,
            },
            parent_event_id=parent_for_completed,
        )
        event_idx += 1
        emitted = (e0, e1, e2, e_exec, e3) if e_exec is not None else (e0, e1, e2, e3)
        for env in emitted:
            out_lines.append(json.dumps(env, sort_keys=True, separators=(",", ":")))

    _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # newline="" disables platform newline translation; canonical JSONL is
    # \n-only regardless of host OS.
    with _FIXTURE_PATH.open("w", encoding="utf-8", newline="") as f:
        for line in out_lines:
            f.write(line)
            f.write("\n")
    print(
        f"✓ wrote {_FIXTURE_PATH} "
        f"({len(out_lines)} events across {_TASK_COUNT} tasks; "
        f"{_TASKS_WITH_EXECUTION} with task.execution.started)"
    )


if __name__ == "__main__":
    main()
