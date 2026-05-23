"""clawhip-bridge MCP server — append-only event emission surface (Story 2.8).

Exports:
  ``build_server(*, base_dir, clock, actor_kind, actor_id) -> FastMCP``
      Factory that creates the FastMCP server with all 5 tools + 1 resource
      registered. Inject configuration at startup; call ``mcp.run()`` to serve.

Architecture notes:
  - ALL 5 tools are append-only — they call ``EventLogWriter.append()`` and
    return ``{"event_id": ..., "emitted_at": ...}``. No mutation or deletion
    path exists on this server (AC-2 / FR18b structural guarantee).
  - Tier enforcement (AC-8) uses ``capabilities.check_tier`` — every emit
    tool maps to Tier.ONE. ``check_tier`` returns ``CapabilityOk`` or raises
    ``CapabilityDenied`` (Story 6.1).
  - ``recent_events`` resource (AC-1 / AC-9) reads JSONL via
    ``read_log_lines``; wraps ``FileNotFoundError`` → returns ``""`` so a
    missing day file is not an error. The ``limit`` is a real URI-template
    parameter (``recent-events://current-day/{limit}``) and is validated by
    ``_validate_limit`` in the production path.
  - Startup recovery (AC-6): ``recover_all_logs(base_dir)`` runs in
    FastMCP's lifespan context (entered before the first tool call
    dispatches). Trims trailing partial JSONL lines from prior process
    crashes per Story 2.4 AC-6. ``build_server`` itself stays a synchronous
    factory (safe to call from both sync and async contexts) because the
    actual recovery I/O is deferred to the lifespan ``async with``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

from capabilities import CallerContext, Tier, check_tier  # noqa: IMP001 — packages/
from capabilities.emit import build_capability_denied_payload  # noqa: IMP001 — packages/
from events import (  # noqa: IMP001 — events is packages/
    Actor,
    EventEnvelope,
    new_event_id,
    new_request_id,
)
from events.canonical import to_canonical_json
from events.clock import Clock
from events.envelope import ActorKind, is_valid_trace_id
from events.errors import CapabilityDenied  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP
from registry_state import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch line 272
    EventLogWriter,
    current_day_path,
    read_log_lines,
    recover_all_logs,
)

log = logging.getLogger(__name__)

TIER_MAP: dict[str, Tier] = {
    "emit_event": Tier.ONE,
    "emit_blocker": Tier.ONE,
    "emit_summary": Tier.ONE,
    "emit_approval_request": Tier.ONE,
    "emit_completion": Tier.ONE,
}


def _make_approval_lookup(
    base_dir: Path,
    clock: Clock,
) -> Callable[[str, str], Awaitable[bool]]:
    """Return an async ``(task_id, action) -> bool`` callable.

    Scans today's JSONL event log for ``approval.granted`` events
    matching *task_id*. Approvals are currently task-scoped only
    (the *action* parameter is accepted but unused — reserved for
    future wildcard matching). Story 6.5 adds the emitter; this
    lookup is ready for it.

    Phase-1 limitation: only scans today's JSONL log file. Cross-day
    approvals (granted yesterday, used today) are not covered. This is
    acceptable for the current scale and will be addressed when the
    materialized Event table becomes the primary approval source.
    """

    async def _lookup(task_id: str, action: str) -> bool:  # noqa: ARG001 — action reserved for future wildcard matching
        # NOTE: O(n) linear scan of today's JSONL per check — acceptable for
        # current scale, but cache or index if event volume grows.
        path = current_day_path(base_dir, clock.now())
        try:
            for envelope in read_log_lines(path):
                if (
                    envelope.type == "approval.granted"
                    and envelope.payload.get("task_id") == task_id
                ):
                    return True
        except FileNotFoundError:
            pass
        return False

    return _lookup


def _validate_limit(limit: int) -> None:
    """Validate ``limit`` is in the AC-9 range ``[1, 1000]``.

    Raises:
        ValueError: if ``limit < 1`` or ``limit > 1000``.
    """
    if not (1 <= limit <= 1000):
        raise ValueError("limit must be between 1 and 1000")


def validate_caller_trace_id(caller_trace_id: str) -> None:
    """Reject invalid ``caller_trace_id`` per Story 9.1 contract.

    Public helper used by every ``@mcp.tool()`` handler in this server to
    validate the operator-originating correlation ID supplied as an explicit
    Pydantic-validated input (Story 9.5 / FR58 MCP). Validation uses
    :func:`events.envelope.is_valid_trace_id` so the shape contract (UUIDv7
    bare form OR ``tg:<update_id>``) stays in one place — Story 9.4 pass-2 S1
    lesson (shape-validation, not just type-check, avoids whitespace/CRLF
    injection).

    Public name (no leading underscore) per Story 9.5 pass-1 review T4:
    these helpers are part of the public tool-validation contract documented
    in the Story 9.5 spec and exercised by ``tests/contract/`` — the contract
    test for byte-identical body sync (T2) requires a public symbol.

    NOTE: Duplicated byte-identically in ``task-registry`` and
    ``session-registry``. mcp-servers cannot share code per Story 5.8's
    import-graph constraint; the helper body MUST stay in sync across all
    three servers. Drift is guarded by
    ``tests/contract/test_mcp_tool_schemas.py::test_validate_caller_trace_id_byte_identical_across_servers``
    (Story 9.5 pass-1 T2).

    Raises:
        ValueError: if ``caller_trace_id`` doesn't match the Story 9.1
            contract (UUIDv7 bare form OR ``tg:<digits>``).
    """
    if not is_valid_trace_id(caller_trace_id):
        raise ValueError(
            f"caller_trace_id must match Story 9.1 contract "
            f"(UUIDv7 or tg:<update_id>); got {caller_trace_id!r}"
        )


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

    # Story 11.2.2: schema-version and actor-override config for event types
    # whose only registered version is NOT v1.0.0. Currently scoped to the
    # ``capability.denied`` audit event (registered at v1.1.0 only —
    # registry-state's ``domain/event_types.py:275``). Future audit-only
    # event types added at v1.1.0+ should be registered here so the
    # public ``emit_event`` tool selects the correct schema version AND
    # actor identity in one place. Tuple: (schema_version, actor_override).
    _SYSTEM_EMITTER = Actor(kind="system", id="clawhip-bridge-mcp")  # noqa: N806 — constant role inside the factory closure
    _CAPABILITY_DENIED = "capability.denied"  # noqa: N806 — constant role inside the factory closure
    _emit_overrides: dict[str, tuple[str, Actor]] = {
        _CAPABILITY_DENIED: ("1.1.0", _SYSTEM_EMITTER),
    }

    # ------------------------------------------------------------------
    # Internal helper: build + write envelope, return result dict
    # ------------------------------------------------------------------

    async def _emit(
        event_type: str,
        payload: dict[str, object],
        parent_event_id: str | None,
        *,
        caller_trace_id: str,
        schema_version: str = "1.0.0",
        actor_override: Actor | None = None,
    ) -> dict[str, str]:
        """Build, validate, persist and return an event envelope.

        ``caller_trace_id`` (Story 9.5 / FR58 MCP) is threaded into
        ``EventEnvelope.create(trace_id=...)`` so every event emitted via the
        5 ``@mcp.tool()`` handlers carries the operator-originating correlation
        ID end-to-end. This silences the Story 9.1 DeprecationWarning for the
        5 clawhip-bridge emit_* callsites.

        Story 9.5 pass-1 review T6: defense-in-depth re-validation of
        ``caller_trace_id`` even though every ``@mcp.tool()`` handler already
        calls :func:`validate_caller_trace_id` at the tool boundary. Belt-and-
        braces against a future internal helper that bypasses the tool layer.
        Story 9.5 pass-1 review T16: ``caller_trace_id`` is enforced kwarg-only
        via the leading ``*,`` separator so positional drift cannot reorder
        the field silently across the 5 callsites.

        Story 11.2.2: ``schema_version`` and ``actor_override`` parameterize
        the audit-emission path (AC3-A self-deny). ``capability.denied`` is
        registered ONLY at v1.1.0, so the default v1.0.0 would fail
        ``EventEnvelope.create`` for that type. ``actor_override`` stamps the
        envelope's ``actor`` field with the system-emitter identity per
        OQ-2 (``Actor(kind="system", id="clawhip-bridge-mcp")``) instead of
        the actor that triggered the denial.
        """
        validate_caller_trace_id(caller_trace_id)
        envelope = EventEnvelope.create(
            event_id=new_event_id(clock=clock),
            schema_version=schema_version,
            type=event_type,  # noqa: EVT001 — type validated by REGISTRY at envelope.create()
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=actor_override or Actor(kind=actor_kind, id=actor_id),
            payload=payload,
            parent_event_id=parent_event_id,
            trace_id=caller_trace_id,
            request_id=new_request_id(clock=clock),
        )
        await writer.append(envelope)
        return {
            "event_id": envelope.event_id,
            "emitted_at": envelope.emitted_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # Story 11.2.2 AC3-A — self-deny audit emission
    # ------------------------------------------------------------------

    async def _check_tier_with_self_emit(
        action: str,
        caller: CallerContext,
        required_tier: Tier,
        *,
        caller_trace_id: str,
    ) -> None:
        """Call ``check_tier``; on ``CapabilityDenied`` emit audit via internal ``_emit``.

        Story 11.2.2 AC3-A: clawhip-bridge IS the FR26 single writer, so
        a denial here cannot route through the standard task/session-registry
        emission path (no ``emit_event`` MCP-RPC client) — and even if it
        could, the emission would loop back through ``check_tier`` for the
        same denied actor, causing infinite recursion.

        Solution: build the ``capability.denied`` payload locally and invoke
        the in-process ``_emit`` helper directly. ``_emit`` writes to the
        event log without going through ``check_tier`` (the audit envelope
        is system-emitted, not actor-emitted — the actor's missing
        capability is the SUBJECT of the audit, not its gate).

        PD-1 fail-soft: emission failures (writer broken, schema mismatch)
        are logged at ERROR and SWALLOWED; the original CapabilityDenied
        is ALWAYS re-raised so MCP transport-level error semantics are
        preserved (AC6). CancelledError / KeyboardInterrupt propagate
        explicitly (PP1 mirror discipline).
        """
        try:
            check_tier(action, caller, required_tier)
        except CapabilityDenied as exc:
            try:
                audit_payload = build_capability_denied_payload(
                    required_tier=required_tier,
                    boundary="mcp",
                    actor_id=caller.actor_id,
                    attempted_action=action,
                    reason=exc.reason,
                )
                # Internal ``_emit`` bypasses ``check_tier`` — see AC3-A above.
                # Uses _CAPABILITY_DENIED / _SYSTEM_EMITTER constants so
                # schema version, event type, and actor identity stay in
                # sync with _emit_overrides above.
                await _emit(
                    _CAPABILITY_DENIED,
                    audit_payload,
                    None,
                    caller_trace_id=caller_trace_id,
                    schema_version="1.1.0",
                    actor_override=_SYSTEM_EMITTER,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                # Defensive: BaseException subclasses never caught by
                # ``except Exception`` on 3.12+, but explicit re-raise
                # documents intent and guards against future restructuring.
                raise
            except Exception as emit_exc:
                log.error(
                    "capability_denied_self_emission_failed",
                    extra={
                        "action": action,
                        "actor_id": caller.actor_id,
                        "error_type": type(emit_exc).__name__,
                        "error_message": str(emit_exc),
                    },
                    exc_info=True,
                )
            raise

    # ------------------------------------------------------------------
    # Tool: emit_event — generic escape hatch validated by REGISTRY
    # ------------------------------------------------------------------

    @mcp.tool()
    async def emit_event(
        type: str,  # noqa: A002 — `type` is the canonical envelope field name; F13 deferred (FastMCP arg-coercion does not unwrap a single Pydantic model with field aliases)
        payload: dict[str, object],
        *,
        caller_trace_id: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a typed event to the spine. Validated against REGISTRY.

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (bare UUIDv7 OR
                ``tg:<update_id>``). Invalid values raise ``ValueError``
                surfaced as an MCP tool-error response.

        Raises:
            EventSchemaUnknown: if ``type`` is not registered.
            ValueError: if ``caller_trace_id`` fails Story 9.1 validation.
        """
        validate_caller_trace_id(caller_trace_id)
        # PQ9 (pass-1 review): system-stamped audit event types (those that
        # would receive an ``actor_override`` via ``_emit_overrides``) MUST
        # NOT be emittable from the PUBLIC emit_event tool. Otherwise an
        # MCP client could call
        # ``emit_event(type="capability.denied", payload={...attacker})``
        # and the override would stamp ``Actor(kind="system",
        # id="clawhip-bridge-mcp")`` on the forgery — laundering attacker
        # input as a system-emitted audit record. Force such types to
        # the internal ``_emit`` callsite (only reachable via
        # ``_check_tier_with_self_emit``).
        if type in _emit_overrides:
            raise PermissionError(
                f"event type {type!r} is reserved for system-emitted audit "
                "records; cannot be invoked via the public emit_event tool"
            )
        await _check_tier_with_self_emit(
            "emit_event",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["emit_event"],
            caller_trace_id=caller_trace_id,
        )
        return await _emit(
            type,
            payload,
            parent_event_id,
            caller_trace_id=caller_trace_id,
            schema_version="1.0.0",
            actor_override=None,
        )

    # ------------------------------------------------------------------
    # Typed sugar tools — type literals baked in, no EVT001 needed
    # ------------------------------------------------------------------

    @mcp.tool()
    async def emit_blocker(
        task_id: str,
        reason: str,
        *,
        caller_trace_id: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.blocker_raised`` event.

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``.
        """
        validate_caller_trace_id(caller_trace_id)
        await _check_tier_with_self_emit(
            "emit_blocker",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["emit_blocker"],
            caller_trace_id=caller_trace_id,
        )
        return await _emit(
            "task.blocker_raised",
            {"task_id": task_id, "reason": reason},
            parent_event_id,
            caller_trace_id=caller_trace_id,
        )

    @mcp.tool()
    async def emit_summary(
        task_id: str,
        summary: str,
        *,
        caller_trace_id: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.summary_emitted`` event.

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``.
        """
        validate_caller_trace_id(caller_trace_id)
        await _check_tier_with_self_emit(
            "emit_summary",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["emit_summary"],
            caller_trace_id=caller_trace_id,
        )
        return await _emit(
            "task.summary_emitted",
            {"task_id": task_id, "summary": summary},
            parent_event_id,
            caller_trace_id=caller_trace_id,
        )

    @mcp.tool()
    async def emit_approval_request(
        task_id: str,
        action: str,
        justification: str,
        *,
        caller_trace_id: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.approval_requested`` event.

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``.
        """
        validate_caller_trace_id(caller_trace_id)
        await _check_tier_with_self_emit(
            "emit_approval_request",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["emit_approval_request"],
            caller_trace_id=caller_trace_id,
        )
        return await _emit(
            "task.approval_requested",
            {"task_id": task_id, "action": action, "justification": justification},
            parent_event_id,
            caller_trace_id=caller_trace_id,
        )

    @mcp.tool()
    async def emit_completion(
        task_id: str,
        summary: str,
        *,
        caller_trace_id: str,
        pr_url: str | None = None,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Emit a ``task.completed`` event.

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``.
        """
        validate_caller_trace_id(caller_trace_id)
        await _check_tier_with_self_emit(
            "emit_completion",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["emit_completion"],
            caller_trace_id=caller_trace_id,
        )
        payload: dict[str, object] = {"task_id": task_id, "summary": summary}
        if pr_url is not None:
            payload["pr_url"] = pr_url
        return await _emit(
            "task.completed",
            payload,
            parent_event_id,
            caller_trace_id=caller_trace_id,
        )

    # ------------------------------------------------------------------
    # Resource: recent_events — read-only tail of today's JSONL log
    # ------------------------------------------------------------------

    @mcp.resource("recent-events://current-day/{limit}")
    async def recent_events(limit: int = 50) -> str:
        """Return the last ``limit`` events from today's JSONL log as newline-joined JSON.

        Args:
            limit: Number of trailing events to return. Must be in
                ``[1, 1000]`` (default 50, AC-9 contract).

        Returns:
            Newline-joined canonical-JSON envelopes, or ``""`` when no events
            have been written today.

        Raises:
            ValueError: if ``limit`` is outside ``[1, 1000]``.
        """
        _validate_limit(limit)
        path = current_day_path(base_dir, clock.now())
        try:
            envelopes = list(read_log_lines(path))
        except FileNotFoundError:
            return ""
        recent = envelopes[-limit:]
        return "\n".join(to_canonical_json(env).decode("utf-8") for env in recent)

    return mcp


__all__ = ["build_server"]
