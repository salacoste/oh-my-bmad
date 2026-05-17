"""Contract tests for MCP tool input schemas (Story 9.5 / AC5).

Story 2.8 placeholder upgraded in Story 9.5: assert every ``@mcp.tool()``-
decorated handler across the 3 MCP servers (``clawhip-bridge``,
``task-registry``, ``session-registry``) exposes ``caller_trace_id`` as a
required ``string`` field in its FastMCP-auto-derived input schema.

Story 9.5 pass-1 review (T12): renamed from ``test_placeholder.py`` to
``test_mcp_tool_schemas.py`` — the file now contains substantive contract
tests, not a placeholder. The stub ``test_placeholder()`` sentinel is
preserved at the bottom for backward-compat git-bisect continuity (the
function was in CI history at Story 1.5; removing it would confuse blame).

Pass-1 additions landed in this file:
  T2  — byte-identical sync guard for ``validate_caller_trace_id`` across 3 servers
  T3  — extended CRLF/whitespace negative round-trip vectors
  T7  — whitelist-based schema-required assertion (won't break on read-only tools)
  T8  — MCP wire-path test (missing field in JSON payload raises via Pydantic)
  T11 — defensive schema-required assertion form (no KeyError on absent "required")
  T13 — tg: boundary positive + negative vectors
  T14 — DeprecationWarning lock for all 5 emit_* tools

Negative round-trip: an input dict missing ``caller_trace_id`` raises
``ValueError`` from the validation helper before the tool body runs.

Positive round-trips:
  - UUIDv7 bare form (e.g. ``01917e5c-a7d1-7000-8abc-...``)
  - Telegram-derived form (``tg:<update_id>``)
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
import pytest_asyncio
from events import FROZEN_EPOCH, FrozenClock
from registry_state.schema import Base  # noqa: IMP001 — test
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from tests.contract._trace_id_vectors import (
    INVALID_TG_BOUNDARY_TRACE_IDS,
    INVALID_TRACE_IDS,
    VALID_TG_BOUNDARY_TRACE_IDS,
    VALID_TG_TRACE_ID,
    VALID_UUIDV7_TRACE_ID,
)

# ---------------------------------------------------------------------------
# Whitelist: only these tools are FR58-required to carry caller_trace_id
# (T7: won't break if a future read-only tool is added to any server)
# ---------------------------------------------------------------------------

_BRIDGE_FR58_TOOLS: frozenset[str] = frozenset(
    {
        "emit_event",
        "emit_blocker",
        "emit_summary",
        "emit_approval_request",
        "emit_completion",
    }
)
_TASK_FR58_TOOLS: frozenset[str] = frozenset(
    {
        "task_add_note",
        "task_attach_artifact",
        "task_emit_event",
    }
)
_SESSION_FR58_TOOLS: frozenset[str] = frozenset(
    {
        "session_register",
        "session_heartbeat",
        "session_close",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _empty_session_maker() -> async_sessionmaker[AsyncSession]:
    """Minimal session_maker for the registry-MCP build_server() factories."""
    engine: AsyncEngine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn: object, _rec: object) -> None:
        cur = dbapi_conn.cursor()  # type: ignore[union-attr]
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Helper: defensive schema-required assertion (T11)
# ---------------------------------------------------------------------------


def _assert_caller_trace_id_required(tool: object, *, context: str) -> None:
    """Assert ``caller_trace_id`` is in a tool's ``required`` + ``properties``.

    Story 9.5 pass-1 T11: plain ``tool.inputSchema["required"]`` raises
    ``KeyError`` if the key is absent, producing a confusing error. Use
    ``.get()`` so the failure message names the tool and the missing field.
    """
    schema = tool.inputSchema  # type: ignore[attr-defined]
    required: list[str] = schema.get("required") or []
    assert isinstance(required, list), (
        f"{context}: tool {tool.name!r}: 'required' is not a list: {required!r}"  # type: ignore[attr-defined]
    )
    assert "caller_trace_id" in required, (
        f"{context}: tool {tool.name!r}: caller_trace_id missing from required: {required!r}"  # type: ignore[attr-defined]
    )
    properties: dict[str, object] = schema.get("properties") or {}
    assert isinstance(properties.get("caller_trace_id"), dict), (
        f"{context}: tool {tool.name!r}: caller_trace_id not in properties: {properties!r}"
    )
    ctid_prop = properties["caller_trace_id"]  # type: ignore[index]
    assert ctid_prop.get("type") == "string", (  # type: ignore[union-attr]
        f"{context}: tool {tool.name!r}: caller_trace_id properties type wrong: {ctid_prop!r}"
    )


# ---------------------------------------------------------------------------
# AC5: Schema round-trips — each MCP tool's input schema lists
# ``caller_trace_id`` as a required ``string`` field (T7 + T11).
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.asyncio
async def test_clawhip_bridge_tool_schemas_require_caller_trace_id(
    tmp_path: Path,
) -> None:
    """All 5 clawhip-bridge FR58 tools require caller_trace_id in their schema.

    Story 9.5 pass-1 T7: uses whitelist so adding a future read-only tool
    (e.g. ``list_recent_events``) does NOT break this test.
    Story 9.5 pass-1 T11: defensive get()-based assertion (no KeyError).
    """
    from clawhip_bridge_mcp.server import build_server  # noqa: IMP001 — contract test

    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    mcp = build_server(base_dir=tmp_path, clock=clock, actor_kind="system", actor_id="contract")
    tools = await mcp.list_tools()
    observed = {t.name for t in tools}
    assert observed >= _BRIDGE_FR58_TOOLS, f"missing FR58 tools: {_BRIDGE_FR58_TOOLS - observed}"
    for tool in tools:
        if tool.name in _BRIDGE_FR58_TOOLS:
            _assert_caller_trace_id_required(tool, context="clawhip-bridge")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_task_registry_tool_schemas_require_caller_trace_id(
    _empty_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """All 3 task-registry FR58 tools require caller_trace_id.

    Story 9.5 pass-1 T7: whitelist guards against future read-only tools.
    Story 9.5 pass-1 T11: defensive assertion.
    """
    from task_registry_mcp.app.main import build_server  # noqa: IMP001

    mcp = build_server(
        actor_kind="worker",
        actor_id="contract",
        _session_maker=_empty_session_maker,
    )
    tools = await mcp.list_tools()
    observed = {t.name for t in tools}
    assert observed >= _TASK_FR58_TOOLS, f"missing FR58 tools: {_TASK_FR58_TOOLS - observed}"
    for tool in tools:
        if tool.name in _TASK_FR58_TOOLS:
            _assert_caller_trace_id_required(tool, context="task-registry")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_session_registry_tool_schemas_require_caller_trace_id(
    _empty_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """All 3 session-registry FR58 tools require caller_trace_id.

    Story 9.5 pass-1 T7: whitelist guards against future read-only tools.
    Story 9.5 pass-1 T11: defensive assertion.
    """
    from session_registry_mcp.app.main import build_server  # noqa: IMP001

    mcp = build_server(
        actor_kind="worker",
        actor_id="contract",
        _session_maker=_empty_session_maker,
    )
    tools = await mcp.list_tools()
    observed = {t.name for t in tools}
    assert observed >= _SESSION_FR58_TOOLS, f"missing FR58 tools: {_SESSION_FR58_TOOLS - observed}"
    for tool in tools:
        if tool.name in _SESSION_FR58_TOOLS:
            _assert_caller_trace_id_required(tool, context="session-registry")


# ---------------------------------------------------------------------------
# AC5 positive round-trips
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("trace_id", [VALID_UUIDV7_TRACE_ID, VALID_TG_TRACE_ID])
def test_caller_trace_id_positive_round_trip_validates(trace_id: str) -> None:
    """Positive round-trip: each helper accepts valid UUIDv7 + tg: forms."""
    from clawhip_bridge_mcp.server import validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_task,
    )

    _v_bridge(trace_id)
    _v_task(trace_id)
    _v_sess(trace_id)


@pytest.mark.contract
@pytest.mark.parametrize("trace_id", list(VALID_TG_BOUNDARY_TRACE_IDS))
def test_caller_trace_id_tg_boundary_positive(trace_id: str) -> None:
    """T13: tg: boundary positive vectors pass all 3 helpers."""
    from clawhip_bridge_mcp.server import validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_task,
    )

    _v_bridge(trace_id)
    _v_task(trace_id)
    _v_sess(trace_id)


# ---------------------------------------------------------------------------
# AC5 negative round-trips (T3 extended vectors + T13 boundary negatives)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("bad", list(INVALID_TRACE_IDS))
def test_caller_trace_id_negative_round_trip_rejected(bad: str) -> None:
    """T3: extended negative round-trip covering CRLF/whitespace vectors.

    Story 9.5 pass-1 T3: extends beyond the original 4-entry list to cover
    the CRLF/whitespace injection vectors that Story 9.4 pass-2 S1 motivated.
    All shapes validated consistently across all 3 servers.
    """
    from clawhip_bridge_mcp.server import validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_task,
    )

    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_bridge(bad)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_task(bad)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_sess(bad)


@pytest.mark.contract
@pytest.mark.parametrize("bad", list(INVALID_TG_BOUNDARY_TRACE_IDS))
def test_caller_trace_id_tg_boundary_negative(bad: str) -> None:
    """T13: tg: boundary negative vectors rejected by all 3 helpers."""
    from clawhip_bridge_mcp.server import validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_task,
    )

    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_bridge(bad)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_task(bad)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_sess(bad)


# ---------------------------------------------------------------------------
# T2: byte-identical body sync guard across 3 servers
# ---------------------------------------------------------------------------


def _extract_fn_body_ast(fn: object) -> str:
    """Return normalised source of a function's body, excluding the docstring.

    Strips the leading docstring node (if present) then ``ast.unparse``s the
    remaining statements so minor whitespace differences don't cause false
    failures.  The leading ``NOTE:`` clauses in the per-sibling docstrings name
    different servers, which would cause a naïve source-compare to fail — this
    extracts only the executable body.
    """
    src = inspect.getsource(fn)  # type: ignore[arg-type]
    # Dedent so ast.parse doesn't choke on indented class/function bodies.
    src = textwrap.dedent(src)
    tree = ast.parse(src)
    fn_def = next(
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    body = fn_def.body
    # Drop the leading docstring Expr node if present.
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


@pytest.mark.contract
def test_validate_caller_trace_id_byte_identical_across_servers() -> None:
    """T2: AC2 sync invariant — validate_caller_trace_id body identical across 3 servers.

    Compares the executable body (excluding per-sibling NOTE docstring clauses)
    via AST unparsing so whitespace diffs don't cause spurious failures. Catches
    logic drift — e.g. one server using a different error message or a different
    validation function — that wouldn't be caught by per-server unit tests alone.
    """
    from clawhip_bridge_mcp.server import validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        validate_caller_trace_id as _v_task,
    )

    bridge_body = _extract_fn_body_ast(_v_bridge)
    sess_body = _extract_fn_body_ast(_v_sess)
    task_body = _extract_fn_body_ast(_v_task)

    assert bridge_body == sess_body, (
        "validate_caller_trace_id body differs between clawhip-bridge and session-registry"
        f"\n--- bridge ---\n{bridge_body}"
        f"\n--- session ---\n{sess_body}"
    )
    assert bridge_body == task_body, (
        "validate_caller_trace_id body differs between clawhip-bridge and task-registry"
        f"\n--- bridge ---\n{bridge_body}"
        f"\n--- task ---\n{task_body}"
    )


# ---------------------------------------------------------------------------
# T8: MCP wire-path test — missing field in JSON payload raises via Pydantic
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.asyncio
async def test_emit_event_missing_caller_trace_id_in_json_payload_raises(
    tmp_path: Path,
) -> None:
    """T8/AC4: omitting caller_trace_id from JSON payload raises via Pydantic.

    Exercises the MCP wire path through ``mcp.call_tool()`` — what a real
    MCP client experiences when it omits ``caller_trace_id`` from the
    tool-call JSON dict. The per-server unit tests exercise the Python kwarg
    path (passing ``caller_trace_id`` as a Python kwarg to the ``fn`` object);
    this test exercises the JSON-schema validation path via FastMCP's runtime.
    AC4's "validation error surfaced as structured MCP error response" claim
    is verified for the production failure mode.
    """
    from clawhip_bridge_mcp.server import build_server  # noqa: IMP001

    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    mcp = build_server(base_dir=log_dir, clock=clock, actor_kind="system", actor_id="wire-test")

    async with mcp._mcp_server.lifespan(mcp._mcp_server):
        with pytest.raises(Exception) as exc_info:  # noqa: B017 — FastMCP wraps; assert detail
            await mcp.call_tool(
                "emit_event",
                {
                    "type": "task.created",
                    "payload": {
                        "task_id": "t-00000001-0001-7000-8000-000000000001",
                        "title": "x",
                    },
                    # caller_trace_id intentionally omitted — must raise
                },
            )
        assert "caller_trace_id" in str(exc_info.value), (
            f"Expected error mentioning 'caller_trace_id', got: {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# T14: DeprecationWarning lock for all 5 emit_* tools
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,call_kwargs",
    [
        (
            "emit_event",
            {
                "type": "task.created",
                "payload": {
                    "task_id": "t-00000001-0001-7000-8000-000000000001",
                    "title": "x",
                },
                "caller_trace_id": VALID_UUIDV7_TRACE_ID,
            },
        ),
        (
            "emit_blocker",
            {
                "task_id": "t-00000001-0001-7000-8000-000000000002",
                "reason": "test blocker",
                "caller_trace_id": VALID_UUIDV7_TRACE_ID,
            },
        ),
        (
            "emit_summary",
            {
                "task_id": "t-00000001-0001-7000-8000-000000000003",
                "summary": "test summary",
                "caller_trace_id": VALID_UUIDV7_TRACE_ID,
            },
        ),
        (
            "emit_approval_request",
            {
                "task_id": "t-00000001-0001-7000-8000-000000000004",
                "action": "deploy",
                "justification": "test",
                "caller_trace_id": VALID_UUIDV7_TRACE_ID,
            },
        ),
        (
            "emit_completion",
            {
                "task_id": "t-00000001-0001-7000-8000-000000000005",
                "summary": "done",
                "caller_trace_id": VALID_UUIDV7_TRACE_ID,
            },
        ),
    ],
)
async def test_emit_tools_do_not_emit_deprecation_warning(
    tmp_path: Path,
    tool_name: str,
    call_kwargs: dict[str, object],
) -> None:
    """T14/AC7: emit_* tools must not trigger EventEnvelope DeprecationWarning.

    After Story 9.5, ``_emit()`` passes ``caller_trace_id`` as ``trace_id=``
    to ``EventEnvelope.create(...)``. This locks the no-DeprecationWarning
    invariant as a runtime-enforced contract (replaces the prose claim in the
    Dev Agent Record). Story 9.7 will remove the ``pyproject.toml``
    filterwarnings entry; this test guards against regression where the filter
    is the only thing hiding warnings.
    """
    import warnings

    from clawhip_bridge_mcp.server import build_server  # noqa: IMP001

    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    mcp = build_server(
        base_dir=log_dir,
        clock=clock,
        actor_kind="system",
        actor_id=f"dep-{tool_name}",
    )
    fn = mcp._tool_manager._tools[tool_name].fn
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = await fn(**call_kwargs)
    assert result is not None


# ---------------------------------------------------------------------------
# Backward-compat sentinel (Story 1.5 / T12: keep for git-bisect continuity)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_placeholder() -> None:
    """Backward-compat sentinel — original placeholder from Story 1.5.

    Kept so ``git bisect`` on early CI runs that relied on this test name
    still finds a passing test. The file was renamed from ``test_placeholder.py``
    to ``test_mcp_tool_schemas.py`` in Story 9.5 pass-1 T12; this function
    survives the rename.
    """
    assert True
