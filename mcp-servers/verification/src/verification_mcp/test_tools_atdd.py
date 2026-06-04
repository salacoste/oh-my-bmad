"""Red-phase ATDD acceptance contracts for the verification MCP tools (Epic 17 / Story 17.1).

These are the *executable acceptance criteria* that drive Story 17.3 (the
sandboxed Tier-2 tools — ``verification.run_build`` / ``verification.run_tests``)
and Story 17.4 (structured ``{pass/fail, logs, coverage}`` result + the
``verification.*`` event emission) to green. They mirror the git-mcp Story 15.1
ATDD precedent verbatim — verification-mcp is git-mcp's closest sibling because
both run SANDBOXED SUBPROCESSES confined to the active worktree.

Tool names (canonical dotted MCP ids, per epics.md Story 17.3):
  * ``verification.run_build``  — Tier-2; runs the project's build recipe.
  * ``verification.run_tests``  — Tier-2; runs the project's test recipe.

--------------------------------------------------------------------------------
WHY EVERY CONTRACT IS @pytest.mark.xfail(strict=True)  (ADR-0010)
--------------------------------------------------------------------------------
The verification tools DO NOT EXIST yet — ``TIER_MAP`` is empty and
``build_server`` (the Story 17.2 scaffold) registers zero tools. A plain failing
test would turn the PR-gate (``pytest -m "not slow"``) red and block every
intervening commit. ADR-0010's decision: mark each red-phase contract
``xfail(strict=True)`` so:

  * NOW  — the contract fails at runtime (an assertion or a ``KeyError`` inside
    the test body), which pytest records as ``xfailed`` == an expected failure ==
    a GREEN PR-gate.
  * 17.3 / 17.4 — once the tool lands, the contract starts *passing*; because
    ``strict=True``, an unexpected pass (``xpass``) is itself a hard FAILURE that
    shouts "this contract is satisfied — delete the xfail marker and let it guard
    the implementation as a real green test."
  * SAFETY NET — if a tool *accidentally* already works, ``strict=True`` makes the
    silent xpass fail loudly instead of hiding a half-shipped capability.

CRITICAL — the failure MUST occur at RUNTIME (inside the test body), NOT at
import/collection. ``xfail`` does **not** swallow collection-time ``ImportError``.
Therefore this module:

  * imports ONLY symbols that exist in the Story 17.2 scaffold
    (``build_server``, ``VerificationExecutor``, ``TIER_MAP``,
    ``validate_caller_trace_id``, ``Tier``);
  * NEVER imports a not-yet-existing tool symbol at module top;
  * asserts against the live ``await mcp.list_tools()`` (empty today → runtime
    assertion failure → clean xfail) and against ``TIER_MAP["verification.<tool>"]``
    (``KeyError`` today → runtime failure inside the body → clean xfail).

When 17.3/17.4 land, the implementer removes the ``xfail`` marker on each
satisfied contract and the test flips to a real green guard.

--------------------------------------------------------------------------------
CONTRACT MATRIX (one xfail contract per tool × concern)
--------------------------------------------------------------------------------
Tier expectation:    verification.run_build/run_tests == Tier.TWO.
Registration:        each tool appears in list_tools() with a required
                     ``caller_trace_id`` string field in its input schema.
Structured result:   a successful run returns ``{pass/fail (ok), logs, coverage}``.
Sandbox contract:    a path/cwd escaping the worktree root is refused
                     (ValueError/PermissionError); the executor's ``_contains``
                     rejects ``../escape`` (a NON-xfail reference test on the
                     executor directly); the child-env carries NO secret beyond
                     the allowlist; a hung run is killed by the wall-clock timeout.
caller_trace_id:     a missing/invalid caller_trace_id is rejected
                     (ValueError, "Story 9.1 contract").
verification.* events: a successful run emits a ``verification.*`` event carrying
                     the inbound trace_id (xfail until 17.4).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from capabilities import Tier
from events import FROZEN_EPOCH, FrozenClock
from mcp.server.fastmcp import FastMCP

from verification_mcp.handlers.tools import TIER_MAP, validate_caller_trace_id
from verification_mcp.server import VerificationExecutor, build_server

# ---------------------------------------------------------------------------
# Shared vectors (locally declared — mcp-servers/* may not import tests/*,
# guarded by scripts/check_imports.py; mirrors git-mcp test_tools_atdd.py).
# ---------------------------------------------------------------------------

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_INVALID_TRACE_ID = "not-a-uuid"

# Expected tier per verification tool — the contract Story 17.3 must satisfy by
# populating TIER_MAP. Names are the canonical ``verification.<tool>`` MCP ids.
_TIER2_TOOLS = ("verification.run_build", "verification.run_tests")

# Each verification tool emits a ``verification.*`` event carrying the inbound
# trace_id (Story 17.4). The exact event type is ``verification.completed`` for
# both (recipe-invoked + exit-status discriminate inside the payload).
_EVENT_BY_TOOL = {
    "verification.run_build": "verification.completed",
    "verification.run_tests": "verification.completed",
}

_XFAIL_REASON = "verification tools land in Story 17.3/17.4 — red-phase ATDD contract"


def _build(worktree_root: Path) -> FastMCP:
    """Build the scaffold server exactly as the audit-off test path does."""
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
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 17.3/17.4"
    )
    return by_name[name]


async def _tool_fn(mcp: FastMCP, name: str):  # type: ignore[no-untyped-def]
    """Return the callable for the registered tool *name*, or fail the assertion.

    ``mcp.list_tools()`` returns ``mcp.types.Tool`` schema objects (no ``.fn``);
    the invocable handler lives on the FastMCP tool-manager. We assert presence
    against ``list_tools()`` (empty today for absent tools → clean xfail) and
    return the manager's ``.fn``.
    """
    tools = await mcp.list_tools()
    by_name = {t.name for t in tools}
    assert name in by_name, (
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 17.3/17.4"
    )
    return mcp._tool_manager._tools[name].fn


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
# 1. Tier expectation — TIER_MAP["verification.<tool>"] equals Tier.TWO.
#    Today TIER_MAP is empty → TIER_MAP[...] raises KeyError inside the body
#    → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
def test_verification_tools_are_tier_two(tool: str) -> None:
    """verification.run_build/run_tests are Tier-2 (run project code, no external mutation)."""
    assert TIER_MAP[tool] == Tier.TWO


# ===========================================================================
# 2. Registration + required caller_trace_id in the input schema.
#    Today list_tools() == [] → _tool_by_name assertion fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
async def test_tool_registered_with_required_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each verification tool registers and requires ``caller_trace_id`` — Story 17.3 (GREEN)."""
    mcp = _build(tmp_path)
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


