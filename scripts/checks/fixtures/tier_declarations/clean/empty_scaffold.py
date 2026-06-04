"""Fixture: empty TIER_MAP + zero @mcp.tool() handlers — VACUOUSLY CLEAN.

Mirrors git-mcp's current Story 15.2 scaffold: an empty ``TIER_MAP`` and no
registered tools. The scanner must report ZERO violations (nothing to tier).
"""

from __future__ import annotations


class Tier:
    ONE = 1


TIER_MAP: dict[str, Tier] = {}


def validate_caller_trace_id(caller_trace_id: str) -> None:
    if not caller_trace_id:
        raise ValueError("caller_trace_id required")
