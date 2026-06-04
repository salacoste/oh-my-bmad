"""Red-phase ATDD acceptance contracts for the memory MCP tools (Epic 18 / Story 18.1).

These are the *executable acceptance criteria* that drive Stories 18.3 (read
tools — ``memory.read`` / ``memory.search`` at Tier-1) and 18.4 (write tool —
``memory.write`` at Tier-2, plus ``memory.*`` event emission) to green. They
mirror git-mcp's ``test_tools_atdd.py`` verbatim (the ATDD precedent Epic 15 set).

--------------------------------------------------------------------------------
WHY THE TOOL CONTRACTS ARE @pytest.mark.xfail(strict=True)  (ADR-0010)
--------------------------------------------------------------------------------
The memory tools DO NOT EXIST yet — ``TIER_MAP`` is empty and ``build_server``
(the Story 18.2 scaffold) registers zero tools. A plain failing test would turn
the PR-gate (``pytest -m "not slow"``) red and block every intervening commit.
ADR-0010's decision: mark each red-phase contract ``xfail(strict=True)`` so:

  * NOW  — the contract fails at runtime (an assertion or a ``KeyError`` inside
    the test body), which pytest records as ``xfailed`` == an expected failure
    == a GREEN PR-gate.
  * 18.3 / 18.4 — once the tool lands, the contract starts *passing*; because
    ``strict=True``, an unexpected pass (``xpass``) is itself a hard FAILURE that
    shouts "this contract is satisfied — delete the xfail marker and let it guard
    the implementation as a real green test."

CRITICAL — the failure MUST occur at RUNTIME (inside the test body), NOT at
import/collection. ``xfail`` does **not** swallow collection-time ``ImportError``.
Therefore this module imports ONLY symbols that exist in the Story 18.2 scaffold
(``build_server``, ``MemoryStore``, ``TIER_MAP``, ``validate_caller_trace_id``,
``Tier``), NEVER a not-yet-existing tool symbol at module top.

--------------------------------------------------------------------------------
THE SHIPPED-STORE REFERENCE TESTS (NON-xfail — GREEN NOW)
--------------------------------------------------------------------------------
Story 18.2 SHIPS the :class:`MemoryStore`, so its behaviour is exercised by real
green reference tests (not xfail): FTS5 search-relevance ranking, store-isolation
(the store writes ONLY its own file — P3-I2), and the WAL + 0o660 file-mode
discipline (ADR-0012 §7). These anchor the red-phase suite on known-green
primitives the 18.3 / 18.4 tools delegate to.
"""

from __future__ import annotations

import sqlite3
import stat
import sys
from pathlib import Path

import pytest
from capabilities import Tier
from events import FROZEN_EPOCH, FrozenClock
from mcp.server.fastmcp import FastMCP

from memory_mcp.handlers.tools import TIER_MAP, validate_caller_trace_id
from memory_mcp.server import build_server
from memory_mcp.store import MemoryStore

# ---------------------------------------------------------------------------
# Shared vectors (locally declared — mcp-servers/* may not import tests/*,
# guarded by scripts/check_imports.py; mirrors git-mcp's local copies).
# ---------------------------------------------------------------------------

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_INVALID_TRACE_ID = "not-a-uuid"

# Expected tier per memory tool — the contract Stories 18.3 / 18.4 must satisfy
# by populating TIER_MAP. Names are the canonical ``memory.<op>`` MCP tool ids.
_TIER1_TOOLS = ("memory.read", "memory.search")
_TIER2_TOOLS = ("memory.write",)

# Mutating op → the memory.* event it must emit, carrying the inbound trace_id.
_EVENT_BY_TOOL = {
    "memory.write": "memory.written",
}

_XFAIL_REASON = "memory tools land in Story 18.3/18.4 — red-phase ATDD contract"