# ===========================================================================
# 3. Structured-result shape — a successful run returns {pass/fail, logs,
#    coverage}. Today the tool is not registered → _tool_fn fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
async def test_tool_returns_structured_result(tool: str, tmp_path: Path) -> None:
    """A successful verification run returns ``{pass/fail (ok), logs, coverage}`` (GREEN).

    The 17.4 handler runs the recipe in the sandbox and returns a structured
    result carrying the pass/fail flag, the captured logs, and a coverage summary
    (FR74). We drive the contract through the live tool call against a trivial
    worktree and assert the keys are present. Today the tool is absent →
    ``_tool_fn`` fails → clean xfail.
    """
    root = tmp_path / "worktree"
    root.mkdir()
    mcp = _build(root)
    fn = await _tool_fn(mcp, tool)

    result = await fn(caller_trace_id=_VALID_TRACE_ID)
    assert isinstance(result, dict), f"{tool}: result is not a dict: {result!r}"
    # Pass/fail flag — accept either an ``ok`` boolean or an explicit ``pass``
    # key; logs + coverage are required structured fields.
    assert ("ok" in result) or ("pass" in result), (
        f"{tool}: result missing pass/fail flag (ok|pass): {sorted(result)}"
    )
    assert "logs" in result, f"{tool}: result missing 'logs': {sorted(result)}"
    assert "coverage" in result, f"{tool}: result missing 'coverage': {sorted(result)}"


# ===========================================================================
# 4. Sandbox contract — a path/cwd escaping the worktree root is refused; the
#    child-env carries no secret beyond the allowlist; a hung run is killed by
#    the wall-clock timeout. The escaping-path tool refusal is an xfail (no tool
#    today); the executor-level guards are NON-xfail reference tests on the
#    shipped Story 17.2 primitives.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
async def test_tool_refuses_path_escaping_worktree_root(tool: str, tmp_path: Path) -> None:
    """A verification tool given a path/cwd outside the worktree root refuses it.

    The 17.3 tools resolve any path/cwd arg against the worktree root with a
    realpath-containment check (no 'repo selection' arg). This contract drives
    that refusal. Today the tool is not registered → _tool_fn fails → clean xfail.
    """
    root = tmp_path / "worktree"
    root.mkdir()
    mcp = _build(root)
    fn = await _tool_fn(mcp, tool)

    escaping = str(root / ".." / "evil")
    with pytest.raises((ValueError, PermissionError)):
        await fn(caller_trace_id=_VALID_TRACE_ID, cwd=escaping)


def test_worktree_executor_refuses_escape(tmp_path: Path) -> None:
    """Reference (non-xfail): the VerificationExecutor containment guard the tools call.

    This already passes against the Story 17.2 scaffold — it documents the
    invariant the 17.3 tools delegate to. It is NOT an xfail contract; it asserts
    the existing, shipped containment helper so the red-phase suite anchors on a
    known-green primitive (mirror of git-mcp's test_worktree_executor_refuses_escape).
    """
    ex = VerificationExecutor(tmp_path)
    assert ex._contains(tmp_path / "ok.txt") is True
    assert ex._contains(tmp_path / ".." / "evil.txt") is False
    assert ex._contains(Path("/etc/passwd")) is False


