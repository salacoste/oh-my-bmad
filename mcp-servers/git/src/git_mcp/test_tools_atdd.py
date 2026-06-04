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

import os
import subprocess
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
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 15.3/15.4"
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
# 1. Tier expectation — TIER_MAP["git.<tool>"] equals the expected Tier.
#    Today TIER_MAP is empty → TIER_MAP[...] raises KeyError inside the body
#    → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.parametrize("tool", _TIER1_TOOLS)
def test_read_tools_are_tier_one(tool: str) -> None:
    """git.status/diff/log/branch are Tier-1 (bounded read) — Story 15.3 (GREEN)."""
    assert TIER_MAP[tool] == Tier.ONE


@pytest.mark.parametrize("tool", _TIER2_TOOLS)
def test_write_tools_are_tier_two(tool: str) -> None:
    """git.add/commit are Tier-2 (repo mutation) — Story 15.4 (GREEN)."""
    assert TIER_MAP[tool] == Tier.TWO


@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_high_risk_tools_are_tier_three(tool: str) -> None:
    """git.push and history-rewrite (rebase) are Tier-3 (approval-gated) — Story 15.4 (GREEN)."""
    assert TIER_MAP[tool] == Tier.THREE


# ===========================================================================
# 2. Registration + required caller_trace_id in the input schema.
#    Today list_tools() == [] → _tool_by_name assertion fails → clean xfail.
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS)
async def test_tier1_tool_registered_with_required_caller_trace_id(
    tool: str, tmp_path: Path
) -> None:
    """Each Tier-1 read tool is registered and requires a ``caller_trace_id`` (GREEN)."""
    mcp = _build(tmp_path)
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER2_TOOLS + _TIER3_TOOLS)
async def test_tool_registered_with_required_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each Tier-2/3 git tool registers and requires ``caller_trace_id`` — Story 15.4 (GREEN)."""
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


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tier3_denied_without_approval(tool: str, tmp_path: Path) -> None:
    """git.push / history-rewrite raises CapabilityDenied when no approval.granted matches.

    The 15.4 handler calls ``check_tier_with_approval`` with an
    ``approval_lookup`` that returns False when no matching ``approval.granted``
    exists for the task; the absence of approval MUST deny the Tier-3 action.
    We build a server with an EMPTY events dir (a real approval source with zero
    ``approval.granted`` entries → lookup returns False) and assert the denial
    via the live tool call. The Tier-3 tools require a ``task_id`` kwarg threaded
    into the approval lookup. (GREEN — Story 15.4.)
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    # actor_kind="operator" so the actor-kind gate ADMITS Tier-3 and the denial
    # is driven specifically by the MISSING approval.granted (the AC's negative
    # test), not by the coarse actor-kind cap (worker maxes at Tier.2).
    mcp = build_server(
        worktree_root=tmp_path,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="operator",
        actor_id="test-operator",
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    fn = mcp._tool_manager._tools[tool].fn

    extra: dict[str, str] = {"upstream": "main"} if tool == "git.rebase" else {}
    with pytest.raises(CapabilityDenied):
        await fn(caller_trace_id=_VALID_TRACE_ID, task_id="t-1", **extra)


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
    fn = await _tool_fn(mcp, "git.status")

    escaping = str(root / ".." / "evil")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS)
async def test_tier1_tool_rejects_invalid_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each Tier-1 read tool rejects an invalid ``caller_trace_id`` first (GREEN)."""
    mcp = _build(tmp_path)
    fn = await _tool_fn(mcp, tool)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER2_TOOLS + _TIER3_TOOLS)
async def test_tool_rejects_invalid_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each mutating git tool rejects an invalid ``caller_trace_id`` first — Story 15.4 (GREEN).

    ``validate_caller_trace_id`` runs FIRST in every handler body, BEFORE the tier
    gate / git spawn. The required tool-specific kwargs (``path`` / ``message`` /
    ``task_id`` / ``upstream``) must be supplied so Python doesn't raise a
    missing-arg ``TypeError`` before the body runs; the invalid trace_id is then
    rejected with the Story 9.1 contract message.
    """
    mcp = _build(tmp_path)
    fn = await _tool_fn(mcp, tool)
    extra: dict[str, str] = {
        "git.add": {"path": "x"},
        "git.commit": {"message": "m"},
        "git.push": {"task_id": "t-1"},
        "git.rebase": {"task_id": "t-1", "upstream": "main"},
    }[tool]
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID, **extra)


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


