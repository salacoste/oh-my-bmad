"""Approval waiter — polls JSONL for operator approval decisions (Story 6.7).

Scans the event log for ``approval.granted`` or ``approval.rejected`` events
matching a specific ``task_id``. Uses ``asyncio.sleep`` between polls (not
busy-wait). Follows the same ``read_log_lines`` + ``current_day_path`` pattern
from ``clawhip-bridge/server.py:_make_approval_lookup``.

Phase-1 limitation: only scans today's JSONL log file, matching the
clawhip-bridge lookup. Cross-day approvals are not yet covered.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from events.clock import Clock
from registry_state import current_day_path, read_log_lines

logger = structlog.get_logger(__name__)


@dataclass
class ApprovalResult:
    """Outcome of an approval wait."""

    granted: bool
    event_id: str = ""
    idempotency_key: str = ""
    reason: str = ""


class ApprovalWaiter:
    """Poll JSONL event log for approval decisions.

    Parameters
    ----------
    event_log_dir:
        Base directory containing daily JSONL event log files.
    clock:
        Injectable clock (production: ``UTC_clock``; tests: ``MockClock``).
    poll_interval_s:
        Seconds between polls (default 2.0).
    timeout_s:
        Maximum seconds to wait before raising ``TimeoutError``.
    """

    def __init__(
        self,
        event_log_dir: Path,
        clock: Clock,
        poll_interval_s: float = 2.0,
        timeout_s: float = 3600.0,
    ) -> None:
        self._event_log_dir = event_log_dir
        self._clock = clock
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        self._scan_offset: int = 0

    async def wait_for_approval(self, task_id: str) -> ApprovalResult:
        """Poll until ``approval.granted`` or ``approval.rejected`` is found.

        Returns the first matching approval event.
        Raises ``TimeoutError`` if no decision arrives within ``timeout_s``.
        """
        deadline = time.monotonic() + self._timeout_s
        while True:
            result = await asyncio.to_thread(self._scan_today, task_id)
            if result is not None:
                return result

            if time.monotonic() >= deadline:
                logger.error(
                    "approval_wait_timeout",
                    task_id=task_id,
                    timeout=self._timeout_s,
                )
                raise TimeoutError(
                    f"Approval wait timed out after "
                    f"{self._timeout_s}s for task {task_id}"
                )

            await asyncio.sleep(self._poll_interval_s)

    def _scan_today(self, task_id: str) -> ApprovalResult | None:
        """Scan today's JSONL for the first matching approval event.

        Tracks the number of envelopes already scanned so that subsequent
        polls skip previously-processed lines (O(new) instead of O(n)).
        """
        path = current_day_path(self._event_log_dir, self._clock.now())
        try:
            idx = 0
            for envelope in read_log_lines(path):
                idx += 1
                if idx <= self._scan_offset:
                    continue
                payload = _safe_payload(envelope)
                if payload is None:
                    continue
                if payload.get("task_id") != task_id:
                    continue
                evt_type = getattr(envelope, "type", "")
                if evt_type == "approval.granted":
                    self._scan_offset = idx
                    return ApprovalResult(
                        granted=True,
                        event_id=getattr(envelope, "event_id", ""),
                        idempotency_key=payload.get(
                            "idempotency_key", "",
                        ),
                        reason=payload.get("reason", ""),
                    )
                if evt_type == "approval.rejected":
                    self._scan_offset = idx
                    return ApprovalResult(
                        granted=False,
                        event_id=getattr(envelope, "event_id", ""),
                        reason=payload.get("reason", "operator rejected"),
                    )
            self._scan_offset = idx
        except FileNotFoundError:
            pass
        return None


def _safe_payload(envelope: Any) -> dict[str, Any] | None:
    """Extract payload dict from an envelope, tolerating missing/invalid data."""
    payload = getattr(envelope, "payload", None)
    if isinstance(payload, dict):
        return payload
    return None
