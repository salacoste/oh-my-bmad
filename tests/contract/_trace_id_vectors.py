"""Shared trace_id test vectors for Story 9.5 contract tests.

Story 9.5 pass-2 review (U6) — honest scope statement: this module is the
SOURCE OF TRUTH for ``caller_trace_id`` shape vectors used by
``tests/contract/test_mcp_tool_schemas.py``. It is NOT directly imported by
the three per-server ``mcp-servers/*/test_server.py`` files because
``scripts/check_imports.py`` forbids ``mcp-servers/*`` → ``tests/*``
imports (the constraint preserves mcp-servers' decoupling from the test
harness, an architectural invariant).

Practical consequence: the per-server test files maintain their own
locally-declared copies of the negative-vector parametrize list (11 entries
each) and the ``tg:42`` valid sentinel. These local copies MUST be kept in
sync with this module by hand when vectors here change. The
``test_validate_caller_trace_id_byte_identical_across_servers`` and
``test_validate_caller_trace_id_runtime_messages_identical`` contract tests
provide a tripwire if a behavioural drift creeps in across the three
helpers; vector-list drift across the four test files is, however, not
automatically caught — review-time vigilance is the only mitigation.

If a future architecture change relaxes the ``check_imports`` constraint
(e.g. by moving these vectors into ``packages/events/.../_test_vectors.py``
so cross-package imports become legal), update all four call sites to
import from a single module.

Story 9.5 pass-1 review (T9/T10/T13) — eliminates the drift between
per-server negative vectors (clawhip-bridge had 8 entries; session/task had
6) and the contract-test parametrize list (4 entries). One source of truth
for the Story 9.1 shape contract: bare UUIDv7 OR ``tg:<digits>`` with the
Story 9.4 pass-2 S1 whitespace/CRLF rejection invariants.

The central UUIDv7 fixture (``VALID_UUIDV7_TRACE_ID``) is shape-only — its
value is checked exactly once per assertion site, so sharing one literal
cannot cause cross-test bleed.

Story 9.5 pass-2 review (U15) — the unused per-server
``BRIDGE_TG_TRACE_ID`` / ``SESSION_TG_TRACE_ID`` / ``TASK_TG_TRACE_ID``
constants were removed (dead code; the import-graph constraint above means
per-server test files use their own local literals).
"""

from __future__ import annotations

from typing import Final

VALID_UUIDV7_TRACE_ID: Final[str] = "01917e5c-a7d1-7000-8abc-0123456789ab"
VALID_TG_TRACE_ID: Final[str] = "tg:42"

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
    "INVALID_TG_BOUNDARY_TRACE_IDS",
    "INVALID_TRACE_IDS",
    "VALID_TG_BOUNDARY_TRACE_IDS",
    "VALID_TG_TRACE_ID",
    "VALID_UUIDV7_TRACE_ID",
]
