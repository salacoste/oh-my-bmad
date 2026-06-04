"""Red-phase ATDD acceptance contracts for the github MCP tools (Epic 16 / Story 16.1).

These are the *executable acceptance criteria* that drive Stories 16.3 (read
tools at Tier-1) and 16.4 (write tools at Tier-3-gated, plus ``github.*`` event
emission) to green. They mirror the git-mcp ATDD precedent (Story 15.1) verbatim
in shape, adapted to GitHub's REST surface (no worktree; a SCOPED-token credential
contract replaces git-mcp's worktree-containment contract).

--------------------------------------------------------------------------------
TOOL NAMING CONVENTION
--------------------------------------------------------------------------------
Tool ids follow git-mcp's dotted scheme extended one level for GitHub's
noun-collections: ``github.<collection>.<verb>``. The READ tools list/get an
issue, PR, or review; the WRITE tools create/update/request/comment. This keeps
the FastMCP tool id (``@mcp.tool(name="github.issues.list")``) human-scannable
and parallel to ``git.<op>``.

  READ  (Tier-1): github.issues.list / github.issues.get
                  github.prs.list    / github.prs.get
                  github.reviews.list / github.reviews.get
  WRITE (Tier-3): github.issues.create / github.issues.update
                  github.prs.create    / github.prs.update
                  github.reviews.request
                  github.comment.create

GitHub has NO Tier-2 band — every state-changing GitHub call is a remote,
externally-visible mutation (an issue, a PR, a review request, a comment), so all
writes are Tier-3 approval-gated. (git-mcp's Tier-2 add/commit are LOCAL repo
mutations; there is no local-only analogue on the GitHub API.)

--------------------------------------------------------------------------------
WHY EVERY CONTRACT IS @pytest.mark.xfail(strict=True)  (ADR-0010)
--------------------------------------------------------------------------------
The github tools DO NOT EXIST yet — ``TIER_MAP`` is empty and ``build_server``
(the Story 16.2 scaffold) registers zero tools. A plain failing test would turn
the PR-gate (``pytest -m "not slow"``) red and block every intervening commit.
ADR-0010's decision: mark each red-phase contract ``xfail(strict=True)`` so:

  * NOW  — the contract fails at runtime (an assertion or a ``KeyError`` /
    ``CapabilityDenied`` inside the test body), which pytest records as
    ``xfailed`` == an expected failure == a GREEN PR-gate.
  * 16.3 / 16.4 — once the tool lands, the contract starts *passing*; because
    ``strict=True``, an unexpected pass (``xpass``) is itself a hard FAILURE
    that shouts "this contract is satisfied — delete the xfail marker and let it
    guard the implementation as a real green test."
  * SAFETY NET — if a tool *accidentally* already works (e.g. a tier is wired
    early), ``strict=True`` makes the silent xpass fail loudly instead of hiding
    a half-shipped capability.

CRITICAL — the failure MUST occur at RUNTIME (inside the test body), NOT at
import/collection. ``xfail`` does **not** swallow collection-time ``ImportError``.
Therefore this module:

  * imports ONLY symbols that exist in the Story 16.2 scaffold
    (``build_server``, ``TIER_MAP``, ``validate_caller_trace_id``, ``Tier``,
    ``CapabilityDenied``, ``CallerContext``);
  * NEVER imports a not-yet-existing tool symbol at module top;
  * asserts against the live ``await mcp.list_tools()`` (empty today → runtime
    assertion failure → clean xfail) and against ``TIER_MAP["github.<tool>"]``
    (``KeyError`` today → runtime failure inside the body → clean xfail).

When 16.3/16.4 land, the implementer removes the ``xfail`` marker on each
satisfied contract and the test flips to a real green guard.

--------------------------------------------------------------------------------
CONTRACT MATRIX (one xfail contract per tool × concern)
--------------------------------------------------------------------------------
Tier expectation:    read tools == Tier.ONE; write tools == Tier.THREE.
Registration:        each tool appears in list_tools() with a required
                     ``caller_trace_id`` string field in its input schema.
Tier-3 denial:       each write tool is DENIED without a matching
                     approval.granted (CapabilityDenied).
Scoped credential:   the server authenticates with GITHUB_MCP_SCOPED_TOKEN and
                     NOT the broad GITHUB_TOKEN (Story 16.5).
caller_trace_id:     a missing/invalid caller_trace_id is rejected.
github.* events:     a successful write emits github.* carrying the inbound
                     trace_id.
"""

