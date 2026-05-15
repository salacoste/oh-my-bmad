"""Tests for ApprovalWaiter — approval polling, incremental scan, timeout (Story 6.7).

Uses mock envelopes and a FrozenClock so no real file I/O or time passes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from events.clock import FrozenClock

from worker_wrapper.adapters.approval_waiter import (
    ApprovalWaiter,
    _safe_payload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FROZEN_NOW = datetime(2026, 5, 10, tzinfo=UTC)
_CLOCK = FrozenClock(now=_FROZEN_NOW)


@dataclass
class _FakeEnvelope:
    """Minimal envelope-like object for _scan_today."""

    event_id: str = "ev-1"
    type: str = "approval.granted"
    payload: dict[str, Any] | None = None
    emitted_at: datetime = _FROZEN_NOW
    emitted_at_monotonic_ns: int = 0
    schema_version: str = "1.0.0"
    actor: dict[str, Any] | None = None
    request_id: str = "req-1"


def _granted_envelope(
    task_id: str = "t-1",
    event_id: str = "ev-granted",
) -> _FakeEnvelope:
    return _FakeEnvelope(
        event_id=event_id,
        type="approval.granted",
        payload={
            "task_id": task_id,
            "idempotency_key": "key-123",
            "reason": "looks good",
        },
    )


def _rejected_envelope(
    task_id: str = "t-1",
    event_id: str = "ev-rejected",
) -> _FakeEnvelope:
    return _FakeEnvelope(
        event_id=event_id,
        type="approval.rejected",
        payload={
            "task_id": task_id,
            "reason": "bad idea",
        },
    )


# ---------------------------------------------------------------------------
# _safe_payload tests
# ---------------------------------------------------------------------------


class TestSafePayload:
    def test_dict_payload_returned(self) -> None:
        env = _FakeEnvelope(payload={"task_id": "t-1"})
        assert _safe_payload(env) == {"task_id": "t-1"}

    def test_none_payload_returns_none(self) -> None:
        env = _FakeEnvelope(payload=None)
        assert _safe_payload(env) is None

    def test_non_dict_payload_returns_none(self) -> None:
        env = _FakeEnvelope(payload="not a dict")
        assert _safe_payload(env) is None

    def test_missing_payload_attr_returns_none(self) -> None:
        assert _safe_payload(object()) is None


# ---------------------------------------------------------------------------
# _scan_today tests (synchronous, direct call)
# ---------------------------------------------------------------------------


class TestScanToday:
    def _waiter(self, **overrides: Any) -> ApprovalWaiter:
        defaults: dict[str, Any] = {
            "event_log_dir": Path("/tmp/fake-logs"),
            "clock": _CLOCK,
            "poll_interval_s": 0.01,
            "timeout_s": 5.0,
        }
        defaults.update(overrides)
        return ApprovalWaiter(**defaults)

    def test_granted_event_found(self) -> None:
        envelopes = [_granted_envelope()]
        waiter = self._waiter()
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                return_value=iter(envelopes),
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result = waiter._scan_today("t-1")

        assert result is not None
        assert result.granted is True
        assert result.event_id == "ev-granted"
        assert result.idempotency_key == "key-123"
        assert result.reason == "looks good"

    def test_rejected_event_found(self) -> None:
        envelopes = [_rejected_envelope()]
        waiter = self._waiter()
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                return_value=iter(envelopes),
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result = waiter._scan_today("t-1")

        assert result is not None
        assert result.granted is False
        assert result.event_id == "ev-rejected"
        assert result.reason == "bad idea"

    def test_wrong_task_id_skipped(self) -> None:
        envelopes = [_granted_envelope(task_id="other-task")]
        waiter = self._waiter()
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                return_value=iter(envelopes),
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result = waiter._scan_today("t-1")
        assert result is None

    def test_file_not_found_returns_none(self) -> None:
        waiter = self._waiter()
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                side_effect=FileNotFoundError,
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result = waiter._scan_today("t-1")
        assert result is None

    def test_empty_log_returns_none(self) -> None:
        waiter = self._waiter()
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                return_value=iter([]),
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result = waiter._scan_today("t-1")
        assert result is None

    def test_incremental_scan_skips_already_scanned(self) -> None:
        irrelevant = [
            _FakeEnvelope(type="other.event", payload={"task_id": "t-1"}),
            _FakeEnvelope(type="other.event", payload={"task_id": "t-1"}),
        ]
        granted = _granted_envelope()
        waiter = self._waiter()

        call_count = 0

        def fake_read_lines(path: Path) -> Iterator[_FakeEnvelope]:
            nonlocal call_count
            call_count += 1
            return iter(irrelevant + [granted])

        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                side_effect=fake_read_lines,
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result1 = waiter._scan_today("t-1")
            assert result1 is not None
            assert result1.granted is True

            result2 = waiter._scan_today("t-1")
            assert result2 is None

        assert call_count == 2


# ---------------------------------------------------------------------------
# wait_for_approval async tests
# ---------------------------------------------------------------------------


class TestWaitForApproval:
    @pytest.mark.asyncio
    async def test_finds_approval_immediately(self) -> None:
        waiter = ApprovalWaiter(
            event_log_dir=Path("/tmp/fake-logs"),
            clock=_CLOCK,
            poll_interval_s=0.01,
            timeout_s=2.0,
        )
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                return_value=iter([_granted_envelope()]),
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
        ):
            result = await waiter.wait_for_approval("t-1")

        assert result.granted is True

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        waiter = ApprovalWaiter(
            event_log_dir=Path("/tmp/fake-logs"),
            clock=_CLOCK,
            poll_interval_s=0.01,
            timeout_s=0.05,
        )
        with (
            patch(
                "worker_wrapper.adapters.approval_waiter.read_log_lines",
                return_value=iter([]),
            ),
            patch(
                "worker_wrapper.adapters.approval_waiter.current_day_path",
                return_value=Path("/tmp/fake-logs/2026-05-10.jsonl"),
            ),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            await waiter.wait_for_approval("t-1")
