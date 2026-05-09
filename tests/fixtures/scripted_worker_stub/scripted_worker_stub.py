"""Scripted worker stub — test fixture for the S-1 separability test.

Story 5.16 / FR34 / NFR-M4: this script proves that the platform's
worker-wrapper layer is swappable via a single ``WORKER_IMAGE`` env-var
override. It connects to the ``clawhip-bridge`` MCP server (using the
same ``StdioServerParameters`` pattern as the real worker), detects
``task.created`` events from the JSONL log, and emits the canonical task
lifecycle events via ``clawhip-bridge``'s ``emit_event`` tool.

The stub does NOT import from ``services/worker-wrapper/`` — it only uses
``packages/events/`` (for payload construction) and the MCP SDK. This is
the core separability proof: the MCP surface contract is sufficient.

Environment variables (``WORKER_`` prefix, matching real worker):

* ``WORKER_TASK_REGISTRY_COMMAND`` / ``WORKER_TASK_REGISTRY_ARGS``
* ``WORKER_SESSION_REGISTRY_COMMAND`` / ``WORKER_SESSION_REGISTRY_ARGS``
* ``WORKER_CLAWHIP_BRIDGE_COMMAND`` / ``WORKER_CLAWHIP_BRIDGE_ARGS``
* ``EVENT_LOG_DIR`` — directory containing ``YYYY-MM-DD.jsonl`` files.
* ``SCRIPTED_WORKER_SCENARIO`` — canned event sequence name (default
  ``simple_green``).

Canned scenarios:

* ``simple_green`` — plan → execute 1 step → complete with green CI.
* ``with_pr`` — plan → execute 2 steps → complete with PR URL.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from events import Actor, SystemClock, new_session_id
from events.clock import Clock
from events.payloads import PlanStep, SessionFinishedPayload, SessionStartedPayload
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from registry_state.adapters.event_log import read_log_lines

log = structlog.get_logger("scripted-worker-stub")

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_POLL_INTERVAL_S = 0.5
_MCP_INIT_TIMEOUT: float = 30.0

_ACTOR: Actor = Actor(kind="worker", id="scripted-worker-stub")
_READY_FILE = Path("/tmp/ready")  # noqa: S108

# Event types the stub itself emits (for dedupe on restart).
STUB_EMITTED_TYPES: frozenset[str] = frozenset(
    {
        "task.planning.started",
        "task.plan.ready",
        "task.execution.started",
        "task.step.completed",
        "task.completed",
    }
)


class ScriptedWorkerSettings(BaseSettings):
    """Settings for the scripted worker stub, using WORKER_ env prefix."""

    model_config = SettingsConfigDict(env_prefix="WORKER_")

    task_registry_command: str = "python"
    task_registry_args: list[str] = Field(default=["-m", "task_registry_mcp"])
    session_registry_command: str = "python"
    session_registry_args: list[str] = Field(default=["-m", "session_registry_mcp"])
    clawhip_bridge_command: str = "python"
    clawhip_bridge_args: list[str] = Field(default=["-m", "clawhip_bridge_mcp"])

    session_id: str = ""
    worker_id: str = ""

    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S


# --- Canned scenarios -------------------------------------------------------
# Each scenario returns a list of (event_type, payload_dict) tuples.

_SIMPLE_GREEN_STEPS = [
    PlanStep(step=1, description="Execute task"),
]


def _scenario_simple_green(task_id: str, session_id: str) -> list[dict[str, Any]]:
    """Plan → 1 step → complete with green CI."""
    return [
        {"type": "task.planning.started", "payload": {"task_id": task_id}},
        {
            "type": "task.plan.ready",
            "payload": {
                "task_id": task_id,
                "plan_summary": "scripted-worker-stub simple_green",
                "plan": [s.model_dump() for s in _SIMPLE_GREEN_STEPS],
                "estimated_steps": 1,
            },
        },
        {
            "type": "task.execution.started",
            "payload": {"task_id": task_id, "session_id": session_id},
        },
        {
            "type": "task.step.completed",
            "payload": {
                "task_id": task_id,
                "step": 1,
                "description": "Execute task",
                "output_summary": "1 passed",
            },
        },
        {
            "type": "task.completed",
            "payload": {
                "task_id": task_id,
                "summary": "scripted-worker-stub completion (simple_green)",
                "files_changed": 1,
                "lines_added": 10,
                "tests_added": 1,
                "ci_state": "green",
            },
        },
    ]


def _scenario_with_pr(task_id: str, session_id: str) -> list[dict[str, Any]]:
    """Plan → 2 steps → complete with PR URL."""
    steps = [
        PlanStep(step=1, description="Implement feature"),
        PlanStep(step=2, description="Write tests"),
    ]
    events: list[dict[str, Any]] = [
        {"type": "task.planning.started", "payload": {"task_id": task_id}},
        {
            "type": "task.plan.ready",
            "payload": {
                "task_id": task_id,
                "plan_summary": "scripted-worker-stub with_pr",
                "plan": [s.model_dump() for s in steps],
                "estimated_steps": 2,
            },
        },
        {
            "type": "task.execution.started",
            "payload": {"task_id": task_id, "session_id": session_id},
        },
        {
            "type": "task.step.completed",
            "payload": {
                "task_id": task_id,
                "step": 1,
                "description": "Implement feature",
                "output_summary": "Feature implemented",
            },
        },
        {
            "type": "task.step.completed",
            "payload": {
                "task_id": task_id,
                "step": 2,
                "description": "Write tests",
                "output_summary": "3 passed",
            },
        },
        {
            "type": "task.completed",
            "payload": {
                "task_id": task_id,
                "summary": "scripted-worker-stub completion (with_pr)",
                "files_changed": 3,
                "lines_added": 42,
                "tests_added": 3,
                "ci_state": "green",
                "pr_url": "https://github.com/example/repo/pull/1",
                "pr_number": 1,
                "pr_branch": "task/test-branch",
            },
        },
    ]
    return events


SCENARIOS: dict[str, Callable[[str, str], list[dict[str, Any]]]] = {  # type: ignore[valid-type]
    "simple_green": _scenario_simple_green,
    "with_pr": _scenario_with_pr,
}


# --- MCP client helpers ------------------------------------------------------


async def _connect_mcp(
    name: str,
    command: str,
    args: list[str],
    stack: contextlib.AsyncExitStack,
) -> ClientSession:
    """Connect to one MCP server via stdio subprocess."""
    params = StdioServerParameters(command=command, args=args)
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await asyncio.wait_for(session.initialize(), timeout=_MCP_INIT_TIMEOUT)
    log.info("mcp_client_connected", server=name)
    return session


async def _emit_via_clawhip(
    session: ClientSession,
    event_type: str,
    payload: dict[str, Any],
    *,
    parent_event_id: str | None = None,
) -> dict[str, Any]:
    """Emit one event via clawhip-bridge's ``emit_event`` tool."""
    args: dict[str, Any] = {"type": event_type, "payload": payload}
    if parent_event_id is not None:
        args["parent_event_id"] = parent_event_id
    result = await session.call_tool("emit_event", arguments=args)
    # Result content is a list of TextContent objects.
    text_parts = [c.text for c in result.content if hasattr(c, "text")]
    raw = "".join(text_parts)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        log.warning("emit_via_clawhip_non_json_response", raw=raw[:200])
        return {"raw": raw}


