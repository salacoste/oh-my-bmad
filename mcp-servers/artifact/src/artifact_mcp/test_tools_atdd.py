"""Red-phase ATDD acceptance contracts for the artifact MCP tools (Epic 19 / Story 19.1).

These are the *executable acceptance criteria* that drive Stories 19.3 (the four
artifact tools — ``artifact.get`` / ``artifact.list`` at Tier-1, ``artifact.put``
at Tier-2, ``artifact.delete`` at Tier-3) and 19.4 (``artifact.*`` event emission)
to green. They mirror git-mcp / memory-mcp's ``test_tools_atdd.py`` verbatim (the
ATDD precedent Epic 15 set).

--------------------------------------------------------------------------------
WHY THE TOOL CONTRACTS ARE @pytest.mark.xfail(strict=True)  (ADR-0010)
--------------------------------------------------------------------------------
The artifact tools DO NOT EXIST yet — ``TIER_MAP`` is empty and ``build_server``
(the Story 19.2 scaffold) registers zero tools. A plain failing test would turn
the PR-gate (``pytest -m "not slow"``) red and block every intervening commit.
ADR-0010's decision: mark each red-phase contract ``xfail(strict=True)`` so:

  * NOW  — the contract fails at runtime (an assertion or a ``KeyError`` /
    ``CapabilityDenied`` inside the test body), which pytest records as
    ``xfailed`` == an expected failure == a GREEN PR-gate.
  * 19.3 / 19.4 — once the tool lands, the contract starts *passing*; because
    ``strict=True``, an unexpected pass (``xpass``) is itself a hard FAILURE that
    shouts "this contract is satisfied — delete the xfail marker and let it guard
    the implementation as a real green test."

CRITICAL — the failure MUST occur at RUNTIME (inside the test body), NOT at
import/collection. ``xfail`` does **not** swallow collection-time ``ImportError``.
Therefore this module imports ONLY symbols that exist in the Story 19.2 scaffold
(``build_server``, ``ArtifactStore``, ``TIER_MAP``, ``validate_caller_trace_id``,
``Tier``, ``CapabilityDenied``), NEVER a not-yet-existing tool symbol at module top.

--------------------------------------------------------------------------------
THE SHIPPED-STORE REFERENCE TESTS (NON-xfail — GREEN NOW)
--------------------------------------------------------------------------------
Story 19.2 SHIPS the :class:`ArtifactStore`, so its behaviour is exercised by real
green reference tests (not xfail): content-addressing round-trip, dedup,
tamper/partial-write detection, store-isolation (P3-I2), retention (TTL +
size-cap sweep), and the 0o660 file-mode discipline (ADR-0011 §7). These anchor
the red-phase suite on known-green primitives the 19.3 / 19.4 tools delegate to.
"""

from __future__ import annotations

import base64
import hashlib
import stat
import sys
from pathlib import Path

import pytest
from capabilities import CallerContext, Tier, check_tier
from events import FROZEN_EPOCH, FrozenClock
from events.errors import CapabilityDenied
from mcp.server.fastmcp import FastMCP

from artifact_mcp.handlers.tools import TIER_MAP, validate_caller_trace_id
from artifact_mcp.server import build_server
from artifact_mcp.store import ArtifactStore

# ---------------------------------------------------------------------------
# Shared vectors (locally declared — mcp-servers/* may not import tests/*,
# guarded by scripts/check_imports.py; mirrors git/memory-mcp's local copies).
# ---------------------------------------------------------------------------

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_INVALID_TRACE_ID = "not-a-uuid"

# Expected tier per artifact tool — the contract Stories 19.3 / 19.4 must satisfy
# by populating TIER_MAP. Names are the canonical ``artifact.<op>`` MCP tool ids.
_TIER1_TOOLS = ("artifact.get", "artifact.list")
_TIER2_TOOLS = ("artifact.put",)
_TIER3_TOOLS = ("artifact.delete",)  # operator-approved single-object deletion

# Mutating op → the artifact.* event it must emit, carrying the inbound trace_id.
_EVENT_BY_TOOL = {
    "artifact.put": "artifact.stored",
}


