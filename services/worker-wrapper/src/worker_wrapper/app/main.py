"""Session lifecycle — start / heartbeat / finish (Stories 5.2, 5.3).

Coordinates session state with two MCP servers:

* **clawhip-bridge** — typed event emission (``emit_event`` tool).
* **session-registry** — session state RPC (``session.register``,
  ``session.heartbeat``, ``session.close``).  Currently a stub; calls
  are best-effort.

Story 5.3 adds worktree lock acquisition (not best-effort — prevents
worker start if worktree is locked) and release (best-effort during
shutdown).

Story 6.7 adds ``run_task`` — the approval-gated task execution driver
that wires LifecycleManager, ApprovalWaiter, and event emission into
the session lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import structlog
from events.clock import SystemClock
from events.payloads import (
    SessionFinishedPayload,
    SessionHeartbeatPayload,
    SessionStartedPayload,
    TaskApprovalRequestedPayload,
    Tier3ActionPerformedPayload,
)
from mcp import ClientSession

from worker_wrapper.adapters.approval_waiter import ApprovalWaiter
from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner
from worker_wrapper.adapters.lifecycle_manager import LifecycleManager
from worker_wrapper.adapters.mcp_clients import MCPClientGroup
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.approval_gate import needs_approval
from worker_wrapper.domain.lifecycle import LifecycleEvent, LifecycleFSM, WorkerState
from worker_wrapper.domain.worktree_lock import acquire_lock, release_lock

_MCP_CALL_TIMEOUT: float = 10.0
_CLAMP_FLOOR: float = 1.0


def _clamp_timeout(interval_s: float) -> float:
    """Clamp MCP call timeout to at most half the heartbeat interval, minimum 1s."""
    return max(_CLAMP_FLOOR, min(_MCP_CALL_TIMEOUT, interval_s * 0.5))


async def _call_tool_best_effort(
    session: ClientSession | None,
    tool_name: str,
    arguments: dict[str, object],
    *,
    label: str,
    timeout: float = _MCP_CALL_TIMEOUT,
) -> None:
    """Call an MCP tool; log and swallow any ``Exception``.

    ``BaseException`` subclasses (``CancelledError``, ``KeyboardInterrupt``,
    ``SystemExit``) propagate — they must not be silently swallowed.
    """
    log = structlog.get_logger(__name__)
    if session is None:
        log.warning("mcp_tool_skipped_no_session", label=label, tool=tool_name)
        return
    try:
        await asyncio.wait_for(
            session.call_tool(tool_name, arguments=arguments),
            timeout=timeout,
        )
    except TimeoutError:
        log.error(
            "mcp_tool_call_timeout",
            label=label,
            tool=tool_name,
            timeout=timeout,
            _hint="session may be in inconsistent state — MCP stdio may be corrupted",
        )
    except Exception:
        log.warning("mcp_tool_call_failed", label=label, tool=tool_name, exc_info=True)


async def start_session(
    clients: MCPClientGroup,
    settings: WorkerSettings,
) -> tuple[str, str]:
    """Emit ``session.started``, acquire worktree lock, call ``session.register``.

    Returns ``(session_id, worker_id)`` for use by the heartbeat loop and
    finish_session.

    Order: session.register → acquire_lock → emit_event.
    Lock acquisition raises :class:`WorktreeLockHeld` if the worktree is
    already locked — this prevents the session from starting (by design,
    FR27).
    """
    log = structlog.get_logger(__name__)
    session_id = settings.resolve_session_id()
    worker_id = settings.resolve_worker_id()
    task_id = settings.resolve_task_id()

    started = SessionStartedPayload(
        session_id=session_id,
        worker_id=worker_id,
        task_id=task_id,
    )

    # Register with session-registry BEFORE acquiring the lock so that
    # the registry is aware of the session before the worker claims a
    # worktree.
    reg_args: dict[str, object] = {
        "session_id": session_id,
        "worker_id": worker_id,
    }
    if started.task_id is not None:
        reg_args["task_id"] = started.task_id
    await _call_tool_best_effort(
        clients.session_registry,
        "session.register",
        reg_args,
        label="session_register",
    )

    # Story 5.3 — acquire worktree lock (raises WorktreeLockHeld on
    # contention; no-op if worktree_path is empty).  Run off-thread to
    # avoid blocking the event loop on filesystem I/O.
    if settings.worktree_path:
        await asyncio.to_thread(
            acquire_lock,
            Path(settings.worktree_path),
            session_id,
            worker_id,
        )

    try:
        await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {"type": "session.started", "payload": started.model_dump()},
            label="emit_session_started",
        )
    except BaseException:
        # Lock was acquired but emit failed critically — release the lock
        # so it is not orphaned.  Only BaseException (not plain Exception)
        # reaches here because _call_tool_best_effort swallows Exception.
        if settings.worktree_path:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    release_lock,
                    Path(settings.worktree_path),
                    session_id,
                )
        raise

    log.info(
        "session_started",
        session_id=session_id,
        worker_id=worker_id,
        task_id=started.task_id,
    )
    return session_id, worker_id


async def heartbeat_loop(
    clients: MCPClientGroup,
    settings: WorkerSettings,
    session_id: str,
    stop_event: asyncio.Event,
) -> None:
    """Periodic ``session.heartbeat`` event + MCP tool call (AC-2, AC-5).

    Exits when ``stop_event`` is set.
    """
    log = structlog.get_logger(__name__)
    log.info("heartbeat_loop_started", interval_s=settings.heartbeat_interval_s)
    mcp_timeout = _clamp_timeout(settings.heartbeat_interval_s)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.heartbeat_interval_s,
            )
            return  # stop_event was set
        except TimeoutError:
            pass  # interval elapsed — emit heartbeat

        hb = SessionHeartbeatPayload(session_id=session_id)
        await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {"type": "session.heartbeat", "payload": hb.model_dump()},
            label="emit_session_heartbeat",
            timeout=mcp_timeout,
        )
        await _call_tool_best_effort(
            clients.session_registry,
            "session.heartbeat",
            {"session_id": session_id},
            label="session_heartbeat_mcp",
            timeout=mcp_timeout,
        )
        log.debug("heartbeat_emitted", session_id=session_id)

    log.info("heartbeat_loop_stopped", session_id=session_id)


async def finish_session(
    clients: MCPClientGroup,
    session_id: str,
    worker_id: str,
    worktree_path: str = "",
) -> None:
    """Emit ``session.finished``, release worktree lock, call ``session.close``.

    Lock release is best-effort (catches ``Exception``, logs warning).
    """
    log = structlog.get_logger(__name__)

    fin = SessionFinishedPayload(session_id=session_id)
    await _call_tool_best_effort(
        clients.clawhip_bridge,
        "emit_event",
        {"type": "session.finished", "payload": fin.model_dump()},
        label="emit_session_finished",
    )

    # Story 5.3 — release worktree lock (best-effort, off-thread).
    if worktree_path:
        try:
            await asyncio.to_thread(release_lock, Path(worktree_path), session_id)
        except Exception:
            log.warning(
                "worktree_lock_release_failed",
                worktree_path=worktree_path,
                session_id=session_id,
                exc_info=True,
            )

    await _call_tool_best_effort(
        clients.session_registry,
        "session.close",
        {"session_id": session_id, "worker_id": worker_id},
        label="session_close",
    )

    log.info("session_finished", session_id=session_id, worker_id=worker_id)


async def run_task(
    clients: MCPClientGroup,
    settings: WorkerSettings,
    prompt: str,
    worktree_path: Path,
) -> None:
    """Approval-gated task execution driver (Story 6.7, AC-2 through AC-6).

    Runs Claude Code, detects Tier-3 actions (git push), enters the
    approval-wait state, and resumes on approval or fails on rejection.
    Emits ``task.approval_requested``, ``tier3.action_performed`` as needed.
    """
    log = structlog.get_logger(__name__)
    task_id = settings.resolve_task_id()
    if task_id is None:
        raise ValueError("task_id is required for run_task")

    state_path = worktree_path / ".lifecycle-state.json"

    async def _emit_event(event_type: str, payload: dict) -> str:
        try:
            result = await clients.clawhip_bridge.call_tool(
                "emit_event",
                {"type": event_type, "payload": payload},
            )
            return str(result)
        except Exception:
            log.warning(
                "emit_event_failed",
                emit_type=event_type,
                exc_info=True,
            )
            return ""

    async def _gated_action() -> None:
        """Execute git push + PR draft (Tier-3 gated action).

        The actual git push was already attempted by Claude Code — the
        gated action here is a placeholder for PR draft creation. In
        production, this would use GitHubClient.create_pr_draft with
        owner/repo/branch resolved from the worktree's git state.

        # TODO(future-story): implement real PR draft creation via
        # GitHubClient, resolving owner/repo/head from worktree git state.
        """
        log.warning(
            "gated_action_placeholder",
            task_id=task_id,
            _hint="gated action is a no-op placeholder — PR draft creation not yet implemented",
        )

    # Try to restore from a previous run (restart recovery, 5.17b).
    mgr = LifecycleManager.restore_from(
        state_path=state_path,
        emit_event=_emit_event,
        gated_action=_gated_action,
    )
    if mgr is not None and mgr.current_state == WorkerState.AWAITING_APPROVAL:
        # Approval may have arrived during downtime — check immediately.
        log.info("restored_awaiting_approval", task_id=task_id)
        await _handle_pending_approval(
            mgr, clients, settings, task_id,
        )
        return
    if mgr is not None:
        # Non-terminal state found on restart — the previous run crashed.
        # Transition to TASK_FAILED and remove stale sidecar so a fresh run
        # can proceed (otherwise subsequent run_task calls hit this path and
        # return immediately, orphaning the task permanently).
        log.warning(
            "restored_non_awaiting_state",
            task_id=task_id,
            state=mgr.current_state.value,
        )
        await mgr.handle_event(LifecycleEvent.TASK_FAILED)
        state_path.unlink(missing_ok=True)
        return

    # Fresh run — create LifecycleManager and run Claude Code.
    # TODO(future-story): instantiate IdempotencyCacheStore and pass
    # idempotency_cache= below for AC-5 exactly-once enforcement.
    mgr = LifecycleManager(
        fsm=LifecycleFSM(),
        state_path=state_path,
        task_id=task_id,
        emit_event=_emit_event,
        gated_action=_gated_action,
    )

    runner = ClaudeCodeRunner(settings)
    result = await runner.run(prompt, worktree_path)

    push_event = needs_approval(result.events)
    if push_event is None:
        # Normal completion — no Tier-3 actions detected.
        await mgr.handle_event(LifecycleEvent.TASK_COMPLETED)
        log.info(
            "task_completed_no_approval",
            task_id=task_id,
            events=len(result.events),
        )
        return

    # Validate event_log_dir BEFORE entering approval state — if it's not
    # configured the task cannot complete the approval workflow.
    if not settings.event_log_dir:
        log.error("event_log_dir_not_configured_pre_check", task_id=task_id)
        await mgr.handle_event(LifecycleEvent.TASK_FAILED)
        return

    # Tier-3 action detected — enter approval gate.
    log.info("tier3_detected", task_id=task_id, event_type=push_event.event_type)
    await mgr.handle_event(LifecycleEvent.TASK_AWAITING_APPROVAL)

    # Emit task.approval_requested (AC-2).
    # TODO(future-story): populate diff_summary from git diff --stat for
    # richer operator context in the Telegram approval renderer.
    approval_payload = TaskApprovalRequestedPayload(
        task_id=task_id,
        action="git_push",
        justification=f"Claude Code attempted: {push_event.tool_input.get('command', 'git push')}",
    )
    emit_result = await _emit_event(
        "task.approval_requested",
        approval_payload.model_dump(),
    )
    if not emit_result:
        # Emission failed — operator will never see the request, so the
        # approval workflow cannot complete.  Fail the task immediately
        # rather than polling for an approval that will never arrive.
        log.error("approval_request_emission_failed", task_id=task_id)
        await mgr.handle_event(LifecycleEvent.TASK_FAILED)
        return

    # Wait for approval (AC-3).
    await _handle_pending_approval(
        mgr, clients, settings, task_id,
    )


async def _handle_pending_approval(
    mgr: LifecycleManager,
    clients: MCPClientGroup,
    settings: WorkerSettings,
    task_id: str,
) -> None:
    """Poll for approval, then execute or fail the gated action."""
    log = structlog.get_logger(__name__)

    if mgr.current_state != WorkerState.AWAITING_APPROVAL:
        log.warning(
            "handle_approval_wrong_state",
            task_id=task_id,
            state=mgr.current_state.value,
        )
        return

    event_log_dir = Path(settings.event_log_dir) if settings.event_log_dir else None
    if event_log_dir is None:
        log.error("event_log_dir_not_configured", task_id=task_id)
        await mgr.handle_event(LifecycleEvent.TASK_FAILED)
        await _emit_tier3_performed(
            clients, task_id, accepted=False,
            reason="event_log_dir not configured",
        )
        return

    waiter = ApprovalWaiter(
        event_log_dir=event_log_dir,
        clock=SystemClock(),
        poll_interval_s=settings.approval_poll_interval_s,
        timeout_s=settings.approval_timeout_s,
    )

    try:
        approval = await waiter.wait_for_approval(task_id)
    except TimeoutError:
        await mgr.handle_event(LifecycleEvent.TASK_FAILED)
        await _emit_tier3_performed(
            clients, task_id, accepted=False,
            reason=f"Approval timed out after {settings.approval_timeout_s}s",
        )
        log.error("approval_timeout", task_id=task_id)
        return

    if not approval.granted:
        # Rejection path (AC-3).
        await mgr.handle_event(LifecycleEvent.APPROVAL_REJECTED)
        await _emit_tier3_performed(
            clients, task_id, accepted=False,
            reason=approval.reason or "operator rejected",
        )
        log.info("approval_rejected", task_id=task_id)
        return

    # Approval granted — execute gated action (AC-5).
    try:
        await mgr.handle_approval(idempotency_key=approval.idempotency_key or task_id)
    except Exception:
        log.exception("handle_approval_failed", task_id=task_id)
        await _emit_tier3_performed(
            clients, task_id, accepted=False,
            approval_event_id=approval.event_id,
            reason="gated action execution failed",
        )
        return
    await _emit_tier3_performed(
        clients, task_id, accepted=True,
        approval_event_id=approval.event_id,
    )
    log.info("tier3_action_performed", task_id=task_id)


async def _emit_tier3_performed(
    clients: MCPClientGroup,
    task_id: str,
    *,
    accepted: bool,
    approval_event_id: str = "",
    reason: str = "",
) -> None:
    """Emit ``tier3.action_performed`` via clawhip-bridge (AC-6)."""
    payload = Tier3ActionPerformedPayload(
        task_id=task_id,
        action="git_push",
        accepted=accepted,
        approval_event_id=approval_event_id or None,
        reason=reason or None,
    )
    await _call_tool_best_effort(
        clients.clawhip_bridge,
        "emit_event",
        {"type": "tier3.action_performed", "payload": payload.model_dump()},
        label="emit_tier3_action_performed",
    )
