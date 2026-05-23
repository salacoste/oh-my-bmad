"""Capability-tier classification and enforcement helpers (FR37, FR38).

Provides Tier enum, CallerContext, CapabilityOk, check_tier, and
check_tier_with_approval for uniform access control at MCP handler
and HTTP middleware boundaries.
"""

from __future__ import annotations

from capabilities.emit import (
    CapabilityDeniedEmitter,
    build_capability_denied_payload,
    emit_capability_denied_on_deny,
)
from capabilities.tiers import (
    CallerContext,
    CapabilityDenied,
    CapabilityOk,
    Tier,
    check_tier,
    check_tier_with_approval,
)

__version__ = "0.1.0"

__all__ = [
    "CallerContext",
    "CapabilityDenied",
    "CapabilityDeniedEmitter",
    "CapabilityOk",
    "Tier",
    "build_capability_denied_payload",
    "check_tier",
    "check_tier_with_approval",
    "emit_capability_denied_on_deny",
]