# --- JSONL tailing for task.created detection --------------------------------


def _read_new_lines(path: Path, offset: int) -> tuple[int, list[dict[str, Any]]]:
    """Tail-read complete JSONL lines from *path* starting at *offset*."""
    if not path.exists():
        return offset, []
    envelopes: list[dict[str, Any]] = []
    new_offset = offset
    with open(path, "rb") as f:
        f.seek(offset)
        while True:
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # Advance offset past partial line so next poll doesn't
                # re-read it forever (will be re-read when complete).
                new_offset += len(raw)
                break
            line = raw.rstrip(b"\r\n").strip()
            if not line:
                new_offset += len(raw)
                continue
            with contextlib.suppress(json.JSONDecodeError):
                envelopes.append(json.loads(line))
            new_offset += len(raw)
    return new_offset, envelopes


def _scan_processed_task_ids(base_dir: Path) -> set[str]:
    """Return task_ids that already have stub-emitted lifecycle events."""
    seen: set[str] = set()
    if not base_dir.exists():
        return seen
    for path in sorted(base_dir.glob("*.jsonl")):
        try:
            for env_obj in read_log_lines(path):
                if env_obj.type not in STUB_EMITTED_TYPES:
                    continue
                payload = env_obj.payload
                if isinstance(payload, dict):
                    task_id = payload.get("task_id")
                else:
                    task_id = getattr(payload, "task_id", None)
                if isinstance(task_id, str):
                    seen.add(task_id)
        except FileNotFoundError:
            continue
    return seen


