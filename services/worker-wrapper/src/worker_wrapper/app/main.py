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

Story 9.6 review pass-1 H1 / H3 / H7 / M5: every session-lifecycle and
tier3 emission now carries the worker's resolved ``trace_id`` as
``caller_trace_id``; the API is uniform — every helper that emits MCP
calls takes the same ``settings: WorkerSettings`` parameter and resolves
the trace_id internally (no more defensive ``or new_uuid7()`` forks).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path
from typing import Any, Protocol

import structlog
from events.clock import SystemClock
from events.payloads import (
    DiffSummary,
    SessionFinishedPayload,
    SessionHeartbeatPayload,
    SessionStartedPayload,
    TaskApprovalRequestedPayload,
    TaskBudgetEnforcementTriggeredPayload,
    TaskLicenseFlaggedPayload,
    Tier3ActionPerformedPayload,
)
from idempotency import IdempotencyCacheStore, create_idempotency_schema
from mcp import ClientSession

# secret_hygiene is a workspace package; imported here for license scan.
from secret_hygiene.license_scan import LicenseFinding, scan_files_for_licenses
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from worker_wrapper.adapters.approval_waiter import ApprovalWaiter
from worker_wrapper.adapters.lifecycle_manager import LifecycleManager
from worker_wrapper.adapters.mcp_clients import MCPClientGroup
from worker_wrapper.adapters.runtime_factory import get_runtime_adapter
from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.approval_gate import needs_approval
from worker_wrapper.domain.budget_supervisor import (
    _BudgetSupervisorResult,
    watch_for_budget_exceeded,
)
from worker_wrapper.domain.lifecycle import LifecycleEvent, LifecycleFSM, WorkerState
from worker_wrapper.domain.worktree_lock import acquire_lock, release_lock

_MCP_CALL_TIMEOUT: float = 10.0
_CLAMP_FLOOR: float = 1.0

# Story 9.6 review pass-1 H8 — bounded preview for log injection avoidance.
_TRACE_ID_PREVIEW_LEN: int = 80


class BudgetExceededDuringHandoffError(Exception):
    """Runtime handoff rejected — cumulative budget exceeded (P5-I3)."""


class _MCPClients(Protocol):
    """Structural view of the MCP client trio used by the task driver.

    ``MCPClientGroup`` satisfies this protocol nominally; declaring the
    dependency structurally lets test doubles substitute without inheriting
    from the concrete adapter. Type-only — no runtime behaviour change.
    """

    task_registry: ClientSession | None
    session_registry: ClientSession | None
    clawhip_bridge: ClientSession | None


def _clamp_timeout(interval_s: float) -> float:
    """Clamp MCP call timeout to at most half the heartbeat interval, minimum 1s."""
    return max(_CLAMP_FLOOR, min(_MCP_CALL_TIMEOUT, interval_s * 0.5))


