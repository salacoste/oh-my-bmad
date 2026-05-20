"""Exit-code matrix tests for :func:`metrics_subscriber.run_subscriber` (Q6).

Story 10.2 pass-2 P2-H2 + P2-H3 add structured exit codes:

* **0** — graceful shutdown.
* **1** — concurrent-start refused (VH-10) OR filesystem-unsupported
  (P2-H6 + P3-H1 — typed exception
  :class:`CursorLockUnsupportedFilesystemError`).
* **2** — cursor schema_version refused (VH-9 + P2-H2).
* **3** — corrupt-region detected (P2-H3 — ParseSkipThresholdExceeded;
  P3-M2 persists ``exc.offset`` for restart-without-re-parsing).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator, Iterator, MutableMapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from events.schema_registry import register
from pydantic import BaseModel

from metrics_subscriber import __main__ as ms_main
from metrics_subscriber.app.config import MetricsSubscriberSettings


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _register_test_type() -> Generator[None, None, None]:
    register("test.exit_codes.envelope", "1.0.0", _SimplePayload)
    yield


@pytest.fixture
def captured_log_events() -> Iterator[list[MutableMapping[str, Any]]]:
    with structlog.testing.capture_logs() as caps:
        yield caps


_TODAY = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
_TODAY_DATE: date = datetime.now(UTC).date()


@pytest.mark.asyncio
async def test_main_exit_2_on_schema_version_refused(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """P2-H2 — VH-9 CursorSchemaVersionError → structured log + exit code 2."""
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    cursor_path.parent.mkdir(parents=True)
    # Persist a cursor with an unknown schema_version.
    cursor_path.write_text(
        json.dumps(
            {
                "schema_version": "9999",  # unknown
                "path": str(events_dir / f"{_TODAY_DATE.isoformat()}.jsonl"),
                "offset": 0,
            }
        )
    )
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )
    rc = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    assert rc == 2
    assert any(
        entry.get("event") == "metrics_subscriber_cursor_schema_version_refused"
        for entry in captured_log_events
    )


@pytest.mark.asyncio
async def test_main_exit_3_on_corrupt_region_detected(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """P2-H3 — ParseSkipThresholdExceeded → structured log + exit code 3.

    Restart-loop scenario: write a contiguous run of un-parseable
    lines exceeding the threshold; the subscriber catches the typed
    exception, drains the cursor at the last successful offset, and
    exits 3.  The next restart (not exercised in this single-test
    run; covered by the multi-restart test below) would re-read the
    same region and exit 3 again, not crash-loop.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    log_path = events_dir / f"{_TODAY_DATE.isoformat()}.jsonl"
    # 200 contiguous garbage lines → trips threshold (default 100).
    log_path.write_bytes(b"{not json line\n" * 200)
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )
    rc = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    assert rc == 3
    assert any(
        entry.get("event") == "metrics_subscriber_corrupt_region_detected"
        for entry in captured_log_events
    )


