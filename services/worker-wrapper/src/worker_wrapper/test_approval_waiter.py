"""Unit tests for ApprovalWaiter — JSONL polling for approval events (Story 6.7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from worker_wrapper.adapters.approval_waiter import (
    ApprovalResult,
    ApprovalWaiter,
)

# ---------------------------------------------------------------------------
# Lightweight fakes — avoids importing EventEnvelope which needs full registry
# ---------------------------------------------------------------------------


@dataclass
class FakeEnvelope:
    """Minimal envelope shape that ApprovalWaiter._scan_today expects."""

    type: str
    event_id: str = ""
    payload: dict[str, Any] | None = None


class FakeClock:
    """Minimal clock returning a fixed UTC datetime."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAY_PATH = Path("/fake/2026-05-10.jsonl")


def _granted_event(task_id: str, event_id: str = "evt-1") -> FakeEnvelope:
    return FakeEnvelope(
        type="approval.granted",
        event_id=event_id,
        payload={"task_id": task_id, "idempotency_key": "key-abc"},
    )


def _rejected_event(task_id: str, reason: str = "nope") -> FakeEnvelope:
    return FakeEnvelope(
        type="approval.rejected",
        event_id="evt-rej",
        payload={"task_id": task_id, "reason": reason},
    )


def _unrelated_event() -> FakeEnvelope:
    return FakeEnvelope(
        type="task.started",
        event_id="evt-other",
        payload={"task_id": "other-task"},
    )


def _patch_day_path(return_value: Path = _DAY_PATH):
    return patch(
        "worker_wrapper.adapters.approval_waiter.current_day_path",
        return_value=return_value,
    )


def _patch_read_log_lines(**kwargs):
    return patch(
        "worker_wrapper.adapters.approval_waiter.read_log_lines",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApprovalWaiterGranted:
    """Happy-path: approval.granted found on first scan."""

    @pytest.mark.asyncio
    async def test_finds_granted_immediately(self, tmp_path: Path) -> None:
        clock = FakeClock()
        waiter = ApprovalWaiter(
            event_log_dir=tmp_path,
            clock=clock,
            poll_interval_s=0.01,
            timeout_s=1.0,
        )
        granted = _granted_event("t-1")

        with _patch_day_path(), _patch_read_log_lines(
            return_value=[granted],
        ):
            result = await waiter.wait_for_approval("t-1")

        assert result.granted is True
        assert result.event_id == "evt-1"
        assert result.idempotency_key == "key-abc"


class TestApprovalWaiterRejected:
    """Rejection path: approval.rejected found."""

    @pytest.mark.asyncio
    async def test_finds_rejected(self, tmp_path: Path) -> None:
        clock = FakeClock()
        waiter = ApprovalWaiter(
            event_log_dir=tmp_path,
            clock=clock,
            poll_interval_s=0.01,
            timeout_s=1.0,
        )
        rejected = _rejected_event("t-2")

        with _patch_day_path(), _patch_read_log_lines(
            return_value=[rejected],
        ):
            result = await waiter.wait_for_approval("t-2")

        assert result.granted is False
        assert result.reason == "nope"


class TestApprovalWaiterPolling:
    """Polling behavior: not found on first scan, found on second."""

    @pytest.mark.asyncio
    async def test_polls_until_found(self, tmp_path: Path) -> None:
        clock = FakeClock()
        waiter = ApprovalWaiter(
            event_log_dir=tmp_path,
            clock=clock,
            poll_interval_s=0.01,
            timeout_s=2.0,
        )
        granted = _granted_event("t-3")
        call_count = 0

        def _scan_sequence(path: Path) -> list[FakeEnvelope]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return []
            return [granted]

        with _patch_day_path(), _patch_read_log_lines(
            side_effect=lambda p: _scan_sequence(p),
        ):
            result = await waiter.wait_for_approval("t-3")

        assert result.granted is True
        assert call_count >= 3


class TestApprovalWaiterTimeout:
    """Timeout when no approval event arrives."""

    @pytest.mark.asyncio
    async def test_raises_timeout(self, tmp_path: Path) -> None:
        clock = FakeClock()
        waiter = ApprovalWaiter(
            event_log_dir=tmp_path,
            clock=clock,
            poll_interval_s=0.01,
            timeout_s=0.05,
        )

        with (
            _patch_day_path(),
            _patch_read_log_lines(return_value=[]),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            await waiter.wait_for_approval("t-timeout")


class TestApprovalWaiterFileNotFound:
    """FileNotFoundError from read_log_lines is handled."""

    @pytest.mark.asyncio
    async def test_handles_missing_file(self, tmp_path: Path) -> None:
        clock = FakeClock()
        waiter = ApprovalWaiter(
            event_log_dir=tmp_path,
            clock=clock,
            poll_interval_s=0.01,
            timeout_s=0.05,
        )

        def _raise_fnf(path: Path) -> None:
            raise FileNotFoundError(path)

        with (
            _patch_day_path(),
            _patch_read_log_lines(side_effect=_raise_fnf),
            pytest.raises(TimeoutError),
        ):
            await waiter.wait_for_approval("t-missing")


class TestApprovalWaiterTaskIdFiltering:
    """Only events matching the requested task_id are considered."""

    @pytest.mark.asyncio
    async def test_ignores_other_task_ids(self, tmp_path: Path) -> None:
        clock = FakeClock()
        waiter = ApprovalWaiter(
            event_log_dir=tmp_path,
            clock=clock,
            poll_interval_s=0.01,
            timeout_s=0.05,
        )
        events = [_granted_event("other-task"), _unrelated_event()]

        with (
            _patch_day_path(),
            _patch_read_log_lines(return_value=events),
            pytest.raises(TimeoutError),
        ):
            await waiter.wait_for_approval("t-not-in-log")


class TestApprovalResult:
    """Dataclass smoke tests."""

    def test_granted_result(self) -> None:
        r = ApprovalResult(granted=True, event_id="e1", idempotency_key="k1")
        assert r.granted is True
        assert r.event_id == "e1"

    def test_rejected_result(self) -> None:
        r = ApprovalResult(granted=False, reason="denied")
        assert r.granted is False
        assert r.reason == "denied"
