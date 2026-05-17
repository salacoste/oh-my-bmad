"""Shared trace_id test vectors for Story 9.5 contract + per-server tests.

Centralises the positive and negative ``caller_trace_id`` shape vectors used
by:

* ``tests/contract/test_mcp_tool_schemas.py``
* ``mcp-servers/clawhip-bridge/.../test_server.py``
* ``mcp-servers/session-registry/.../test_server.py``
* ``mcp-servers/task-registry/.../test_server.py``

Story 9.5 pass-1 review (T9/T10/T13) — eliminates the drift between
per-server negative vectors (clawhip-bridge had 8 entries; session/task had
6) and the contract-test parametrize list (4 entries). One source of truth
for the Story 9.1 shape contract: bare UUIDv7 OR ``tg:<digits>`` with the
Story 9.4 pass-2 S1 whitespace/CRLF rejection invariants.

The cross-test-class bleed concern (pass-1 Edge H6) is addressed by
per-test-CLASS unique values where envelope.trace_id assertions matter —
see ``BRIDGE_TG_TRACE_ID`` / ``SESSION_TG_TRACE_ID`` /
``TASK_TG_TRACE_ID``. The central UUIDv7 fixture (``VALID_UUIDV7_TRACE_ID``)
is shape-only — its value is checked exactly once per assertion site, so
sharing one literal cannot cause cross-test bleed.
"""

from __future__ import annotations

from typing import Final

VALID_UUIDV7_TRACE_ID: Final[str] = "01917e5c-a7d1-7000-8abc-0123456789ab"
VALID_TG_TRACE_ID: Final[str] = "tg:42"

# Per-server tg: distinct values used where the test asserts on the
# envelope.trace_id field and we want a unique signature per test class to
# guard against accidental cross-test fixture bleed (pass-1 Edge H6).
BRIDGE_TG_TRACE_ID: Final[str] = "tg:90501"
SESSION_TG_TRACE_ID: Final[str] = "tg:90502"
TASK_TG_TRACE_ID: Final[str] = "tg:90503"

# Comprehensive negative shape vectors — covers Story 9.1 F2 (leading-zero
# rejection), Story 9.4 pass-2 S1 (whitespace/CRLF guard), and Story 9.5
# pass-1 T3 (CRLF-injection attempt).
INVALID_TRACE_IDS: Final[tuple[str, ...]] = (
    "",
    "bad-format",
    "not-a-uuid",
    "tg:",
    "tg:0",
    "tg:abc",
    "01917e5c-a7d1-7000-8abc-0123456789ab\n",  # trailing LF
    " 01917e5c-a7d1-7000-8abc-0123456789ab",  # leading space
    "01917e5c-a7d1-7000-8abc-0123456789ab\t",  # trailing tab
    "01917e5c-a7d1-7000-8abc-0123456789ab\r\n",  # CRLF
    "tg:42\nX-Evil: 1",  # CRLF-injection attempt (T3)
)

# Boundary positive vectors for the ``tg:`` form (Story 9.5 pass-1 T13).
# Story 9.1 uses signed int64 as the upper bound (max = 9_223_372_036_854_775_807,
# 19 digits). The regex is r"\Atg:(?P<update_id>[1-9][0-9]{0,18})\Z" so the
# max valid string is tg:9223372036854775807 (19 digits, ≤ INT64_MAX).
VALID_TG_BOUNDARY_TRACE_IDS: Final[tuple[str, ...]] = (
    "tg:1",  # smallest non-zero
    "tg:9999999999",  # 10-digit
    "tg:9223372036854775807",  # max signed int64 (Story 9.1 upper bound)
)

# Boundary negative vectors for the ``tg:`` form (Story 9.5 pass-1 T13).
INVALID_TG_BOUNDARY_TRACE_IDS: Final[tuple[str, ...]] = (
    "tg:01",  # leading zero non-tg:0
    "tg:-1",  # negative
    "tg:18446744073709551615",  # u64 max — exceeds signed int64 (20 digits, > INT64_MAX)
)

__all__ = [
    "BRIDGE_TG_TRACE_ID",
    "INVALID_TG_BOUNDARY_TRACE_IDS",
    "INVALID_TRACE_IDS",
    "SESSION_TG_TRACE_ID",
    "TASK_TG_TRACE_ID",
    "VALID_TG_BOUNDARY_TRACE_IDS",
    "VALID_TG_TRACE_ID",
    "VALID_UUIDV7_TRACE_ID",
]