from __future__ import annotations

import os

import pytest
from capabilities import CallerContext, Tier, check_tier
from events import FROZEN_EPOCH, FrozenClock
from events.errors import CapabilityDenied

from github_mcp.handlers.tools import TIER_MAP, validate_caller_trace_id
from github_mcp.server import build_server

# ---------------------------------------------------------------------------
# Shared vectors (locally declared — mcp-servers/* may not import tests/*,
# guarded by scripts/check_imports.py; mirrors git-mcp test_tools_atdd.py).
# ---------------------------------------------------------------------------

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_INVALID_TRACE_ID = "not-a-uuid"

_SCOPED_TOKEN = "ghs_scaffold_test_scoped_token"  # noqa: S105 — test fixture, not a real credential

# Expected tier per github tool — the contract Stories 16.3 / 16.4 must satisfy by
# populating TIER_MAP. Names are the canonical ``github.<collection>.<verb>`` ids.
_TIER1_TOOLS = (
    "github.issues.list",
    "github.issues.get",
    "github.prs.list",
    "github.prs.get",
    "github.reviews.list",
    "github.reviews.get",
)
_TIER3_TOOLS = (
    "github.issues.create",
    "github.issues.update",
    "github.prs.create",
    "github.prs.update",
    "github.reviews.request",
    "github.comment.create",
)

# Write ops and the github.* event each must emit, carrying the inbound trace_id.
_EVENT_BY_TOOL = {
    "github.issues.create": "github.issue.created",
    "github.issues.update": "github.issue.updated",
    "github.prs.create": "github.pr.created",
    "github.prs.update": "github.pr.updated",
    "github.reviews.request": "github.review.requested",
    "github.comment.create": "github.comment.created",
}

# Tool-specific kwargs each write tool will require (so an invalid-trace-id test
# does not trip a missing-arg TypeError before the validator runs in the body).
_WRITE_TOOL_KWARGS: dict[str, dict[str, object]] = {
    "github.issues.create": {"task_id": "t-1", "title": "t", "body": "b"},
    "github.issues.update": {"task_id": "t-1", "number": 1, "body": "b"},
    "github.prs.create": {"task_id": "t-1", "title": "t", "head": "h", "base": "main"},
    "github.prs.update": {"task_id": "t-1", "number": 1, "body": "b"},
    "github.reviews.request": {"task_id": "t-1", "number": 1, "reviewers": ["r"]},
    "github.comment.create": {"task_id": "t-1", "number": 1, "body": "b"},
}

_XFAIL_REASON = "github tools land in Story 16.3/16.4 — red-phase ATDD contract"


def _build():  # type: ignore[no-untyped-def]
    """Build the scaffold server (audit-off path), authenticating with the scoped token."""
    return build_server(
        actor_kind="worker",
        actor_id="test-worker",
        scoped_token=_SCOPED_TOKEN,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
    )


async def _tool_by_name(mcp, name: str):  # type: ignore[no-untyped-def]
    """Return the registered tool object named *name*, or fail the assertion.

    Today list_tools() is empty → this assertion fails at RUNTIME → clean xfail.
    """
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    assert name in by_name, (
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 16.3/16.4"
    )
    return by_name[name]