@pytest.mark.asyncio
async def test_main_exit_3_corrupt_region_restart_loop_does_not_crash(
    tmp_path: Path,
) -> None:
    """P2-H3 — restart after corrupt-region also exits 3 (no crash loop).

    The cursor is persisted at the last successful offset (i.e.,
    before the corrupted region).  On restart the subscriber reads
    the same corrupted region and exits 3 again — predictably, not as
    an uncaught exception.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    log_path = events_dir / f"{_TODAY_DATE.isoformat()}.jsonl"
    log_path.write_bytes(b"{not json line\n" * 200)
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )
    rc1 = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    rc2 = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    assert rc1 == 3
    assert rc2 == 3


@pytest.mark.asyncio
async def test_main_exit_1_filesystem_unsupported_uses_isinstance_not_substring(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """P3-H1 / Q10 — discrimination via ``isinstance``, not substring.

    Patches ``fcntl.flock`` to raise the typed
    :class:`CursorLockUnsupportedFilesystemError`.  The subscriber
    MUST:
    * Exit with rc=1.
    * Emit ``metrics_subscriber_concurrent_start_refused`` with
      ``reason="filesystem_unsupported"``.

    Pre-pass-3 the discriminator was
    ``"unsupported" in str(exc)`` — any future error-message refactor
    (locale, refactor, error-string review) silently inverts the
    dashboard alerting split.
    """
    import errno
    import fcntl

    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    cursor_path.parent.mkdir(parents=True)
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )

    # The lock() method maps OSError(non-EWOULDBLOCK) →
    # CursorLockUnsupportedFilesystemError.  Patch fcntl.flock to
    # raise the underlying OSError(EOPNOTSUPP) so we exercise the
    # production type-mapping path.
    real_flock = fcntl.flock

    def _flock_raises_unsupported(fd: int, op: int) -> None:
        del fd, op
        raise OSError(errno.EOPNOTSUPP, "operation not supported")

    with patch.object(fcntl, "flock", _flock_raises_unsupported):
        rc = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    # Sanity: real flock is restored.
    assert fcntl.flock is real_flock

    assert rc == 1
    refused_entries = [
        entry
        for entry in captured_log_events
        if entry.get("event") == "metrics_subscriber_concurrent_start_refused"
    ]
    assert refused_entries, "must emit concurrent_start_refused on flock-unsupported path"
    assert refused_entries[-1].get("reason") == "filesystem_unsupported", (
        f"reason field must reflect typed-exception isinstance; got "
        f"{refused_entries[-1].get('reason')!r}"
    )


def _write_envelope_bytes(path: Path, count: int) -> int:
    """Helper for P3-M3 — write *count* canonical envelopes; return byte size of run."""
    from random import Random

    from events import (
        FROZEN_EPOCH,
        Actor,
        EventEnvelope,
        new_event_id,
        new_uuid7,
        to_canonical_json,
    )
    from events.clock import FrozenClock

    actor = Actor(kind="system", id="exit-codes-test")
    trace_id = "01917e5c-a7d1-7000-8abc-000000000000"

    path.parent.mkdir(parents=True, exist_ok=True)
    payload_lines = b""
    for i in range(count):
        clk = FrozenClock(mono_ns=i, now=FROZEN_EPOCH)
        rng = Random(i)
        env = EventEnvelope(
            event_id=new_event_id(clock=clk, rng=rng),
            schema_version="1.0.0",
            type="test.exit_codes.envelope",  # noqa: EVT001 test-only fixture envelope
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=i,
            actor=actor,
            payload={"value": f"v{i}"},
            trace_id=trace_id,
            request_id=new_uuid7(clock=clk, rng=rng),
        )
        payload_lines += to_canonical_json(env) + b"\n"
    path.write_bytes(payload_lines)
    return len(payload_lines)


@pytest.mark.asyncio
async def test_main_exit_3_with_valid_prefix_then_garbage_persists_at_corruption_start(
    tmp_path: Path,
) -> None:
    """P3-M2 + P3-M3 — cursor persists at corrupt-region start, not pre-corruption.

    Pre-pass-3 the subscriber persisted ``reader.cursor_offset``
    (last successful yield, before the corruption).  Restart re-read
    the same good prefix between cursor.offset and exc.offset →
    re-parsed → re-tripped threshold → exit 3.  Wasteful but
    correct.  P3-M2 persists ``exc.offset`` instead so restart picks
    up AT the corrupt-region anchor.

    Realistic scenario: N valid envelopes then M garbage lines.
    First run rc=3 with cursor.offset between valid-prefix-end and
    valid-prefix-end + garbage-region-size.  Second run rc=3 with no
    cursor advance past the corruption.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    log_path = events_dir / f"{_TODAY_DATE.isoformat()}.jsonl"
    # 5 valid + 200 garbage lines.  Threshold default 100 → trips on
    # the 100th garbage line.  ``exc.offset`` must equal the byte
    # position immediately AFTER the 5th valid envelope.
    valid_bytes_len = _write_envelope_bytes(log_path, 5)
    garbage_line = b"{not json line\n"
    with open(log_path, "ab") as f:
        f.write(garbage_line * 200)
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )
    rc1 = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    assert rc1 == 3

    # Read the persisted cursor — must be at the byte boundary
    # between valid prefix and garbage region (P3-M2 anchor).
    body = json.loads(cursor_path.read_text())
    persisted_offset_1 = int(body["offset"])
    end_of_valid = valid_bytes_len
    end_of_garbage = valid_bytes_len + 200 * len(garbage_line)
    assert end_of_valid <= persisted_offset_1 < end_of_garbage, (
        f"persisted offset {persisted_offset_1} must land between valid_end "
        f"({end_of_valid}) and garbage_end ({end_of_garbage}); pre-P3-M2 would "
        f"have been ~{end_of_valid} (last successful yield, NOT exc.offset)"
    )
    # After P3-M2 the persisted offset SHOULD be exactly the
    # corruption-run-start anchor.
    assert persisted_offset_1 == end_of_valid, (
        f"P3-M2: persisted offset must equal exc.offset = corruption-run-start "
        f"({end_of_valid}); got {persisted_offset_1}"
    )

    # Second run: no cursor advance past corruption.
    rc2 = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    assert rc2 == 3
    body2 = json.loads(cursor_path.read_text())
    persisted_offset_2 = int(body2["offset"])
    assert persisted_offset_2 == persisted_offset_1, (
        "Second run must not advance the cursor past the corrupt region; "
        f"got {persisted_offset_2}, expected {persisted_offset_1}"
    )


