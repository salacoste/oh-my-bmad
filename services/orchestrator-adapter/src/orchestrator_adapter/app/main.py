"""Main adapter lifecycle — MCP connections, task polling, OMC driving (Story 5.10).

Coordinates the orchestrator-adapter's main loop:

1. Connect to MCP servers (task-registry, session-registry, clawhip-bridge).
2. Poll task-registry for tasks needing planning.
3. Drive OMC subprocess via ``OMCRunner``.
4. Emit ``task.planning.started`` / ``task.plan.ready`` events via clawhip-bridge.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import structlog
from events.envelope import is_valid_trace_id

from orchestrator_adapter.adapters.github_adapter import GitHubAdapter, PRDraftResult
from orchestrator_adapter.adapters.mcp_clients import MCPClientGroup
from orchestrator_adapter.adapters.omc_runner import OMCRunner
from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.domain.task_dispatch import (
    BudgetTracker,
    PlanParseResult,
    build_blocker_raised_payload,
    build_budget_exceeded_payload,
    build_completion_payload,
    build_execution_started_payload,
    build_omc_prompt,
    build_plan_ready_payload,
    build_planning_started_payload,
    build_step_completed_payload,
    parse_omc_plan_output,
    parse_step_metrics,
    parse_token_usage,
)

_MCP_CALL_TIMEOUT: float = 10.0


async def _call_tool(
    session: object,
    tool_name: str,
    arguments: dict[str, object],
    *,
    label: str,
    timeout: float = _MCP_CALL_TIMEOUT,
) -> object | None:
    """Call an MCP tool with timeout; log and return ``None`` on failure."""
    from mcp import ClientSession

    log = structlog.get_logger(__name__)
    if session is None or not isinstance(session, ClientSession):
        log.warning("mcp_tool_skipped_no_session", label=label, tool=tool_name)
        return None
    try:
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments=arguments),
            timeout=timeout,
        )
        return result
    except TimeoutError:
        log.error("mcp_tool_call_timeout", label=label, tool=tool_name, timeout=timeout)
    except Exception:
        log.warning("mcp_tool_call_failed", label=label, tool=tool_name, exc_info=True)
    return None


async def _read_task_list(clients: MCPClientGroup) -> list[dict[str, object]]:
    """Read the task list from task-registry MCP resource ``task://list``."""
    log = structlog.get_logger(__name__)
    if clients.task_registry is None:
        log.warning("task_registry_not_connected")
        return []
    try:
        result = await clients.task_registry.read_resource("task://list")
        if result is None or not hasattr(result, "contents"):
            return []
        # Resource results contain text content blocks.
        text = ""
        for content in result.contents:
            if hasattr(content, "text"):
                text += content.text
        if not text:
            return []
        tasks = json.loads(text)
        if isinstance(tasks, list):
            return tasks
        return []
    except Exception:
        log.warning("task_list_read_failed", exc_info=True)
        return []


