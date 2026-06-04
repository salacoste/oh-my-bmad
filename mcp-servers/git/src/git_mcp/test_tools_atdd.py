"""Red-phase ATDD acceptance contracts for the git MCP tools (Epic 15 / Story 15.1).

These are the *executable acceptance criteria* that drive Stories 15.3 (read
tools — ``status``/``diff``/``log``/``branch`` at Tier-1) and 15.4 (mutating
tools — ``add``/``commit`` at Tier-2; ``push``/history-rewrite at Tier-3-gated,
plus ``git.*`` event emission) to green. They establish the ATDD precedent the
rest of the fleet (Epics 16–19) mirrors verbatim.

--------------------------------------------------------------------------------
WHY EVERY CONTRACT IS @pytest.mark.xfail(strict=True)  (ADR-0010)
--------------------------------------------------------------------------------
The git tools DO NOT EXIST yet — ``TIER_MAP`` is empty and ``build_server`` (the
Story 15.2 scaffold) registers zero tools. A plain failing test would turn the
PR-gate (``pytest -m "not slow"``) red and block every intervening commit. ADR-
0010's decision: mark each red-phase contract ``xfail(strict=True)`` so:

  * NOW  — the contract fails at runtime (an assertion or a ``KeyError`` /
    ``CapabilityDenied`` inside the test body), which pytest records as
    ``xfailed`` == an expected failure == a GREEN PR-gate.
  * 15.3 / 15.4 — once the tool lands, the contract starts *passing*; because
    ``strict=True``, an unexpected pass (``xpass``) is itself a hard FAILURE
    that shouts "this contract is satisfied — delete the xfail marker and let it
    guard the implementation as a real green test."
  * SAFETY NET — if a tool *accidentally* already works (e.g. a tier is wired
    early), ``strict=True`` makes the silent xpass fail loudly instead of hiding
    a half-shipped capability.

CRITICAL — the failure MUST occur at RUNTIME (inside the test body), NOT at
import/collection. ``xfail`` does **not** swallow collection-time ``ImportError``.
Therefore this module:

  * imports ONLY symbols that exist in the Story 15.2 scaffold
    (``build_server``, ``GitExecutor``, ``TIER_MAP``, ``validate_caller_trace_id``,
    ``Tier``, ``CapabilityDenied``);
  * NEVER imports a not-yet-existing tool symbol at module top;
  * asserts against the live ``await mcp.list_tools()`` (empty today → runtime
    assertion failure → clean xfail) and against ``TIER_MAP["git.<tool>"]``
    (``KeyError`` today → runtime failure inside the body → clean xfail).

When 15.3/15.4 land, the implementer removes the ``xfail`` marker on each
satisfied contract and the test flips to a real green guard.

--------------------------------------------------------------------------------
CONTRACT MATRIX (one xfail contract per tool × concern)
--------------------------------------------------------------------------------
Tier expectation:   git.status/diff/log/branch == Tier.ONE;
                    git.add/commit == Tier.TWO;
                    git.push/history-rewrite == Tier.THREE.
Registration:       each tool appears in list_tools() with a required
                    ``caller_trace_id`` string field in its input schema.
Tier-3 denial:      git.push / git.rebase (history-rewrite) is DENIED without a
                    matching approval.granted (CapabilityDenied).
Worktree containment: a path escaping GIT_MCP_WORKTREE_ROOT is refused.
caller_trace_id:    a missing/invalid caller_trace_id is rejected.
git.* events:       a successful commit/push emits git.committed / git.pushed
                    carrying the inbound trace_id.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from capabilities import CallerContext, Tier, check_tier
from events import FROZEN_EPOCH, FrozenClock
from events.errors import CapabilityDenied
from mcp.server.fastmcp import FastMCP

from git_mcp.handlers.tools import TIER_MAP, validate_caller_trace_id
from git_mcp.server import GitExecutor, build_server

# ---------------------------------------------------------------------------
# Shared vectors (locally declared — mcp-servers/* may not import tests/*,
# guarded by scripts/check_imports.py; mirrors test_server.py's local copies).
# ---------------------------------------------------------------------------

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_INVALID_TRACE_ID = "not-a-uuid"

# Expected tier per git tool — the contract Stories 15.3 / 15.4 must satisfy by
# populating TIER_MAP. Names are the canonical ``git.<tool>`` MCP tool ids.
_TIER1_TOOLS = ("git.status", "git.diff", "git.log", "git.branch")
_TIER2_TOOLS = ("git.add", "git.commit")
_TIER3_TOOLS = ("git.push", "git.rebase")  # rebase == history-rewrite

# Mutating ops and the git.* event each must emit, carrying the inbound trace_id.
_EVENT_BY_TOOL = {
    "git.commit": "git.committed",
    "git.push": "git.pushed",
}

_XFAIL_REASON = "git tools land in Story 15.3/15.4 — red-phase ATDD contract"


def _build(worktree_root: Path) -> FastMCP:
    """Build the scaffold server exactly as test_server.py does (audit-off path)."""
    return build_server(
        worktree_root=worktree_root,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="worker",
        actor_id="test-worker",
    )


async def _tool_by_name(mcp: FastMCP, name: str) -> object:
    """Return the registered tool object named *name*, or fail the assertion.

    Today list_tools() is empty → this assertion fails at RUNTIME → clean xfail.
    """
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    assert name in by_name, (
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 15.3/15.4"
    )
    return by_name[name]


def _assert_caller_trace_id_required(tool: object, *, name: str) -> None:
    """Assert ``caller_trace_id`` is a required string field on the tool schema.

    Mirrors tests/contract/test_mcp_tool_schemas.py::_assert_caller_trace_id_required.
    """
    schema = tool.inputSchema  # type: ignore[attr-defined]
    required = schema.get("required") or []
    assert "caller_trace_id" in required, (
        f"tool {name!r}: caller_trace_id missing from required: {required!r}"
    )
    properties = schema.get("properties") or {}
    ctid = properties.get("caller_trace_id")
    assert isinstance(ctid, dict) and ctid.get("type") == "string", (
        f"tool {name!r}: caller_trace_id not a required string field: {ctid!r}"
    )


# ===========================================================================
# 1. Tier expectation — TIER_MAP["git.<tool>"] equals the expected Tier.
#    Today TIER_MAP is empty → TIER_MAP[...] raises KeyError inside the body
#    → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER1_TOOLS)
def test_read_tools_are_tier_one(tool: str) -> None:
    """git.status/diff/log/branch are Tier-1 (bounded read) — Story 15.3."""
    assert TIER_MAP[tool] == Tier.ONE


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
def test_write_tools_are_tier_two(tool: str) -> None:
    """git.add/commit are Tier-2 (repo mutation) — Story 15.4."""
    assert TIER_MAP[tool] == Tier.TWO


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_high_risk_tools_are_tier_three(tool: str) -> None:
    """git.push and history-rewrite (rebase) are Tier-3 (approval-gated) — Story 15.4."""
    assert TIER_MAP[tool] == Tier.THREE


# ===========================================================================
# 2. Registration + required caller_trace_id in the input schema.
#    Today list_tools() == [] → _tool_by_name assertion fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS + _TIER2_TOOLS + _TIER3_TOOLS)
async def test_tool_registered_with_required_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Every git tool is registered and requires a ``caller_trace_id`` string."""
    mcp = _build(tmp_path)
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


