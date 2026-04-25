"""clawhip-bridge MCP server — append-only event emission surface (Story 2.8).

Exports:
  ``build_server(*, base_dir, clock, actor_kind, actor_id) -> FastMCP``
      Factory that creates the FastMCP server with all 5 tools + 1 resource
      registered. Inject configuration at startup; call ``mcp.run()`` to serve.

Architecture notes:
  - ALL 5 tools are append-only — they call ``EventLogWriter.append()`` and
    return ``{"event_id": ..., "emitted_at": ...}``. No mutation or deletion
    path exists on this server (AC-2 / FR18b structural guarantee).
  - Tier enforcement (AC-8) is a NO-OP placeholder; full tiers land in
    Stories 6.1-6.3.
  - ``recent_events`` resource (AC-1 / AC-9) reads JSONL via
    ``read_log_lines``; wraps ``FileNotFoundError`` → returns ``""`` so a
    missing day file is not an error.
  - Recovery (AC-6): ``writer.recover()`` is awaited lazily on the FIRST
    tool call rather than at factory-build time, so ``build_server`` stays
    a synchronous factory (safe to call from both sync and async contexts).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from events import (  # noqa: IMP001 — events is packages/
    Actor,
    EventEnvelope,
    EventSchemaUnknown,
    new_event_id,
    new_request_id,
)
from events.canonical import to_canonical_json
from events.clock import Clock
from events.envelope import ActorKind
from mcp.server.fastmcp import FastMCP
from registry_state import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch line 272
    EventLogWriter,
    current_day_path,
    read_log_lines,
    recover_all_logs,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capability-tier enforcement placeholder (AC-8)
# TODO(story-6.1): tighten to actual tier enforcement
# ---------------------------------------------------------------------------


def _check_tier(actor_kind: ActorKind, tool_name: str) -> bool:
    """NO-OP capability-tier gate (Phase 1 placeholder).

    Story 6.1-6.3 replaces this with real Tier 0/1/2/3 enforcement.
    """
    log.debug(
        "tier-check (no-op): actor_kind=%s tool=%s — full enforcement in Story 6.1",
        actor_kind,
        tool_name,
    )
    return True


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def build_server(
    *,
    base_dir: Path,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Registers 5 append-only emission tools + 1 read-only resource.
    ``recover_all_logs(base_dir)`` is called during server lifespan startup
    (AC-6) so that trailing partial lines from a previous crash are trimmed
    before any tool call is served. Using the lifespan hook (not a sync call
    in this factory) keeps ``build_server`` safe to call from both sync and
    async contexts (e.g., tests).

    Args:
        base_dir: Root directory for the JSONL event log.
        clock: Injected clock for deterministic testing.
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the emitting actor instance.

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    writer = EventLogWriter(base_dir=base_dir, clock=clock)

    @contextlib.asynccontextmanager
    async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
        """Run recovery before serving; nothing to tear down."""
        await recover_all_logs(base_dir)
        log.debug("clawhip-bridge: recovery complete; ready to serve")
        yield

    mcp = FastMCP("clawhip-bridge", lifespan=_lifespan)

    # ------------------------------------------------------------------
    # Internal helper: build + write envelope, return result dict
    # ------------------------------------------------------------------

    async def _emit(
        event_type: str,
        payload: dict[str, object],
        parent_event_id: str | None,
    ) -> dict[str, str]:
        """Build, validate, persist and return an event envelope."""
        envelope = EventEnvelope.create(
            event_id=new_event_id(clock=clock),
            schema_version="1.0.0",
            type=event_type,  # noqa: EVT001 — type validated by REGISTRY at envelope.create()
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind=actor_kind, id=actor_id),
            payload=payload,
            parent_event_id=parent_event_id,
            request_id=new_request_id(clock=clock),
        )
        await writer.append(envelope)
        return {
            "event_id": envelope.event_id,
            "emitted_at": envelope.emitted_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # Tool: emit_event — generic escape hatch validated by REGISTRY
    # ------------------------------------------------------------------

    @mcp.tool()
    async def emit_event(
        type: str,  # noqa: A002 — `type` is the canonical envelope field name
        payload: dict[str, object],
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a typed event to the spine. Validated against REGISTRY.

        Raises ``EventSchemaUnknown`` if ``type`` is not registered.
        """
        _check_tier(actor_kind, "emit_event")
        if not isinstance(type, str):  # noqa: A002
            raise TypeError(f"type must be a str, got {type!r}")  # noqa: A002
        try:
            return await _emit(type, payload, parent_event_id)  # noqa: A002
        except EventSchemaUnknown:
            raise

    # ------------------------------------------------------------------
    # Typed sugar tools — type literals baked in, no EVT001 needed
    # ------------------------------------------------------------------

    @mcp.tool()
    async def emit_blocker(
        task_id: str,
        reason: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.blocker_raised`` event."""
        _check_tier(actor_kind, "emit_blocker")
        return await _emit(
            "task.blocker_raised",
            {"task_id": task_id, "reason": reason},
            parent_event_id,
        )

    @mcp.tool()
    async def emit_summary(
        task_id: str,
        summary: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.summary_emitted`` event."""
        _check_tier(actor_kind, "emit_summary")
        return await _emit(
            "task.summary_emitted",
            {"task_id": task_id, "summary": summary},
            parent_event_id,
        )

    @mcp.tool()
    async def emit_approval_request(
        task_id: str,
        action: str,
        justification: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.approval_requested`` event."""
        _check_tier(actor_kind, "emit_approval_request")
        return await _emit(
            "task.approval_requested",
            {"task_id": task_id, "action": action, "justification": justification},
            parent_event_id,
        )

    @mcp.tool()
    async def emit_completion(
        task_id: str,
        summary: str,
        pr_url: str | None = None,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.completed`` event."""
        _check_tier(actor_kind, "emit_completion")
        return await _emit(
            "task.completed",
            {"task_id": task_id, "summary": summary, "pr_url": pr_url},
            parent_event_id,
        )

    # ------------------------------------------------------------------
    # Resource: recent_events — read-only tail of today's JSONL log
    # ------------------------------------------------------------------

    @mcp.resource("recent-events://current-day")
    async def recent_events() -> str:
        """Return the last 50 events from today's JSONL log as newline-joined JSON.

        Use the ``limit`` query parameter (1-1000, default 50) to control
        how many lines are returned. Returns ``""`` when no events have been
        written today.

        Note: ``limit`` cannot be a resource function parameter when the URI
        is static (FastMCP requires URI-template parameters to match function
        parameters). The default 50 is used; callers needing a different
        limit should use the ``emit_*`` tools and filter client-side.
        """
        limit = 50
        path = current_day_path(base_dir, clock.now())
        try:
            envelopes = list(read_log_lines(path))
        except FileNotFoundError:
            return ""
        recent = envelopes[-limit:]
        return "\n".join(to_canonical_json(env).decode("utf-8") for env in recent)

    return mcp


__all__ = ["build_server"]
