"""Story 2.11 — synthetic crash-injection harness for registry-state.

Boots ``registry-state`` under a self-contained docker-compose stack
(:data:`_crash_compose.COMPOSE_FILE`), drives a synthesized task through
each of 4 lifecycle phases by appending JSONL events to the host-side
bind-mount, kills the container via SIGKILL on all platforms (no grace
window — true crash semantics per NFR-R1), restarts via ``compose start``
(waiting for the
healthcheck to flip to ``healthy`` via the ``/tmp/ready`` touchpoint),
and asserts post-restart state-reconstruction with **zero duplicate
events** (NFR-R2) and **100% restart recoverability** (NFR-R1).

Phase 1 mapping: the spec's ``verifying`` lifecycle phase has no typed
status in the materializer yet; ``task.summary_emitted`` is the closest
existing post-execution observability event. Real ``verifying`` lands
in Epic 5 worker-lifecycle stories — see the ``Phase.VERIFYING`` TODO
in ``_crash_events.py``.

Performance: each phase shares one compose stack via the session-scoped
``crash_harness`` fixture (boots once, restarts 4 times, tears down
once). Total budget ≈ 5 minutes; typical run ≈ 80–180s on a warm
machine.

Skip behaviour: when ``docker info`` fails (Docker daemon unreachable
or unavailable), the ``skip_if_no_docker`` autouse fixture skips all
4 tests with a stable reason — local-dev `just test` without Docker
remains green.

NFR-R2 reconstruction-path note: the per-phase ``_run_phase_test``
sequence appends events → kills → restarts → waits for materialization.
There is no pre-kill ``wait_for_materialization`` call. This means the
post-restart subscriber MUST replay the JSONL log to reconstruct the
events (and thus the task row + ``last_event_id`` pointer) — exactly
the recovery path NFR-R1 / NFR-R2 mandates. A pre-kill wait would
materialize the events before the kill, defeating the test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING

import aiosqlite
import pytest
from _crash_compose import CrashHarness
from _crash_events import (
    Phase,
    drive_task_through_phase,
    make_clock_and_rng,
    wait_for_materialization,
)
from events import TickingClock, new_task_id

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def crash_harness(
    tmp_path_factory: pytest.TempPathFactory,
    skip_if_no_docker: None,  # noqa: PT019 — ensure docker check fires before harness boots
) -> Iterator[CrashHarness]:
    """Session-scoped CrashHarness — boots once, all 4 phases share it.

    Per-phase tests reset state between runs by killing + restarting; the
    JSONL log accumulates across phases (additive sequences), and the
    SQLite DB materializes everything on each restart's startup-replay.
    The harness's own ``__exit__`` runs ``down -v`` after all 4 phases
    complete.
    """
    base = tmp_path_factory.mktemp("crash-injection")
    with CrashHarness(base) as harness:
        yield harness


# ---------------------------------------------------------------------------
# Per-phase assertion helper (AC-7a..AC-7f)
# ---------------------------------------------------------------------------


async def _assert_phase_recovery(
    *,
    harness: CrashHarness,
    task_id: str,
    synthesized_envelopes: list[object],
    pre_kill_max_mono_ns: int = 0,
) -> dict[str, object]:
    """Run AC-7a..AC-7f assertions; return summary dict for the artifact.

    The :class:`object` typing on ``synthesized_envelopes`` is a deliberate
    mypy-friendly compromise — runtime each entry is an ``EventEnvelope``
    but typing across the test-only ``_crash_events`` import chain inside
    ``# type: ignore``-free scope avoids a circular-import dance with
    :class:`CrashHarness` (which lives in a sibling module in the same
    importlib-discovered directory).
    """
    from events import (
        EventEnvelope,  # noqa: PLC0415 — local import keeps top free of test-only deps
    )

    envelopes_typed: list[EventEnvelope] = []
    for env in synthesized_envelopes:
        assert isinstance(env, EventEnvelope)
        envelopes_typed.append(env)

    # Empty input would produce ``IN ()`` SQL syntax errors below; surface
    # the wiring bug clearly instead.
    assert len(envelopes_typed) >= 1, (
        "synthesized_envelopes must contain at least one envelope per phase"
    )

    log_dir: Path = harness.event_log_dir()
    db_path: Path = harness.db_path()

    # AC-7a: zero duplicate synthesized event IDs in the harness output.
    assert len({env.event_id for env in envelopes_typed}) == len(envelopes_typed)

    uri = f"file:{db_path}?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as conn:
        # AC-7b: every synthesized event_id is in the events table.
        ids_jsonl = {env.event_id for env in envelopes_typed}
        # SQLite parameter binding doesn't do `IN (?)` lists; build a
        # placeholder string the size of ids_jsonl. Already guarded above
        # against the empty case (which would produce invalid `IN ()`).
        placeholders = ",".join("?" * len(ids_jsonl))
        cursor = await conn.execute(
            f"SELECT id FROM events WHERE id IN ({placeholders})",  # noqa: S608 — placeholders are ?-bound, not user input
            tuple(ids_jsonl),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        ids_db = {r[0] for r in rows}

        # AC-7c, AC-7d: tasks row exists post-restart, last_event_id matches.
        cursor = await conn.execute(
            "SELECT id, status, last_event_id FROM tasks WHERE id = ?", (task_id,)
        )
        task_row = await cursor.fetchone()
        await cursor.close()

        # AC-7a refinement: count only the synthesized IDs for this phase.
        # Registry-state may append/materialize auxiliary audit events such as
        # task.state_transition; those are valid side effects and must not be
        # mistaken for duplicate replay of the harness-emitted lifecycle.
        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM events WHERE id IN ({placeholders})",  # noqa: S608 — placeholders are ?-bound
            tuple(ids_jsonl),
        )
        synthesized_count_row = await cursor.fetchone()
        await cursor.close()
        assert synthesized_count_row is not None
        count_db_for_synthesized = int(synthesized_count_row[0])

        # AC-7e: capture post-restart MAX for strict cursor-advancement check.
        cursor = await conn.execute("SELECT MAX(emitted_at_monotonic_ns) FROM events")
        max_row = await cursor.fetchone()
        await cursor.close()
        assert max_row is not None
        post_restart_max_mono_ns = int(max_row[0]) if max_row[0] is not None else 0

    # Em7: verify harness's aiosqlite URI is actually read-only.
    await _assert_ro_enforced(db_path)

    # AC-7a (per-task): exact match between events synthesized by this
    # phase and those materialized by ID. Auxiliary audit rows are allowed.
    count_jsonl_for_task = len(envelopes_typed)
    assert count_db_for_synthesized == count_jsonl_for_task, (
        f"synthesized events not materialized exactly once for task {task_id!r}: "
        f"jsonl={count_jsonl_for_task} db={count_db_for_synthesized}"
    )
    # AC-7b
    assert ids_db == ids_jsonl, f"missing event ids in DB: expected={ids_jsonl} got={ids_db}"
    # AC-7c
    assert task_row is not None, f"task {task_id} not in DB after restart"
    # AC-7d: the final synthesized event is materialized. Newer registry-state
    # versions may update tasks.last_event_id to a system audit child (for
    # example task.state_transition), so last_event_id is no longer required
    # to equal the final harness-emitted lifecycle event exactly.
    final_env = envelopes_typed[-1]
    assert final_env.event_id in ids_db, (
        f"final synthesized event missing from DB: {final_env.event_id!r}"
    )
    # AC-7e (strict): post-restart MAX must strictly exceed pre-kill MAX.
    # This is meaningful because there is NO pre-kill wait_for_materialization
    # call in _async_phase_body — the synthesized events were appended to JSONL
    # only; the DB cursor was at pre_kill_max_mono_ns at kill time (0 for Phase
    # 1, or the prior phase's cursor for Phases 2-4). The post-restart subscriber
    # MUST replay JSONL to advance the cursor past pre_kill_max_mono_ns. If
    # we had done a pre-kill wait, the rows would already be durable and
    # post_restart_max_mono_ns ≥ final_env.emitted_at_monotonic_ns would be
    # vacuously guaranteed by durability, not by replay.
    assert post_restart_max_mono_ns > pre_kill_max_mono_ns, (
        f"AC-7e: replay cursor did not strictly advance post-restart; "
        f"pre_kill_max={pre_kill_max_mono_ns} post_restart_max={post_restart_max_mono_ns} "
        f"(subscriber may not have replayed JSONL)"
    )
    assert post_restart_max_mono_ns >= final_env.emitted_at_monotonic_ns, (
        f"AC-7e: replay cursor advanced but didn't reach final synthesized event; "
        f"post_restart_max={post_restart_max_mono_ns} "
        f"synthesized_last={final_env.emitted_at_monotonic_ns}"
    )

    # AC-7f: every JSONL file ends with \n (recover_all_logs trimmed any partial).
    for jsonl in sorted(log_dir.glob("*.jsonl")):
        data = jsonl.read_bytes()
        if not data:
            continue
        assert data.endswith(b"\n"), f"{jsonl} does not end with \\n (partial line)"

    return {
        "events_synthesized": len(envelopes_typed),
        "events_in_db_post_restart": count_db_for_synthesized,
        "events_in_db_for_task": count_db_for_synthesized,
        "duplicate_count": count_db_for_synthesized - len(envelopes_typed),
        "task_status": task_row[1] if task_row else None,
        "pre_kill_max_mono_ns": pre_kill_max_mono_ns,
        "post_restart_max_mono_ns": post_restart_max_mono_ns,
    }


# ---------------------------------------------------------------------------
# Per-phase test runner — shared logic across the 4 phase tests.
# ---------------------------------------------------------------------------


async def _read_max_mono_ns(db_path: Path) -> int:
    """Return MAX(emitted_at_monotonic_ns) from events, or 0 if table empty/missing."""
    uri = f"file:{db_path}?mode=ro"
    try:
        async with aiosqlite.connect(uri, uri=True) as conn:
            cur = await conn.execute("SELECT MAX(emitted_at_monotonic_ns) FROM events")
            row = await cur.fetchone()
            await cur.close()
            return int(row[0]) if row and row[0] is not None else 0
    except aiosqlite.OperationalError:
        # DB does not exist yet (first phase, pre-restart).
        return 0


async def _assert_ro_enforced(db_path: Path) -> None:
    """Assert the harness's aiosqlite URI truly opens the DB read-only (Em7).

    Attempts an INSERT through the read-only connection; expects an
    OperationalError. If the INSERT succeeds the single-writer discipline
    is violated and the test fails immediately.
    """
    uri = f"file:{db_path}?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as conn:
        try:
            await conn.execute(
                "INSERT INTO events(id, schema_version, type, emitted_at, "
                "emitted_at_monotonic_ns, actor_kind, actor_id, payload_json) "
                "VALUES ('__ro_test__','0','x',0,0,'x','x','{}')"
            )
            # If we get here the DB was opened writable — fail hard.
            raise AssertionError(
                "aiosqlite opened DB in WRITABLE mode despite mode=ro URI; "
                "single-writer discipline may be violated"
            )
        except aiosqlite.OperationalError as exc:
            # Expected: "attempt to write a readonly database"
            if "readonly" not in str(exc).lower():
                raise AssertionError(
                    f"Unexpected OperationalError from read-only check: {exc}"
                ) from exc


async def _async_phase_body(
    *,
    harness: CrashHarness,
    phase: Phase,
    seed: int,
) -> tuple[str, list[object], float, dict[str, object]]:
    """Async core of a phase test — single event loop, no nested asyncio.run.

    Returns ``(task_id, envelopes, restart_duration_s, metrics)`` so the
    sync caller can record a summary entry.

    Sequence:
      1. drive_task_through_phase (sync — appends JSONL events to JSONL only).
      2. Capture pre_kill_max_mono_ns from DB (should be 0 for phase 1, or the
         cursor left by any prior materialization). This is the AC-7e baseline.
      3. kill (SIGKILL, hard) → restart. ``asyncio.to_thread`` keeps the loop
         responsive while the subprocess calls block.
      4. wait_for_materialization (post-restart only — NO pre-kill wait).
         This is intentional: there is no ``wait_for_materialization`` before
         the kill. The post-restart subscriber MUST replay JSONL from scratch
         to reconstruct the task row, ``last_event_id``, and events table —
         exactly the NFR-R1 / NFR-R2 recovery path. A pre-kill wait would
         materialize the events before the kill, making AC-7e vacuously true
         (the rows were already durable) and bypassing the reconstruction test.
      5. _assert_phase_recovery — AC-7a..AC-7f, with pre_kill_max_mono_ns
         passed for the strict AC-7e "cursor strictly advanced post-restart"
         check.
    """
    clock, rng = make_clock_and_rng(seed=seed)
    task_id = _new_task_id(clock=clock, rng=rng)

    envelopes = drive_task_through_phase(
        harness, task_id=task_id, phase=phase, clock=clock, rng=rng
    )
    final_event_id = envelopes[-1].event_id

    # Snapshot the DB cursor BEFORE the kill. For the PLANNING phase this
    # will be 0 (empty DB). For subsequent phases it will be the MAX cursor
    # left by the prior phase's materialization. Post-restart we assert the
    # new MAX is strictly greater — proving the subscriber actually replayed
    # new events rather than just serving pre-existing durable rows.
    pre_kill_max_mono_ns = await _read_max_mono_ns(harness.db_path())

    # Kill → restart cycle (NFR-R1 hard kill = SIGKILL). Use to_thread so
    # subprocess blocking waits don't pin the asyncio loop.
    await asyncio.to_thread(harness.kill)
    restart_duration_s = await asyncio.to_thread(harness.restart)

    # Post-restart: wait for the subscriber's startup-replay path to
    # materialize the events from JSONL → SQLite. This is the ONLY
    # wait_for_materialization call — see sequence note above.
    await wait_for_materialization(harness.db_path(), last_event_id=final_event_id, timeout_s=30.0)

    metrics = await _assert_phase_recovery(
        harness=harness,
        task_id=task_id,
        synthesized_envelopes=list(envelopes),
        pre_kill_max_mono_ns=pre_kill_max_mono_ns,
    )

    return task_id, list(envelopes), restart_duration_s, metrics


def _run_phase_test(
    *,
    harness: CrashHarness,
    phase: Phase,
    seed: int,
    summary_collector: list[dict[str, object]],
) -> None:
    """Drive *phase*, kill, restart, assert; record a summary entry.

    Wraps :func:`_async_phase_body` in a single ``asyncio.run`` so the
    full per-phase async work shares one event loop (was previously
    three separate ``asyncio.run`` invocations). On AssertionError the
    summary entry records ``passed=False`` with the error message
    before re-raising — failures previously vanished from the artifact.
    """
    try:
        task_id, _envelopes, restart_duration_s, metrics = asyncio.run(
            _async_phase_body(harness=harness, phase=phase, seed=seed)
        )
    except AssertionError as exc:
        summary_collector.append(
            {
                "phase": phase.value,
                "task_id": None,
                "events_synthesized": None,
                "events_in_db_post_restart": None,
                "duplicate_count": None,
                "restart_duration_s": None,
                "passed": False,
                "error_message": str(exc),
            }
        )
        raise

    summary_collector.append(
        {
            "phase": phase.value,
            "task_id": task_id,
            "events_synthesized": metrics["events_synthesized"],
            "events_in_db_post_restart": metrics["events_in_db_post_restart"],
            "events_in_db_for_task": metrics.get("events_in_db_for_task"),
            "duplicate_count": metrics["duplicate_count"],
            "pre_kill_max_mono_ns": metrics.get("pre_kill_max_mono_ns"),
            "post_restart_max_mono_ns": metrics.get("post_restart_max_mono_ns"),
            "restart_duration_s": restart_duration_s,
            "passed": True,
        }
    )


def _new_task_id(*, clock: TickingClock, rng: Random) -> str:
    """Local wrapper to keep the import surface narrow."""
    return new_task_id(clock=clock, rng=rng)


# ---------------------------------------------------------------------------
# The 4 phase tests (AC-6).
# ---------------------------------------------------------------------------


@pytest.mark.crash
@pytest.mark.slow
def test_crash_recovery_planning_phase(
    crash_harness: CrashHarness,
    crash_summary_collector: list[dict[str, object]],
) -> None:
    """Drive PLANNING, kill, restart, verify AC-7a..AC-7f."""
    _run_phase_test(
        harness=crash_harness,
        phase=Phase.PLANNING,
        seed=11,
        summary_collector=crash_summary_collector,
    )


@pytest.mark.crash
@pytest.mark.slow
def test_crash_recovery_executing_phase(
    crash_harness: CrashHarness,
    crash_summary_collector: list[dict[str, object]],
) -> None:
    """Drive EXECUTING (additive), kill, restart, verify AC-7a..AC-7f."""
    _run_phase_test(
        harness=crash_harness,
        phase=Phase.EXECUTING,
        seed=22,
        summary_collector=crash_summary_collector,
    )


@pytest.mark.crash
@pytest.mark.slow
def test_crash_recovery_awaiting_approval_phase(
    crash_harness: CrashHarness,
    crash_summary_collector: list[dict[str, object]],
) -> None:
    """Drive AWAITING_APPROVAL (additive), kill, restart, verify.

    ``task.approval_requested``'s materializer handler does NOT transition
    ``tasks.status`` to ``"awaiting_approval"`` (no such enum exists in
    Phase 1). It DOES update ``tasks.last_event_id`` though — verified at
    handlers.py:260. The harness asserts the event is in the events table
    post-restart, NOT a specific status value (per Story 2.11 spec).
    """
    _run_phase_test(
        harness=crash_harness,
        phase=Phase.AWAITING_APPROVAL,
        seed=33,
        summary_collector=crash_summary_collector,
    )


@pytest.mark.crash
@pytest.mark.slow
def test_crash_recovery_verifying_phase(
    crash_harness: CrashHarness,
    crash_summary_collector: list[dict[str, object]],
) -> None:
    """Drive VERIFYING (Phase 1 proxy: task.summary_emitted), kill, restart, verify.

    Phase 1 mapping (per Story 2.11 spec): the typed ``verifying`` status
    lands in Epic 5; ``task.summary_emitted`` is the closest existing
    post-execution observability event today. The handler updates
    ``tasks.last_event_id`` (handlers.py:236), so AC-7d holds for this
    phase post-restart.
    """
    _run_phase_test(
        harness=crash_harness,
        phase=Phase.VERIFYING,
        seed=44,
        summary_collector=crash_summary_collector,
    )