# --- Main loop ---------------------------------------------------------------


async def run_scripted_worker(
    *,
    base_dir: Path,
    settings: ScriptedWorkerSettings,
    clock: Clock,
    stop_event: asyncio.Event,
) -> None:
    """Main async loop: connect MCP → tail JSONL → emit lifecycle per task."""
    session_id = settings.session_id or new_session_id(clock=clock)
    scenario_name = os.environ.get("SCRIPTED_WORKER_SCENARIO", "simple_green")
    scenario_fn = SCENARIOS.get(scenario_name, _scenario_simple_green)

    # Dedupe on restart.
    processed: set[str] = _scan_processed_task_ids(base_dir)
    log.info("startup", processed=len(processed), scenario=scenario_name)

    # Connect MCP servers.
    async with contextlib.AsyncExitStack() as stack:
        clawhip = await _connect_mcp(
            "clawhip-bridge",
            settings.clawhip_bridge_command,
            settings.clawhip_bridge_args,
            stack,
        )

        # Emit session.started.
        session_started = SessionStartedPayload(
            session_id=session_id,
            worker_id=settings.worker_id or "scripted-worker-stub",
            task_id=None,
        )
        await _emit_via_clawhip(
            clawhip,
            "session.started",
            session_started.model_dump(),
        )
        log.info("session_started", session_id=session_id)

        # Touch readiness file.
        with contextlib.suppress(OSError):
            _READY_FILE.touch()

        # Tail JSONL for task.created events.
        offsets: dict[str, int] = {}
        first_iteration_done = False

        while not stop_event.is_set():
            for path in sorted(base_dir.glob("*.jsonl")):
                offset_key = str(path.relative_to(base_dir))
                prior = offsets.get(offset_key, 0)
                new_offset, raw_envelopes = await asyncio.to_thread(_read_new_lines, path, prior)
                offsets[offset_key] = new_offset

                for env_obj in raw_envelopes:
                    if env_obj.get("type") != "task.created":
                        continue
                    payload = env_obj.get("payload", {})
                    task_id = payload.get("task_id") if isinstance(payload, dict) else None
                    if not isinstance(task_id, str) or not task_id:
                        continue
                    if task_id in processed:
                        continue

                    parent_event_id = env_obj.get("event_id")

                    # Emit canned scenario.
                    events = scenario_fn(task_id, session_id)
                    for evt in events:
                        await _emit_via_clawhip(
                            clawhip,
                            evt["type"],
                            evt["payload"],
                            parent_event_id=parent_event_id,
                        )
                        log.info("emitted", type=evt["type"], task_id=task_id)

                    processed.add(task_id)

            if not first_iteration_done:
                first_iteration_done = True

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_interval_s)

        # Emit session.finished on shutdown.
        session_finished = SessionFinishedPayload(session_id=session_id)
        await _emit_via_clawhip(
            clawhip,
            "session.finished",
            session_finished.model_dump(),
        )
        log.info("session_finished", session_id=session_id)

    with contextlib.suppress(OSError):
        _READY_FILE.unlink(missing_ok=True)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Register SIGTERM/SIGINT handlers that set *stop_event*."""
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            continue


async def _async_main() -> None:
    """Async entrypoint."""
    base_dir = Path(os.environ.get("EVENT_LOG_DIR", _DEFAULT_LOG_DIR))
    settings = ScriptedWorkerSettings()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, stop_event)

    base_dir.mkdir(parents=True, exist_ok=True)
    await run_scripted_worker(
        base_dir=base_dir,
        settings=settings,
        clock=SystemClock(),
        stop_event=stop_event,
    )


def main() -> None:
    """Sync entrypoint for ``python -m scripted_worker_stub``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_async_main())


if __name__ == "__main__":
    main()
