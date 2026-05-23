"""Bounded-write MCP tool handlers — session register, heartbeat, close (Story 5.9).

Phase 1: validated stubs. They check tier, validate session/task exists, validate
parameters, and return ``{"ok": true}``. Actual persistence routes through the
event spine via clawhip-bridge — deferred to Story 5.12 integration.

Story 11.2.2: every tier-gated handler is wrapped by
``emit_capability_denied_on_deny`` so a ``CapabilityDenied`` from
``check_tier`` emits a ``capability.denied`` v1.1.0 audit envelope via
clawhip-bridge (FR26-compliant single writer) before re-raising. The
emitter is injected via an ``EmitterHolder`` populated by the FastMCP
lifespan in ``app/main.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from capabilities import CallerContext, Tier, check_tier
from capabilities.emit import emit_capability_denied_on_deny
from events.envelope import ActorKind, is_valid_trace_id  # noqa: IMP001 — packages/
from registry_state.schema import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    Event,
    Session,
    Task,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from session_registry_mcp.adapters.clawhip_client import EmitterHolder

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)

TIER_MAP: dict[str, Tier] = {
    "session.register": Tier.ONE,
    "session.heartbeat": Tier.ONE,
    "session.close": Tier.ONE,
}


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

    NOTE: Duplicated byte-identically in ``clawhip-bridge`` and
    ``task-registry``. mcp-servers cannot share code per Story 5.8's
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


def _make_approval_lookup(
    session_maker: async_sessionmaker[AsyncSession],
) -> Callable[[str, str], Awaitable[bool]]:
    """Return an async ``(task_id, action) -> bool`` callable.

    Queries the materialized ``Event`` table for ``approval.granted``
    events matching *task_id*. Approvals are currently task-scoped only
    (the *action* parameter is accepted but unused — reserved for
    future wildcard matching). Story 6.5 adds the emitter; this
    lookup is ready for it.

    NOTE: Duplicated in task-registry — keep in sync. Extracting to a
    shared package is blocked by the import-graph constraint (mcp-servers
    cannot share code directly; shared helpers belong in packages/ or
    services/).
    """

    async def _lookup(task_id: str, action: str) -> bool:  # noqa: ARG001 — action reserved for future wildcard matching
        async with session_maker() as session:
            stmt = select(Event).where(
                Event.type == "approval.granted",
                Event.task_id == task_id,
            )
            result = await session.execute(stmt)
            return result.scalars().first() is not None

    return _lookup


async def _validate_task_exists(
    session_maker: async_sessionmaker[AsyncSession],
    task_id: str,
) -> bool:
    """Return True if task exists in the materialized state."""
    async with session_maker() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none() is not None


async def _validate_session_exists(
    session_maker: async_sessionmaker[AsyncSession],
    session_id: str,
) -> bool:
    """Return True if session exists in the materialized state."""
    async with session_maker() as session:
        result = await session.execute(select(Session).where(Session.id == session_id))
        return result.scalar_one_or_none() is not None


def _make_actor_id_extractor(actor_id: str) -> Callable[..., str]:
    """Return a ``get_actor_id`` callable for ``emit_capability_denied_on_deny``.

    Mirrors the task-registry sibling helper. The configured ``actor_id``
    is the calling actor's identity for the duration of this server
    process; tool kwargs do not carry actor identity.
    """

    def _get_actor_id(*_args: object, **_kwargs: object) -> str:
        return actor_id

    return _get_actor_id


def register_tools(
    mcp: FastMCP,
    session_maker: async_sessionmaker[AsyncSession],
    actor_kind: ActorKind,
    actor_id: str,
    emitter_holder: EmitterHolder | None = None,
) -> None:
    """Register 3 bounded-write MCP tools on *mcp*.

    Story 11.2.2: when *emitter_holder* is provided, every tier-gated
    handler is wrapped with ``emit_capability_denied_on_deny`` so a
    ``CapabilityDenied`` from ``check_tier`` emits a ``capability.denied``
    audit envelope via clawhip-bridge before re-raising. When None,
    the handlers are registered without wrapping (test fixtures that
    don't need the audit path).
    """

    get_actor_id = _make_actor_id_extractor(actor_id)

    def _maybe_wrap(
        tool_name: str,
    ) -> Callable[
        [Callable[..., Awaitable[dict[str, object]]]],
        Callable[..., Awaitable[dict[str, object]]],
    ]:
        """Apply the audit-emission decorator iff an emitter holder is wired."""
        if emitter_holder is None:
            return lambda fn: fn
        return emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter_holder.emit_event,
            attempted_action=tool_name,
            get_actor_id=get_actor_id,
        )

    @mcp.tool()
    @_maybe_wrap("session.register")
    async def session_register(
        task_id: str,
        worker_kind: str,
        worktree_path: str,
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Register a new session (Tier-1 bounded write, Phase 1 stub).

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``. Phase 1 stub logs this at
                INFO level so the contract is observable before Story 5.12
                envelope emission lands.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "session.register",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id, task_id=task_id),
            TIER_MAP["session.register"],
        )
        if not task_id or not worker_kind:
            return {"ok": False, "error": "task_id and worker_kind are required"}
        exists = await _validate_task_exists(session_maker, task_id)
        if not exists:
            return {"ok": False, "error": f"task {task_id!r} not found"}
        # Story 9.5 pass-1 T5/T15: caller_trace_id pre-validated by
        # validate_caller_trace_id() above (T15 sync invariant). Use stdlib
        # structured logging via ``extra=`` so the fields are queryable and
        # CRLF/control characters in ``caller_trace_id`` cannot inject fake
        # log lines (shape contract already rejects them — defense in depth).
        log.info(
            "session.register (stub)",
            extra={
                "task_id": task_id,
                "worker_kind": worker_kind,
                "actor_id": actor_id,
                "caller_trace_id": caller_trace_id,
            },
        )
        return {"ok": True}

    @mcp.tool()
    @_maybe_wrap("session.heartbeat")
    async def session_heartbeat(
        session_id: str,
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Update session heartbeat timestamp (Tier-1 bounded write, Phase 1 stub).

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``. Phase 1 stub logs this at
                INFO level so the contract is observable before Story 5.12
                envelope emission lands.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "session.heartbeat",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["session.heartbeat"],
        )
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        exists = await _validate_session_exists(session_maker, session_id)
        if not exists:
            return {"ok": False, "error": f"session {session_id!r} not found"}
        # Story 9.5 pass-1 T5/T15: caller_trace_id pre-validated above.
        # Structured logging via ``extra=`` for queryability + CRLF safety.
        log.info(
            "session.heartbeat (stub)",
            extra={
                "session_id": session_id,
                "actor_id": actor_id,
                "caller_trace_id": caller_trace_id,
            },
        )
        return {"ok": True}

    @mcp.tool()
    @_maybe_wrap("session.close")
    async def session_close(
        session_id: str,
        *,
        caller_trace_id: str,
    ) -> dict[str, object]:
        """Close a session (Tier-1 bounded write, Phase 1 stub).

        Args:
            caller_trace_id: Story 9.5 / FR58 MCP. Required correlation ID
                matching the Story 9.1 contract (UUIDv7 OR ``tg:<update_id>``).
                Invalid values raise ``ValueError``. Phase 1 stub logs this at
                INFO level so the contract is observable before Story 5.12
                envelope emission lands.
        """
        validate_caller_trace_id(caller_trace_id)
        check_tier(
            "session.close",
            CallerContext(actor_kind=actor_kind, actor_id=actor_id),
            TIER_MAP["session.close"],
        )
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        exists = await _validate_session_exists(session_maker, session_id)
        if not exists:
            return {"ok": False, "error": f"session {session_id!r} not found"}
        # Story 9.5 pass-1 T5/T15: caller_trace_id pre-validated above.
        # Structured logging via ``extra=`` for queryability + CRLF safety.
        log.info(
            "session.close (stub)",
            extra={
                "session_id": session_id,
                "actor_id": actor_id,
                "caller_trace_id": caller_trace_id,
            },
        )
        return {"ok": True}
