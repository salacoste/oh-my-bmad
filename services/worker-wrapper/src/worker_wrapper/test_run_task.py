"""Integration tests for run_task approval-gated execution (Story 6.7, AC-7).

Tests cover:
  - Happy-path: no approval needed (no git.push events)
  - Happy-path: approval granted → gated action executed
  - Rejection path: approval.rejected → FAILED transition
  - Timeout path: no approval arrives
  - Restart recovery: AWAITING_APPROVAL sidecar re-attach
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker_wrapper.adapters.claude_code_runner import (
    ClaudeCodeResult,
    ExtractedEvent,
)
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.app.main import run_task


def _make_settings(
    tmp_path: Path,
    *,
    event_log_dir: str = "",
) -> WorkerSettings:
    return WorkerSettings(
        task_id="t-00000000-0000-7000-8000-000000000001",
        worktree_path=str(tmp_path),
        event_log_dir=event_log_dir,
    )


class _FakeClients:
    """Minimal MCPClientGroup stub with async call_tool."""

    def __init__(self) -> None:
        self.clawhip_bridge = AsyncMock()
        self.clawhip_bridge.call_tool = AsyncMock(return_value="evt-1")
        self.session_registry = AsyncMock()
        self.task_registry = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class TestRunTaskNoApprovalNeeded:
    """When Claude Code result has no git.push events, task completes normally."""

    @pytest.mark.asyncio
    async def test_completes_without_approval(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, event_log_dir=str(tmp_path / "logs"))
        clients = _FakeClients()

        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-1",
            events=[
                ExtractedEvent(
                    event_type="file.edited",
                    tool_name="Write",
                    tool_input={},
                ),
            ],
        )

        with patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner:
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(clients, settings, "do stuff", tmp_path)

        # State file should exist with COMPLETED.
        state_file = tmp_path / ".lifecycle-state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["state"] == "completed"


class TestRunTaskApprovalGranted:
    """When git.push detected and approval granted, gated action executes."""

    @pytest.mark.asyncio
    async def test_granted_executes_gated_action(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            event_log_dir=str(tmp_path / "logs"),
        )
        clients = _FakeClients()

        push_event = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push origin main"},
        )
        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-2",
            events=[push_event],
        )

        fake_approval = MagicMock()
        fake_approval.granted = True
        fake_approval.event_id = "approval-evt-1"
        fake_approval.idempotency_key = "key-123"
        fake_approval.reason = ""

        with (
            patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner,
            patch("worker_wrapper.app.main.ApprovalWaiter") as mock_waiter,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ) as mock_tier3,
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            mock_waiter.return_value.wait_for_approval = AsyncMock(
                return_value=fake_approval,
            )
            await run_task(clients, settings, "implement X", tmp_path)

        # tier3.action_performed emitted with accepted=True.
        mock_tier3.assert_awaited_once()
        call_kwargs = mock_tier3.call_args
        assert call_kwargs.kwargs["accepted"] is True

        # State file shows COMPLETED.
        state_file = tmp_path / ".lifecycle-state.json"
        data = json.loads(state_file.read_text())
        assert data["state"] == "completed"


class TestRunTaskApprovalRejected:
    """When approval.rejected, task transitions to FAILED."""

    @pytest.mark.asyncio
    async def test_rejected_transitions_to_failed(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            event_log_dir=str(tmp_path / "logs"),
        )
        clients = _FakeClients()

        push_event = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push origin main"},
        )
        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-3",
            events=[push_event],
        )

        fake_rejection = MagicMock()
        fake_rejection.granted = False
        fake_rejection.event_id = "rej-evt-1"
        fake_rejection.reason = "operator denied"

        with (
            patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner,
            patch("worker_wrapper.app.main.ApprovalWaiter") as mock_waiter,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ) as mock_tier3,
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            mock_waiter.return_value.wait_for_approval = AsyncMock(
                return_value=fake_rejection,
            )
            await run_task(clients, settings, "implement Y", tmp_path)

        mock_tier3.assert_awaited_once()
        assert mock_tier3.call_args.kwargs["accepted"] is False

        state_file = tmp_path / ".lifecycle-state.json"
        data = json.loads(state_file.read_text())
        assert data["state"] == "failed"


class TestRunTaskApprovalTimeout:
    """When approval times out, task transitions to FAILED."""

    @pytest.mark.asyncio
    async def test_timeout_transitions_to_failed(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            event_log_dir=str(tmp_path / "logs"),
        )
        clients = _FakeClients()

        push_event = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push"},
        )
        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-4",
            events=[push_event],
        )

        with (
            patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner,
            patch("worker_wrapper.app.main.ApprovalWaiter") as mock_waiter,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ) as mock_tier3,
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            mock_waiter.return_value.wait_for_approval = AsyncMock(
                side_effect=TimeoutError("timed out"),
            )
            await run_task(clients, settings, "implement Z", tmp_path)

        assert mock_tier3.call_args.kwargs["accepted"] is False

        state_file = tmp_path / ".lifecycle-state.json"
        data = json.loads(state_file.read_text())
        assert data["state"] == "failed"


class TestRunTaskRestartRecovery:
    """When sidecar shows AWAITING_APPROVAL, skip Claude Code run."""

    @pytest.mark.asyncio
    async def test_reuses_sidecar_state(self, tmp_path: Path) -> None:
        # Pre-create sidecar with AWAITING_APPROVAL state.
        state_file = tmp_path / ".lifecycle-state.json"
        state_file.write_text(
            json.dumps(
                {
                    "state": "awaiting_approval",
                    "task_id": "t-00000000-0000-7000-8000-000000000001",
                }
            )
        )

        settings = _make_settings(
            tmp_path,
            event_log_dir=str(tmp_path / "logs"),
        )
        clients = _FakeClients()

        fake_approval = MagicMock()
        fake_approval.granted = True
        fake_approval.event_id = "approval-evt-2"
        fake_approval.idempotency_key = "key-456"
        fake_approval.reason = ""

        with (
            patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner,
            patch("worker_wrapper.app.main.ApprovalWaiter") as mock_waiter,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ),
        ):
            mock_waiter.return_value.wait_for_approval = AsyncMock(
                return_value=fake_approval,
            )
            await run_task(clients, settings, "implement W", tmp_path)

        # ClaudeCodeRunner.run should NOT have been called (restored state).
        mock_runner.return_value.run.assert_not_called()


class TestRunTaskNoTaskId:
    """run_task raises ValueError if task_id is not configured."""

    @pytest.mark.asyncio
    async def test_raises_without_task_id(self, tmp_path: Path) -> None:
        settings = WorkerSettings(
            task_id="",
            worktree_path=str(tmp_path),
        )
        clients = _FakeClients()

        with pytest.raises(ValueError, match="task_id is required"):
            await run_task(clients, settings, "do stuff", tmp_path)


class TestRunTaskEventLogDirNotConfigured:
    """When event_log_dir is empty, task fails before reaching tier3."""

    @pytest.mark.asyncio
    async def test_fails_without_event_log_dir(self, tmp_path: Path) -> None:
        settings = _make_settings(
            tmp_path,
            event_log_dir="",  # Not configured.
        )
        clients = _FakeClients()

        push_event = ExtractedEvent(
            event_type="git.push",
            tool_name="Bash",
            tool_input={"command": "git push"},
        )
        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-5",
            events=[push_event],
        )

        with (
            patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ) as mock_tier3,
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(clients, settings, "implement Z", tmp_path)

        # Early return at event_log_dir check — tier3 never reached.
        assert mock_tier3.call_args is None

        state_file = tmp_path / ".lifecycle-state.json"
        data = json.loads(state_file.read_text())
        assert data["state"] == "failed"


# ---------------------------------------------------------------------------
# Story 9.6 / FR59 — caller_trace_id threading through run_task emissions
# ---------------------------------------------------------------------------


class TestRunTaskTraceIdPropagation:
    """run_task threads the same trace_id to every clawhip-bridge emission."""

    @pytest.mark.asyncio
    async def test_run_task_threads_same_trace_id_to_all_emissions(
        self,
        tmp_path: Path,
    ) -> None:
        """AC4 / AC5 / AC9: every emit_event call from run_task carries the
        same caller_trace_id (the worker-resolved trace_id, byte-identical)."""
        from events.ids import new_uuid7

        tid = new_uuid7()
        settings = _make_settings(tmp_path, event_log_dir=str(tmp_path / "logs"))
        settings.trace_id = tid
        settings._resolved_trace_id = None  # ensure fresh resolve

        clients = _FakeClients()
        captured: list[str] = []

        async def capture(name: str, arguments: dict[str, object]) -> None:
            if name == "emit_event":
                captured.append(arguments["caller_trace_id"])
            return "evt-1"

        clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)

        # No push events → straight to TASK_COMPLETED path (uses the inner
        # _emit_event helper via LifecycleManager).
        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-1",
            events=[
                ExtractedEvent(
                    event_type="file.edited",
                    tool_name="Write",
                    tool_input={},
                ),
            ],
        )

        with patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner:
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(clients, settings, "do stuff", tmp_path)

        # Every lifecycle FSM transition emitted by LifecycleManager goes
        # through _emit_event → caller_trace_id must be present + identical.
        assert len(captured) >= 1
        assert all(t == tid for t in captured)

    @pytest.mark.asyncio
    async def test_run_task_mints_fresh_trace_id_when_settings_unset(
        self,
        tmp_path: Path,
    ) -> None:
        """AC2 / AC5: when WORKER_TRACE_ID is absent, run_task uses the
        WorkerSettings-minted UUIDv7 and threads it to all emissions."""
        from events.envelope import is_valid_trace_id

        settings = _make_settings(tmp_path, event_log_dir=str(tmp_path / "logs"))
        # trace_id is None on this settings instance; resolve_trace_id will mint.

        clients = _FakeClients()
        captured: list[str] = []

        async def capture(name: str, arguments: dict[str, object]) -> None:
            if name == "emit_event":
                captured.append(arguments["caller_trace_id"])
            return "evt-1"

        clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)

        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-1",
            events=[],
        )

        with patch("worker_wrapper.app.main.ClaudeCodeRunner") as mock_runner:
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(clients, settings, "do stuff", tmp_path)

        assert len(captured) >= 1
        # All emissions share a single minted trace_id (per-invocation singleton).
        assert all(t == captured[0] for t in captured)
        assert is_valid_trace_id(captured[0]) is True

    @pytest.mark.asyncio
    async def test_tier3_action_emit_includes_caller_trace_id(
        self,
        tmp_path: Path,
    ) -> None:
        """AC4 / AC9: tier3.action_performed envelope carries caller_trace_id
        equal to the worker's resolved trace_id."""
        from events.ids import new_uuid7

        from worker_wrapper.app.main import _emit_tier3_performed

        tid = new_uuid7()
        clients = _FakeClients()

        await _emit_tier3_performed(
            clients,
            task_id="t-00000000-0000-7000-8000-000000000001",
            accepted=True,
            approval_event_id="evt-approval",
            trace_id=tid,
        )

        emit_call = clients.clawhip_bridge.call_tool.call_args
        args = emit_call[1]["arguments"]
        assert args["type"] == "tier3.action_performed"
        assert args["caller_trace_id"] == tid

    @pytest.mark.asyncio
    async def test_tier3_action_mints_trace_id_when_absent(
        self,
        tmp_path: Path,
    ) -> None:
        """Defensive fallback: legacy callers omitting trace_id get a fresh
        UUIDv7 so the emit_event Pydantic validation still passes (no crash)."""
        from events.envelope import is_valid_trace_id

        from worker_wrapper.app.main import _emit_tier3_performed

        clients = _FakeClients()

        await _emit_tier3_performed(
            clients,
            task_id="t-00000000-0000-7000-8000-000000000001",
            accepted=False,
            reason="some reason",
        )

        emit_call = clients.clawhip_bridge.call_tool.call_args
        args = emit_call[1]["arguments"]
        assert is_valid_trace_id(args["caller_trace_id"]) is True