def _build(store_root: Path) -> FastMCP:
    """Build the scaffold server exactly as a test would (audit-off path)."""
    return build_server(
        store_root=store_root,
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
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 19.3/19.4"
    )
    return by_name[name]


async def _tool_fn(mcp: FastMCP, name: str):  # type: ignore[no-untyped-def]
    """Return the invocable callable for the registered tool *name*.

    ``mcp.list_tools()`` returns ``mcp.types.Tool`` schema objects (no ``.fn``);
    the invocable handler lives on the FastMCP tool-manager. We assert presence
    against ``list_tools()`` (empty today for absent tools → clean xfail) and
    return the manager's ``.fn``. Mirrors git/memory-mcp's ``_tool_fn``.
    """
    tools = await mcp.list_tools()
    by_name = {t.name for t in tools}
    assert name in by_name, (
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 19.3/19.4"
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
# 1. Tier expectation — TIER_MAP["artifact.<op>"] equals the expected Tier.
#    Today TIER_MAP is empty → TIER_MAP[...] raises KeyError inside the body
#    → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.parametrize("tool", _TIER1_TOOLS)
def test_read_tools_are_tier_one(tool: str) -> None:
    """artifact.get / artifact.list are Tier-1 (bounded read) — Story 19.3 (GREEN)."""
    assert TIER_MAP[tool] == Tier.ONE


@pytest.mark.parametrize("tool", _TIER2_TOOLS)
def test_put_tool_is_tier_two(tool: str) -> None:
    """artifact.put is Tier-2 (store mutation) — Story 19.3 (GREEN)."""
    assert TIER_MAP[tool] == Tier.TWO


@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_delete_tool_is_tier_three(tool: str) -> None:
    """artifact.delete is Tier-3 (approval-gated deletion) — Story 19.3 (GREEN)."""
    assert TIER_MAP[tool] == Tier.THREE


# ===========================================================================
# 2. Registration + required caller_trace_id in the input schema.
#    Today list_tools() == [] → _tool_by_name assertion fails → clean xfail.
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS + _TIER2_TOOLS + _TIER3_TOOLS)
async def test_tool_registered_with_required_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each artifact tool is registered and requires a ``caller_trace_id`` string (GREEN)."""
    mcp = _build(tmp_path / "store")
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


# ===========================================================================
# 3. Tier-3 denial — artifact.delete is DENIED without a matching
#    approval.granted. A NON-xfail SEMANTICS test pinned on the shared
#    capability gate (independent of tool registration): a worker requesting
#    the delete tier with has_approval=False must raise CapabilityDenied.
#    Today TIER_MAP[...] raises KeyError → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.parametrize("tool", _TIER3_TOOLS)
def test_tier3_denial_semantics_via_check_tier(tool: str) -> None:
    """Tier-3 artifact.delete with no approval is denied by the shared capability gate.

    Independent of tool registration, this pins the *semantics* 19.3 inherits:
    a worker requesting the tool's tier (TIER_MAP[tool], expected Tier.THREE)
    with ``has_approval=False`` must raise CapabilityDenied. Now that the tier is
    wired this is a real green guard over the denial path.
    """
    required = TIER_MAP[tool]
    caller = CallerContext(actor_kind="worker", actor_id="w-1", task_id="t-1")
    with pytest.raises(CapabilityDenied):
        check_tier(tool, caller, required, has_approval=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tier3_delete_denied_without_approval(tool: str, tmp_path: Path) -> None:
    """artifact.delete raises CapabilityDenied when no approval.granted matches (GREEN).

    The 19.3 handler calls ``check_tier_with_approval`` with an ``approval_lookup``
    that returns False when no matching ``approval.granted`` exists for the task;
    the absence of approval MUST deny the Tier-3 deletion. We build a server with
    an EMPTY events dir (a real approval source with zero ``approval.granted``
    entries → lookup returns False) and assert the denial via the live tool call.
    ``actor_kind="operator"`` so the coarse actor-kind gate ADMITS Tier-3 and the
    denial is driven specifically by the MISSING approval.granted.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    mcp = build_server(
        store_root=tmp_path / "store",
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="operator",
        actor_id="test-operator",
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    fn = mcp._tool_manager._tools[tool].fn

    with pytest.raises(CapabilityDenied):
        await fn(caller_trace_id=_VALID_TRACE_ID, hash="0" * 64, task_id="t-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER3_TOOLS)
async def test_tier3_delete_permitted_with_approval(tool: str, tmp_path: Path) -> None:
    """artifact.delete is PERMITTED when a matching approval.granted exists (GREEN).

    Mirrors git-mcp's Tier-3 positive test: seed an ``approval.granted`` for the
    caller's task into today's JSONL event log, ``put`` a real object so there is
    something to delete, then assert the live ``artifact.delete`` call succeeds and
    reports ``deleted=True``. ``actor_kind="operator"`` admits Tier-3; the gate is
    satisfied purely by the seeded approval.
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    task_id = "t-0190000a-0000-7000-8000-000000000001"  # valid t-<uuidv7> pattern

    mcp = build_server(
        store_root=tmp_path / "store",
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="operator",
        actor_id="test-operator",
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    put_fn = mcp._tool_manager._tools["artifact.put"].fn
    delete_fn = mcp._tool_manager._tools[tool].fn

    put_result = await put_fn(
        caller_trace_id=_VALID_TRACE_ID,
        content=base64.b64encode(b"to-be-deleted").decode("ascii"),
        name="k",
    )
    content_hash = put_result["hash"]

    _seed_approval(events_dir, task_id)
    result = await delete_fn(caller_trace_id=_VALID_TRACE_ID, hash=content_hash, task_id=task_id)

    assert isinstance(result, dict) and result.get("ok") is True, (
        f"{tool}: approved delete did not succeed: {result!r}"
    )
    assert result.get("deleted") is True, (
        f"{tool}: approved delete must report deleted=True; got {result!r}"
    )


def _seed_approval(events_dir: Path, task_id: str) -> None:
    """Write an ``approval.granted`` JSONL line for *task_id* into today's log.

    Mirrors git-mcp's ``_seed_approval`` (the Tier-3 positive-path seeder).
    """
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


# ===========================================================================
# 4. Content-addressing — NON-xfail reference tests on the SHIPPED store.
#    put→hash, get round-trips identical bytes, dedup (same content → same
#    hash, deduped=True), and a TAMPERED object yields a hash-mismatch None.
# ===========================================================================


def test_put_returns_content_hash_and_get_round_trips(tmp_path: Path) -> None:
    """Reference (non-xfail): put records the SHA-256 hash; get round-trips the bytes."""
    store = ArtifactStore(tmp_path / "store")
    content = b"build-output: the quick brown fox\n"
    expected_hash = hashlib.sha256(content).hexdigest()

    record = store.put(content, name="build.log")
    assert record["hash"] == expected_hash, "put must content-address by SHA-256"
    assert record["size"] == len(content)
    assert record["deduped"] is False

    got = store.get(expected_hash)
    assert got == content, "get-by-hash must round-trip the exact bytes"


def test_put_same_content_dedups(tmp_path: Path) -> None:
    """Reference (non-xfail): two puts of identical bytes map to ONE blob (dedup)."""
    store = ArtifactStore(tmp_path / "store")
    content = b"identical payload"

    first = store.put(content, name="a")
    second = store.put(content, name="b")

    assert first["hash"] == second["hash"], "identical content must content-address identically"
    assert first["deduped"] is False
    assert second["deduped"] is True, "second put of identical bytes must dedup the blob"

    # Exactly one object blob exists for the shared hash.
    object_dir = tmp_path / "store" / "objects" / first["hash"][:2]
    blobs = sorted(p.name for p in object_dir.iterdir())
    assert blobs == [first["hash"]], f"dedup must keep a single blob, found {blobs!r}"


def test_get_tampered_object_detects_hash_mismatch(tmp_path: Path) -> None:
    """Reference (non-xfail): get re-hashes the object — a tampered blob yields None.

    Content-addressing makes a partial-write / corruption / tampered object file
    detectable on read: the recomputed SHA-256 no longer matches the requested
    hash, so get returns None rather than handing back bad bytes.
    """
    store = ArtifactStore(tmp_path / "store")
    content = b"trusted artifact bytes"
    record = store.put(content)
    content_hash = record["hash"]

    # Tamper with the on-disk blob (a corruption / partial-write surrogate).
    object_path = tmp_path / "store" / "objects" / content_hash[:2] / content_hash
    object_path.write_bytes(b"corrupted!")

    assert store.get(content_hash) is None, (
        "a tampered/partial object must fail the re-hash check and return None"
    )


# ===========================================================================
# 5. Store-isolation (P3-I2) — NON-xfail. The store writes ONLY under its own
#    root; a sibling registry DB path is NEVER created.
# ===========================================================================


def test_store_writes_only_under_its_own_root(tmp_path: Path) -> None:
    """Reference (non-xfail): an artifact put touches ONLY the store root (ADR-0011 P3-I2).

    Point store_root at ``tmp_path/store`` and assert a sibling
    ``tmp_path/registry.db`` is NEVER created by a put — the store shares no
    engine/file with registry-state.
    """
    store_root = tmp_path / "store"
    registry_path = tmp_path / "registry.db"
    assert not registry_path.exists()

    store = ArtifactStore(store_root)
    store.put(b"isolation payload", name="iso")
    _ = store.list()

    assert store_root.exists(), "the store must create its OWN root dir"
    assert not registry_path.exists(), (
        "P3-I2 violated: an artifact put created/modified the registry DB path"
    )
    # The store may only create files UNDER its own root.
    created = sorted(p.name for p in tmp_path.iterdir())
    assert created == ["store"], f"store touched files outside its root: {created!r}"


# ===========================================================================
# 6. Retention — NON-xfail. With max_bytes small, put N objects → sweep evicts
#    the oldest past the cap and returns the deleted hashes. With a TTL, an aged
#    entry is swept.
# ===========================================================================


def test_sweep_evicts_oldest_past_size_cap(tmp_path: Path) -> None:
    """Reference (non-xfail): sweep deletes oldest objects past the total-size cap."""
    # Cap = 250 bytes; three 100-byte distinct objects → 300 bytes total → one must go.
    store = ArtifactStore(tmp_path / "store", max_bytes=250)
    payloads = [b"a" * 100, b"b" * 100, b"c" * 100]
    records = []
    for i, payload in enumerate(payloads):
        # Stagger created_at so "oldest" is unambiguous (sweep ORDER BY created_at).
        _stub_clock(store, base=1000.0 + i)
        records.append(store.put(payload, name=f"obj-{i}"))

    deleted = store.sweep()

    assert deleted == [records[0]["hash"]], (
        f"sweep must evict the OLDEST object past the cap; got {deleted!r}"
    )
    # The evicted blob is gone; the survivors remain.
    assert store.get(records[0]["hash"]) is None
    assert store.get(records[1]["hash"]) == payloads[1]
    assert store.get(records[2]["hash"]) == payloads[2]
    remaining = {row["name"] for row in store.list()}
    assert remaining == {"obj-1", "obj-2"}


def test_sweep_evicts_ttl_expired_entry(tmp_path: Path) -> None:
    """Reference (non-xfail): sweep deletes an entry older than the TTL."""
    store = ArtifactStore(tmp_path / "store", ttl_seconds=60)

    # An aged object (created well before the TTL window).
    _stub_clock(store, base=1000.0)
    old = store.put(b"stale build output", name="old")
    # A fresh object (created "now").
    _stub_clock(store, base=2000.0)
    fresh = store.put(b"fresh build output", name="fresh")

    # Sweep "now" = 2000.0 → cutoff = 1940.0 → the 1000.0 entry is expired.
    deleted = store.sweep()

    assert deleted == [old["hash"]], f"sweep must evict the TTL-expired entry; got {deleted!r}"
    assert store.get(old["hash"]) is None
    assert store.get(fresh["hash"]) == b"fresh build output"


def _stub_clock(store: ArtifactStore, *, base: float) -> None:
    """Pin the store's wall-clock to *base* so retention ordering is deterministic."""
    store._now = staticmethod(lambda: base)  # type: ignore[method-assign]


# ===========================================================================
# 7. File mode — NON-xfail (ADR-0011 §7). Objects + index DB are 0o660 after a
#    put. Scoped to the FILE mode (macOS preserves it; setgid-on-dir may strip).
# ===========================================================================


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes; Windows chmod is a no-op")
def test_object_and_index_file_modes_are_0o660(tmp_path: Path) -> None:
    """Reference (non-xfail): the shipped store creates 0o660 objects + index (ADR-0011 §7).

    After a put, assert the content-addressed object blob AND the sqlite index DB
    are mode 0o660 (masked to 0o777). The 0o660 group-write defeats the umask so a
    cross-uid recovery path is not locked out (the systemic umask-022 bug).
    """
    store_root = tmp_path / "store"
    store = ArtifactStore(store_root)
    record = store.put(b"bytes that force an object + index write", name="k")

    object_path = store_root / "objects" / record["hash"][:2] / record["hash"]
    object_mode = stat.S_IMODE(object_path.stat().st_mode)
    assert object_mode == 0o660, (
        f"object blob mode must be 0o660 (ADR-0011 §7); got {oct(object_mode)}"
    )

    index_path = store_root / "index.db"
    index_mode = stat.S_IMODE(index_path.stat().st_mode)
    assert index_mode == 0o660, f"index DB mode must be 0o660 (ADR-0011 §7); got {oct(index_mode)}"


# ===========================================================================
# 8. caller_trace_id required-and-validated — a missing/invalid value is
#    rejected. The shared validator already rejects bad shapes (Story 19.2
#    scaffold ships it); the RED contract is that the *registered tool* rejects
#    an invalid caller_trace_id. Today no tool exists → clean xfail.
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS + _TIER2_TOOLS + _TIER3_TOOLS)
async def test_tool_rejects_invalid_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each artifact tool rejects an invalid ``caller_trace_id`` first — Story 19.3 (GREEN).

    ``validate_caller_trace_id`` runs FIRST in every handler body, BEFORE the tier
    gate / store call. The required tool-specific kwargs must carry defaults (or be
    supplied here) so Python does not raise a missing-arg ``TypeError`` before the
    body runs; the invalid trace_id is then rejected with the Story 9.1 contract
    message.
    """
    mcp = _build(tmp_path / "store")
    fn = await _tool_fn(mcp, tool)
    extra: dict[str, str] = {
        "artifact.get": {"hash": "0" * 64},
        "artifact.list": {},
        "artifact.put": {"content": "x"},
        "artifact.delete": {"hash": "0" * 64, "task_id": "t-1"},
    }[tool]
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID, **extra)


def test_validator_rejects_invalid_caller_trace_id() -> None:
    """Reference (non-xfail): the shared validator the tools must call first.

    Anchors the suite on the shipped Story 19.2 helper — invalid trace_ids are
    rejected with the Story 9.1 contract message. The xfail contract above pins
    that the registered tool *invokes* this validator first.
    """
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        validate_caller_trace_id(_INVALID_TRACE_ID)
    # Sanity: a valid id passes (no raise).
    validate_caller_trace_id(_VALID_TRACE_ID)


# ===========================================================================
# 9. artifact.* event emission — a successful put emits artifact.stored carrying
#    the inbound trace_id. Today the tool is absent, so we drive the contract
#    through the live tool call and inspect the emitted event. list_tools() empty
#    → _tool_fn fails → clean xfail (until 19.4).
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "event_type"), list(_EVENT_BY_TOOL.items()))
async def test_put_emits_artifact_event_with_trace_id(
    tool: str, event_type: str, tmp_path: Path
) -> None:
    """A successful artifact.put emits artifact.stored carrying the inbound trace_id.

    The 19.4 handler surfaces a typed ``artifact.*`` event descriptor in the
    structured result with ``trace_id=caller_trace_id``. This contract drives that:
    invoking the tool must produce an event of *event_type* whose ``trace_id`` is
    the inbound caller_trace_id. The descriptor is surfaced UNCONDITIONALLY
    (audit-off path — emitter_holder is None in the test build), mirroring
    verification-mcp / git-mcp.
    """
    mcp = _build(tmp_path / "store")
    fn = await _tool_fn(mcp, tool)
    result = await fn(
        caller_trace_id=_VALID_TRACE_ID,
        content=base64.b64encode(b"artifact bytes").decode("ascii"),
        name="k",
    )

    assert isinstance(result, dict) and result.get("ok") is True, (
        f"{tool}: put did not succeed: {result!r}"
    )
    emitted = result.get("event")
    assert emitted is not None, f"{tool}: no artifact.* event surfaced in result {result!r}"
    assert emitted.get("type") == event_type, (
        f"{tool}: expected {event_type}, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        f"{tool}: emitted {event_type} must carry inbound trace_id"
    )
    # P0/security (ADR-0011 §5): the event descriptor carries NO artifact bytes.
    assert "content" not in emitted and "body" not in emitted, (
        f"{tool}: artifact bytes must NEVER enter the spine event: {emitted!r}"
    )


@pytest.mark.asyncio
async def test_delete_emits_artifact_deleted_with_trace_id(tmp_path: Path) -> None:
    """A successful Tier-3 artifact.delete emits artifact.deleted (reason="requested").

    Seed an ``approval.granted`` for the caller's task, put a real object, then the
    approved delete must surface an ``artifact.deleted`` event descriptor carrying
    the inbound trace_id, ``reason="requested"``, and NO artifact bytes (ADR-0011 §5).
    """
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    task_id = "t-0190000a-0000-7000-8000-000000000001"

    mcp = build_server(
        store_root=tmp_path / "store",
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="operator",
        actor_id="test-operator",
        registry_events_dir=events_dir,
    )
    await mcp.list_tools()
    put_fn = mcp._tool_manager._tools["artifact.put"].fn
    delete_fn = mcp._tool_manager._tools["artifact.delete"].fn

    put_result = await put_fn(
        caller_trace_id=_VALID_TRACE_ID,
        content=base64.b64encode(b"to-be-deleted").decode("ascii"),
        name="k",
    )
    content_hash = put_result["hash"]

    _seed_approval(events_dir, task_id)
    result = await delete_fn(caller_trace_id=_VALID_TRACE_ID, hash=content_hash, task_id=task_id)

    assert result.get("deleted") is True, f"approved delete must succeed: {result!r}"
    emitted = result.get("event")
    assert emitted is not None, f"no artifact.deleted event surfaced: {result!r}"
    assert emitted.get("type") == "artifact.deleted", (
        f"expected artifact.deleted, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        "emitted artifact.deleted must carry inbound trace_id"
    )
    assert "content" not in emitted and "body" not in emitted, (
        f"artifact bytes must NEVER enter the spine event: {emitted!r}"
    )


@pytest.mark.asyncio
async def test_put_retention_sweep_emits_artifact_deleted(tmp_path: Path) -> None:
    """A put past the size cap triggers a retention sweep emitting artifact.deleted.

    Build a server with a small ``max_bytes`` cap. Putting distinct objects past the
    cap runs the post-put retention sweep, which evicts the oldest object(s) and
    surfaces an ``artifact.deleted`` event (``reason="retention"``) per eviction,
    carrying the put's inbound trace_id — system-initiated deletion (ADR-0011 §5),
    NO artifact bytes in the event.
    """
    # Cap = 250 bytes; three 100-byte distinct objects → the third put forces a sweep.
    mcp = build_server(
        store_root=tmp_path / "store",
        max_bytes=250,
        clock=FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH),
        actor_kind="worker",
        actor_id="test-worker",
    )
    await mcp.list_tools()
    put_fn = mcp._tool_manager._tools["artifact.put"].fn

    payloads = [b"a" * 100, b"b" * 100, b"c" * 100]
    last_result: dict[str, object] = {}
    for i, payload in enumerate(payloads):
        last_result = await put_fn(
            caller_trace_id=_VALID_TRACE_ID,
            content=base64.b64encode(payload).decode("ascii"),
            name=f"obj-{i}",
        )

    retention = last_result.get("retention_deleted")
    assert isinstance(retention, list) and retention, (
        f"put past the size cap must surface retention artifact.deleted events: {last_result!r}"
    )
    for ev in retention:
        assert ev.get("type") == "artifact.deleted", (
            f"retention event must be artifact.deleted: {ev!r}"
        )
        assert ev.get("trace_id") == _VALID_TRACE_ID, (
            "retention artifact.deleted must carry the put's inbound trace_id"
        )
        assert "content" not in ev and "body" not in ev, (
            f"artifact bytes must NEVER enter the spine event: {ev!r}"
        )