def _tasks_needing_planning(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
    """Filter tasks that need planning (status is ``pending`` or ``new``)."""
    planning_statuses = {"pending", "new", "ready"}
    return [t for t in tasks if t.get("status", "") in planning_statuses]


async def _emit_event(
    clients: MCPClientGroup,
    event_type: str,
    payload: dict[str, object],
    *,
    label: str,
    caller_trace_id: str,
) -> None:
    """Emit a typed event via clawhip-bridge ``emit_event`` tool.

    Story 9.6 review pass-3 TH0: ``caller_trace_id`` is now REQUIRED on
    every emission to satisfy Story 9.5's clawhip-bridge MCP tool contract
    (caller_trace_id is a required input field, validated server-side).
    """
    await _call_tool(
        clients.clawhip_bridge,
        "emit_event",
        {
            "type": event_type,
            "payload": payload,
            "caller_trace_id": caller_trace_id,
        },
        label=label,
    )


async def _create_pr_draft(
    settings: OrchestratorSettings,
    task_id: str,
    repo: str,
    plan_summary: str,
    title: str | None,
) -> PRDraftResult | None:
    """Attempt PR draft creation; return None on config/parse errors (non-blocking)."""
    log = structlog.get_logger(__name__)
    token = settings.github_token.get_secret_value()
    if not token:
        log.debug("github_pr_skipped_no_token", task_id=task_id)
        return None
    parts = repo.split("/", 1)
    if len(parts) != 2:
        log.warning("github_pr_skipped_bad_repo", task_id=task_id, repo=repo)
        return None
    owner, repo_name = parts
    if not owner or not repo_name:
        log.warning("github_pr_skipped_bad_repo", task_id=task_id, repo=repo)
        return None
    adapter = GitHubAdapter(
        token=token,
        base_url=settings.github_api_base_url,
        timeout_s=settings.github_timeout_s,
    )
    head_branch = f"task/{task_id}"
    pr_title = str(title) if title else f"Task {task_id}"
    try:
        result = await adapter.create_pr_draft(
            owner=owner,
            repo=repo_name,
            title=pr_title,
            head=head_branch,
            base=settings.github_base_branch,
            body=plan_summary[:1000],
        )
        if not result.success:
            log.warning("github_pr_failed", task_id=task_id, error=result.error)
        return result
    except Exception:
        log.warning("github_pr_error", task_id=task_id, exc_info=True)
        return None


async def process_task(
    clients: MCPClientGroup,
    runner: OMCRunner,
    settings: OrchestratorSettings,
    task: dict[str, object],
) -> None:
    """Process a single task: plan, then drive execution step-by-step.

    Story 9.6 review pass-3 TH0 + TH2: ``caller_trace_id`` is resolved
    per-task — preferring the task's own ``trace_id`` field, falling back
    to the adapter's resolved trace_id when absent.  The same value is
    threaded into ``runner.run(..., trace_id=...)`` so the OMC subprocess
    env carries the right token.
    """
    log = structlog.get_logger(__name__)
    task_id = str(task.get("id", ""))
    if not task_id:
        log.warning("task_missing_id, skipping")
        return
    title = task.get("title")
    hint = task.get("hint")
    repo = task.get("repo")

    # Story 9.6 review pass-3 TH0 / TH2 — resolve trace_id PER task.
    # If the task carries its own valid trace_id (set upstream), use it;
    # otherwise fall back to the adapter's settings-scoped trace_id.
    raw_task_trace = task.get("trace_id")
    task_trace_id: str
    if isinstance(raw_task_trace, str) and raw_task_trace and is_valid_trace_id(raw_task_trace):
        task_trace_id = raw_task_trace
    elif isinstance(raw_task_trace, str) and raw_task_trace:
        log.warning(
            "task_trace_id_invalid_falling_back_to_settings",
            task_id=task_id,
            value_preview=repr(raw_task_trace[:80]),
        )
        task_trace_id = settings.resolve_trace_id()
    else:
        task_trace_id = settings.resolve_trace_id()

    # Emit task.planning.started.
    started_payload = build_planning_started_payload(task_id)
    await _emit_event(
        clients,
        "task.planning.started",
        started_payload,
        label=f"planning_started_{task_id}",
        caller_trace_id=task_trace_id,
    )
    log.info("planning_started", task_id=task_id)

    # Build prompt and drive OMC.
    prompt = build_omc_prompt(
        task_id=task_id,
        title=str(title) if title else None,
        hint=str(hint) if hint else None,
        repo=str(repo) if repo else None,
    )
    result = await runner.run(prompt, trace_id=task_trace_id)

    if result.error:
        log.error(
            "omc_run_failed",
            task_id=task_id,
            error=result.error,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
        )
        await _emit_event(
            clients,
            "task.planning.failed",
            {"task_id": task_id, "error": result.error},
            label=f"planning_failed_{task_id}",
            caller_trace_id=task_trace_id,
        )
        return

    # Parse OMC output and emit task.plan.ready.
    plan_result: PlanParseResult = parse_omc_plan_output(result.stdout)
    ready_payload = build_plan_ready_payload(task_id, plan_result)
    await _emit_event(
        clients,
        "task.plan.ready",
        ready_payload,
        label=f"plan_ready_{task_id}",
        caller_trace_id=task_trace_id,
    )
    log.info(
        "plan_ready",
        task_id=task_id,
        plan_len=len(plan_result.summary),
        step_count=plan_result.estimated_steps,
        duration_ms=result.duration_ms,
    )

    # --- Story 5.12: execution loop ---

    # Guard: skip execution when plan has no steps.
    if not plan_result.steps:
        log.info("no_steps_to_execute", task_id=task_id)
        completion_payload = build_completion_payload(task_id, plan_result, {})
        await _emit_event(
            clients,
            "task.completed",
            completion_payload,
            label=f"task_completed_{task_id}",
            caller_trace_id=task_trace_id,
        )
        log.info("task_completed_no_steps", task_id=task_id)
        return

    # Emit task.execution.started.
    # TODO(5.17a): replace "s-placeholder" with real worker-wrapper session ID.
    exec_started_payload = build_execution_started_payload(task_id, "s-placeholder")
    await _emit_event(
        clients,
        "task.execution.started",
        exec_started_payload,
        label=f"execution_started_{task_id}",
        caller_trace_id=task_trace_id,
    )
    log.info("execution_started", task_id=task_id)

    # Drive each step via OMC.
    step_outputs: dict[int, str] = {}
    blockers_count = 0
    budget_limit = settings.task_token_budget
    tracker = BudgetTracker(limit=budget_limit) if budget_limit > 0 else None
    for step in plan_result.steps:
        step_prompt = build_omc_prompt(task_id, hint=f"Step {step.step}: {step.description}")
        step_result = await runner.run(step_prompt, trace_id=task_trace_id)

        if step_result.error:
            log.error(
                "step_omc_failed",
                task_id=task_id,
                step=step.step,
                error=step_result.error,
            )
            reason = f"Step {step.step} failed: {step_result.error}"
            blocker_payload = build_blocker_raised_payload(task_id, reason)
            await _emit_event(
                clients,
                "task.blocker_raised",
                blocker_payload,
                label=f"step_blocker_{task_id}_{step.step}",
                caller_trace_id=task_trace_id,
            )
            blockers_count += 1
            break

        output_summary = step_result.stdout[:2000] if step_result.stdout else ""
        step_outputs[step.step] = output_summary

        step_completed_payload = build_step_completed_payload(task_id, step, output_summary)
        await _emit_event(
            clients,
            "task.step.completed",
            step_completed_payload,
            label=f"step_completed_{task_id}_{step.step}",
            caller_trace_id=task_trace_id,
        )
        log.info("step_completed", task_id=task_id, step=step.step)

        # Story 5.15: accumulate token usage and check budget.
        if tracker is not None:
            tokens = parse_token_usage(step_result.stdout or "")
            if tokens is not None:
                tracker = tracker.consume(tokens)
                if tracker.is_exceeded:
                    overshoot_ratio = tracker.used / tracker.limit
                    log.warning(
                        "task_budget_exceeded",
                        task_id=task_id,
                        step=step.step,
                        tokens_used=tracker.used,
                        token_limit=tracker.limit,
                        overshoot=f"{overshoot_ratio:.1%}",
                    )
                    if overshoot_ratio > 1.1:
                        log.warning(
                            "task_budget_overshoot_exceeded_10pct",
                            task_id=task_id,
                            tokens_used=tracker.used,
                            token_limit=tracker.limit,
                        )
                    budget_payload = build_budget_exceeded_payload(task_id, tracker, step.step)
                    await _emit_event(
                        clients,
                        "task.budget_exceeded",
                        budget_payload,
                        label=f"budget_exceeded_{task_id}",
                        caller_trace_id=task_trace_id,
                    )
                    break
            else:
                log.debug(
                    "budget_tracking_no_telemetry",
                    task_id=task_id,
                    step=step.step,
                    tracker_used=tracker.used,
                    tracker_limit=tracker.limit,
                    note="step output contained no parseable token usage",
                )

    # Emit task.completed with synthesized summary and FR9 structured metrics.
    metrics = parse_step_metrics(step_outputs)
    metrics = dataclasses.replace(metrics, blockers_count=blockers_count)

    # Story 5.14: PR draft auto-creation on green CI.
    pr_url: str | None = None
    pr_number: int | None = None
    pr_branch: str | None = None
    if (
        metrics.ci_state == "green"
        and repo
        and plan_result.steps
        and blockers_count == 0
        and (tracker is None or not tracker.is_exceeded)
    ):
        pr_result = await _create_pr_draft(
            settings,
            task_id,
            str(repo),
            plan_result.summary,
            title,
        )
        if pr_result is not None and pr_result.success:
            pr_url = pr_result.url
            pr_number = pr_result.number
            pr_branch = pr_result.branch

    completion_payload = build_completion_payload(
        task_id,
        plan_result,
        step_outputs,
        metrics,
        pr_url=pr_url,
        pr_number=pr_number,
        pr_branch=pr_branch,
        token_usage=tracker.used if tracker is not None else None,
    )
    await _emit_event(
        clients,
        "task.completed",
        completion_payload,
        label=f"task_completed_{task_id}",
        caller_trace_id=task_trace_id,
    )
    log.info("task_completed", task_id=task_id, steps=len(step_outputs))


async def adapter_loop(
    clients: MCPClientGroup,
    settings: OrchestratorSettings,
    stop_event: asyncio.Event,
) -> None:
    """Main polling loop — poll task-registry, process tasks, repeat.

    Story 9.6 review pass-3 TH2: ``OMCRunner`` is constructed without
    trace_id (per-call kwarg model); each ``runner.run(prompt, trace_id=...)``
    inside ``process_task`` carries the task-scoped token.
    """
    log = structlog.get_logger(__name__)
    runner = OMCRunner(
        omc_path=Path(settings.omc_path),
        timeout_s=settings.omc_timeout_s,
    )

    log.info("adapter_loop_started", poll_interval=settings.poll_interval_s)

    while not stop_event.is_set():
        tasks = await _read_task_list(clients)
        needing_planning = _tasks_needing_planning(tasks)

        if needing_planning:
            log.info(
                "tasks_needing_planning",
                count=len(needing_planning),
                total=len(tasks),
            )
            for task in needing_planning:
                if stop_event.is_set():
                    break
                await process_task(clients, runner, settings, task)
        else:
            log.debug("no_tasks_need_planning", total=len(tasks))

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_interval_s)
            return  # stop_event was set
        except TimeoutError:
            pass  # poll interval elapsed — loop again

    log.info("adapter_loop_stopped")


async def run_adapter(settings: OrchestratorSettings, stop_event: asyncio.Event) -> None:
    """Wire up MCP clients and run the adapter loop."""
    log = structlog.get_logger(__name__)
    ready = Path(settings.ready_file_path)
    clients = MCPClientGroup(settings=settings)

    async with clients:
        try:
            from orchestrator_adapter.adapters.mcp_clients import verify_connectivity

            results = await verify_connectivity(clients)
            if not results or not all(results.values()):
                log.error("connectivity_check_failed_partial", results=results)

            ready.touch()
            log.info("orchestrator_adapter_ready", actor_id=settings.resolve_actor_id())

            await adapter_loop(clients, settings, stop_event)
        finally:
            ready.unlink(missing_ok=True)