# ===========================================================================
# 3. Tier-3 denial negative test — push / history-rewrite is DENIED without a
#    matching approval.granted. Encodes the behavior 15.4 must satisfy via
#    check_tier_with_approval(..., approval_lookup=...). Today the tool is not
#    registered → _tool_by_name fails → clean xfail. The denial expectation is
#    expressed as the post-condition the wired handler must raise.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tier3_denied_without_approval(tool: str, tmp_path: Path) -> None:
    """git.push / history-rewrite raises CapabilityDenied when no approval.granted matches.

    The 15.4 handler must call ``check_tier_with_approval`` with an
    ``approval_lookup`` that returns False when no matching ``approval.granted``
    exists for the task; the absence of approval MUST deny the Tier-3 action.
    We assert the denial via the live tool call (today: tool absent → xfail).
    """
    mcp = _build(tmp_path)
    registered = await _tool_by_name(mcp, tool)  # absent today → clean xfail

    # Contract the wired handler must satisfy: a worker calling a Tier-3 git op
    # without a matching approval.granted is denied. We invoke the tool and
    # expect CapabilityDenied (surfaced through the MCP error path).
    fn = registered.fn  # type: ignore[attr-defined]
    with pytest.raises(CapabilityDenied):
        await fn(caller_trace_id=_VALID_TRACE_ID)


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_tier3_denial_semantics_via_check_tier(tool: str) -> None:
    """Tier-3 git ops with no approval are denied by the shared capability gate.

    Independent of tool registration, this pins the *semantics* 15.4 inherits:
    a worker requesting the tool's tier (TIER_MAP[tool], expected Tier.THREE)
    with ``has_approval=False`` must raise CapabilityDenied. Today TIER_MAP[tool]
    raises KeyError → runtime failure → clean xfail; when the tier is wired this
    becomes a real green guard over the denial path.
    """
    required = TIER_MAP[tool]  # KeyError today → clean xfail
    caller = CallerContext(actor_kind="worker", actor_id="w-1", task_id="t-1")
    with pytest.raises(CapabilityDenied):
        check_tier(tool, caller, required, has_approval=False)


