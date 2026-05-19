"""Exit-code matrix tests for :func:`metrics_subscriber.run_subscriber` (Q6).

Story 10.2 pass-2 P2-H2 + P2-H3 add structured exit codes:

* **0** — graceful shutdown.
* **1** — concurrent-start refused (VH-10) OR filesystem-unsupported (P2-H6).
* **2** — cursor schema_version refused (VH-9 + P2-H2).
* **3** — corrupt-region detected (P2-H3 — ParseSkipThresholdExceeded).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator, Iterator, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
                "path": str(events_dir / "2026-05-19.jsonl"),
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
    log_path = events_dir / "2026-05-19.jsonl"
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
    log_path = events_dir / "2026-05-19.jsonl"
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
