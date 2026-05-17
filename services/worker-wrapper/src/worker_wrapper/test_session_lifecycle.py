"""Tests for session lifecycle — start / heartbeat / finish (Story 5.2)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from events.ids import new_session_id, new_worker_id

from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.app.main import (
    _clamp_timeout,
    finish_session,
    heartbeat_loop,
    start_session,
)


def _make_clients(
    *,
    clawhip_ok: bool = True,
    session_reg_ok: bool = True,
) -> MagicMock:
    """Build a mock MCPClientGroup with configurable tool-call success."""
    clients = MagicMock()

    async def _call_tool_ok(name: str, arguments: dict[str, object]) -> None:
        pass

    async def _call_tool_fail(name: str, arguments: dict[str, object]) -> None:
        raise RuntimeError(f"MCP call failed: {name}")

    clawhip = AsyncMock()
    clawhip.call_tool = AsyncMock(
        side_effect=_call_tool_ok if clawhip_ok else _call_tool_fail,
    )
    clients.clawhip_bridge = clawhip

    session_reg = AsyncMock()
    session_reg.call_tool = AsyncMock(
        side_effect=_call_tool_ok if session_reg_ok else _call_tool_fail,
    )
    clients.session_registry = session_reg

    return clients


def _settings(
    *,
    session_id: str = "",
    worker_id: str = "",
    task_id: str = "",
    worktree_path: str = "",
    heartbeat_interval_s: float = 30.0,
) -> WorkerSettings:
    return WorkerSettings(
        session_id=session_id,
        worker_id=worker_id,
        task_id=task_id,
        worktree_path=worktree_path,
        heartbeat_interval_s=heartbeat_interval_s,
    )


# ---------------------------------------------------------------------------
# _clamp_timeout
# ---------------------------------------------------------------------------


def test_clamp_timeout_has_floor() -> None:
    """_clamp_timeout never returns below 1.0s (MED-4 fix)."""
    assert _clamp_timeout(0.01) == 1.0
    assert _clamp_timeout(1.0) == 1.0
    assert _clamp_timeout(5.0) == 2.5
    assert _clamp_timeout(30.0) == 10.0


# ---------------------------------------------------------------------------
# start_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_emits_started_event() -> None:
    clients = _make_clients()
    settings = _settings()
    session_id, worker_id = await start_session(clients, settings)
    assert session_id.startswith("s-")
    assert worker_id.startswith("w-")

    # Verify the clawhip-bridge call used the correct event type and payload.
    clients.clawhip_bridge.call_tool.assert_called_once()
    args = clients.clawhip_bridge.call_tool.call_args
    assert args[0][0] == "emit_event"
    assert args[1]["arguments"]["type"] == "session.started"
    payload = args[1]["arguments"]["payload"]
    assert payload["session_id"] == session_id
    assert payload["worker_id"] == worker_id


@pytest.mark.asyncio
async def test_start_session_uses_provided_ids() -> None:
    sid = new_session_id()
    wid = new_worker_id()
    clients = _make_clients()
    settings = _settings(session_id=sid, worker_id=wid, task_id="")
    session_id, worker_id = await start_session(clients, settings)
    assert session_id == sid
    assert worker_id == wid

    # Verify session.register received the correct IDs.
    clients.session_registry.call_tool.assert_called_once()
    reg_args = clients.session_registry.call_tool.call_args
    assert reg_args[0][0] == "session.register"
    assert reg_args[1]["arguments"]["session_id"] == sid
    assert reg_args[1]["arguments"]["worker_id"] == wid


@pytest.mark.asyncio
async def test_start_session_registers_before_emit() -> None:
    """session.register is called BEFORE emit_event (LOW-11 fix)."""
    call_order: list[str] = []

    async def _track(name: str, arguments: dict[str, object]) -> None:
        call_order.append(name)

    clients = MagicMock()
    clients.session_registry = AsyncMock()
    clients.session_registry.call_tool = AsyncMock(side_effect=_track)
    clients.clawhip_bridge = AsyncMock()
    clients.clawhip_bridge.call_tool = AsyncMock(side_effect=_track)

    await start_session(clients, _settings())
    assert call_order == ["session.register", "emit_event"]


@pytest.mark.asyncio
async def test_start_session_best_effort_on_failure() -> None:
    """start_session does not raise even when MCP calls fail."""
    clients = _make_clients(clawhip_ok=False, session_reg_ok=False)
    settings = _settings()
    session_id, worker_id = await start_session(clients, settings)
    assert session_id.startswith("s-")


# ---------------------------------------------------------------------------
# finish_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_session_emits_finished_event() -> None:
    clients = _make_clients()
    sid = new_session_id()
    wid = new_worker_id()
    await finish_session(clients, sid, wid)
    clients.clawhip_bridge.call_tool.assert_called_once()
    args = clients.clawhip_bridge.call_tool.call_args
    assert args[0][0] == "emit_event"
    assert args[1]["arguments"]["type"] == "session.finished"
    assert args[1]["arguments"]["payload"]["session_id"] == sid


@pytest.mark.asyncio
async def test_finish_session_calls_session_close() -> None:
    clients = _make_clients()
    sid = new_session_id()
    wid = new_worker_id()
    await finish_session(clients, sid, wid)
    clients.session_registry.call_tool.assert_called_once()
    args = clients.session_registry.call_tool.call_args
    assert args[0][0] == "session.close"
    assert args[1]["arguments"]["session_id"] == sid
    assert args[1]["arguments"]["worker_id"] == wid


@pytest.mark.asyncio
async def test_finish_session_best_effort_on_failure() -> None:
    """finish_session does not raise even when MCP calls fail."""
    clients = _make_clients(clawhip_ok=False, session_reg_ok=False)
    await finish_session(clients, new_session_id(), new_worker_id())


# ---------------------------------------------------------------------------
# heartbeat_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_loop_emits_on_interval() -> None:
    clients = _make_clients()
    settings = _settings(heartbeat_interval_s=0.01)
    stop_event = asyncio.Event()

    tick_count = 0

    original_side_effect = clients.clawhip_bridge.call_tool.side_effect

    async def counting_call(name: str, arguments: dict[str, object]) -> None:
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 3:
            stop_event.set()
        if original_side_effect is not None:
            await original_side_effect(name, arguments)

    clients.clawhip_bridge.call_tool = AsyncMock(side_effect=counting_call)

    await heartbeat_loop(clients, settings, new_session_id(), stop_event)
    assert tick_count == 3


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_on_stop_event() -> None:
    clients = _make_clients()
    settings = _settings(heartbeat_interval_s=30.0)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        heartbeat_loop(clients, settings, new_session_id(), stop_event),
    )
    await asyncio.sleep(0.01)
    stop_event.set()
    await task
    clients.clawhip_bridge.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_loop_best_effort_on_failure() -> None:
    """Heartbeat loop continues even when MCP calls fail."""
    clients = _make_clients(clawhip_ok=False, session_reg_ok=False)
    settings = _settings(heartbeat_interval_s=0.01)
    stop_event = asyncio.Event()

    tick_count = 0

    async def counting_fail(name: str, arguments: dict[str, object]) -> None:
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 3:
            stop_event.set()
        raise RuntimeError("MCP down")

    clients.clawhip_bridge.call_tool = AsyncMock(side_effect=counting_fail)
    clients.session_registry.call_tool = AsyncMock()

    await heartbeat_loop(clients, settings, new_session_id(), stop_event)
    assert tick_count == 3


# ---------------------------------------------------------------------------
# Worktree lock integration (Story 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_acquires_worktree_lock(tmp_path: Path) -> None:
    from worker_wrapper.domain.worktree_lock import is_lock_held

    wt = tmp_path / "worktree"
    wt.mkdir()
    clients = _make_clients()
    settings = _settings(worktree_path=str(wt))
    session_id, worker_id = await start_session(clients, settings)
    assert is_lock_held(wt)
    lock = (wt / ".oh-my-bmad.lock").read_text()
    assert session_id in lock


@pytest.mark.asyncio
async def test_start_session_no_lock_without_worktree() -> None:
    clients = _make_clients()
    settings = _settings()  # no worktree_path
    session_id, worker_id = await start_session(clients, settings)
    assert session_id.startswith("s-")


@pytest.mark.asyncio
async def test_start_session_raises_on_locked_worktree(tmp_path: Path) -> None:
    from events.errors import WorktreeLockHeld

    from worker_wrapper.domain.worktree_lock import acquire_lock

    wt = tmp_path / "worktree"
    wt.mkdir()
    other_sid = new_session_id()
    acquire_lock(wt, other_sid, new_worker_id())

    clients = _make_clients()
    settings = _settings(worktree_path=str(wt))
    with pytest.raises(WorktreeLockHeld):
        await start_session(clients, settings)


@pytest.mark.asyncio
async def test_finish_session_releases_worktree_lock(tmp_path: Path) -> None:
    from worker_wrapper.domain.worktree_lock import acquire_lock, is_lock_held

    wt = tmp_path / "worktree"
    wt.mkdir()
    sid = new_session_id()
    wid = new_worker_id()
    acquire_lock(wt, sid, wid)

    clients = _make_clients()
    await finish_session(clients, sid, wid, worktree_path=str(wt))
    assert is_lock_held(wt) is False


@pytest.mark.asyncio
async def test_finish_session_no_lock_without_worktree() -> None:
    clients = _make_clients()
    await finish_session(clients, new_session_id(), new_worker_id())


# ---------------------------------------------------------------------------
# Story 9.6 / FR59 — caller_trace_id threading on emit_event MCP calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_started_emit_includes_caller_trace_id() -> None:
    """AC4 / AC9: start_session forwards the resolved trace_id on the
    clawhip-bridge ``emit_event`` MCP call as ``caller_trace_id``."""
    from events.ids import new_uuid7

    tid = new_uuid7()
    clients = _make_clients()
    settings = _settings()
    settings.trace_id = tid
    settings._resolved_trace_id = None  # ensure fresh resolve

    await start_session(clients, settings)

    # The clawhip-bridge.emit_event call must carry caller_trace_id.
    emit_call = clients.clawhip_bridge.call_tool.call_args
    args = emit_call[1]["arguments"]
    assert args["type"] == "session.started"
    assert args["caller_trace_id"] == tid


@pytest.mark.asyncio
async def test_heartbeat_emit_includes_caller_trace_id() -> None:
    """AC4: heartbeat_loop forwards the resolved trace_id on every tick."""
    from events.ids import new_uuid7

    tid = new_uuid7()
    clients = _make_clients()
    settings = _settings(heartbeat_interval_s=0.01)
    settings.trace_id = tid
    settings._resolved_trace_id = None

    stop_event = asyncio.Event()
    captured_trace_ids: list[str] = []

    async def capture(name: str, arguments: dict[str, object]) -> None:
        if name == "emit_event":
            captured_trace_ids.append(arguments["caller_trace_id"])
            if len(captured_trace_ids) >= 2:
                stop_event.set()

    clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)

    await heartbeat_loop(clients, settings, new_session_id(), stop_event)
    assert len(captured_trace_ids) >= 2
    # AC5: every emission shares the SAME trace_id.
    assert all(t == tid for t in captured_trace_ids)


@pytest.mark.asyncio
async def test_finish_session_emit_includes_caller_trace_id() -> None:
    """AC4: finish_session forwards an explicit trace_id (keyword param)."""
    from events.ids import new_uuid7

    tid = new_uuid7()
    clients = _make_clients()
    await finish_session(
        clients,
        new_session_id(),
        new_worker_id(),
        trace_id=tid,
    )

    emit_call = clients.clawhip_bridge.call_tool.call_args
    args = emit_call[1]["arguments"]
    assert args["type"] == "session.finished"
    assert args["caller_trace_id"] == tid


@pytest.mark.asyncio
async def test_finish_session_mints_trace_id_when_absent() -> None:
    """Defensive AC4 path: legacy caller omits trace_id → fresh UUIDv7 minted
    to satisfy Story 9.5 mandatory-input contract (no crash)."""
    from events.envelope import is_valid_trace_id

    clients = _make_clients()
    await finish_session(clients, new_session_id(), new_worker_id())

    emit_call = clients.clawhip_bridge.call_tool.call_args
    args = emit_call[1]["arguments"]
    assert is_valid_trace_id(args["caller_trace_id"]) is True


@pytest.mark.asyncio
async def test_start_session_and_heartbeat_share_same_trace_id() -> None:
    """AC5: within a single WorkerSettings instance, start_session and
    heartbeat_loop emit byte-identical caller_trace_id values."""
    settings = _settings(heartbeat_interval_s=0.01)
    expected_tid = settings.resolve_trace_id()  # mint + cache once

    clients = _make_clients()
    captured: list[str] = []

    async def capture(name: str, arguments: dict[str, object]) -> None:
        if name == "emit_event":
            captured.append(arguments["caller_trace_id"])

    clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)

    await start_session(clients, settings)

    stop_event = asyncio.Event()

    async def capture_then_stop(name: str, arguments: dict[str, object]) -> None:
        if name == "emit_event":
            captured.append(arguments["caller_trace_id"])
            stop_event.set()

    clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture_then_stop)
    await heartbeat_loop(
        clients,
        settings,
        new_session_id(),
        stop_event,
    )

    # At least one start emission + at least one heartbeat emission.
    assert len(captured) >= 2
    assert all(t == expected_tid for t in captured)