@pytest.mark.asyncio
async def test_main_exit_3_persist_failure_still_returns_3_with_warning(
    tmp_path: Path, captured_log_events: list[MutableMapping[str, Any]]
) -> None:
    """P3-M6 — persist failure on corrupt-exit path is exercised + logged.

    Pre-pass-3 the persist-failure branch (``except OSError``
    around ``cursor.persist_now``) was annotated
    ``# pragma: no cover`` — an untested escape hatch for "disk
    full / parent dir deleted during exit-3".  P2-H3's core
    guarantee (no infinite crash loop) is partially undone by an
    unobserved silent persist-failure.  P3-M6 removes the pragma
    and asserts:

    * Subscriber still returns rc=3 (operator runbook unaffected).
    * Structured warning
      ``metrics_subscriber_persist_on_corrupt_region_failed`` is
      emitted.
    """
    events_dir = tmp_path / "events"
    cursor_path = tmp_path / "metrics" / "cursor.json"
    events_dir.mkdir(parents=True)
    log_path = events_dir / f"{_TODAY_DATE.isoformat()}.jsonl"
    log_path.write_bytes(b"{not json line\n" * 200)
    settings = MetricsSubscriberSettings(
        event_log_dir=events_dir,
        cursor_path=cursor_path,
        poll_interval_s=0.01,
        persist_every_n_events=10,
    )

    # Patch persist_now to raise OSError on the corrupt-exit drain.
    # The final-block ``persist_now`` (shutdown drain) is also
    # affected but it has its own try/except guard (P2-H3 / P2-M6).
    raise_count = [0]
    from metrics_subscriber import cursor as cursor_module

    real_persist_now = cursor_module.CursorPersistence.persist_now

    def _persist_now_raises(self: cursor_module.CursorPersistence, offset: int, path: Path) -> None:
        del self, offset, path
        raise_count[0] += 1
        raise OSError("disk full simulated")

    with patch.object(cursor_module.CursorPersistence, "persist_now", _persist_now_raises):
        rc = await ms_main.run_subscriber(settings, stop_event=asyncio.Event())
    # Real persist_now restored.
    assert cursor_module.CursorPersistence.persist_now is real_persist_now

    assert rc == 3, f"persist failure on corrupt-exit must still return 3; got {rc}"
    assert raise_count[0] >= 1
    assert any(
        entry.get("event") == "metrics_subscriber_persist_on_corrupt_region_failed"
        for entry in captured_log_events
    ), "must emit corrupt-region persist-failure warning"