async def _tool_fn(mcp, name: str):  # type: ignore[no-untyped-def]
    """Return the callable for the registered tool *name*, or fail the assertion.

    ``mcp.list_tools()`` returns ``mcp.types.Tool`` schema objects (no ``.fn``);
    the invocable handler lives on the FastMCP tool-manager. We assert presence
    against ``list_tools()`` (empty today for absent tools → clean xfail) and
    return the manager's ``.fn``.
    """
    tools = await mcp.list_tools()
    by_name = {t.name for t in tools}
    assert name in by_name, (
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 16.3/16.4"
    )
    return mcp._tool_manager._tools[name].fn


def _assert_caller_trace_id_required(tool, *, name: str) -> None:  # type: ignore[no-untyped-def]
    """Assert ``caller_trace_id`` is a required string field on the tool schema.

    Mirrors tests/contract/test_mcp_tool_schemas.py::_assert_caller_trace_id_required.
    """
    schema = tool.inputSchema
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
# 1. Tier expectation — TIER_MAP["github.<tool>"] equals the expected Tier.
#    Today TIER_MAP is empty → TIER_MAP[...] raises KeyError inside the body
#    → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.parametrize("tool", _TIER1_TOOLS)
def test_read_tools_are_tier_one(tool: str) -> None:
    """github read tools (issues/prs/reviews list+get) are Tier-1 — Story 16.3 (GREEN)."""
    assert TIER_MAP[tool] == Tier.ONE


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_write_tools_are_tier_three(tool: str) -> None:
    """github write tools (remote mutations) are Tier-3 (approval-gated) — Story 16.4 (GREEN)."""
    assert TIER_MAP[tool] == Tier.THREE


# ===========================================================================
# 2. Registration + required caller_trace_id in the input schema.
#    Today list_tools() == [] → _tool_by_name assertion fails → clean xfail.
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS)
async def test_tier1_tool_registered_with_required_caller_trace_id(tool: str) -> None:
    """Each Tier-1 read tool is registered and requires a ``caller_trace_id`` (GREEN)."""
    mcp = _build()
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tool_registered_with_required_caller_trace_id(tool: str) -> None:
    """Each Tier-3 github write tool registers and requires ``caller_trace_id`` — 16.4 (GREEN)."""
    mcp = _build()
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


