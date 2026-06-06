"""Integration tests for run_task approval-gated execution (Story 6.7, AC-7).

Tests cover:
  - Happy-path: no approval needed (no git.push events)
  - Happy-path: approval granted → gated action executed
  - Rejection path: approval.rejected → FAILED transition
  - Timeout path: no approval arrives
  - Restart recovery: AWAITING_APPROVAL sidecar re-attach

Story 9.6 review pass-1 H6 / L1: ambient ``WORKER_TRACE_ID`` (and aliases)
is cleared from the env per test; ``new_uuid7`` / ``is_valid_trace_id``
imports are hoisted to module-top instead of inline.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from events.envelope import is_valid_trace_id
from events.ids import new_uuid7

from worker_wrapper.adapters.claude_code_runner import (
    ClaudeCodeResult,
    ExtractedEvent,
)
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.app.main import _MCPClients, run_task

# Story 9.6 review pass-2 PH5 — env-cleaning fixture moved to
# ``worker_wrapper/conftest.py`` (single shared autouse fixture).


def _make_settings(
    tmp_path: Path,
    *,
    event_log_dir: str = "",
    trace_id: str | None = None,
) -> WorkerSettings:
    return WorkerSettings(
        task_id="t-00000000-0000-7000-8000-000000000001",
        worktree_path=str(tmp_path),
        event_log_dir=event_log_dir,
        trace_id=trace_id,
    )


class _FakeClients:
    """Minimal MCPClientGroup stub with async call_tool.

    The three session attributes are ``AsyncMock`` doubles so individual
    tests can reassign ``call_tool`` and introspect ``call_args`` /
    ``await_args_list``. The fake is passed to ``run_task`` /
    ``_emit_tier3_performed`` via ``_as_clients`` which casts it to the
    ``_MCPClients`` protocol at the call seam.
    """

    def __init__(self) -> None:
        self.clawhip_bridge = AsyncMock()
        self.clawhip_bridge.call_tool = AsyncMock(return_value="evt-1")
        self.session_registry = AsyncMock()
        self.task_registry = AsyncMock()

    async def __aenter__(self) -> _FakeClients:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _as_clients(fake: _FakeClients) -> _MCPClients:
    """Cast a ``_FakeClients`` double to the protocol ``run_task`` expects.

    Test-seam cast only: the fake provides the three session attributes the
    protocol declares; mypy cannot see the structural match through the
    ``AsyncMock`` attribute types, so the boundary is bridged here.
    """
    return cast("_MCPClients", fake)


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

        with patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner:
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(_as_clients(clients), settings, "do stuff", tmp_path)

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
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
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
            await run_task(_as_clients(clients), settings, "implement X", tmp_path)

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
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
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
            await run_task(_as_clients(clients), settings, "implement Y", tmp_path)

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
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
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
            await run_task(_as_clients(clients), settings, "implement Z", tmp_path)

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
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
            patch("worker_wrapper.app.main.ApprovalWaiter") as mock_waiter,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ),
        ):
            mock_waiter.return_value.wait_for_approval = AsyncMock(
                return_value=fake_approval,
            )
            await run_task(_as_clients(clients), settings, "implement W", tmp_path)

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
            await run_task(_as_clients(clients), settings, "do stuff", tmp_path)


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
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
            patch(
                "worker_wrapper.app.main._emit_tier3_performed",
                new_callable=AsyncMock,
            ) as mock_tier3,
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(_as_clients(clients), settings, "implement Z", tmp_path)

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
        same caller_trace_id (the worker-resolved trace_id, byte-identical).

        Story 9.6 review pass-1 H6: settings built via constructor with
        ``trace_id=tid`` — exercises the real validator path; no
        post-construction private-attr mutation.
        """
        tid = new_uuid7()
        settings = _make_settings(
            tmp_path,
            event_log_dir=str(tmp_path / "logs"),
            trace_id=tid,
        )

        clients = _FakeClients()
        captured: list[str] = []

        async def capture(name: str, arguments: dict[str, object]) -> str:
            if name == "emit_event":
                tid_val = arguments["caller_trace_id"]
                if isinstance(tid_val, str):
                    captured.append(tid_val)
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

        with patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner:
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(_as_clients(clients), settings, "do stuff", tmp_path)

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
        settings = _make_settings(tmp_path, event_log_dir=str(tmp_path / "logs"))
        # trace_id is None on this settings instance; model_post_init mints it.

        clients = _FakeClients()
        captured: list[str] = []

        async def capture(name: str, arguments: dict[str, object]) -> str:
            if name == "emit_event":
                tid_val = arguments["caller_trace_id"]
                if isinstance(tid_val, str):
                    captured.append(tid_val)
            return "evt-1"

        clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)

        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-1",
            events=[],
        )

        with patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner:
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(_as_clients(clients), settings, "do stuff", tmp_path)

        assert len(captured) >= 1
        # All emissions share a single minted trace_id (per-invocation singleton).
        assert all(t == captured[0] for t in captured)
        assert is_valid_trace_id(captured[0]) is True

    @pytest.mark.asyncio
    async def test_approval_timeout_tier3_emit_uses_worker_trace_id(
        self,
        tmp_path: Path,
    ) -> None:
        """Approval-gate timeout path threads the worker trace_id to tier3 emit."""
        tid = new_uuid7()
        settings = _make_settings(
            tmp_path,
            event_log_dir=str(tmp_path / "logs"),
            trace_id=tid,
        )

        clients = _FakeClients()
        captured: list[dict[str, object]] = []

        async def capture(
            name: str,
            arguments: dict[str, object] | None = None,
            **kwargs: object,
        ) -> str:
            if name == "emit_event":
                call_args = (
                    arguments
                    if arguments is not None
                    else cast(dict[str, object], kwargs["arguments"])
                )
                captured.append(call_args)
            return "evt-1"

        clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)

        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-timeout-trace",
            events=[
                ExtractedEvent(
                    event_type="git.push",
                    tool_name="Bash",
                    tool_input={"command": "git push"},
                ),
            ],
        )

        with (
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
            patch("worker_wrapper.app.main.ApprovalWaiter") as mock_waiter,
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            mock_waiter.return_value.wait_for_approval = AsyncMock(
                side_effect=TimeoutError("timed out"),
            )
            await run_task(_as_clients(clients), settings, "implement timeout path", tmp_path)

        tier3_calls = [call for call in captured if call["type"] == "tier3.action_performed"]
        assert len(tier3_calls) == 1
        assert tier3_calls[0]["caller_trace_id"] == tid
        payload = cast(dict[str, object], tier3_calls[0]["payload"])
        assert payload["accepted"] is False

    @pytest.mark.asyncio
    async def test_tier3_action_emit_includes_caller_trace_id(
        self,
        tmp_path: Path,
    ) -> None:
        """AC4 / AC9: tier3.action_performed envelope carries caller_trace_id
        equal to the worker's resolved trace_id.

        Story 9.6 review pass-1 H3 / M5: signature now takes ``settings``
        instead of an Optional ``trace_id`` kwarg (uniform API).
        """
        from worker_wrapper.app.main import _emit_tier3_performed

        tid = new_uuid7()
        clients = _FakeClients()
        settings = _make_settings(tmp_path, trace_id=tid)

        await _emit_tier3_performed(
            _as_clients(clients),
            settings,
            task_id="t-00000000-0000-7000-8000-000000000001",
            accepted=True,
            approval_event_id="evt-approval",
        )

        emit_call = clients.clawhip_bridge.call_tool.call_args
        args = emit_call[1]["arguments"]
        assert args["type"] == "tier3.action_performed"
        assert args["caller_trace_id"] == tid

    @pytest.mark.asyncio
    async def test_tier3_action_uses_settings_minted_trace_id(
        self,
        tmp_path: Path,
    ) -> None:
        """Story 9.6 review pass-1 H3: when WORKER_TRACE_ID is absent the
        settings-minted UUIDv7 is threaded — no defensive ``or new_uuid7()``
        diverges from the run_task chain."""
        from worker_wrapper.app.main import _emit_tier3_performed

        clients = _FakeClients()
        settings = _make_settings(tmp_path)
        expected = settings.resolve_trace_id()

        await _emit_tier3_performed(
            _as_clients(clients),
            settings,
            task_id="t-00000000-0000-7000-8000-000000000001",
            accepted=False,
            reason="some reason",
        )

        emit_call = clients.clawhip_bridge.call_tool.call_args
        args = emit_call[1]["arguments"]
        assert args["caller_trace_id"] == expected
        assert is_valid_trace_id(args["caller_trace_id"]) is True


# ---------------------------------------------------------------------------
# Story 9.6 review pass-1 M6 — triple-equality byte-identity test (AC9)
# ---------------------------------------------------------------------------


class TestTriEqualByteIdentity:
    """AC9: a single test that asserts the same trace_id literally appears on
    all three surfaces — argv ``--trace-id`` value, env ``OMB_TRACE_ID``, and
    MCP ``caller_trace_id`` — for one ``WorkerSettings`` instance."""

    @pytest.mark.asyncio
    async def test_trace_id_byte_identical_across_argv_env_and_mcp_call(
        self,
        tmp_path: Path,
    ) -> None:
        from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner

        tid = new_uuid7()
        settings = WorkerSettings(
            anthropic_api_key="sk-test",
            claude_command="claude",
            claude_max_turns=0,
            claude_output_format="stream-json",
            trace_id=tid,
            # H2 — flag-gating opt-in so argv contains --trace-id <value>.
            emit_trace_id_flag=True,
        )

        # Build runner — exposes argv via _build_args and env via _spawn.
        runner = ClaudeCodeRunner(settings)
        args = runner._build_args("hi")
        idx = args.index("--trace-id")
        argv_tid = args[idx + 1]

        # Capture env from a faked subprocess_exec.
        captured_env: dict[str, str] = {}

        async def _fake_exec(*a: Any, **kw: Any) -> Any:
            captured_env.update(kw.get("env", {}))
            proc = AsyncMock(spec=asyncio.subprocess.Process)
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await runner._spawn("prompt", tmp_path)
        env_tid = captured_env["OMB_TRACE_ID"]

        # Emit a single MCP call and capture caller_trace_id from arguments.
        clients = _FakeClients()
        from worker_wrapper.app.main import _emit_tier3_performed

        captured_mcp: dict[str, object] = {}

        async def capture(name: str, arguments: dict[str, object]) -> str:
            captured_mcp.update(arguments)
            return "evt-1"

        clients.clawhip_bridge.call_tool = AsyncMock(side_effect=capture)
        await _emit_tier3_performed(
            _as_clients(clients),
            settings,
            task_id="t-00000000-0000-7000-8000-000000000001",
            accepted=True,
        )
        mcp_tid = captured_mcp["caller_trace_id"]

        # Single triple-equality assertion per spec M6.
        assert argv_tid == env_tid == mcp_tid == tid


# ---------------------------------------------------------------------------
# Story 9.6 review pass-1 H2 — --trace-id flag gating
# ---------------------------------------------------------------------------


class TestTraceIdFlagGating:
    """H2: the ``--trace-id`` CLI flag is absent unless
    ``emit_trace_id_flag`` is enabled."""

    def test_flag_absent_by_default(self, tmp_path: Path) -> None:
        from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner

        # Default settings (emit_trace_id_flag=False).
        settings = WorkerSettings(
            anthropic_api_key="sk-test",
            claude_command="claude",
            claude_output_format="stream-json",
            trace_id=new_uuid7(),
        )
        runner = ClaudeCodeRunner(settings)
        args = runner._build_args("hi")
        assert "--trace-id" not in args

    def test_flag_present_when_enabled(self, tmp_path: Path) -> None:
        from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner

        tid = new_uuid7()
        settings = WorkerSettings(
            anthropic_api_key="sk-test",
            claude_command="claude",
            claude_output_format="stream-json",
            trace_id=tid,
            emit_trace_id_flag=True,
        )
        runner = ClaudeCodeRunner(settings)
        args = runner._build_args("hi")
        assert "--trace-id" in args
        assert args[args.index("--trace-id") + 1] == tid


# ---------------------------------------------------------------------------
# Story 12.3a Phase 2 — budget supervisor override-intercepted vs terminated
# ---------------------------------------------------------------------------


def _find_enforcement_emit(clients: _FakeClients) -> dict[str, Any]:
    """Return the payload dict of the single ``task.budget_enforcement_triggered``
    emit_event call recorded on the fake clawhip_bridge."""
    matches: list[dict[str, Any]] = []
    for call in clients.clawhip_bridge.call_tool.await_args_list:
        # _call_tool_best_effort calls session.call_tool(tool_name, arguments=...)
        if call.args and call.args[0] == "emit_event":
            arguments = call.kwargs["arguments"]
            if arguments.get("type") == "task.budget_enforcement_triggered":
                matches.append(arguments["payload"])
    assert len(matches) == 1, f"expected exactly one enforcement emit, got {len(matches)}"
    return matches[0]


class TestRunTaskBudgetOverrideIntercepted:
    """Story 12.3a Phase 2 — override arrives in the grace window: the
    subprocess SURVIVES, NO TASK_FAILED, audit records the prevented
    enforcement with ``action_taken="override_intercepted"`` +
    ``post_trigger_transition="awaiting_approval"``."""

    @pytest.mark.asyncio
    async def test_override_intercepted_falls_through_to_completion(self, tmp_path: Path) -> None:
        from worker_wrapper.domain.budget_supervisor import BudgetSupervisorResult

        settings = _make_settings(tmp_path, event_log_dir=str(tmp_path / "logs"))
        clients = _FakeClients()

        # Runner completes normally (no Tier-3) — the subprocess survived the
        # budget breach because the override landed in the grace window.
        result = ClaudeCodeResult(
            exit_code=0,
            session_id="s-budget-override",
            events=[
                ExtractedEvent(event_type="file.edited", tool_name="Write", tool_input={}),
            ],
        )

        override_result = BudgetSupervisorResult(
            triggered=True,
            override_received=True,
            event_id="evt-budget-1",
            tokens_used=1500,
            token_limit=1000,
            step=3,
            detection_latency_s=0.4,
        )

        async def _fake_supervisor(*_args: Any, **_kwargs: Any) -> BudgetSupervisorResult:
            return override_result

        with (
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
            patch(
                "worker_wrapper.app.main.watch_for_budget_exceeded",
                side_effect=_fake_supervisor,
            ),
        ):
            mock_runner.return_value.run = AsyncMock(return_value=result)
            await run_task(
                _as_clients(clients),
                settings,
                "do stuff under budget override",
                tmp_path,
            )

        # FALL-THROUGH: task COMPLETED (not failed) — the subprocess survived.
        state_file = tmp_path / ".lifecycle-state.json"
        data = json.loads(state_file.read_text())
        assert data["state"] == "completed"

        # Audit event truthfully records the prevented enforcement.
        payload = _find_enforcement_emit(clients)
        assert payload["action_taken"] == "override_intercepted"
        assert payload["post_trigger_transition"] == "awaiting_approval"
        assert payload["budget_threshold"] == 1000
        assert payload["actual_spend"] == 1500
        assert payload["step"] == 3


class TestRunTaskBudgetTerminated:
    """Story 12.3a Phase 2 — no override in the window: the subprocess is
    terminated, the FSM is driven to FAILED, and the audit records
    ``action_taken="subprocess_terminated"`` +
    ``post_trigger_transition="failed"`` (even under an awaiting_approval
    policy — audit must match the actual FSM outcome)."""

    @pytest.mark.asyncio
    async def test_terminated_drives_failed_with_post_transition_failed(
        self, tmp_path: Path
    ) -> None:
        from worker_wrapper.domain.budget_supervisor import BudgetSupervisorResult

        # Policy is awaiting_approval, but NO override arrived — fail-closed.
        settings = WorkerSettings(
            task_id="t-00000000-0000-7000-8000-000000000001",
            worktree_path=str(tmp_path),
            event_log_dir=str(tmp_path / "logs"),
            default_budget_action="awaiting_approval",
        )
        clients = _FakeClients()

        # Runner raises (the SIGTERM cascade) — typical budget-enforcement case.
        terminated_result = BudgetSupervisorResult(
            triggered=True,
            override_received=False,
            event_id="evt-budget-2",
            tokens_used=2000,
            token_limit=1000,
            step=5,
            detection_latency_s=0.3,
            termination_latency_s=0.2,
            termination_method="sigterm",
        )

        async def _fake_supervisor(*_args: Any, **_kwargs: Any) -> BudgetSupervisorResult:
            return terminated_result

        with (
            patch("worker_wrapper.app.main.get_runtime_adapter") as mock_runner,
            patch(
                "worker_wrapper.app.main.watch_for_budget_exceeded",
                side_effect=_fake_supervisor,
            ),
        ):
            mock_runner.return_value.run = AsyncMock(
                side_effect=BrokenPipeError("subprocess SIGTERMed mid-stream")
            )
            await run_task(
                _as_clients(clients),
                settings,
                "do stuff over budget",
                tmp_path,
            )

        # Terminated path drives TASK_FAILED.
        state_file = tmp_path / ".lifecycle-state.json"
        data = json.loads(state_file.read_text())
        assert data["state"] == "failed"

        # Audit event matches the actual FSM outcome.
        payload = _find_enforcement_emit(clients)
        assert payload["action_taken"] == "subprocess_terminated"
        assert payload["post_trigger_transition"] == "failed"
        assert payload["budget_threshold"] == 1000
        assert payload["actual_spend"] == 2000
        assert payload["step"] == 5
