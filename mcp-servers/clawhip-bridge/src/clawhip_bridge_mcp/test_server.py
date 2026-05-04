"""Tests for clawhip-bridge MCP server (Story 2.8 AC-12).

Tests across 6 test classes. No conftest — fixtures inlined per
Story 2.4/2.5 convention. All async tests use pytest-asyncio strict mode.

Classes:
  TestServerConstruction   — server-construction structural checks
  TestEmitEventTool        — generic emit_event tool (incl. concurrency)
  TestTypedEmitTools       — 4 typed sugar tools
  TestRecentEventsResource — recent-events resource (incl. limit URI template)
  TestFastMCPIntegration   — end-to-end via mcp.call_tool() w/ lifespan
  TestEntryPoint           — env-var validation (subprocess)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from random import Random

import pytest
from events import (  # mcp-servers→services allowed per AC-7
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskSummaryEmittedPayload,
    TickingClock,
    new_event_id,
    new_request_id,
    new_task_id,
)
from events.schema_registry import register as _reg
from mcp.server.fastmcp.resources.types import FunctionResource
from registry_state.adapters.event_log import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7
    current_day_path,
    read_log_lines,
)

from clawhip_bridge_mcp.server import (  # noqa: IMP001 — test file in mcp-servers
    _validate_limit,
    build_server,
)

# ---------------------------------------------------------------------------
# Local fixtures (inlined — no conftest per project convention)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register all 8 event types before each test.

    The event_log tests use an autouse fixture that calls ``unregister_all()``
    at teardown. Re-registering here (idempotent) ensures a clean known state.
    """
    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
    _reg("task.summary_emitted", "1.0.0", TaskSummaryEmittedPayload)
    _reg("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)


def _task_id(seed: int = 42) -> str:
    rng = Random(seed)
    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    return new_task_id(clock=clock, rng=rng)


# ---------------------------------------------------------------------------
# TestServerConstruction
# ---------------------------------------------------------------------------


class TestServerConstruction:
    """AC-1 / AC-2 structural checks on the FastMCP server instance."""

    @pytest.mark.asyncio
    async def test_build_server_registers_all_5_tools(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """build_server registers exactly 5 emit tools."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="test-actor",
        )
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "emit_event",
            "emit_blocker",
            "emit_summary",
            "emit_approval_request",
            "emit_completion",
        }

    @pytest.mark.asyncio
    async def test_build_server_registers_recent_events_resource(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """build_server registers the recent-events://current-day/{limit} URI template (F3)."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="test-actor",
        )
        templates = await mcp.list_resource_templates()
        uri_templates = [t.uriTemplate for t in templates]
        assert "recent-events://current-day/{limit}" in uri_templates

    @pytest.mark.asyncio
    async def test_no_mutation_tools_exposed(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """AC-2: No tool name contains mutation keywords."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="test-actor",
        )
        tools = await mcp.list_tools()
        mutation_keywords = {"edit", "delete", "modify", "update", "patch", "remove"}
        for tool in tools:
            for kw in mutation_keywords:
                assert kw not in tool.name.lower(), (
                    f"Tool {tool.name!r} contains mutation keyword {kw!r} — "
                    "clawhip-bridge must be append-only."
                )


# ---------------------------------------------------------------------------
# TestEmitEventTool
# ---------------------------------------------------------------------------


class TestEmitEventTool:
    """Tests for the generic emit_event tool."""

    @pytest.mark.asyncio
    async def test_emit_event_returns_event_id_and_emitted_at(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """emit_event returns dict with event_id and emitted_at keys."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="test-actor",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        result = await fn(
            type="task.created",
            payload={"task_id": _task_id(), "title": "test"},
        )
        assert "event_id" in result
        assert "emitted_at" in result
        assert result["event_id"].startswith("e-")

    @pytest.mark.asyncio
    async def test_emit_event_validates_type_against_registry(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """emit_event raises EventSchemaUnknown for unregistered type."""
        from events import EventSchemaUnknown

        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="test-actor",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        with pytest.raises(EventSchemaUnknown):
            await fn(
                type="task.does_not_exist",
                payload={"task_id": _task_id()},
            )

    @pytest.mark.asyncio
    async def test_emit_event_envelope_contains_injected_actor(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """The envelope written to the log carries the injected actor kind/id."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="orchestrator",
            actor_id="orch-42",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        await fn(
            type="task.created",
            payload={"task_id": _task_id(), "title": "actor test"},
        )
        path = current_day_path(tmp_path, fixed_clock.now())
        envelopes = list(read_log_lines(path))
        assert len(envelopes) == 1
        assert envelopes[0].actor == Actor(kind="orchestrator", id="orch-42")

    @pytest.mark.asyncio
    async def test_emit_event_envelope_uses_injected_clock(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """The envelope's emitted_at matches the FrozenClock epoch."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="clk-test",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        result = await fn(
            type="task.created",
            payload={"task_id": _task_id(), "title": "clock test"},
        )
        assert result["emitted_at"] == FROZEN_EPOCH.isoformat()

    @pytest.mark.asyncio
    async def test_emit_event_writes_to_log(self, tmp_path: Path, fixed_clock: FrozenClock) -> None:
        """Empirical AC-1: emit_event → envelope appears in today's JSONL log."""
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="system",
            actor_id="log-test",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        task_id = _task_id(seed=77)
        result = await fn(
            type="task.created",
            payload={"task_id": task_id, "title": "log test"},
        )
        path = current_day_path(tmp_path, fixed_clock.now())
        envelopes = list(read_log_lines(path))
        assert len(envelopes) == 1
        assert envelopes[0].event_id == result["event_id"]
        assert envelopes[0].type == "task.created"

    @pytest.mark.asyncio
    async def test_concurrent_emit_event_calls_serialize(self, tmp_path: Path) -> None:
        """F7: 10 concurrent emit_event calls land 10 distinct lines in JSONL.

        Story 2.4's writer holds an internal ``asyncio.Lock``; the 5 emit-tool
        closures share a single writer. ``asyncio.gather`` 10 dispatches and
        assert all 10 envelopes appear in the log with distinct event_ids.
        """
        log_dir = tmp_path / "events"
        log_dir.mkdir()
        clock = TickingClock(start_now=FROZEN_EPOCH)  # Each call gets a unique mono_ns
        mcp = build_server(
            base_dir=log_dir,
            clock=clock,
            actor_kind="worker",
            actor_id="concurrent-tester",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        results = await asyncio.gather(
            *[
                fn(
                    type="task.created",
                    payload={
                        "task_id": f"t-019b76da-0001-7000-8000-{i:012d}",
                        "title": f"ev-{i}",
                    },
                )
                for i in range(10)
            ]
        )
        assert len(results) == 10
        assert len({r["event_id"] for r in results}) == 10
        # Verify all 10 lines persisted to the log.
        log_path = current_day_path(log_dir, clock.now())
        envelopes = list(read_log_lines(log_path))
        assert len(envelopes) == 10
        assert len({e.event_id for e in envelopes}) == 10

    @pytest.mark.asyncio
    async def test_emit_event_rejects_extra_fields_in_typed_payload(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """F14: emit_event with extra payload fields → ValidationError per ``extra='forbid'``.

        ``TaskCompletedPayload`` is configured with ``extra='forbid'``. Routing
        through the generic ``emit_event`` tool MUST still reject extra
        fields, not silently drop them.
        """
        mcp = build_server(
            base_dir=tmp_path,
            clock=fixed_clock,
            actor_kind="worker",
            actor_id="extra-fields-tester",
        )
        fn = mcp._tool_manager._tools["emit_event"].fn
        with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError or wrapped
            await fn(
                type="task.completed",
                payload={
                    "task_id": _task_id(seed=88),
                    "summary": "x",
                    "pr_url": "https://example.com",
                    "EXTRA_FIELD": "should be rejected",
                },
            )


# ---------------------------------------------------------------------------
# TestTypedEmitTools
# ---------------------------------------------------------------------------


class TestTypedEmitTools:
    """Each typed sugar tool uses the correct event type."""

    @pytest.mark.asyncio
    async def test_emit_blocker_uses_task_blocker_raised_type(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="worker", actor_id="w-1"
        )
        fn = mcp._tool_manager._tools["emit_blocker"].fn
        await fn(task_id=_task_id(), reason="waiting for approval")
        path = current_day_path(tmp_path, fixed_clock.now())
        envs = list(read_log_lines(path))
        assert envs[0].type == "task.blocker_raised"

    @pytest.mark.asyncio
    async def test_emit_summary_uses_task_summary_emitted_type(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="worker", actor_id="w-2"
        )
        fn = mcp._tool_manager._tools["emit_summary"].fn
        await fn(task_id=_task_id(), summary="done step 1")
        path = current_day_path(tmp_path, fixed_clock.now())
        envs = list(read_log_lines(path))
        assert envs[0].type == "task.summary_emitted"

    @pytest.mark.asyncio
    async def test_emit_approval_request_uses_task_approval_requested_type(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="worker", actor_id="w-3"
        )
        fn = mcp._tool_manager._tools["emit_approval_request"].fn
        await fn(task_id=_task_id(), action="deploy", justification="ready")
        path = current_day_path(tmp_path, fixed_clock.now())
        envs = list(read_log_lines(path))
        assert envs[0].type == "task.approval_requested"

    @pytest.mark.asyncio
    async def test_emit_completion_uses_task_completed_type(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="worker", actor_id="w-4"
        )
        fn = mcp._tool_manager._tools["emit_completion"].fn
        await fn(task_id=_task_id(), summary="all done", pr_url="https://github.com/foo/1")
        path = current_day_path(tmp_path, fixed_clock.now())
        envs = list(read_log_lines(path))
        assert envs[0].type == "task.completed"


# ---------------------------------------------------------------------------
# TestRecentEventsResource
# ---------------------------------------------------------------------------


class TestRecentEventsResource:
    """Tests for the recent-events://current-day resource."""

    @pytest.mark.asyncio
    async def test_recent_events_returns_jsonl_text(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """recent_events returns newline-joined canonical JSON lines."""
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="system", actor_id="res-1"
        )
        # Write one event first
        emit_fn = mcp._tool_manager._tools["emit_event"].fn
        await emit_fn(
            type="task.created",
            payload={"task_id": _task_id(), "title": "resource test"},
        )
        # Materialise the resource via the URI template (F3) and read it.
        tpl = mcp._resource_manager._templates["recent-events://current-day/{limit}"]
        res_obj = await tpl.create_resource(
            "recent-events://current-day/50",
            {"limit": 50},
        )
        assert isinstance(res_obj, FunctionResource)
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert len(text) > 0
        import json

        parsed = json.loads(text)
        assert parsed["type"] == "task.created"

    @pytest.mark.asyncio
    async def test_recent_events_respects_limit(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """recent_events returns at most the last N envelopes (default 50)."""
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="system", actor_id="res-2"
        )
        emit_fn = mcp._tool_manager._tools["emit_event"].fn
        # Emit 3 events
        for i in range(3):
            await emit_fn(
                type="task.created",
                payload={"task_id": _task_id(seed=i + 10), "title": f"ev-{i}"},
            )
        tpl = mcp._resource_manager._templates["recent-events://current-day/{limit}"]
        res_obj2 = await tpl.create_resource(
            "recent-events://current-day/50",
            {"limit": 50},
        )
        assert isinstance(res_obj2, FunctionResource)
        raw = await res_obj2.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        lines = [ln for ln in text.split("\n") if ln.strip()]
        # All 3 should be present (limit=50, only 3 events written)
        assert len(lines) == 3

        # Verify limit truncation: ask for 2 and get exactly 2 trailing envelopes.
        res_obj3 = await tpl.create_resource(
            "recent-events://current-day/2",
            {"limit": 2},
        )
        assert isinstance(res_obj3, FunctionResource)
        raw3 = await res_obj3.read()
        text3 = raw3 if isinstance(raw3, str) else raw3.decode("utf-8")
        lines3 = [ln for ln in text3.split("\n") if ln.strip()]
        assert len(lines3) == 2

    @pytest.mark.asyncio
    async def test_recent_events_resource_rejects_limit_out_of_range(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """recent_events resource (production path) raises ValueError for limit ∉ [1, 1000].

        F18: post-F3 the resource takes a real ``limit`` URI-template parameter,
        so the validation is exercised end-to-end through the FastMCP template
        registry (not via a test-local helper).
        """
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="system", actor_id="res-3"
        )
        # Locate the registered template — FastMCP stores templates by URI template string.
        templates = mcp._resource_manager._templates
        tpl = templates["recent-events://current-day/{limit}"]

        # Out-of-range → resource creation propagates ValueError from _validate_limit.
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            await tpl.create_resource(
                "recent-events://current-day/0",
                {"limit": 0},
            )
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            await tpl.create_resource(
                "recent-events://current-day/1001",
                {"limit": 1001},
            )

        # Cross-check the production helper directly (no mcp instance needed).
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            _validate_limit(0)
        with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
            _validate_limit(1001)
        _validate_limit(1)
        _validate_limit(1000)

    @pytest.mark.asyncio
    async def test_recent_events_returns_empty_on_missing_file(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """recent_events returns empty string when no events written today."""
        mcp = build_server(
            base_dir=tmp_path, clock=fixed_clock, actor_kind="system", actor_id="res-4"
        )
        tpl = mcp._resource_manager._templates["recent-events://current-day/{limit}"]
        res_obj4 = await tpl.create_resource(
            "recent-events://current-day/50",
            {"limit": 50},
        )
        assert isinstance(res_obj4, FunctionResource)
        raw = await res_obj4.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert text == ""


# ---------------------------------------------------------------------------
# TestFastMCPIntegration  (F1 + F2)
# ---------------------------------------------------------------------------


class TestFastMCPIntegration:
    """End-to-end tests via mcp.call_tool() — exercises lifespan + JSON-schema + error mapping.

    F1/F2: previous tests bypassed the FastMCP runtime by reaching into
    ``mcp._tool_manager._tools[...].fn``. These tests round-trip through
    ``mcp.call_tool(...)`` AND enter ``mcp._mcp_server.lifespan(...)`` so
    AC-6 recovery + JSON-schema arg-coercion + error-envelope mapping are
    actually exercised.
    """

    @pytest.mark.asyncio
    async def test_emit_event_via_call_tool(self, tmp_path: Path) -> None:
        """F1: round-trip emit_event via FastMCP runtime; verify lifespan recovery + log write."""
        # Pre-populate base_dir with a partial-tail file so we can prove
        # recover_all_logs() ran inside the lifespan context.  We seed the
        # surviving complete line as a real canonical envelope so the post-emit
        # ``read_log_lines`` parse stays clean.
        log_dir = tmp_path / "events"
        log_dir.mkdir()
        partial_path = current_day_path(log_dir, FROZEN_EPOCH)
        partial_path.parent.mkdir(parents=True, exist_ok=True)

        # Build one valid envelope to use as the surviving complete line.
        from events.canonical import to_canonical_json

        seed_clock = FrozenClock(mono_ns=500_000, now=FROZEN_EPOCH)
        seed_envelope = EventEnvelope.create(
            event_id=new_event_id(clock=seed_clock),
            schema_version="1.0.0",
            type="task.created",
            emitted_at=seed_clock.now(),
            emitted_at_monotonic_ns=seed_clock.monotonic_ns(),
            actor=Actor(kind="worker", id="seed"),
            payload={"task_id": _task_id(seed=999), "title": "pre-existing"},
            request_id=new_request_id(clock=seed_clock),
        )
        complete_line = to_canonical_json(seed_envelope) + b"\n"
        partial_tail = b'{"partial": "no_newline"'  # missing trailing \n
        partial_path.write_bytes(complete_line + partial_tail)

        clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
        mcp = build_server(
            base_dir=log_dir,
            clock=clock,
            actor_kind="worker",
            actor_id="test-worker-1",
        )

        # Enter the lifespan context manually — this triggers recover_all_logs().
        async with mcp._mcp_server.lifespan(mcp._mcp_server):
            # Recovery should have trimmed the partial-tail line, keeping only the complete one.
            assert partial_path.read_bytes() == complete_line, (
                "recover_all_logs should trim trailing partial line"
            )

            # Now invoke the tool through FastMCP's call_tool runtime.
            result = await mcp.call_tool(
                "emit_event",
                {
                    "type": "task.created",
                    "payload": {
                        "task_id": _task_id(seed=101),
                        "title": "integration test",
                    },
                },
            )
            # FastMCP returns (Sequence[ContentBlock], dict). The tuple form is
            # the unstructured + structured response.
            assert result is not None
            _content, structured = result if isinstance(result, tuple) else (result, None)
            # Structured result (when present) carries the dict the tool returned.
            if structured is not None:
                assert "event_id" in structured
                assert structured["event_id"].startswith("e-")

            # Verify the JSONL log got the event appended (complete-line preserved + new emission).
            envelopes = list(read_log_lines(partial_path))
            assert len(envelopes) == 2, (
                f"expected 2 envelopes (seed + emitted), got {len(envelopes)}"
            )
            # The seed envelope is at index 0; the emitted envelope at index 1.
            assert envelopes[0].event_id == seed_envelope.event_id
            assert envelopes[1].type == "task.created"

    @pytest.mark.asyncio
    async def test_emit_event_call_tool_error_maps_to_mcp_error(self, tmp_path: Path) -> None:
        """F2: unknown event type → FastMCP wraps the error in JSON-RPC error envelope."""
        log_dir = tmp_path / "events"
        log_dir.mkdir()
        clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
        mcp = build_server(
            base_dir=log_dir,
            clock=clock,
            actor_kind="worker",
            actor_id="test-worker-2",
        )
        async with mcp._mcp_server.lifespan(mcp._mcp_server):
            # call_tool should propagate or wrap EventSchemaUnknown.
            with pytest.raises(Exception):  # noqa: B017 — FastMCP wraps; assert SOME exception
                await mcp.call_tool(
                    "emit_event",
                    {"type": "unregistered.type", "payload": {}},
                )


# ---------------------------------------------------------------------------
# TestEntryPoint
# ---------------------------------------------------------------------------


class TestEntryPoint:
    """AC-5 entry point env-var validation tests."""

    def test_main_exits_2_on_missing_actor_kind(self, tmp_path: Path) -> None:
        """python -m clawhip_bridge_mcp without CLAWHIP_BRIDGE_ACTOR_KIND → exit 2.

        F8: extend the parent env (preserving PYTHONPATH/VIRTUAL_ENV/etc.) and
        explicitly delete the variable under test, instead of replacing the env
        with a hand-rolled minimal one (which broke on tox / non-editable
        installs / Windows CI).
        """
        env = {k: v for k, v in os.environ.items() if k != "CLAWHIP_BRIDGE_ACTOR_KIND"}
        env["CLAWHIP_BRIDGE_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "clawhip_bridge_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2, (
            f"got {result.returncode} (stdout: {result.stdout!r}, stderr: {result.stderr!r})"
        )
        assert "CLAWHIP_BRIDGE_ACTOR_KIND" in result.stderr

    def test_main_exits_2_on_missing_actor_id(self, tmp_path: Path) -> None:
        """python -m clawhip_bridge_mcp without CLAWHIP_BRIDGE_ACTOR_ID → exit 2."""
        env = {k: v for k, v in os.environ.items() if k != "CLAWHIP_BRIDGE_ACTOR_ID"}
        env["CLAWHIP_BRIDGE_ACTOR_KIND"] = "system"
        result = subprocess.run(
            [sys.executable, "-m", "clawhip_bridge_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2, (
            f"got {result.returncode} (stdout: {result.stdout!r}, stderr: {result.stderr!r})"
        )
        assert "CLAWHIP_BRIDGE_ACTOR_ID" in result.stderr
