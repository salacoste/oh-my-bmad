"""Auto-approval stub — test fixture for Journey 1 integration test (Story 5.18).

Monitors the JSONL event log for ``task.awaiting_approval`` events and
automatically emits ``approval.granted`` via ``clawhip-bridge``'s
``emit_event`` tool. This replaces the operator approval step in Journey 1,
allowing the end-to-end test to run without human interaction.

Uses the same MCP SDK + structlog pattern as the scripted worker stub.

Environment variables:

* ``APPROVAL_CLAWHIP_BRIDGE_COMMAND`` / ``APPROVAL_CLAWHIP_BRIDGE_ARGS``
* ``EVENT_LOG_DIR`` — directory containing ``YYYY-MM-DD.jsonl`` files.
* ``APPROVAL_POLL_INTERVAL_S`` — polling interval in seconds (default ``0.5``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import structlog
from events import new_uuid7  # noqa: IMP001 — test fixture; events is a workspace package
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = structlog.get_logger("auto-approval-stub")

# Story 9.5: caller_trace_id is now required for clawhip-bridge emit_* tools.
# Generate a per-process UUIDv7 so all approvals from this stub run share one
# trace root (consistent with the stub's single-operator-action semantics).
_STUB_TRACE_ID: str = new_uuid7()

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"
_DEFAULT_POLL_INTERVAL_S = 0.5
_MCP_INIT_TIMEOUT: float = 30.0

_READY_FILE = Path("/tmp/auto-approval-ready")  # noqa: S108


class AutoApprovalSettings(BaseSettings):
    """Settings for the auto-approval stub, using APPROVAL_ env prefix."""

    model_config = SettingsConfigDict(env_prefix="APPROVAL_")

    clawhip_bridge_command: str = "python"
    clawhip_bridge_args: list[str] = Field(default=["-m", "clawhip_bridge_mcp"])

    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S


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
                break
            line = raw.rstrip(b"\r\n").strip()
            if not line:
                new_offset += len(raw)
                continue
            try:
                envelopes.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("jsonl_parse_error", raw=line[:200])
            new_offset += len(raw)
    return new_offset, envelopes


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


async def _emit_approval(
    session: ClientSession,
    task_id: str,
    *,
    parent_event_id: str | None = None,
) -> dict[str, Any]:
    """Emit ``approval.granted`` for *task_id* via clawhip-bridge."""
    args: dict[str, Any] = {
        "type": "approval.granted",
        "payload": {
            "task_id": task_id,
            "decision": "approved",
            "actor_kind": "auto-approval-stub",
            "actor_id": "auto-approval-stub",
            "reason": "auto-approved by Journey 1 test fixture",
        },
    }
    if parent_event_id is not None:
        args["parent_event_id"] = parent_event_id
    # Story 9.5: caller_trace_id is now required for clawhip-bridge emit_* tools.
    args["caller_trace_id"] = _STUB_TRACE_ID
    result = await session.call_tool("emit_event", arguments=args)
    text_parts = [c.text for c in result.content if hasattr(c, "text")]
    raw = "".join(text_parts)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        log.warning("emit_approval_non_json_response", raw=raw[:200])
        return {"raw": raw}


async def run_auto_approval(
    *,
    base_dir: Path,
    settings: AutoApprovalSettings,
    stop_event: asyncio.Event,
) -> None:
    """Main async loop: connect MCP → tail JSONL → auto-approve tasks."""
    approved_tasks: set[str] = set()
    offsets: dict[str, int] = {}

    async with contextlib.AsyncExitStack() as stack:
        clawhip = await _connect_mcp(
            "clawhip-bridge",
            settings.clawhip_bridge_command,
            settings.clawhip_bridge_args,
            stack,
        )

        # Touch readiness file.
        with contextlib.suppress(OSError):
            _READY_FILE.touch()

        log.info("monitoring_started", log_dir=str(base_dir))

        while not stop_event.is_set():
            for path in sorted(base_dir.rglob("*.jsonl")):
                offset_key = str(path.relative_to(base_dir))
                prior = offsets.get(offset_key, 0)
                new_offset, raw_envelopes = await asyncio.to_thread(_read_new_lines, path, prior)
                offsets[offset_key] = new_offset

                for env_obj in raw_envelopes:
                    if env_obj.get("type") != "task.awaiting_approval":
                        continue
                    payload = env_obj.get("payload", {})
                    task_id = payload.get("task_id") if isinstance(payload, dict) else None
                    if not isinstance(task_id, str) or not task_id:
                        continue
                    if task_id in approved_tasks:
                        continue

                    parent_event_id = env_obj.get("event_id")
                    try:
                        await _emit_approval(clawhip, task_id, parent_event_id=parent_event_id)
                    except Exception:
                        log.warning("emit_approval_failed", task_id=task_id, exc_info=True)
                        continue
                    approved_tasks.add(task_id)
                    log.info("auto_approved", task_id=task_id)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_interval_s)

        log.info("monitoring_stopped", tasks_approved=len(approved_tasks))

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
    settings = AutoApprovalSettings()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, stop_event)

    await asyncio.to_thread(base_dir.mkdir, parents=True, exist_ok=True)
    await run_auto_approval(
        base_dir=base_dir,
        settings=settings,
        stop_event=stop_event,
    )


def main() -> None:
    """Sync entrypoint for ``python -m auto_approval_stub``."""
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
