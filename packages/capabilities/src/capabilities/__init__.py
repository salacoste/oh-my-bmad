"""Capability-tier classification and enforcement helpers (FR37, FR38).

Provides Tier enum, CallerContext, CapabilityOk, and check_tier for
uniform access control at MCP handler and HTTP middleware boundaries.
"""

from __future__ import annotations

from capabilities.tiers import (
    CallerContext,
    CapabilityDenied,
    CapabilityOk,
    Tier,
    check_tier,
)

__version__ = "0.1.0"

__all__ = [
    "CallerContext",
    "CapabilityDenied",
    "CapabilityOk",
    "Tier",
    "check_tier",
]