# ===========================================================================
# 3. Tier-3 denial negative test — each write tool is DENIED without a matching
#    approval.granted. Encodes the behavior 16.4 must satisfy via
#    check_tier_with_approval(..., approval_lookup=...). Today the tool is not
#    registered → _tool_by_name fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tier3_denied_without_approval(tool: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Each github write tool raises CapabilityDenied when no approval.granted matches.

    The 16.4 handler calls ``check_tier_with_approval`` with an ``approval_lookup``
    that returns False when no matching ``approval.granted`` exists for the task;
    the absence of approval MUST deny the Tier-3 action. We build a server with an
    EMPTY events dir (a real approval source with zero ``approval.granted`` entries
    → lookup returns False) and assert the denial via the live tool call. The
    Tier-3 tools require a ``task_id`` kwarg threaded into the approval lookup.
    (GREEN — Story 16.4.)
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    # actor_kind="operator" so the actor-kind gate ADMITS Tier-3 and the denial
    # is driven specifically by the MISSING approval.granted (the AC's negative
    # test), not by the coarse actor-kind cap (worker maxes at Tier.2).
    mcp = build_server(
        actor_kind="operator",
        actor_id="test-operator",
        scoped_token=_SCOPED_TOKEN,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    fn = mcp._tool_manager._tools[tool].fn

    kwargs = dict(_WRITE_TOOL_KWARGS[tool])
    with pytest.raises(CapabilityDenied):
        await fn(caller_trace_id=_VALID_TRACE_ID, **kwargs)


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_tier3_denial_semantics_via_check_tier(tool: str) -> None:
    """Tier-3 github ops with no approval are denied by the shared capability gate.

    Independent of tool registration, this pins the *semantics* 16.4 inherits: a
    worker requesting the tool's tier (TIER_MAP[tool], expected Tier.THREE) with
    ``has_approval=False`` must raise CapabilityDenied. Today TIER_MAP[tool] raises
    KeyError → runtime failure → clean xfail; when the tier is wired this becomes a
    real green guard over the denial path.
    """
    required = TIER_MAP[tool]  # KeyError today → clean xfail
    caller = CallerContext(actor_kind="worker", actor_id="w-1", task_id="t-1")
    with pytest.raises(CapabilityDenied):
        check_tier(tool, caller, required, has_approval=False)


# ===========================================================================
# 4. Scoped-credential contract — the server authenticates with the SCOPED
#    GITHUB_MCP_SCOPED_TOKEN and NOT the broad GITHUB_TOKEN (Story 16.5). Today
#    no tool issues a REST call, so we assert the intended behavior: a read tool
#    invocation must carry the scoped token (not the broad token) on its outbound
#    request. The tool is absent → _tool_fn fails → clean xfail.
# ===========================================================================


@pytest.mark.asyncio
async def test_read_tool_authenticates_with_scoped_token_not_broad_token(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """A github read tool authenticates with GITHUB_MCP_SCOPED_TOKEN, never GITHUB_TOKEN.

    Story 16.5 wires the scoped credential; this contract pins it: the server is
    built with a distinct ``scoped_token`` and a DIFFERENT broad ``GITHUB_TOKEN``
    in the environment, then the read tool's outbound REST request must carry the
    SCOPED token's bearer value — proving the broad operator token is never the
    auth source. We record the OUTBOUND ``Authorization`` header the adapter would
    attach (intercepting ``GitHubReadClient._read``) rather than asserting a token
    echoed in the tool result — disclosing the credential through the tool boundary
    would itself be the leak this contract guards against.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_broad_operator_token_MUST_NOT_be_used")

    import github_mcp.adapters.github_rest as _rest

    captured: dict[str, object] = {}

    async def _recording_read(self: _rest.GitHubReadClient, path: str) -> _rest.ReadResult:
        captured["token"] = self._token
        captured["authorization"] = self._headers().get("Authorization")
        return _rest.ReadResult(ok=True, status=200, data=[])

    monkeypatch.setattr(_rest.GitHubReadClient, "_read", _recording_read)

    mcp = build_server(
        actor_kind="worker",
        actor_id="test-worker",
        scoped_token=_SCOPED_TOKEN,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
    )
    fn = await _tool_fn(mcp, "github.issues.list")
    result = await fn(caller_trace_id=_VALID_TRACE_ID, owner="acme", repo="widgets")

    # The tool result MUST NOT carry the credential.
    assert isinstance(result, dict)
    assert "_auth_token" not in result, "tool result must NEVER disclose the scoped token"
    assert result.get("ok") is True
    # The outbound bearer MUST be the scoped token, NEVER the broad GITHUB_TOKEN.
    assert captured["token"] == _SCOPED_TOKEN, (
        f"github read tool must authenticate with the scoped token, got {captured.get('token')!r}"
    )
    assert captured["authorization"] == f"Bearer {_SCOPED_TOKEN}"
    assert captured["token"] != os.environ.get("GITHUB_TOKEN"), (
        "github read tool must NEVER authenticate with the broad GITHUB_TOKEN"
    )


# ===========================================================================
# 5. caller_trace_id required-and-validated — a missing/invalid value is
#    rejected. The shared validator already rejects bad shapes (Story 16.2
#    scaffold ships it); the RED contract is that the *registered tool* rejects
#    an invalid caller_trace_id. Today no tool exists → clean xfail.
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS)
async def test_tier1_tool_rejects_invalid_caller_trace_id(tool: str) -> None:
    """Each Tier-1 read tool rejects an invalid ``caller_trace_id`` first (GREEN)."""
    mcp = _build()
    fn = await _tool_fn(mcp, tool)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID)


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tool_rejects_invalid_caller_trace_id(tool: str) -> None:
    """Each github write tool rejects an invalid ``caller_trace_id`` first — Story 16.4 (GREEN).

    ``validate_caller_trace_id`` runs FIRST in every handler body, BEFORE the tier
    gate / REST call. The required tool-specific kwargs must be supplied so Python
    doesn't raise a missing-arg ``TypeError`` before the body runs; the invalid
    trace_id is then rejected with the Story 9.1 contract message.
    """
    mcp = _build()
    fn = await _tool_fn(mcp, tool)
    kwargs = dict(_WRITE_TOOL_KWARGS[tool])
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID, **kwargs)


# ===========================================================================
# 6. github.* event emission — a successful write op emits the expected github.*
#    event carrying the inbound trace_id. Today the tool is absent, so we drive
#    the contract through the live tool call and inspect the surfaced event.
#    list_tools() empty → _tool_fn fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "event_type"), list(_EVENT_BY_TOOL.items()))
async def test_write_op_emits_github_event_with_trace_id(
    tool: str,
    event_type: str,
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """A successful github write emits the github.* event carrying trace_id (GREEN).

    The 16.4 handlers surface a typed ``github.*`` event descriptor in the
    structured result with ``trace_id=caller_trace_id``. This contract seeds a
    matching ``approval.granted`` for the Tier-3 write, drives the tool to the
    event-surface path, then asserts the surfaced ``result["event"]`` names the
    github.* event and echoes the inbound trace_id. Runs audit-OFF (no clawhip),
    so the descriptor is independent of whether the best-effort emit actually
    fired. Today the tool is absent → _tool_fn fails → clean xfail.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    task_id = "t-0190000a-0000-7000-8000-000000000001"  # valid t-<uuidv7> pattern
    _seed_approval(events_dir, task_id)

    mcp = build_server(
        actor_kind="operator",
        actor_id="test-operator",
        scoped_token=_SCOPED_TOKEN,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        registry_events_dir=events_dir,
    )
    fn = await _tool_fn(mcp, tool)

    kwargs = dict(_WRITE_TOOL_KWARGS[tool])
    kwargs["task_id"] = task_id
    result = await fn(caller_trace_id=_VALID_TRACE_ID, **kwargs)

    assert isinstance(result, dict) and result.get("ok") is True, (
        f"{tool}: write op did not succeed: {result!r}"
    )
    emitted = result.get("event")
    assert emitted is not None, f"{tool}: no github.* event surfaced in result {result!r}"
    assert emitted.get("type") == event_type, (
        f"{tool}: expected {event_type}, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        f"{tool}: emitted {event_type} must carry inbound trace_id"
    )


# ===========================================================================
# Reference (non-xfail) anchor — the shipped Story 16.2 scaffold primitive the
# tools delegate to. This passes against the scaffold today; it is NOT an xfail
# contract, so it anchors the red-phase suite on a known-green primitive
# (mirrors git-mcp's test_validator_rejects_invalid_caller_trace_id).
# ===========================================================================


def test_validator_rejects_invalid_caller_trace_id() -> None:
    """Reference (non-xfail): the shared validator the tools must call first.

    Anchors the suite on the shipped Story 16.2 helper — invalid trace_ids are
    rejected with the Story 9.1 contract message. The xfail contracts above pin
    that the registered tool *invokes* this validator first.
    """
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        validate_caller_trace_id(_INVALID_TRACE_ID)
    # Sanity: a valid id passes (no raise).
    validate_caller_trace_id(_VALID_TRACE_ID)


def _seed_approval(events_dir, task_id: str) -> None:  # type: ignore[no-untyped-def]
    """Write an ``approval.granted`` JSONL line for *task_id* into today's log."""
    from events import current_day_path
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
    day_path = current_day_path(events_dir, clock.now())
    day_path.parent.mkdir(parents=True, exist_ok=True)
    day_path.write_bytes(to_canonical_json(envelope) + b"\n")
