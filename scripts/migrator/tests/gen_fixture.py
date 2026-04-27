#!/usr/bin/env python3
"""Deterministic 100-event v1.0.0 fixture generator (Story 2.14 AC-5).

Produces ``scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl`` containing
25 tasks × 4 events each = 100 events covering the four canonical task
event types of the happy-path lifecycle:

  task.created -> task.planning.started -> task.plan.ready -> task.completed

Each event line is a canonical-JSON object with **sorted keys** and the
``(",", ":")`` separator pair — byte-identical to what the platform's
:func:`events.to_canonical_json` would emit for an envelope carrying a
plain-dict payload. We deliberately emit dict payloads (not BaseModel
payloads) to match the on-disk shape the migrator + materializer parse
back through :func:`events.from_canonical_json`. v1.0.0 envelopes
intentionally OMIT the new ``extensions`` field — the migrator under
test is what adds it.

Determinism is guaranteed by:

* :class:`events.FrozenClock` / :func:`events.new_uuid7` with
  :class:`random.Random(42)` — every UUIDv7 is reproducible across runs.
* A 1ms tick on both real-time (``emitted_at``) and monotonic
  (``emitted_at_monotonic_ns``) clocks, anchored at
  ``2026-04-22T00:00:00Z`` / ``1_000_000`` ns.

Re-run as a script to rebuild the fixture (e.g., after adding event types).
The committed fixture is the reproducibility ground-truth; the script is
the rebuild aid.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

from events import FrozenClock, new_event_id, new_task_id, new_uuid7

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_v1.0.0.jsonl"
_FIXTURE_START = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
_FIXTURE_MONO_START_NS = 1_000_000  # 1ms; matches Story 2.10 idiom.
_FIXTURE_TICK_NS = 1_000_000  # 1ms per event.
_TASK_COUNT = 25
_EVENTS_PER_TASK = 4
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
    for task_idx in range(_TASK_COUNT):
        # Mint task_id from the FIRST event's clock so the UUIDv7 timestamp
        # aligns with the task's birth.
        task_clock = _build_clock(task_idx * _EVENTS_PER_TASK)
        task_id = new_task_id(clock=task_clock, rng=rng)

        # Event 1: task.created
        e0 = _envelope(
            clk=_build_clock(task_idx * _EVENTS_PER_TASK + 0),
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
        # Event 2: task.planning.started
        e1 = _envelope(
            clk=_build_clock(task_idx * _EVENTS_PER_TASK + 1),
            rng=rng,
            type="task.planning.started",
            payload={"task_id": task_id},
            parent_event_id=e0["event_id"],
        )
        # Event 3: task.plan.ready
        e2 = _envelope(
            clk=_build_clock(task_idx * _EVENTS_PER_TASK + 2),
            rng=rng,
            type="task.plan.ready",
            payload={"task_id": task_id, "plan_summary": f"Plan {task_idx:02d}: 3 steps"},
            parent_event_id=e1["event_id"],
        )
        # Event 4: task.completed
        e3 = _envelope(
            clk=_build_clock(task_idx * _EVENTS_PER_TASK + 3),
            rng=rng,
            type="task.completed",
            payload={
                "task_id": task_id,
                "summary": f"Task {task_idx:02d} done",
                "pr_url": None,
            },
            parent_event_id=e2["event_id"],
        )
        for env in (e0, e1, e2, e3):
            out_lines.append(json.dumps(env, sort_keys=True, separators=(",", ":")))

    _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _FIXTURE_PATH.open("w", encoding="utf-8") as f:
        for line in out_lines:
            f.write(line)
            f.write("\n")
    print(
        f"✓ wrote {_FIXTURE_PATH} "
        f"({_TASK_COUNT * _EVENTS_PER_TASK} events, "
        f"{_TASK_COUNT} tasks x {_EVENTS_PER_TASK} events)"
    )


if __name__ == "__main__":
    main()