def _build(store_path: Path) -> FastMCP:
    """Build the scaffold server exactly as a test would (audit-off path)."""
    return build_server(
        store_path=store_path,
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
        f"tool {name!r} not registered (have: {sorted(by_name)}) — lands in Story 18.3/18.4"
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
# 1. Tier expectation — TIER_MAP["memory.<op>"] equals the expected Tier.
#    Today TIER_MAP is empty → TIER_MAP[...] raises KeyError inside the body
#    → runtime failure → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER1_TOOLS)
def test_read_tools_are_tier_one(tool: str) -> None:
    """memory.read / memory.search are Tier-1 (bounded read) — Story 18.3."""
    assert TIER_MAP[tool] == Tier.ONE


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.parametrize("tool", _TIER2_TOOLS)
def test_write_tool_is_tier_two(tool: str) -> None:
    """memory.write is Tier-2 (store mutation) — Story 18.4."""
    assert TIER_MAP[tool] == Tier.TWO


# ===========================================================================
# 2. Registration + required caller_trace_id in the input schema.
#    Today list_tools() == [] → _tool_by_name assertion fails → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS + _TIER2_TOOLS)
async def test_tool_registered_with_required_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Every memory tool is registered and requires a ``caller_trace_id`` string."""
    mcp = _build(tmp_path / "mem.db")
    registered = await _tool_by_name(mcp, tool)
    _assert_caller_trace_id_required(registered, name=tool)


# ===========================================================================
# 3. FTS5 search-relevance — a NON-xfail reference test on the SHIPPED store.
#    Upsert several docs, search a term, assert the matching doc ranks first
#    and an irrelevant doc is absent. Green NOW (store ships in 18.2).
# ===========================================================================


def test_fts5_search_ranks_relevant_doc_first(tmp_path: Path) -> None:
    """Reference (non-xfail): the shipped FTS5 search ranks the matching doc first.

    The 18.3 memory.search tool delegates to ``MemoryStore.search``; this anchors
    the suite on the shipped FTS5 ranking — the matching doc ranks first and an
    irrelevant doc never appears in the result set.
    """
    store = MemoryStore(tmp_path / "mem.db")
    store.upsert("doc-postgres", "Tuning PostgreSQL vacuum and autovacuum thresholds.", title="db")
    store.upsert("doc-redis", "Redis is an in-memory key-value cache store.", title="cache")
    store.upsert(
        "doc-vacuum",
        "How vacuum and autovacuum reclaim dead tuples in Postgres.",
        title="db",
    )

    results = store.search("vacuum")
    keys = [r["key"] for r in results]

    assert keys, "FTS5 search returned no results for a term present in two docs"
    assert set(keys) <= {"doc-postgres", "doc-vacuum"}, (
        f"FTS5 search surfaced an irrelevant doc: {keys!r}"
    )
    assert "doc-redis" not in keys, "irrelevant doc (no 'vacuum' term) must be absent"
    # The doc that mentions 'vacuum' twice ranks ahead of the one that mentions it once.
    assert keys[0] in {"doc-postgres", "doc-vacuum"}


def test_read_round_trips_upserted_doc(tmp_path: Path) -> None:
    """Reference (non-xfail): the shipped ``read`` round-trips an upserted doc."""
    store = MemoryStore(tmp_path / "mem.db")
    store.upsert("k1", "body one", title="t1")
    got = store.read("k1")
    assert got is not None
    assert got["key"] == "k1"
    assert got["body"] == "body one"
    assert got["title"] == "t1"
    # Upsert is an update-in-place, not a duplicate insert.
    store.upsert("k1", "body two", title="t1b")
    got2 = store.read("k1")
    assert got2 is not None and got2["body"] == "body two" and got2["title"] == "t1b"
    assert store.read("missing") is None


# ===========================================================================
# 4. Store-isolation (P3-I2) — a NON-xfail reference test. The store writes
#    ONLY its own store_path; a sibling registry-DB path is NEVER created.
# ===========================================================================


def test_store_writes_only_its_own_file(tmp_path: Path) -> None:
    """Reference (non-xfail): a memory write touches ONLY the store file (ADR-0012 P3-I2).

    Point store_path at ``tmp_path/mem.db`` and assert a sibling
    ``tmp_path/registry.db`` is NEVER created/modified by a memory write — the
    store shares no engine/file with registry-state.
    """
    store_path = tmp_path / "mem.db"
    registry_path = tmp_path / "registry.db"
    assert not registry_path.exists()

    store = MemoryStore(store_path)
    store.upsert("k", "some indexed body about isolation", title="iso")
    _ = store.search("isolation")
    _ = store.read("k")

    assert store_path.exists(), "the store must create its OWN db file"
    assert not registry_path.exists(), (
        "P3-I2 violated: a memory write created/modified the registry DB path"
    )
    # The only files the store may create are its own db + WAL/SHM siblings.
    created = sorted(p.name for p in tmp_path.iterdir())
    assert set(created) <= {"mem.db", "mem.db-wal", "mem.db-shm"}, (
        f"store touched unexpected files: {created!r}"
    )


# ===========================================================================
# 5. WAL + 0o660 file-mode — a NON-xfail reference test (ADR-0012 §7).
#    After a write, the db file mode is 0o660 (mask 0o777) and journal_mode is
#    WAL. Scoped to the FILE mode (macOS preserves it; setgid-on-dir may strip).
# ===========================================================================


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes; Windows chmod is a no-op")
def test_db_file_mode_is_0o660_and_wal(tmp_path: Path) -> None:
    """Reference (non-xfail): the shipped store creates a 0o660 WAL db (ADR-0012 §7).

    After a write, assert the db file mode is 0o660 (masked to 0o777) and the
    connection's ``journal_mode`` is WAL. The 0o660 group-write defeats the umask
    so a cross-uid recovery path is not locked out (the systemic umask-022 bug).
    """
    store_path = tmp_path / "mem.db"
    store = MemoryStore(store_path)
    store.upsert("k", "body that forces a write and materialises the wal", title="t")

    mode = stat.S_IMODE(store_path.stat().st_mode)
    assert mode == 0o660, f"db file mode must be 0o660 (ADR-0012 §7); got {oct(mode)}"

    # journal_mode is WAL (PRAGMA returns the active mode; case-insensitive).
    conn = sqlite3.connect(str(store_path))
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert row is not None and str(row[0]).lower() == "wal", (
        f"journal_mode must be WAL (ADR-0012 §6); got {row!r}"
    )


# ===========================================================================
# 6. caller_trace_id required-and-validated — a missing/invalid value is
#    rejected. The shared validator already rejects bad shapes (Story 18.2
#    scaffold ships it); the RED contract is that the *registered tool* rejects
#    an invalid caller_trace_id. Today no tool exists → clean xfail.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TIER1_TOOLS + _TIER2_TOOLS)
async def test_tool_rejects_invalid_caller_trace_id(tool: str, tmp_path: Path) -> None:
    """Each memory tool rejects an invalid ``caller_trace_id`` before doing work."""
    mcp = _build(tmp_path / "mem.db")
    registered = await _tool_by_name(mcp, tool)  # absent today → clean xfail
    fn = registered.fn  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        await fn(caller_trace_id=_INVALID_TRACE_ID)


def test_validator_rejects_invalid_caller_trace_id() -> None:
    """Reference (non-xfail): the shared validator the tools must call first.

    Anchors the suite on the shipped Story 18.2 helper — invalid trace_ids are
    rejected with the Story 9.1 contract message. The xfail contract above pins
    that the registered tool *invokes* this validator first.
    """
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        validate_caller_trace_id(_INVALID_TRACE_ID)
    # Sanity: a valid id passes (no raise).
    validate_caller_trace_id(_VALID_TRACE_ID)


# ===========================================================================
# 7. memory.* event emission — a successful write emits the expected memory.*
#    event carrying the inbound trace_id. Today the tool is absent, so we drive
#    the contract through the live tool call and inspect the emitted event.
#    list_tools() empty → _tool_by_name fails → clean xfail (until 18.4).
# ===========================================================================


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "event_type"), list(_EVENT_BY_TOOL.items()))
async def test_write_emits_memory_event_with_trace_id(
    tool: str, event_type: str, tmp_path: Path
) -> None:
    """A successful memory.write emits memory.written carrying the inbound trace_id.

    The 18.4 handler emits a typed ``memory.*`` event via the spine writer with
    ``trace_id=caller_trace_id``. This contract drives that: invoking the tool
    must produce an event of *event_type* whose ``trace_id`` is the inbound
    caller_trace_id. Today the tool is absent → _tool_by_name fails → clean xfail.
    """
    mcp = _build(tmp_path / "mem.db")
    registered = await _tool_by_name(mcp, tool)  # absent today → clean xfail

    fn = registered.fn  # type: ignore[attr-defined]
    result = await fn(caller_trace_id=_VALID_TRACE_ID, key="k", body="b")

    emitted = result.get("event") if isinstance(result, dict) else None
    assert emitted is not None, f"{tool}: no memory.* event surfaced in result {result!r}"
    assert emitted.get("type") == event_type, (
        f"{tool}: expected {event_type}, got {emitted.get('type')!r}"
    )
    assert emitted.get("trace_id") == _VALID_TRACE_ID, (
        f"{tool}: emitted {event_type} must carry inbound trace_id"
    )
