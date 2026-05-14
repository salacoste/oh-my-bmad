"""Tests for SessionReconnectingPayload and TaskExecutionResumedPayload (Story 7.8)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from events.payloads import SessionReconnectingPayload, TaskExecutionResumedPayload

_SID = "s-01234567-89ab-7def-8000-000000000001"
_TID = "t-01234567-89ab-7def-8000-000000000002"


# ---------------------------------------------------------------------------
# SessionReconnectingPayload
# ---------------------------------------------------------------------------


def test_session_reconnecting_valid() -> None:
    p = SessionReconnectingPayload(
        session_id=_SID, task_id=_TID, reason="host_restart"
    )
    assert p.reason == "host_restart"


def test_session_reconnecting_frozen() -> None:
    p = SessionReconnectingPayload(
        session_id=_SID, task_id=_TID, reason="host_restart"
    )
    with pytest.raises(ValidationError):
        p.reason = "oom"


def test_session_reconnecting_rejects_invalid_session_id() -> None:
    with pytest.raises(ValidationError):
        SessionReconnectingPayload(
            session_id="bad-id", task_id=_TID, reason="host_restart"
        )


def test_session_reconnecting_rejects_empty_reason() -> None:
    with pytest.raises(ValidationError):
        SessionReconnectingPayload(session_id=_SID, task_id=_TID, reason="")


# ---------------------------------------------------------------------------
# TaskExecutionResumedPayload
# ---------------------------------------------------------------------------


def test_task_execution_resumed_valid() -> None:
    p = TaskExecutionResumedPayload(
        task_id=_TID, session_id=_SID, events_replayed=134, replay_duration_ms=2800
    )
    assert p.events_replayed == 134
    assert p.replay_duration_ms == 2800


def test_task_execution_resumed_frozen() -> None:
    p = TaskExecutionResumedPayload(
        task_id=_TID, session_id=_SID, events_replayed=10, replay_duration_ms=100
    )
    with pytest.raises(ValidationError):
        p.events_replayed = 99


def test_task_execution_resumed_rejects_negative_events_replayed() -> None:
    with pytest.raises(ValidationError):
        TaskExecutionResumedPayload(
            task_id=_TID, session_id=_SID, events_replayed=-1, replay_duration_ms=100
        )


def test_task_execution_resumed_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        TaskExecutionResumedPayload(
            task_id=_TID, session_id=_SID, events_replayed=10, replay_duration_ms=-1
        )