# ===========================================================================
# 4. Worktree containment — a path escaping GIT_MCP_WORKTREE_ROOT is refused.
#    GitExecutor._contains already enforces this (Story 15.2). The red-phase
#    contract is that the *tools* refuse such a path; today no tool exists, so
#    we assert the tool-level refusal contract (registered tool rejects an
#    escaping path arg). list_tools() empty → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
async def test_path_escaping_worktree_root_is_refused(tmp_path: Path) -> None:
    """A read tool given a path outside GIT_MCP_WORKTREE_ROOT refuses it.

    The 15.3 tools resolve every path arg against the worktree root with a
    realpath-containment check (no 'repo selection' arg). This contract drives
    that: git.status with an escaping ``path`` must raise. Today the tool is not
    registered → _tool_by_name fails → clean xfail.
    """
    root = tmp_path / "worktree"
    root.mkdir()
    mcp = _build(root)
    status = await _tool_by_name(mcp, "git.status")  # absent today → clean xfail

    escaping = str(root / ".." / "evil")
    fn = status.fn  # type: ignore[attr-defined]
    with pytest.raises((ValueError, PermissionError)):
        await fn(caller_trace_id=_VALID_TRACE_ID, path=escaping)


def test_worktree_executor_refuses_escape(tmp_path: Path) -> None:
    """Reference (non-xfail): the GitExecutor containment guard the tools call.

    This already passes against the Story 15.2 scaffold — it documents the
    invariant the 15.3 tools delegate to. It is NOT an xfail contract; it asserts
    the existing, shipped containment helper so the red-phase suite anchors on a
    known-green primitive.
    """
    ex = GitExecutor(tmp_path)
    assert ex._contains(tmp_path / "ok.txt") is True
    assert ex._contains(tmp_path / ".." / "evil.txt") is False
    assert ex._contains(Path("/etc/passwd")) is False


# ===========================================================================
# 5. caller_trace_id required-and-validated — a missing/invalid value is
#    rejected. The shared validator already rejects bad shapes (Story 15.2
#    scaffold ships it); the RED contract is that the *registered tool* rejects
#    an invalid caller_trace_id. Today no tool exists → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS + _TIER2_TOOLS + _TIER3_TOOLS)
async def test_tool_rejects_invalid_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each git tool rejects an invalid ``caller_trace_id`` before doing work."""
    mcp = _build(tmp_path)
    registered = await _tool_by_name(mcp, tool)  # absent today → clean xfail
    fn = registered.fn  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID)


def test_validator_rejects_invalid_caller_trace_id() -> None:
    """Reference (non-xfail): the shared validator the tools must call first.

    Anchors the suite on the shipped Story 15.2 helper — invalid trace_ids are
    rejected with the Story 9.1 contract message. The xfail contract above pins
    that the registered tool *invokes* this validator first.
    """
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        validate_caller_trace_id(_INVALID_TRACE_ID)
    # Sanity: a valid id passes (no raise).
    validate_caller_trace_id(_VALID_TRACE_ID)


# ===========================================================================
# 6. git.* event emission — a successful mutating op emits the expected git.*
#    event carrying the inbound trace_id. Today the tool is absent, so we drive
#    the contract through the live tool call and inspect the emitted event.
#    list_tools() empty → _tool_by_name fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "event_type"), list(_EVENT_BY_TOOL.items()))
async def test_mutating_op_emits_git_event_with_trace_id(
    tool: str, event_type: str, tmp_path: Path
) -> None:
    """A successful commit/push emits git.committed / git.pushed carrying trace_id.

    The 15.4 handlers emit a typed ``git.*`` event via the spine writer with
    ``trace_id=caller_trace_id``. This contract drives that: invoking the tool
    must produce an event of *event_type* whose ``trace_id`` is the inbound
    caller_trace_id. The emitted-event surface (recorded emitter) is the seam
    15.4 wires; today the tool is absent → _tool_by_name fails → clean xfail.
    """
    mcp = _build(tmp_path)
    registered = await _tool_by_name(mcp, tool)  # absent today → clean xfail

    fn = registered.fn  # type: ignore[attr-defined]
    result = await fn(caller_trace_id=_VALID_TRACE_ID)

    # The 15.4 handler must surface the emitted event so this contract can read
    # its type + trace_id. We assert the structured result names the git.* event
    # and echoes the inbound trace_id (the exact result shape is defined by the
    # 15.4 impl this contract drives).
    emitted = result.get("event") if isinstance(result, dict) else None
    assert emitted is not None, f"{tool}: no git.* event surfaced in result {result!r}"
    assert emitted.get("type") == event_type, (
        f"{tool}: expected {event_type}, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        f"{tool}: emitted {event_type} must carry inbound trace_id"
    )
