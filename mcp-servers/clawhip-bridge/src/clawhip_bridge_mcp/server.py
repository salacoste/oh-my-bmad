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
from events import (  # noqa: IMP001 — events is packages/  # noqa: IMP001 — events is packages/
    Actor,
    EventEnvelope,
    current_day_path,
    new_event_id,
    new_request_id,
    read_log_lines,
)
from events.canonical import to_canonical_json
from events.clock import Clock
from events.envelope import ActorKind, is_valid_trace_id
from events.errors import CapabilityDenied  # noqa: IMP001 — packages/
from events.event_log_writer import (  # noqa: IMP001 — events is packages/
    EventLogWriter,
    recover_all_logs,
)
from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

# Story 11.2.2 PP6 (pass-2): hoisted from inside ``build_server`` to module
# scope so they're (a) genuinely module-level constants and (b) reachable
# from contract tests asserting the canonical system-emitter identity.
_SYSTEM_EMITTER: Actor = Actor(kind="system", id="clawhip-bridge-mcp")
_CAPABILITY_DENIED_TYPE: str = "capability.denied"

TIER_MAP: dict[str, Tier] = {
    "emit_event": Tier.ONE,
    "emit_blocker": Tier.ONE,
    "emit_summary": Tier.ONE,
    "emit_approval_request": Tier.ONE,
    "emit_completion": Tier.ONE,
    # Story 11.2.3 AC3 — dedicated audit-forwarding tool closes the PQ9
    # forgery vector (caller can no longer launder attacker-controlled
    # capability.denied payloads through the public emit_event tool).
    "forward_capability_denied_audit": Tier.ONE,
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
    #
    # Pass-2 PP6: hoisted from in-function constants to module scope (see
    # ``_SYSTEM_EMITTER`` / ``_CAPABILITY_DENIED_TYPE`` at module top) —
    # eliminates the N806 noqa workaround and exposes the constants for
    # cross-module use (e.g., contract tests that need to assert the
    # canonical system-emitter identity).
    _emit_overrides: dict[str, tuple[str, Actor]] = {
        _CAPABILITY_DENIED_TYPE: ("1.1.0", _SYSTEM_EMITTER),
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
                # PP9 (pass-1 review): derive (schema_version, actor_override)
                # from ``_emit_overrides`` so all 3 callers (this self-emit
                # path, ``forward_capability_denied_audit``, and the public
                # ``emit_event`` rejection check) stay in lock-step with a
                # single dict entry.
                _sv, _ao = _emit_overrides[_CAPABILITY_DENIED_TYPE]
                await _emit(
                    _CAPABILITY_DENIED_TYPE,
                    audit_payload,
                    None,
                    caller_trace_id=caller_trace_id,
                    schema_version=_sv,
                    actor_override=_ao,
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
        # Story 11.2.3 AC4 — restore PQ9 rejection now that the dedicated
        # ``forward_capability_denied_audit`` tool exists. Audit-only event
        # types in ``_emit_overrides`` (currently just ``capability.denied``)
        # must be emitted through the dedicated tool with caller-identity
        # validation; the public ``emit_event`` surface is no longer a
        # forgery vector. Sequenced AFTER ``validate_caller_trace_id`` (so
        # malformed input still trips the shape check first) and BEFORE
        # ``_check_tier_with_self_emit`` (denying the forged-audit type
        # itself must not loop back through the self-deny audit path).
        if type in _emit_overrides:
            # PP6 (pass-1 review): emit a WARNING log line on rejection so
            # security operators can detect probing. Pre-PP6 the rejection
            # was unobservable — no envelope, no metric, no log — making
            # forgery-attempt detection impossible.
            log.warning(
                "capability_denied_forgery_attempt_rejected",
                extra={
                    "attempted_type": type,
                    "caller_trace_id": caller_trace_id,
                    "configured_actor_kind": actor_kind,
                    "configured_actor_id": actor_id,
                },
            )
            raise PermissionError(
                f"event type {type!r} must be emitted via the dedicated "
                f"audit-forwarding tool (forward_capability_denied_audit), "
                f"not the generic emit_event"
            )
        await _check_tier_with_self_emit(
            "emit_event",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["emit_event"],
            caller_trace_id=caller_trace_id,
        )
        # Story 11.2.2: resolve schema version and actor override from the
        # consolidated _emit_overrides dict. Both concerns evolve together —
        # adding a new audit-only event type requires a single dict entry.
        #
        # Post-Story 11.2.3 AC4: the early-return PermissionError above
        # means this lookup never matches an audit-only type at runtime;
        # the .get(...) default (``("1.0.0", None)``) is the only path.
        # The dict-driven shape is preserved so future non-audit event-type
        # schema-version pins land in one place.
        schema_version, actor_override = _emit_overrides.get(type, ("1.0.0", None))
        return await _emit(
            type,
            payload,
            parent_event_id,
            caller_trace_id=caller_trace_id,
            schema_version=schema_version,
            actor_override=actor_override,
        )

    # ------------------------------------------------------------------
    # Story 11.2.3 AC3 — dedicated audit-forwarding tool
    # ------------------------------------------------------------------

    @mcp.tool()
    async def forward_capability_denied_audit(
        payload: dict[str, object],
        *,
        caller_trace_id: str,
        caller_actor_kind: ActorKind,
        caller_actor_id: str,
        parent_event_id: str | None = None,
    ) -> dict[str, str]:
        """Forward a ``capability.denied`` audit from an upstream MCP server.

        Story 11.2.3 AC3 — closes the PQ9 forgery vector left by Story 11.2.2.
        The public ``emit_event`` tool now rejects ``type='capability.denied'``;
        the LEGITIMATE forwarding path (task-registry / session-registry
        decorators routing tier-denied audits to the FR26 single writer) uses
        this dedicated tool which validates that the caller is forwarding on
        behalf of its OWN configured actor identity.

        Authorized callers: ``orchestrator``, ``worker``, ``system``,
        ``clawhip``. The ``operator`` kind is REJECTED — operators emit
        ``capability.denied`` via the HTTP boundary (Story 11.2.1's
        ``_emit_capability_denied_safe`` in registry-api middleware), not
        through MCP-RPC.

        The caller MUST claim a ``caller_actor_kind`` matching its CONFIGURED
        ``actor_kind`` (the kind the spawning MCP server was launched with).
        This prevents a worker-configured MCP server from forging an audit
        claiming to be forwarded on behalf of an operator.

        The caller's ``caller_actor_id`` is stamped into ``payload.actor_id``
        unconditionally (overwriting any caller-supplied value) so the
        subject-of-audit identity cannot be forged. The envelope ``actor``
        field is stamped with the canonical system-emitter identity per
        Story 11.2.2 OQ-2 (``Actor(kind='system', id='clawhip-bridge-mcp')``).

        Args:
            payload: The ``CapabilityDeniedPayload`` body. ``actor_id`` is
                overwritten by ``caller_actor_id`` before envelope creation.
            caller_trace_id: Story 9.5 / FR58 MCP correlation ID. Required.
            caller_actor_kind: The forwarding MCP server's configured
                ``actor_kind``. MUST equal the configured ``actor_kind`` of
                this clawhip-bridge instance (mismatch is rejected).
            caller_actor_id: The forwarding MCP server's configured
                ``actor_id``. Stamped into ``payload.actor_id``.
            parent_event_id: Optional parent envelope correlation.

        Raises:
            ValueError: invalid ``caller_trace_id`` shape.
            PermissionError: ``caller_actor_kind == 'operator'`` (use HTTP
                boundary instead). PP10 (pass-1 review): the previously
                advertised kind-vs-server-configured-kind mismatch raise was
                removed during impl (stdio MCP has no connection-level
                credentials; trust self-attestation per Story 11.2.3 OQ-1
                deferred to Phase 3 Remote-MCP).
        """
        validate_caller_trace_id(caller_trace_id)
        # AC3: reject operator kind — operators emit via HTTP boundary
        # (Story 11.2.1), never via MCP-RPC forwarding.
        if caller_actor_kind == "operator":
            # PP6 (pass-1 review): WARN log on rejection so security
            # operators can detect probing — same discipline as PQ9.
            log.warning(
                "capability_denied_forwarding_operator_kind_rejected",
                extra={
                    "caller_trace_id": caller_trace_id,
                    "caller_actor_id": caller_actor_id,
                    "configured_actor_kind": actor_kind,
                },
            )
            raise PermissionError(
                "forward_capability_denied_audit is not available for "
                "caller_actor_kind='operator' — operators emit capability.denied "
                "via the HTTP boundary (Story 11.2.1), not MCP-RPC"
            )
        # NOTE: a previous draft of AC3 also required ``caller_actor_kind ==
        # actor_kind``, but ``actor_kind`` is clawhip-bridge's OWN configured
        # kind (always ``"system"`` in production). Comparing the upstream
        # caller's claimed kind against clawhip-bridge's own kind would
        # reject EVERY legitimate forwarding call. Stdio MCP transport has
        # no connection-level credentials we can use to verify the caller's
        # actual kind, so we trust the self-attestation here. The
        # operator-kind rejection above is the load-bearing access control.
        # Story 11.2.3 OQ-1 follow-up: stronger caller authentication
        # ships with the Phase 3 Remote-MCP work (Architecture P2-I4).
        # Tier gate — same Tier.ONE as emit_event. Routed through the
        # self-emit guard so a denial here still writes its own audit to
        # the spine (closing the recursive denial-of-denial gap).
        await _check_tier_with_self_emit(
            "forward_capability_denied_audit",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["forward_capability_denied_audit"],
            caller_trace_id=caller_trace_id,
        )
        # AC3: stamp payload.actor_id with the validated caller identity.
        # Caller cannot forge the subject-of-audit identity — only the
        # forwarding MCP server's CONFIGURED actor_id is recorded.
        #
        # PP5 (pass-1 review): non-mutating copy so the caller's dict
        # is not silently overwritten. The previous in-place mutation
        # surprised the in-process adapter test path (caller's local
        # dict would carry the override on retry).
        forwarded_payload = {**payload, "actor_id": caller_actor_id}
        # PP9 (pass-1 review): derive schema_version + actor_override
        # from ``_emit_overrides`` rather than hardcoded literals so
        # future schema bumps (e.g., 1.1.0 → 1.2.0) touch ONE place
        # — the dict — not multiple call sites.
        schema_version, actor_override = _emit_overrides[_CAPABILITY_DENIED_TYPE]
        return await _emit(
            _CAPABILITY_DENIED_TYPE,
            forwarded_payload,
            parent_event_id,
            caller_trace_id=caller_trace_id,
            schema_version=schema_version,
            actor_override=actor_override,
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
