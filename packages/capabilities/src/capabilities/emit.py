"""Story 11.2.2 — MCP-boundary capability.denied emission helper (I/O-free interface).

This module provides ``emit_capability_denied_on_deny`` — a decorator that
wraps a tier-gated MCP tool handler and, on ``CapabilityDenied``, invokes
an injected emitter callable to record a ``capability.denied`` v1.1.0
audit event before re-raising. The emitter contract is I/O-free at this
layer — concrete implementations live in each MCP server's adapters
(typically a ``clawhip_client.MCPClientGroup.emit_event`` invocation).

Mirrors the discipline from Story 11.2.1's HTTP boundary helper
(``services/registry-api/.../adapters/middleware.py:_emit_capability_denied_safe``):

- PP1: explicit ``except (asyncio.CancelledError, KeyboardInterrupt): raise``
  before the broad PD-1 swallow.
- PP3: typed ``_TIER_INT_TO_LITERAL: dict[int, _TierLiteral]`` for closed-domain
  narrowing (re-uses Story 11.2.1's enum-drift discipline; the contract
  test ``test_tier_int_to_literal_covers_every_denyable_tier_member`` covers
  the source-of-truth map in middleware.py — this module imports it).
- PP4: ``actor_id or "unknown"`` fallback (handles None case).
- PP5: ``required_tier.value`` (decouples from IntEnum specifically).
- PP6/PP7: tests round-trip via ``CapabilityDeniedPayload.model_validate``
  and ``from_canonical_json``.

PD-1 fail-soft contract: I/O-shaped emission errors (RuntimeError, OSError,
ValidationError) are logged at ERROR and SWALLOWED. The original
``CapabilityDenied`` exception is ALWAYS re-raised so MCP-transport-level
error semantics are preserved (AC6). Cancellation propagates explicitly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar

from capabilities.tiers import CapabilityDenied, Tier

_log = logging.getLogger("capabilities.emit")

_P = ParamSpec("_P")
_R = TypeVar("_R")

_TierLiteral = Literal["tier1", "tier2", "tier3"]

#: Closed-domain map from ``Tier.value`` → payload literal. Mirror of
#: ``services/registry-api/src/registry_api/adapters/middleware.py
#: ::_TIER_INT_TO_LITERAL`` — the HTTP boundary uses the same map.
#: The Story 11.2.1 enum-drift contract test
#: (``test_tier_int_to_literal_covers_every_denyable_tier_member``) is
#: the single source-of-truth check; this module re-vends the same data
#: so MCP-boundary callers don't need to cross the services→packages
#: import boundary. A separate test in this package asserts byte-equality.
_TIER_INT_TO_LITERAL: dict[int, _TierLiteral] = {1: "tier1", 2: "tier2", 3: "tier3"}


#: Emitter contract — I/O-free at this layer. Concrete implementations
#: typically wrap an ``MCPClientGroup.emit_event`` call against
#: clawhip-bridge (the FR26 single-writer surface).
#:
#: Arguments:
#:   - event_type: always ``"capability.denied"`` (positional for symmetry
#:     with FastMCP's ``emit_event`` tool signature).
#:   - payload: dict with ``tier``, ``boundary``, ``actor_id``,
#:     ``attempted_action``, ``reason``.
#:
#: The emitter MUST be async; failures may raise any Exception. PD-1
#: fail-soft handling lives in the decorator below.
CapabilityDeniedEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


def build_capability_denied_payload(
    *,
    required_tier: Tier,
    boundary: Literal["http", "mcp"],
    actor_id: str | None,
    attempted_action: str,
    reason: str,
) -> dict[str, Any]:
    """Build the ``capability.denied`` payload dict from raw fields.

    Returns a plain dict (not a Pydantic model) because the emitter
    contract typically forwards through an MCP tool invocation whose
    payload arg is ``dict[str, object]``. The Pydantic validation
    happens server-side in clawhip-bridge's ``emit_event`` via
    ``EventEnvelope.create``.

    PP4: ``actor_id or "unknown"`` handles both the missing-attribute
    AND explicit-None cases (the latter would otherwise pass through
    and be rejected by Pydantic's ``min_length=1`` at validation time,
    landing in the PD-1 swallow + silently dropping the audit).

    PP5: ``required_tier.value`` not ``int(required_tier)`` —
    decouples from ``Tier`` being an ``IntEnum`` specifically.

    Raises:
        KeyError: if ``required_tier.value`` is not in
            ``_TIER_INT_TO_LITERAL`` (caught at test time by the
            enum-drift contract test ``test_tier_int_to_literal_*``).
    """
    return {
        "tier": _TIER_INT_TO_LITERAL[required_tier.value],
        "boundary": boundary,
        "actor_id": actor_id or "unknown",
        "attempted_action": attempted_action,
        "reason": reason,
    }


def emit_capability_denied_on_deny(
    *,
    boundary: Literal["mcp"],
    emitter: CapabilityDeniedEmitter,
    attempted_action: str,
    get_actor_id: Callable[..., str | None] = lambda *_a, **_kw: None,
) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Decorator: wrap an async tier-gated handler to emit on ``CapabilityDenied``.

    Args:
        boundary: payload field — always ``"mcp"`` for MCP-server callers
            (HTTP boundary lives in registry-api's helper instead).
        emitter: injected async callable that records the audit event.
            Typically wraps ``MCPClientGroup.emit_event(...)`` against
            clawhip-bridge. PD-1 fail-soft: errors raised by the emitter
            are logged + swallowed.
        attempted_action: payload field — typically the tool name
            (e.g. ``"task.add_note"``). Pass-through; not derived from
            the handler name so renames don't silently break the audit.
        get_actor_id: callable that extracts the caller's actor_id from
            the decorated handler's args/kwargs. Default returns None
            → emits with ``actor_id="unknown"`` (PP4 fallback).

    The decorated handler MUST be async. ``CapabilityDenied`` is RE-RAISED
    after emission so MCP transport-level error semantics are preserved
    (AC6). All other exceptions propagate unchanged.

    PP1: ``asyncio.CancelledError`` and ``KeyboardInterrupt`` are NOT
    caught by the emitter's PD-1 swallow — they propagate so task
    cancellation works correctly.
    """

    def _decorator(handler: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        @wraps(handler)
        async def _wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            try:
                return await handler(*args, **kwargs)
            except CapabilityDenied as exc:
                await _emit_audit_safe(
                    emitter=emitter,
                    boundary=boundary,
                    required_tier=Tier(exc.required_tier),
                    actor_id=get_actor_id(*args, **kwargs),
                    attempted_action=attempted_action,
                    reason=exc.reason,
                )
                raise

        return _wrapper

    return _decorator


async def _emit_audit_safe(
    *,
    emitter: CapabilityDeniedEmitter,
    boundary: Literal["mcp"],
    required_tier: Tier,
    actor_id: str | None,
    attempted_action: str,
    reason: str,
) -> None:
    """Build payload + invoke emitter with PD-1 fail-soft + cancel-propagation.

    PP1: explicit ``except (asyncio.CancelledError, KeyboardInterrupt): raise``
    before the broad ``except Exception`` — cancellation MUST propagate.

    All other exceptions are logged at ERROR and swallowed so the calling
    handler's ``raise`` (re-raising the original ``CapabilityDenied``) is
    NEVER blocked by emission failures (broken pipe to clawhip-bridge,
    ValidationError from schema mismatch, etc.).
    """
    try:
        payload = build_capability_denied_payload(
            required_tier=required_tier,
            boundary=boundary,
            actor_id=actor_id,
            attempted_action=attempted_action,
            reason=reason,
        )
        await emitter("capability.denied", payload)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as emit_exc:
        _log.error(
            "capability_denied_emission_failed",
            extra={
                "boundary": boundary,
                "attempted_action": attempted_action,
                "actor_id": actor_id or "unknown",
                "error_type": type(emit_exc).__name__,
                "error_message": str(emit_exc),
            },
        )


__all__ = [
    "CapabilityDeniedEmitter",
    "build_capability_denied_payload",
    "emit_capability_denied_on_deny",
]