def _accumulate_runtime_tokens(
    result: Any,
    runtime_name: str,
    tokens_consumed_by_runtime: dict[str, int],
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Phase 5 / FR94 — accumulate per-runtime token usage after runner completes.

    Both ``ClaudeCodeResult`` and ``CodexResult`` carry token fields. We
    extract the total token count from whatever fields are available and
    accumulate it under the active runtime's name. The budget limit applies
    to the cumulative total across all runtimes (P5-I3).
    """
    if result is None:
        return
    # ClaudeCodeResult has num_turns (approximate token proxy) and cost_usd.
    # CodexResult has input_tokens + output_tokens.
    tokens = 0
    if hasattr(result, "input_tokens") and hasattr(result, "output_tokens"):
        # CodexResult — direct token counts
        tokens = (getattr(result, "input_tokens", 0) or 0) + (
            getattr(result, "output_tokens", 0) or 0
        )
    elif hasattr(result, "cost_usd"):
        # ClaudeCodeResult — use num_turns as a rough proxy for now.
        # The budget supervisor handles the actual enforcement via
        # task.budget_exceeded events; this tracking is for attribution.
        tokens = getattr(result, "num_turns", 0) or 0
    if tokens > 0:
        tokens_consumed_by_runtime[runtime_name] = (
            tokens_consumed_by_runtime.get(runtime_name, 0) + tokens
        )
        log.debug(
            "runtime_tokens_accumulated",
            runtime=runtime_name,
            tokens_this_run=tokens,
            total=tokens_consumed_by_runtime[runtime_name],
        )


def _coerce_enforcement_fields(
    budget_result: _BudgetSupervisorResult,
    task_id: str,
    log: structlog.stdlib.BoundLogger,
) -> tuple[int, int, int]:
    """Null-coalesce + audit-log the step / threshold / spend enforcement fields.

    Story 12.3c AC6 (carried-over code-reviewer MEDIUM/DRY): both the
    override-intercepted branch and the terminated branch of ``run_task`` need
    the SAME defensive coalescing for ``TaskBudgetEnforcementTriggeredPayload``
    construction — ``step`` defaults to the schema floor (1, ``Field(ge=1)``)
    when the producer omitted it, and ``token_limit`` / ``tokens_used`` default
    to 1 (the smallest value passing the payload's ``gt=0`` validation) so a
    missing optional field never DROPS the whole FR67 audit record. Each
    coalesced default emits the original warning/error log so operators still
    see the missing-field signal. Returns ``(step, threshold, spend)``.

    No behavior change to the reaction leg — this only de-duplicates the two
    identical ~16-line blocks that previously diverged by copy-paste risk.
    """
    step_value = budget_result.step if budget_result.step is not None else 1
    if budget_result.step is None:
        log.warning(
            "budget_enforcement_event_step_missing_defaulted",
            task_id=task_id,
            event_id=budget_result.event_id,
        )
    threshold_value = budget_result.token_limit if budget_result.token_limit is not None else 1
    spend_value = budget_result.tokens_used if budget_result.tokens_used is not None else 1
    if budget_result.token_limit is None or budget_result.tokens_used is None:
        log.error(
            "budget_enforcement_event_spend_fields_missing",
            task_id=task_id,
            token_limit=budget_result.token_limit,
            tokens_used=budget_result.tokens_used,
        )
    return step_value, threshold_value, spend_value


async def _call_tool_best_effort(
    session: ClientSession | None,
    tool_name: str,
    arguments: dict[str, object],
    *,
    label: str,
    timeout: float = _MCP_CALL_TIMEOUT,
    return_result: bool = False,
) -> object | None:
    """Call an MCP tool; log and swallow any ``Exception``.

    ``BaseException`` subclasses (``CancelledError``, ``KeyboardInterrupt``,
    ``SystemExit``) propagate — they must not be silently swallowed.

    Story 9.6 review pass-1 H7 / review pass-2 PH3: ``ValueError`` is split out
    from the generic ``Exception`` catch ONLY when the exception message
    actually mentions ``"trace_id"`` (case-insensitive); other ``ValueError``s
    (raised by the MCP SDK for unrelated transport reasons) fall through to
    the generic ``mcp_tool_call_failed`` branch.  This narrows the H7
    special-case to genuine Story 9.1 contract violations and stops benign
    transport errors from being misclassified.

    Review pass-2 PH3 (E6): if the caller_trace_id in the offending arguments
    is the worker's OWN resolved trace_id, the log records
    ``caller_trace_id_source="worker_resolved"`` and ONLY the first 8 chars of
    the value — operators are not misled into thinking the worker leaked a
    full opaque token.

    Review pass-2 PH4: ``return_result=True`` returns the awaited tool result
    so callers that need an event_id (run_task's ``_emit_event`` closure) can
    share this single best-effort helper instead of duplicating the H7 logic.

    Review pass-3 TH5 — audit:
    All ``return_result=False`` callers discard the return value entirely;
    the ``None`` they receive is indistinguishable from a swallowed failure
    BY DESIGN (best-effort semantics).  Callers that need to distinguish
    success-with-result from failure pass ``return_result=True`` and treat
    a falsy result as "no event id" (cf. ``run_task._emit_event``).
    """
    log = structlog.get_logger(__name__)
    if session is None:
        log.warning("mcp_tool_skipped_no_session", label=label, tool=tool_name)
        return None
    try:
        result = await asyncio.wait_for(
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
        return None
    except ValueError as exc:
        # Review pass-2 PH3 + pass-3 TH4 — only classify as a trace_id
        # contract violation when the exception message actually mentions
        # ``caller_trace_id`` (the specific Story 9.5 contract field name
        # used by all three MCP servers' ``validate_caller_trace_id``);
        # otherwise fall through to the generic best-effort handler.
        # TH4 narrowed from ``"trace_id"`` to ``"caller_trace_id"`` to avoid
        # over-matching benign errors that incidentally contain "trace_id".
        if "caller_trace_id" not in str(exc).lower():
            log.warning("mcp_tool_call_failed", label=label, tool=tool_name, exc_info=True)
            return None
        # Review pass-1 H7: receiving MCP server raises ``ValueError`` for
        # contract violations (e.g. ``caller_trace_id must match Story 9.1
        # contract``). Surface this as a distinct log event with a bounded,
        # escaped preview of the offending caller_trace_id so operators can
        # diagnose the caller without trusting the raw value in log lines.
        caller_trace_id = arguments.get("caller_trace_id")
        preview_src: str = ""
        if isinstance(caller_trace_id, str):
            preview_src = caller_trace_id[:_TRACE_ID_PREVIEW_LEN]
        elif caller_trace_id is not None:
            preview_src = str(caller_trace_id)[:_TRACE_ID_PREVIEW_LEN]
        log.warning(
            "mcp_tool_trace_id_invalid",
            label=label,
            tool=tool_name,
            # Review pass-1 H8 — repr() escapes CRLF / NULL / ANSI / ZWJ etc.
            caller_trace_id_preview=repr(preview_src),
            error=str(exc)[:200],
        )
        return None
    except Exception:
        log.warning("mcp_tool_call_failed", label=label, tool=tool_name, exc_info=True)
        return None
    return result if return_result else None


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

    Story 9.6 review pass-1 H1: ``session.register`` (session-registry MCP
    tool) now carries ``caller_trace_id`` per Story 9.5's mandatory-input
    contract — Story 9.5 made the field REQUIRED on all 3 MCP servers
    (clawhip-bridge, session-registry, task-registry).
    """
    log = structlog.get_logger(__name__)
    session_id = settings.resolve_session_id()
    worker_id = settings.resolve_worker_id()
    task_id = settings.resolve_task_id()
    # Story 9.6 / FR59 — resolve once, thread to every MCP emission.
    trace_id = settings.resolve_trace_id()

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
        # Story 9.6 review pass-1 H1 — required since Story 9.5.
        "caller_trace_id": trace_id,
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
            {
                "type": "session.started",
                "payload": started.model_dump(),
                "caller_trace_id": trace_id,  # Story 9.6 / FR59
            },
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

    Story 9.6 review pass-1 H1: ``session.heartbeat`` (session-registry MCP
    tool) now also carries ``caller_trace_id``.
    """
    log = structlog.get_logger(__name__)
    log.info("heartbeat_loop_started", interval_s=settings.heartbeat_interval_s)
    mcp_timeout = _clamp_timeout(settings.heartbeat_interval_s)
    # Story 9.6 / FR59 — resolve once for the lifetime of the heartbeat loop.
    trace_id = settings.resolve_trace_id()

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
            {
                "type": "session.heartbeat",
                "payload": hb.model_dump(),
                "caller_trace_id": trace_id,  # Story 9.6 / FR59
            },
            label="emit_session_heartbeat",
            timeout=mcp_timeout,
        )
        await _call_tool_best_effort(
            clients.session_registry,
            "session.heartbeat",
            {
                "session_id": session_id,
                # Story 9.6 review pass-1 H1 — required since Story 9.5.
                "caller_trace_id": trace_id,
            },
            label="session_heartbeat_mcp",
            timeout=mcp_timeout,
        )
        log.debug("heartbeat_emitted", session_id=session_id)

    log.info("heartbeat_loop_stopped", session_id=session_id)


async def finish_session(
    clients: MCPClientGroup,
    settings: WorkerSettings,
    session_id: str,
    worker_id: str,
    worktree_path: str = "",
) -> None:
    """Emit ``session.finished``, release worktree lock, call ``session.close``.

    Lock release is best-effort (catches ``Exception``, logs warning).

    Story 9.6 review pass-1 H3 / M5: the ``trace_id`` is resolved from
    ``settings`` so every session-lifecycle helper has the SAME signature
    (``settings`` everywhere — no more ``trace_id: str | None = None`` kwarg
    with a defensive ``or new_uuid7()`` fork). The local-policy trace_id is
    threaded as ``caller_trace_id`` on both clawhip-bridge ``session.finished``
    and session-registry ``session.close`` (review pass-1 H1).
    """
    log = structlog.get_logger(__name__)

    trace_id = settings.resolve_trace_id()

    fin = SessionFinishedPayload(session_id=session_id)
    await _call_tool_best_effort(
        clients.clawhip_bridge,
        "emit_event",
        {
            "type": "session.finished",
            "payload": fin.model_dump(),
            "caller_trace_id": trace_id,  # Story 9.6 / FR59
        },
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
        {
            "session_id": session_id,
            "worker_id": worker_id,
            # Story 9.6 review pass-1 H1 — required since Story 9.5.
            "caller_trace_id": trace_id,
        },
        label="session_close",
    )

    log.info("session_finished", session_id=session_id, worker_id=worker_id)


async def perform_runtime_handoff(
    current_adapter: Any,
    settings: WorkerSettings,
    target_runtime: str,
    trace_id: str,
    task_id: str,
    session_id: str,
    clients: Any,
    worktree_path: Path,
    tokens_consumed_by_runtime: dict[str, int] | None = None,
    budget_token_limit: int | None = None,
) -> Any:
    """Execute a runtime handoff — terminate current adapter, spawn target (FR92).

    Phase 5 handoff flow (ADR-0015 / Epic 28):
    1. Check budget — reject handoff if cumulative tokens exceed limit (P5-I3).
    2. Terminate current runtime adapter (SIGTERM -> grace -> SIGKILL).
    3. Emit ``task.runtime_handoff`` event with from/to runtime and trace_id (P5-I2).
    4. Spawn target runtime adapter via factory.
    5. Return the new adapter (caller is responsible for running it).

    The same trace_id spans both runtime segments (P5-I2).
    The worktree lock is NOT released during handoff (task owns it for entire lifecycle).
    """
    log = structlog.get_logger(__name__)
    source_runtime = current_adapter.runtime_name

    # Phase 5 / P5-I3 — reject handoff if cumulative budget exceeded.
    if tokens_consumed_by_runtime and budget_token_limit is not None:
        cumulative = sum(tokens_consumed_by_runtime.values())
        if cumulative >= budget_token_limit:
            log.error(
                "runtime_handoff_rejected_budget_exceeded",
                task_id=task_id,
                cumulative_tokens=cumulative,
                budget_limit=budget_token_limit,
            )
            raise BudgetExceededDuringHandoffError(
                f"Cumulative budget ({cumulative}) >= limit ({budget_token_limit}). "
                f"Handoff rejected — task must be terminated (P5-I3)."
            )

    # Step 1: terminate current adapter.
    log.info(
        "runtime_handoff_terminating_source",
        source_runtime=source_runtime,
        target_runtime=target_runtime,
        task_id=task_id,
    )
    termination = await current_adapter.terminate_with_grace(grace_period_s=5.0)
    log.info(
        "runtime_handoff_source_terminated",
        method=termination.method,
        exit_code=termination.exit_code,
    )

    # Step 2: emit task.runtime_handoff event (P5-I2 — same trace_id).
    new_session_id = settings.resolve_session_id()
    try:
        await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {
                "type": "task.runtime_handoff",
                "payload": {
                    "task_id": task_id,
                    "trace_id": trace_id,
                    "source_runtime": source_runtime,
                    "target_runtime": target_runtime,
                    "source_session_id": session_id,
                    "target_session_id": new_session_id,
                    "context_summary": "",
                },
                "caller_trace_id": trace_id,
            },
            label="emit_runtime_handoff",
        )
    except Exception:
        log.warning("runtime_handoff_event_emit_failed", task_id=task_id)

    # Step 3: spawn target runtime adapter.
    log.info(
        "runtime_handoff_spawning_target",
        target_runtime=target_runtime,
        task_id=task_id,
    )
    new_adapter = get_runtime_adapter(settings, runtime=target_runtime)
    return new_adapter


async def _get_diff_summary(worktree_path: Path) -> DiffSummary | None:
    """Return a ``DiffSummary`` from ``git diff --stat`` in *worktree_path*.

    Returns ``None`` if git is unavailable, the worktree is not a git repo,
    or the diff output cannot be parsed.  Never raises.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--stat",
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None

        text = stdout.decode().strip()
        if not text:
            return DiffSummary(files=0, insertions=0, deletions=0)

        # The last line of ``git diff --stat`` looks like:
        #   3 files changed, 42 insertions(+), 7 deletions(-)
        # Variants: "1 file changed, 5 insertions(+)" (no deletions),
        # or just "2 files changed" (no insertions/deletions).
        summary_line = text.split("\n")[-1]

        files_m = re.search(r"(\d+) files? changed", summary_line)
        ins_m = re.search(r"(\d+) insertion", summary_line)
        del_m = re.search(r"(\d+) deletion", summary_line)

        if files_m is None:
            return None

        return DiffSummary(
            files=int(files_m.group(1)),
            insertions=int(ins_m.group(1)) if ins_m else 0,
            deletions=int(del_m.group(1)) if del_m else 0,
        )
    except Exception:
        return None


async def run_task(
    clients: _MCPClients,
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

    # Story 9.6 / FR59 — resolve once and thread to every clawhip-bridge
    # emission inside this task execution (LifecycleManager callback,
    # tier3 emitter, license-flagged payload, approval-requested envelope).
    trace_id = settings.resolve_trace_id()

    state_path = worktree_path / ".lifecycle-state.json"

    async def _emit_event(event_type: str, payload: dict[str, Any]) -> str:
        # Review pass-2 PH4 — delegate to ``_call_tool_best_effort`` so the
        # H7 ValueError special-case (Story 9.1 trace_id contract violation)
        # applies uniformly across run_task's emission sites instead of the
        # closure's generic ``except Exception`` swallow.
        result = await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {
                "type": event_type,
                "payload": payload,
                "caller_trace_id": trace_id,  # Story 9.6 / FR59
            },
            label=f"emit_event_{event_type}",
            return_result=True,
        )
        return str(result) if result is not None else ""

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
            mgr,
            clients,
            settings,
            task_id,
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
    cache_db_path = worktree_path / ".idempotency-cache.sqlite3"
    cache_engine = create_async_engine(
        f"sqlite+aiosqlite:///{cache_db_path}",
    )
    await create_idempotency_schema(cache_engine)
    cache_session_maker = async_sessionmaker(cache_engine, expire_on_commit=False)
    idempotency_cache = IdempotencyCacheStore(
        session_maker=cache_session_maker,
        clock=SystemClock(),
    )
    mgr = LifecycleManager(
        fsm=LifecycleFSM(),
        state_path=state_path,
        task_id=task_id,
        emit_event=_emit_event,
        gated_action=_gated_action,
        idempotency_cache=idempotency_cache,
    )

    # Phase 5 / FR89 — select runtime adapter via factory. Per-task override
    # is handled by passing the preferred_runtime through get_runtime_adapter.
    # Default: "claude-code" (backward-compatible with Phase 4 behavior).
    runner = get_runtime_adapter(settings)

    # Phase 5 / FR94 — per-runtime token accounting. The budget limit applies
    # to the cumulative total across all runtimes (P5-I3). When a handoff
    # occurs, the new runtime's tokens accumulate on top of the prior one.
    tokens_consumed_by_runtime: dict[str, int] = {}

    # Story 12.1 — spawn the budget supervisor as a shadow asyncio task that
    # subscribes to ``task.budget_exceeded`` events for this task_id and
    # SIGTERMs ``runner`` if the producer (orchestrator-adapter) signals a
    # budget breach. The shadow is retired cleanly on natural runner exit via
    # ``budget_cancel`` (Decision D4 — runner-first cancel ordering).
    #
    # Skip the supervisor when ``event_log_dir`` is not configured — in that
    # case no event log exists for the supervisor to tail. Production workers
    # always configure ``event_log_dir``; this fallback preserves backwards
    # compatibility for tests / minimal-config runs.
    budget_supervisor_task: asyncio.Task[_BudgetSupervisorResult] | None = None
    budget_cancel = asyncio.Event()
    # Grace period MUST agree with the join timeout below — the supervisor's
    # callback can spend up to ``grace_period_s`` inside ``terminate_with_grace``
    # before returning; the join must wait at least that long (PP6).
    budget_grace_period_s: float = 5.0
    if settings.event_log_dir:
        budget_event_log_dir = Path(settings.event_log_dir)

        async def _terminate_for_budget() -> object:
            # Return the TerminationResult so the supervisor can propagate
            # ``method`` into its BudgetSupervisorResult (PP21).
            term_result = await runner.terminate_with_grace(
                grace_period_s=budget_grace_period_s,
            )
            log.info(
                "budget_termination",
                task_id=task_id,
                method=term_result.method,
                elapsed_s=term_result.elapsed_s,
                exit_code=term_result.exit_code,
            )
            return term_result

        budget_supervisor_task = asyncio.create_task(
            watch_for_budget_exceeded(
                task_id=task_id,
                event_log_dir=budget_event_log_dir,
                terminate_callback=_terminate_for_budget,
                clock=SystemClock(),
                cancel_event=budget_cancel,
                # Story 12.3a Phase 2 — wire the grace-window state machine.
                # ``budget_action`` selects "failed" (immediate SIGTERM, today's
                # behavior, the default) vs "awaiting_approval" (open a bounded
                # override grace window). ``grace_window_s`` is the OVERRIDE
                # window the supervisor waits for an inbound budget override —
                # DISTINCT from ``budget_grace_period_s`` above, which is the
                # SIGTERM→SIGKILL escalation grace inside ``terminate_with_grace``.
                budget_action=settings.default_budget_action,
                grace_window_s=settings.budget_grace_window_s,
            ),
            name=f"budget-supervisor-{task_id}",
        )

    # PP1 (P0) — separate capture of any runner exception. Previously
    # ``budget_result`` was only declared inside the ``finally`` clause, so
    # an exception raised by ``runner.run()`` (BrokenPipeError when the
    # supervisor SIGTERMs Claude Code mid-stream, CancelledError, etc.)
    # propagated past the post-finally block and the ``triggered=True`` path
    # was never reached — the lifecycle FSM stayed in IN_PROGRESS and no
    # audit event was emitted. The runner-raised case is now captured
    # explicitly; budget-enforced paths run BEFORE any re-raise.
    runner_raised: BaseException | None = None
    result: Any = None  # Adapter-specific result (ClaudeCodeResult, CodexResult, etc.)
    budget_result: _BudgetSupervisorResult | None = None
    try:
        # PP4 — defensive ceiling on ``runner.run()``. If the supervisor
        # SIGKILLs the subprocess but the runner's stdout reader fails to
        # observe EOF (rare kernel/pipe-state edge), the await would hang
        # forever and wedge the worker. ``task_overall_timeout_s`` defaults
        # to 900s — far above ``claude_timeout_s`` so this only trips on
        # pathological hangs, never on healthy long runs.
        # PP24 — when the outer ceiling fires, ``wait_for`` cancels the
        # coroutine but does NOT kill the underlying subprocess. We kill it
        # here before re-raising so the worker doesn't leak an orphan
        # process consuming tokens until OS reap.
        try:
            result = await asyncio.wait_for(
                runner.run(prompt, worktree_path),
                timeout=settings.task_overall_timeout_s,
            )
            # Phase 5 / FR94 — accumulate per-runtime token usage after run.
            # Both ClaudeCodeResult and CodexResult carry token fields.
            _accumulate_runtime_tokens(
                result,
                runner.runtime_name,
                tokens_consumed_by_runtime,
                log,
            )
        except TimeoutError:
            log.error(
                "runner_overall_timeout_exceeded",
                task_id=task_id,
                timeout_s=settings.task_overall_timeout_s,
            )
            with contextlib.suppress(Exception):
                await runner.terminate_with_grace(grace_period_s=5.0)
            raise
    except asyncio.CancelledError:
        # PP25 — graceful shutdown path. ``CancelledError`` is a BaseException
        # (PEP 654 distinct from Exception in 3.11+) so the old
        # ``except BaseException`` capture would join the supervisor for up
        # to 7s before re-raising — turning a shutdown that should be
        # milliseconds into a 7s pause. Set cancel, do a tight 100ms join,
        # cancel the supervisor if still running, and re-raise immediately.
        log.info("run_task_cancelled", task_id=task_id)
        if budget_supervisor_task is not None:
            with contextlib.suppress(Exception):  # PP26 — defensive
                budget_cancel.set()
            with contextlib.suppress(Exception, asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(budget_supervisor_task, timeout=0.1)
            if not budget_supervisor_task.done():
                budget_supervisor_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(budget_supervisor_task, timeout=0.5)
        raise
    except Exception as exc:  # noqa: BLE001 — PP25 — narrowed from BaseException
        # PP25 — runner-side Exception capture. ``CancelledError`` no longer
        # routes here (handled above); ``KeyboardInterrupt`` / ``SystemExit``
        # propagate. The captured exception is re-raised after the
        # budget-enforced path is reachable (PP1).
        runner_raised = exc
    finally:
        # Decision D4: runner-first cancel — signal the supervisor to exit
        # cleanly, then join. PP6 — join timeout MUST exceed the supervisor's
        # grace period (the supervisor may be mid-``terminate_with_grace``
        # when the cancel fires). 7.0s = 5s grace + 2s buffer.
        # NOTE: this finally clause only runs on the normal-exit /
        # runner-Exception paths; the CancelledError branch above re-raises
        # before reaching here (PP25).
        if budget_supervisor_task is not None and not budget_supervisor_task.done():
            # PP26 — pathological event-loop teardown could raise from
            # ``Event.set()``; suppress so the original ``runner_raised``
            # is not masked by Python's finally-overrides-try semantic.
            with contextlib.suppress(Exception):
                budget_cancel.set()
            try:
                budget_result = await asyncio.wait_for(
                    budget_supervisor_task,
                    timeout=budget_grace_period_s + 2.0,
                )
            except (TimeoutError, asyncio.CancelledError) as join_exc:
                # PP25 — broader catch: TimeoutError covers the 7s ceiling;
                # CancelledError covers a stray cancel propagating up.
                log.warning(
                    "budget_supervisor_join_timeout_or_cancelled",
                    task_id=task_id,
                    exc_type=type(join_exc).__name__,
                )
                budget_supervisor_task.cancel()
                # PP30 — ``await supervisor_task`` after ``cancel()`` does
                # NOT interrupt the in-flight ``asyncio.to_thread`` worker.
                # Wrap with a hard 2s ceiling so a stuck scan can't wedge
                # the lifespan finally.
                with contextlib.suppress(asyncio.CancelledError, Exception, asyncio.TimeoutError):
                    await asyncio.wait_for(budget_supervisor_task, timeout=2.0)
            except Exception as supervisor_exc:  # noqa: BLE001 — PP9 isolation
                # Supervisor itself raised on join (e.g. a future-set
                # exception path). Log loudly but DO NOT clobber any
                # in-flight runner exception — the lifespan still owns
                # error reporting.
                log.error(
                    "budget_supervisor_join_raised",
                    task_id=task_id,
                    error_type=type(supervisor_exc).__name__,
                    error=str(supervisor_exc),
                )
                budget_result = None
        elif budget_supervisor_task is not None:
            # Task already done — just read its result.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                budget_result = budget_supervisor_task.result()

    if budget_result is not None and budget_result.triggered and budget_result.override_received:
        # Story 12.3a Phase 2 — OVERRIDE-INTERCEPTED path (FR68 headline).
        # A budget breach WAS observed but an operator budget override arrived
        # inside the grace window (only reachable when
        # ``default_budget_action == "awaiting_approval"``), so the supervisor
        # ABORTED this termination — the subprocess is STILL ALIVE and runs to
        # natural completion. We do NOT drive the FSM to FAILED and we do NOT
        # ``return``; we record the prevented-enforcement audit event and FALL
        # THROUGH to the normal-completion path below (re-raise of a real runner
        # error, or TASK_COMPLETED / approval gate) so the runner's natural
        # ``result`` is processed.
        #
        # SCOPE (3-lane review, critic MAJOR-1 — honest framing): this is a
        # ONE-SHOT REPRIEVE, not a re-enforced higher ceiling. 12.3a does NOT
        # re-spawn the supervisor with the override's ``new_limit`` and does NOT
        # re-couple the orchestrator-adapter BudgetTracker — so after this abort
        # the task runs WITHOUT further budget enforcement, bounded only by
        # ``task_overall_timeout_s`` (default 900s). Enforcing the new ceiling
        # going forward is deferred to Story 12.3c (tracked). The operator
        # explicitly chose ``--override budget`` (= "let this task exceed its
        # budget"), so finishing the run is the intended semantic for now.
        #
        # post_trigger_transition="awaiting_approval" is audit-truthful here:
        # the task was NOT failed — it parks for / continues under operator
        # approval (the 12.2 H1/H2 audit-integrity principle: the field MUST
        # match the actual FSM outcome).
        # Story 12.3c AC6 (DRY) — shared coalescing + missing-field audit logs.
        step_value, threshold_value, spend_value = _coerce_enforcement_fields(
            budget_result, task_id, log
        )
        override_payload = TaskBudgetEnforcementTriggeredPayload(
            task_id=task_id,
            budget_threshold=threshold_value,
            actual_spend=spend_value,
            action_taken="override_intercepted",
            post_trigger_transition="awaiting_approval",
            step=step_value,
            runtime=runner.runtime_name,
        )
        await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {
                "type": "task.budget_enforcement_triggered",
                "payload": override_payload.model_dump(),
                "caller_trace_id": settings.resolve_trace_id(),
            },
            label="emit_budget_override_intercepted",
        )
        log.info(
            "budget_override_intercepted",
            task_id=task_id,
            event_id=budget_result.event_id,
            detection_latency_s=budget_result.detection_latency_s,
        )
        # FALL THROUGH — no FSM transition, no return; the subprocess survived
        # so the normal-completion path (~below) processes the runner result.

    elif budget_result is not None and budget_result.triggered:
        # Story 12.1 — task was terminated by budget enforcement.
        # ``task.completed`` is NOT emitted; Story 12.2 will emit
        # ``task.budget_enforcement_triggered`` here (FR67) with the
        # detection / termination latency breakdown captured below.
        #
        # PP23 — if the supervisor's terminate_callback raised, the
        # subprocess may STILL BE ALIVE consuming tokens. Retry termination
        # directly via the runner adapter before transitioning the FSM. The
        # supervisor returns ``triggered=True`` AND ``enforcement_failed=True``
        # to signal this; the retry is best-effort (suppressed) — even on
        # failure we still transition the FSM because the budget decision
        # has been made.
        if budget_result.enforcement_failed:
            log.warning(
                "budget_enforcement_callback_failed_retrying",
                task_id=task_id,
                event_id=budget_result.event_id,
            )
            retry_method: str | None = None
            retry_exit_code: int | None = None
            try:
                retry_result = await runner.terminate_with_grace(
                    grace_period_s=5.0,
                )
                retry_method = retry_result.method
                retry_exit_code = retry_result.exit_code
            except Exception as retry_exc:  # noqa: BLE001 — best-effort retry
                log.error(
                    "budget_enforcement_retry_raised",
                    exc_info=retry_exc,
                    task_id=task_id,
                    event_id=budget_result.event_id,
                )
            else:
                log.info(
                    "budget_enforcement_retry_completed",
                    task_id=task_id,
                    event_id=budget_result.event_id,
                    method=retry_method,
                    exit_code=retry_exit_code,
                )
        #
        # PP1 — if the runner ALSO raised (the common case: the supervisor's
        # SIGTERM caused BrokenPipeError on the stdout reader), treat the
        # exception as the expected cascade of budget enforcement and log it
        # for forensics rather than re-raising.
        # PP37 — pass ``exc_info`` so the traceback is retained for
        # operator-side forensics; the prior string-only log discarded it.
        if runner_raised is not None:
            log.info(
                "runner_raised_after_budget_enforcement",
                exc_info=runner_raised,
                task_id=task_id,
                exc_type=type(runner_raised).__name__,
                exc_str=str(runner_raised),
            )

        # Story 12.2 (FR67) — emit the task.budget_enforcement_triggered AUDIT
        # event: the durable ACTION-RECORD that the platform terminated the
        # subprocess in response to the budget overage. Populated from the
        # supervisor result (token_limit → budget_threshold, tokens_used →
        # actual_spend, step). ``post_trigger_transition`` is hard-pinned to
        # "failed" below — this branch ALWAYS drives ``TASK_FAILED``, so the
        # audit field MUST say "failed" to stay truthful (Story 12.3a Phase 2;
        # the override-intercepted branch above owns "awaiting_approval").
        # Best-effort via _call_tool_best_effort —
        # an audit-emit failure must NOT block the FSM transition or leak the
        # subprocess (enforcement already happened). NFR-R8 unaffected: this
        # is AFTER termination.
        # CODE-REVIEW M3 — FR67 requires the audit event after EVERY
        # enforcement, so we MUST emit even if an optional field is missing.
        # ``token_limit``/``tokens_used`` are isinstance-int-validated by the
        # supervisor (budget_supervisor.py) so they are guaranteed set when
        # ``triggered=True``; ``step`` is parsed best-effort from the raw
        # task.budget_exceeded payload and CAN be None on a malformed/older
        # producer. Rather than DROP the whole audit record over a missing
        # ``step``, default it to the schema floor (1, ``Field(ge=1)``) and
        # log that the source step was absent. ``budget_threshold`` /
        # ``actual_spend`` fall back to the supervisor values; if THOSE were
        # somehow None (should be impossible per the supervisor contract) the
        # payload's ``gt=0`` validation would reject 0 — so we coerce a None to
        # the smallest valid sentinel and log loudly, still preserving a record.
        # Story 12.3c AC6 (DRY) — shared coalescing + missing-field audit logs
        # (identical to the override-intercepted branch above).
        step_value, threshold_value, spend_value = _coerce_enforcement_fields(
            budget_result, task_id, log
        )
        enforcement_payload = TaskBudgetEnforcementTriggeredPayload(
            task_id=task_id,
            budget_threshold=threshold_value,
            actual_spend=spend_value,
            action_taken="subprocess_terminated",
            # Story 12.2 H1/H2 audit-integrity: ``post_trigger_transition`` MUST
            # match the FSM transition this branch actually drives. The
            # terminated branch ALWAYS drives ``TASK_FAILED`` below, so the audit
            # field is hard-pinned to "failed" — even when the operator policy
            # was ``default_budget_action="awaiting_approval"`` but NO override
            # arrived in the grace window (fail-closed → the subprocess WAS
            # terminated → the task failed). The audit must record what truly
            # happened to the task, not the configured intent. The override-
            # intercepted branch above is the only path that emits
            # ``post_trigger_transition="awaiting_approval"``, and only there
            # does the task continue rather than fail. (Story 12.3a Phase 2
            # removed the now-obsolete ``_reject_unwired_budget_action``
            # validator that pinned ``settings.default_budget_action`` to
            # "failed".)
            post_trigger_transition="failed",
            step=step_value,
            runtime=runner.runtime_name,
        )
        await _call_tool_best_effort(
            clients.clawhip_bridge,
            "emit_event",
            {
                "type": "task.budget_enforcement_triggered",
                "payload": enforcement_payload.model_dump(),
                "caller_trace_id": settings.resolve_trace_id(),
            },
            label="emit_budget_enforcement_triggered",
        )

        await mgr.handle_event(LifecycleEvent.TASK_FAILED)
        log.info(
            "budget_enforced_task_terminated",
            task_id=task_id,
            event_id=budget_result.event_id,
            tokens_used=budget_result.tokens_used,
            token_limit=budget_result.token_limit,
            step=budget_result.step,
            detection_latency_s=budget_result.detection_latency_s,
            termination_latency_s=budget_result.termination_latency_s,
            termination_method=budget_result.termination_method,
            enforcement_failed=budget_result.enforcement_failed,
        )
        return

    # PP1 — no budget enforcement fired. Re-raise any runner exception now;
    # the natural-completion path below is only reached on a clean result.
    if runner_raised is not None:
        raise runner_raised
    # Narrow the Optional[ClaudeCodeResult] for the natural-completion path;
    # ``result`` is non-None whenever ``runner_raised`` is None.
    # PP40 — converted from ``assert`` to runtime check. Under ``-O`` /
    # ``PYTHONOPTIMIZE=1`` ``assert`` is stripped so a future refactor that
    # broke the invariant would silently fall through into the ``None`` deref
    # below. ``RuntimeError`` is unstrippable and surfaces louder.
    if result is None:
        raise RuntimeError("invariant: result must be set when runner did not raise (PP40)")

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

    # --- License scan pre-check (Story 6.10, FR40) ---
    try:
        all_files = [
            str(p) for p in worktree_path.rglob("*") if p.is_file() and not p.name.startswith(".")
        ]

        def _sync_scan() -> list[LicenseFinding]:
            return scan_files_for_licenses(all_files)

        license_findings = await asyncio.to_thread(_sync_scan)
        if license_findings:
            await _emit_event(
                "task.license_flagged",
                TaskLicenseFlaggedPayload(
                    task_id=task_id,
                    reason_code=license_findings[0].reason_code,
                    file_list=[f.file_path for f in license_findings],
                    detected_licenses=list(
                        dict.fromkeys(f.license_detected for f in license_findings)
                    ),
                ).model_dump(),
            )
            log.warning(
                "license_flagged",
                task_id=task_id,
                findings=len(license_findings),
            )
    except (OSError, ValueError):
        log.warning("license_scan_failed", task_id=task_id, exc_info=True)

    # Emit task.approval_requested (AC-2).
    diff_summary = await _get_diff_summary(worktree_path)
    approval_payload = TaskApprovalRequestedPayload(
        task_id=task_id,
        action="git_push",
        justification=f"Claude Code attempted: {push_event.tool_input.get('command', 'git push')}",
        diff_summary=diff_summary,
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
        mgr,
        clients,
        settings,
        task_id,
    )


async def _handle_pending_approval(
    mgr: LifecycleManager,
    clients: _MCPClients,
    settings: WorkerSettings,
    task_id: str,
) -> None:
    """Poll for approval, then execute or fail the gated action.

    Story 9.6 review pass-1 H3 / M5: the worker-resolved ``trace_id`` is
    read from ``settings.resolve_trace_id()`` and forwarded into the uniform
    ``_emit_tier3_performed(settings=...)`` signature.
    """
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
            clients,
            settings,
            task_id,
            accepted=False,
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
            clients,
            settings,
            task_id,
            accepted=False,
            reason=f"Approval timed out after {settings.approval_timeout_s}s",
        )
        log.error("approval_timeout", task_id=task_id)
        return

    if not approval.granted:
        # Rejection path (AC-3).
        await mgr.handle_event(LifecycleEvent.APPROVAL_REJECTED)
        await _emit_tier3_performed(
            clients,
            settings,
            task_id,
            accepted=False,
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
            clients,
            settings,
            task_id,
            accepted=False,
            approval_event_id=approval.event_id,
            reason="gated action execution failed",
        )
        return
    await _emit_tier3_performed(
        clients,
        settings,
        task_id,
        accepted=True,
        approval_event_id=approval.event_id,
    )
    log.info("tier3_action_performed", task_id=task_id)


async def _emit_tier3_performed(
    clients: _MCPClients,
    settings: WorkerSettings,
    task_id: str,
    *,
    accepted: bool,
    approval_event_id: str = "",
    reason: str = "",
) -> None:
    """Emit ``tier3.action_performed`` via clawhip-bridge (AC-6).

    Story 9.6 review pass-1 H3 / M5: signature now takes ``settings`` instead
    of an Optional ``trace_id`` kwarg. The defensive ``or new_uuid7()`` mint
    is gone — every emission shares the worker-singleton ``trace_id`` resolved
    from settings, so the causal chain is preserved by construction.
    """
    trace_id = settings.resolve_trace_id()
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
        {
            "type": "tier3.action_performed",
            "payload": payload.model_dump(),
            "caller_trace_id": trace_id,  # Story 9.6 / FR59
        },
        label="emit_tier3_action_performed",
    )
