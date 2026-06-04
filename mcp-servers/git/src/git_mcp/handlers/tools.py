"""git-mcp MCP tool handlers (Epic 15 / Story 15.2 scaffold).

Story 15.2 is the SCAFFOLD only — ``TIER_MAP`` is empty and NO ``@mcp.tool()``
handlers are registered. The git tools (``git.status``, ``git.diff``,
``git.commit``, ``git.push``, …) land in Stories 15.3 / 15.4, where each entry
in ``TIER_MAP`` gates a bounded git operation routed through the sandboxed
``GitExecutor``.

The ``validate_caller_trace_id`` helper is shipped now so the first tool added
in 15.3 inherits the FR58 caller-trace-id contract without re-deriving it. Its
body is duplicated byte-identically across clawhip-bridge, task-registry, and
session-registry (mcp-servers cannot share code per Story 5.8's import-graph
constraint); the same drift guard will extend to git-mcp once it registers
tools.
"""

from __future__ import annotations

import logging

from capabilities import Tier
from events.envelope import is_valid_trace_id  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# Story 15.2 scaffold — empty until the git tools land in 15.3 / 15.4. Each
# future git operation registers its required capability tier here (mirroring
# the task-registry / clawhip-bridge ``TIER_MAP`` shape).
TIER_MAP: dict[str, Tier] = {}


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