def _init_repo_with_commit(root: Path) -> None:
    """Init a real git repo at *root* with one committed file (fixture setup only).

    ``test_*.py`` files are exempt from the SHELL001 / spawn-site gates, so the
    fixture builder may shell out to ``git`` directly.
    """
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.test",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "author@example.test",
    }

    def _git(*args: str) -> None:
        subprocess.run(  # noqa: S603 — test fixture builder, not the request path
            ["git", "-C", str(root), *args], check=True, env=env, capture_output=True
        )

    _git("init", "-q", "-b", "main")
    (root / "tracked.txt").write_text("line1\n")
    _git("add", "tracked.txt")
    _git("commit", "-q", "-m", "initial commit")


def _seed_approval(events_dir: Path, task_id: str) -> None:
    """Write an ``approval.granted`` JSONL line for *task_id* into today's log."""
    from events.canonical import to_canonical_json
    from events.envelope import Actor, EventEnvelope
    from events.ids import new_event_id, new_request_id

    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.1.0",
        type="approval.granted",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="operator", id="op-1"),
        payload={"task_id": task_id, "decision_id": "d-1", "actor_id": "op-1"},
        trace_id="01917e5c-a7d1-7000-8abc-000000000000",
        request_id=new_request_id(clock=clock),
    )
    from events import current_day_path

    day_path = current_day_path(events_dir, clock.now())
    day_path.parent.mkdir(parents=True, exist_ok=True)
    day_path.write_bytes(to_canonical_json(envelope) + b"\n")


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "event_type"), list(_EVENT_BY_TOOL.items()))
async def test_mutating_op_emits_git_event_with_trace_id(
    tool: str, event_type: str, tmp_path: Path
) -> None:
    """A successful commit/push emits git.committed / git.pushed carrying trace_id (GREEN).

    The 15.4 handlers surface a typed ``git.*`` event descriptor in the structured
    result with ``trace_id=caller_trace_id``. This contract seeds a real repo
    (+ a local bare remote for push, + a matching ``approval.granted`` for the
    Tier-3 push) so the tool reaches the event-surface path, then asserts the
    surfaced ``result["event"]`` names the git.* event and echoes the inbound
    trace_id. Runs audit-OFF (no clawhip), so the descriptor is independent of
    whether the best-effort emit actually fired.
    """
    repo = tmp_path / "wt"
    repo.mkdir()
    _init_repo_with_commit(repo)

    events_dir = tmp_path / "events"
    events_dir.mkdir()
    task_id = "t-0190000a-0000-7000-8000-000000000001"  # valid t-<uuidv7> pattern

    # actor_kind="operator" so the Tier-3 push (parametrized branch) is admitted by
    # the actor-kind gate and gated purely on the seeded approval.granted. Tier-2
    # commit is allowed for any actor, so this kind also covers the commit branch.
    mcp = build_server(
        worktree_root=repo,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="operator",
        actor_id="test-operator",
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    fn = mcp._tool_manager._tools[tool].fn

    if tool == "git.commit":
        # Stage a change so the commit produces a new HEAD.
        (repo / "tracked.txt").write_text("line1\nline2\n")
        add_fn = mcp._tool_manager._tools["git.add"].fn
        await add_fn(caller_trace_id=_VALID_TRACE_ID, path="tracked.txt")
        result = await fn(caller_trace_id=_VALID_TRACE_ID, message="second commit")
    else:  # git.push — set up a local bare remote + a matching approval.
        bare = tmp_path / "bare.git"
        subprocess.run(  # noqa: S603 — test fixture builder
            ["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True
        )
        subprocess.run(  # noqa: S603 — test fixture builder
            ["git", "-C", str(repo), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )
        _seed_approval(events_dir, task_id)
        result = await fn(caller_trace_id=_VALID_TRACE_ID, task_id=task_id)

    assert isinstance(result, dict) and result.get("ok") is True, (
        f"{tool}: mutating op did not succeed: {result!r}"
    )
    emitted = result.get("event")
    assert emitted is not None, f"{tool}: no git.* event surfaced in result {result!r}"
    assert emitted.get("type") == event_type, (
        f"{tool}: expected {event_type}, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        f"{tool}: emitted {event_type} must carry inbound trace_id"
    )
