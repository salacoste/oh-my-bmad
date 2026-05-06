"""Tests for session lifecycle payload models (Story 5.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from events.ids import new_session_id, new_task_id, new_worker_id
from events.payloads import (
    SessionFinishedPayload,
    SessionHeartbeatPayload,
    SessionStartedPayload,
)


def _sid() -> str:
    return new_session_id()


def _wid() -> str:
    return new_worker_id()


def _tid() -> str:
    return new_task_id()


class TestSessionStartedPayload:
    def test_valid_with_task_id(self) -> None:
        p = SessionStartedPayload(session_id=_sid(), worker_id=_wid(), task_id=_tid())
        assert p.task_id is not None

    def test_valid_without_task_id(self) -> None:
        p = SessionStartedPayload(session_id=_sid(), worker_id=_wid())
        assert p.task_id is None

    def test_invalid_session_id_format(self) -> None:
        with pytest.raises(ValidationError):
            SessionStartedPayload(session_id="bad-id", worker_id=_wid())

    def test_invalid_worker_id_format(self) -> None:
        with pytest.raises(ValidationError):
            SessionStartedPayload(session_id=_sid(), worker_id="bad-id")

    def test_invalid_task_id_format(self) -> None:
        with pytest.raises(ValidationError):
            SessionStartedPayload(session_id=_sid(), worker_id=_wid(), task_id="bad-id")

    def test_frozen(self) -> None:
        p = SessionStartedPayload(session_id=_sid(), worker_id=_wid())
        with pytest.raises(ValidationError):
            p.session_id = "mutated"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SessionStartedPayload(session_id=_sid(), worker_id=_wid(), extra="nope")  # type: ignore[call-arg]


class TestSessionHeartbeatPayload:
    def test_valid(self) -> None:
        p = SessionHeartbeatPayload(session_id=_sid())
        assert p.session_id.startswith("s-")

    def test_invalid_session_id(self) -> None:
        with pytest.raises(ValidationError):
            SessionHeartbeatPayload(session_id="")

    def test_invalid_session_id_wrong_prefix(self) -> None:
        with pytest.raises(ValidationError):
            SessionHeartbeatPayload(session_id=_tid())

    def test_frozen(self) -> None:
        p = SessionHeartbeatPayload(session_id=_sid())
        with pytest.raises(ValidationError):
            p.session_id = "mutated"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SessionHeartbeatPayload(session_id=_sid(), extra="nope")  # type: ignore[call-arg]


class TestSessionFinishedPayload:
    def test_valid(self) -> None:
        p = SessionFinishedPayload(session_id=_sid())
        assert p.session_id.startswith("s-")

    def test_invalid_session_id(self) -> None:
        with pytest.raises(ValidationError):
            SessionFinishedPayload(session_id="not-a-session")

    def test_invalid_session_id_wrong_prefix(self) -> None:
        with pytest.raises(ValidationError):
            SessionFinishedPayload(session_id=_tid())

    def test_frozen(self) -> None:
        p = SessionFinishedPayload(session_id=_sid())
        with pytest.raises(ValidationError):
            p.session_id = "mutated"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SessionFinishedPayload(session_id=_sid(), extra="nope")  # type: ignore[call-arg]