def test_executor_child_env_carries_no_secret(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Reference (non-xfail): the recipe child-env is the allowlist only — NO secrets.

    The Story 17.2 ``_build_env`` filters the parent env to ``_ENV_ALLOWLIST``
    (no ``os.environ.copy``). A planted secret in the parent env MUST NOT cross
    into the child-env the recipe subprocess inherits. Anchors the sandbox's
    env-allowlist invariant the 17.3 ``run_recipe`` spawn-site relies on.
    """
    from verification_mcp.server import _ENV_ALLOWLIST, _build_env

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_never_leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-never-leak")
    env = _build_env()
    for secret in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY"):
        assert secret not in env, f"{secret!r} leaked into the recipe child-env"
    # Every key that survives MUST be allowlisted (plus the forced PYTHONUNBUFFERED).
    for key in env:
        assert key in _ENV_ALLOWLIST or key == "PYTHONUNBUFFERED", (
            f"non-allowlisted key {key!r} present in recipe child-env"
        )


@pytest.mark.asyncio
async def test_executor_timeout_kills_hung_recipe(tmp_path: Path) -> None:
    """Reference (non-xfail): a wedged recipe is killed by the wall-clock timeout.

    The Story 17.2 ``run_recipe`` bounds every invocation with
    ``asyncio.wait_for`` and kills+reaps on expiry, raising
    ``VerificationTimeout``. We spawn a deliberately-hung ``sleep`` with a tiny
    timeout and assert it is terminated (no leaked process). Anchors the
    timeout invariant the 17.3 tools inherit.
    """
    from verification_mcp.server import VerificationTimeout

    ex = VerificationExecutor(tmp_path)
    with pytest.raises(VerificationTimeout):
        await ex.run_recipe(["sleep", "30"], timeout=0.2)


# ===========================================================================
# 5. caller_trace_id required-and-validated — a missing/invalid value is
#    rejected. The shared validator already rejects bad shapes (Story 17.2
#    scaffold ships it); the RED contract is that the *registered tool* rejects
#    an invalid caller_trace_id. Today no tool exists → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
async def test_tool_rejects_invalid_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each verification tool rejects an invalid ``caller_trace_id`` first — Story 17.3 (GREEN).

    ``validate_caller_trace_id`` runs FIRST in every handler body, BEFORE the tier
    gate / recipe spawn. Today no tool exists → ``_tool_fn`` fails → clean xfail;
    when wired this becomes a real green guard over the validate-first contract.
    """
    mcp = _build(tmp_path)
    fn = await _tool_fn(mcp, tool)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID)


def test_validator_rejects_invalid_caller_trace_id() -> None:
    """Reference (non-xfail): the shared validator the tools must call first.

    Anchors the suite on the shipped Story 17.2 helper — invalid trace_ids are
    rejected with the Story 9.1 contract message. The xfail contract above pins
    that the registered tool *invokes* this validator first.
    """
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        validate_caller_trace_id(_INVALID_TRACE_ID)
    # Sanity: a valid id passes (no raise).
    validate_caller_trace_id(_VALID_TRACE_ID)


# ===========================================================================
# 6. verification.* event emission — a successful run emits the expected
#    verification.* event carrying the inbound trace_id (Story 17.4). Today the
#    tool is absent, so we drive the contract through the live tool call and
#    inspect the surfaced event. list_tools() empty → _tool_fn fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "event_type"), list(_EVENT_BY_TOOL.items()))
async def test_run_emits_verification_event_with_trace_id(
    tool: str, event_type: str, tmp_path: Path
) -> None:
    """A successful verification run emits verification.* carrying trace_id (GREEN).

    The 17.4 handler surfaces a typed ``verification.*`` event descriptor in the
    structured result with ``trace_id=caller_trace_id``. This contract drives the
    tool, then asserts the surfaced ``result["event"]`` names the verification.*
    event and echoes the inbound trace_id. Runs audit-OFF (no clawhip), so the
    descriptor is independent of whether the best-effort emit actually fired.
    """
    root = tmp_path / "worktree"
    root.mkdir()
    events_dir = tmp_path / "events"
    events_dir.mkdir()

    mcp = build_server(
        worktree_root=root,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="worker",
        actor_id="test-worker",
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    fn = mcp._tool_manager._tools[tool].fn

    result = await fn(caller_trace_id=_VALID_TRACE_ID)
    assert isinstance(result, dict), f"{tool}: result is not a dict: {result!r}"
    emitted = result.get("event")
    assert emitted is not None, f"{tool}: no verification.* event surfaced in result {result!r}"
    assert emitted.get("type") == event_type, (
        f"{tool}: expected {event_type}, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        f"{tool}: emitted {event_type} must carry inbound trace_id"
    )


# Silence the unused-import lint for ``os``/``asyncio`` — used by reference tests.
_ = (os, asyncio)
