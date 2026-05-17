"""Tests for task-registry MCP server (Story 5.8 AC-9).

Tests across 5 classes. No conftest — fixtures inlined per project convention.
All async tests use pytest-asyncio strict mode.

Classes:
  TestServerConstruction    — server-construction structural checks
  TestResourceHandlers      — read-only resource queries against seeded data
  TestToolHandlers          — bounded-write tool stubs
  TestTierEnforcement       — capability-tier placeholder behaviour
  TestEntryPoint            — env-var validation (subprocess)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from capabilities import CallerContext, Tier, check_tier
from events.errors import CapabilityDenied
from registry_state.schema import Base, Event, Task  # noqa: IMP001 — test file
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from task_registry_mcp.app.main import build_server
from task_registry_mcp.handlers.tools import TIER_MAP, _validate_caller_trace_id

# ---------------------------------------------------------------------------
# Local fixtures (inlined — no conftest per project convention)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

# Story 9.5: deterministic valid trace_id values for ``caller_trace_id`` kwarg.
_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_VALID_TG_TRACE_ID = "tg:42"


@pytest_asyncio.fixture
async def db_session_maker(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite with schema and return a session maker."""
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

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Seed tasks first (Events have FK to tasks)
    async with session_maker() as session:
        t1 = Task(
            id="t-00000001-0001-7000-8000-000000000001",
            status="executing",
            created_at=_NOW,
            updated_at=_NOW,
            actor_kind="operator",
            actor_id="op-1",
            title="Implement feature X",
        )
        t2 = Task(
            id="t-00000002-0001-7000-8000-000000000002",
            status="plan_ready",
            created_at=_NOW,
            updated_at=_NOW,
            actor_kind="operator",
            actor_id="op-1",
            title="Fix bug Y",
        )
        t3 = Task(
            id="t-00000003-0001-7000-8000-000000000003",
            status="completed",
            created_at=_NOW,
            updated_at=_NOW,
            actor_kind="operator",
            actor_id="op-1",
            title="Refactor Z",
        )
        session.add_all([t1, t2, t3])
        await session.commit()

    # Seed events (after tasks committed — FK constraint)
    async with session_maker() as session:
        e1 = Event(
            id="e-00000001-0001-7000-8000-000000000001",
            type="task.approval_requested",
            schema_version="1.0.0",
            emitted_at=_NOW,
            emitted_at_monotonic_ns=1_000_000,
            actor_kind="worker",
            actor_id="w-1",
            task_id="t-00000002-0001-7000-8000-000000000002",
            session_id=None,
            parent_event_id=None,
            request_id="req-001",
            payload_json='{"task_id": "t-00000002-0001-7000-8000-000000000002"}',
        )
        e2 = Event(
            id="e-00000002-0001-7000-8000-000000000002",
            type="task.blocker_raised",
            schema_version="1.0.0",
            emitted_at=_NOW,
            emitted_at_monotonic_ns=2_000_000,
            actor_kind="worker",
            actor_id="w-1",
            task_id="t-00000001-0001-7000-8000-000000000001",
            session_id=None,
            parent_event_id=None,
            request_id="req-002",
            payload_json='{"task_id": "t-00000001-0001-7000-8000-000000000001", "reason": "stuck"}',
        )
        session.add_all([e1, e2])
        await session.commit()

    return session_maker


def _build(
    session_maker: async_sessionmaker[AsyncSession],
    actor_kind: str = "worker",
    actor_id: str = "test-worker",
):
    return build_server(
        actor_kind=actor_kind,
        actor_id=actor_id,
        _session_maker=session_maker,
    )


# ---------------------------------------------------------------------------
# TestServerConstruction
# ---------------------------------------------------------------------------


class TestServerConstruction:
    """AC-1 / AC-2 structural checks on the FastMCP server instance."""

    @pytest.mark.asyncio
    async def test_build_server_registers_3_tools(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "task_add_note",
            "task_attach_artifact",
            "task_emit_event",
        }

    @pytest.mark.asyncio
    async def test_build_server_registers_4_resources(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        templates = await mcp.list_resource_templates()
        template_uris = {t.uriTemplate for t in templates}
        resources = await mcp.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        # task/detail has a URI template param → shows as template
        assert "task://detail/{task_id}" in template_uris
        # Static resources (no params) → show as resources
        assert "task://list" in resource_uris
        assert "task://approval-queue" in resource_uris
        assert "task://blockers" in resource_uris

    @pytest.mark.asyncio
    async def test_no_mutation_keywords_in_tool_names(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tools = await mcp.list_tools()
        mutation_keywords = {"edit", "delete", "modify", "update", "patch", "remove"}
        for tool in tools:
            for kw in mutation_keywords:
                assert kw not in tool.name.lower(), (
                    f"Tool {tool.name!r} contains mutation keyword {kw!r}"
                )


# ---------------------------------------------------------------------------
# TestResourceHandlers
# ---------------------------------------------------------------------------


class TestResourceHandlers:
    """Tests for the 4 read-only MCP resources."""

    @pytest.mark.asyncio
    async def test_task_list_returns_seeded_tasks(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        res_obj = mcp._resource_manager._resources["task://list"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert len(data) == 3
        assert {t["title"] for t in data} == {
            "Implement feature X",
            "Fix bug Y",
            "Refactor Z",
        }

    @pytest.mark.asyncio
    async def test_task_detail_returns_specific_task(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tpl = mcp._resource_manager._templates["task://detail/{task_id}"]
        tid = "t-00000001-0001-7000-8000-000000000001"
        res = await tpl.create_resource(f"task://detail/{tid}", {"task_id": tid})
        raw = await res.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert data["id"] == tid
        assert data["status"] == "executing"
        assert data["title"] == "Implement feature X"

    @pytest.mark.asyncio
    async def test_task_detail_returns_empty_for_missing_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tpl = mcp._resource_manager._templates["task://detail/{task_id}"]
        res = await tpl.create_resource("task://detail/t-nonexistent", {"task_id": "t-nonexistent"})
        raw = await res.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert text == ""

    @pytest.mark.asyncio
    async def test_approval_queue_returns_tasks_with_approval_events(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        res_obj = mcp._resource_manager._resources["task://approval-queue"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert len(data) == 1
        assert data[0]["title"] == "Fix bug Y"
        assert data[0]["status"] == "plan_ready"

    @pytest.mark.asyncio
    async def test_blockers_returns_tasks_with_blocker_events(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        res_obj = mcp._resource_manager._resources["task://blockers"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert len(data) == 1
        assert data[0]["title"] == "Implement feature X"

    @pytest.mark.asyncio
    async def test_task_to_dict_includes_hint_field(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Story 7.6: _task_to_dict includes hint (null when not set)."""
        mcp = _build(db_session_maker)
        tpl = mcp._resource_manager._templates["task://detail/{task_id}"]
        tid = "t-00000001-0001-7000-8000-000000000001"
        res = await tpl.create_resource(f"task://detail/{tid}", {"task_id": tid})
        raw = await res.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert "hint" in data
        assert data["hint"] is None

    @pytest.mark.asyncio
    async def test_task_to_dict_includes_hint_when_set(self, tmp_path: Path) -> None:
        """Story 7.6: _task_to_dict returns hint value when set on Task."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            session.add(
                Task(
                    id="t-hint-001",
                    status="pending",
                    created_at=_NOW,
                    updated_at=_NOW,
                    actor_kind="operator",
                    actor_id="op-1",
                    title="Hinted task",
                    hint="focus on rate limiting",
                )
            )
            await session.commit()

        mcp = _build(sm)
        tpl = mcp._resource_manager._templates["task://detail/{task_id}"]
        res = await tpl.create_resource("task://detail/t-hint-001", {"task_id": "t-hint-001"})
        raw = await res.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert data["hint"] == "focus on rate limiting"

    @pytest.mark.asyncio
    async def test_blockers_empty_when_no_blocker_events(self, tmp_path: Path) -> None:
        """With no blocker events, blockers returns empty list."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            session.add(
                Task(
                    id="t-solo-002",
                    status="executing",
                    created_at=_NOW,
                    updated_at=_NOW,
                    actor_kind="operator",
                    actor_id="op-1",
                    title="Solo task",
                )
            )
            await session.commit()

        mcp = _build(sm)
        res_obj = mcp._resource_manager._resources["task://blockers"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert json.loads(text) == []

    @pytest.mark.asyncio
    async def test_approval_queue_empty_when_no_approval_events(self, tmp_path: Path) -> None:
        """With no approval events, approval-queue returns empty list."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            session.add(
                Task(
                    id="t-solo-001",
                    status="executing",
                    created_at=_NOW,
                    updated_at=_NOW,
                    actor_kind="operator",
                    actor_id="op-1",
                    title="Solo task",
                )
            )
            await session.commit()

        mcp = _build(sm)
        res_obj = mcp._resource_manager._resources["task://approval-queue"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert json.loads(text) == []


# ---------------------------------------------------------------------------
# TestToolHandlers
# ---------------------------------------------------------------------------


class TestToolHandlers:
    """Tests for the 3 bounded-write tool stubs."""

    @pytest.mark.asyncio
    async def test_task_add_note_succeeds(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            note="This is a test note",
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_task_add_note_rejects_missing_task(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(task_id="t-nonexistent", note="note", caller_trace_id=_VALID_TRACE_ID)
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_task_add_note_rejects_empty_params(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(task_id="", note="some note", caller_trace_id=_VALID_TRACE_ID)
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_task_attach_artifact_succeeds(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_attach_artifact"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            artifact_url="https://example.com/artifact.log",
            artifact_type="log",
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_task_attach_artifact_rejects_missing_task(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_attach_artifact"].fn
        result = await fn(
            task_id="t-nonexistent",
            artifact_url="https://example.com/f.txt",
            artifact_type="text",
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_task_attach_artifact_rejects_empty_params(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_attach_artifact"].fn
        result = await fn(
            task_id="",
            artifact_url="https://example.com/f.txt",
            artifact_type="text",
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_task_emit_event_succeeds(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_emit_event"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            event_type="task.note_added",
            payload={"note": "hello"},
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_task_emit_event_rejects_missing_event_type(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_emit_event"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            event_type="",
            payload={},
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_task_emit_event_rejects_empty_task_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_emit_event"].fn
        result = await fn(
            task_id="",
            event_type="task.note_added",
            payload={},
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# TestTierEnforcement
# ---------------------------------------------------------------------------


class TestTierEnforcement:
    """AC-3: Real tier enforcement via capabilities.check_tier."""

    @pytest.mark.parametrize("kind", ["operator", "orchestrator", "worker", "system", "clawhip"])
    def test_check_tier_allows_valid_callers(self, kind: str) -> None:
        for tool_name, tier in TIER_MAP.items():
            caller = CallerContext(actor_kind=kind, actor_id="w-001")
            result = check_tier(tool_name, caller, tier)
            assert result.tier == tier

    def test_tier_map_all_tier_one(self) -> None:
        assert all(t == Tier.ONE for t in TIER_MAP.values())

    @pytest.mark.asyncio
    async def test_tool_raises_capability_denied_when_tier_denies(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify CapabilityDenied propagates when a caller lacks tier."""
        from unittest.mock import patch

        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        with (
            patch(
                "task_registry_mcp.handlers.tools.check_tier",
                side_effect=CapabilityDenied(
                    action="task.add_note", actor_kind="unknown", required_tier=1, reason="test"
                ),
            ),
            pytest.raises(CapabilityDenied),
        ):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                note="test",
                caller_trace_id=_VALID_TRACE_ID,
            )


class TestTier2Enforcement:
    """AC-1: Tier-2 denial at handler level (Story 6.2)."""

    @pytest.mark.asyncio
    async def test_tier2_denied_when_actor_constrained(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Tier-2 tool denied when actor's max tier is reduced to Tier.ONE."""
        from unittest.mock import patch

        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        patched_map = {**TIER_MAP, "task.add_note": Tier.TWO}
        constrained_actors = {
            "operator": Tier.THREE,
            "system": Tier.THREE,
            "clawhip": Tier.ONE,
            "orchestrator": Tier.ONE,
            "worker": Tier.ONE,
        }
        with (
            patch("task_registry_mcp.handlers.tools.TIER_MAP", patched_map),
            patch("capabilities.tiers._MAX_TIER_BY_ACTOR", constrained_actors),
            pytest.raises(CapabilityDenied) as exc_info,
        ):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                note="test",
                caller_trace_id=_VALID_TRACE_ID,
            )
        assert "allows Tier.1" in exc_info.value.reason


class TestApprovalLookup:
    """AC-5 / AC-6: _make_approval_lookup + Tier-3 approval gate (Story 6.2)."""

    @pytest.mark.asyncio
    async def test_approval_lookup_returns_false_when_no_event(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from task_registry_mcp.handlers.tools import _make_approval_lookup

        lookup = _make_approval_lookup(db_session_maker)
        result = await lookup("t-nonexistent", "git_push")
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_lookup_returns_true_when_event_seeded(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        from task_registry_mcp.handlers.tools import _make_approval_lookup

        task_id = "t-00000099-0001-7000-8000-000000000099"
        async with db_session_maker() as session:
            session.add(
                Task(
                    id=task_id,
                    status="executing",
                    created_at=_NOW,
                    updated_at=_NOW,
                    actor_kind="operator",
                    actor_id="op-1",
                )
            )
            await session.flush()
            session.add(
                Event(
                    id="evt-approval-001",
                    type="approval.granted",
                    schema_version="1.0.0",
                    emitted_at=_NOW,
                    emitted_at_monotonic_ns=0,
                    actor_kind="operator",
                    actor_id="op-1",
                    task_id=task_id,
                    request_id="req-001",
                    payload_json='{"task_id":"' + task_id + '"}',
                )
            )
            await session.commit()

        lookup = _make_approval_lookup(db_session_maker)
        result = await lookup(task_id, "git_push")
        assert result is True

    @pytest.mark.asyncio
    async def test_tier3_denied_via_check_tier_with_approval(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """AC-6: check_tier_with_approval denies Tier-3 without approval."""
        from capabilities import check_tier_with_approval

        from task_registry_mcp.handlers.tools import _make_approval_lookup

        caller = CallerContext(actor_kind="operator", actor_id="op-1", task_id="t-none")
        lookup = _make_approval_lookup(db_session_maker)
        with pytest.raises(CapabilityDenied, match="no_matching_approval"):
            await check_tier_with_approval(
                "git_push",
                caller,
                Tier.THREE,
                approval_lookup=lookup,
            )

    @pytest.mark.asyncio
    async def test_approval_lookup_with_multiple_events(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Multiple approval.granted rows — scalars().first() handles correctly."""
        from task_registry_mcp.handlers.tools import _make_approval_lookup

        task_id = "t-00000099-0002-7000-8000-000000000099"
        async with db_session_maker() as session:
            session.add(
                Task(
                    id=task_id,
                    status="executing",
                    created_at=_NOW,
                    updated_at=_NOW,
                    actor_kind="operator",
                    actor_id="op-1",
                )
            )
            await session.flush()
            for i in range(3):
                session.add(
                    Event(
                        id=f"evt-approval-multi-{i}",
                        type="approval.granted",
                        schema_version="1.0.0",
                        emitted_at=_NOW,
                        emitted_at_monotonic_ns=0,
                        actor_kind="operator",
                        actor_id="op-1",
                        task_id=task_id,
                        request_id=f"req-multi-{i}",
                        payload_json='{"task_id":"' + task_id + '"}',
                    )
                )
            await session.commit()

        lookup = _make_approval_lookup(db_session_maker)
        result = await lookup(task_id, "git_push")
        assert result is True


# ---------------------------------------------------------------------------
# TestEntryPoint
# ---------------------------------------------------------------------------


class TestEntryPoint:
    """AC-6 entry point env-var validation tests."""

    def test_main_exits_2_on_missing_db_path(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TASK_REGISTRY_DB_PATH"}
        env["TASK_REGISTRY_ACTOR_KIND"] = "worker"
        env["TASK_REGISTRY_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "task_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "TASK_REGISTRY_DB_PATH" in result.stderr

    def test_main_exits_2_on_missing_actor_kind(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TASK_REGISTRY_ACTOR_KIND"}
        env["TASK_REGISTRY_DB_PATH"] = "/tmp/test.db"
        env["TASK_REGISTRY_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "task_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "TASK_REGISTRY_ACTOR_KIND" in result.stderr

    def test_main_exits_2_on_missing_actor_id(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TASK_REGISTRY_ACTOR_ID"}
        env["TASK_REGISTRY_DB_PATH"] = "/tmp/test.db"
        env["TASK_REGISTRY_ACTOR_KIND"] = "worker"
        result = subprocess.run(
            [sys.executable, "-m", "task_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "TASK_REGISTRY_ACTOR_ID" in result.stderr

    def test_main_exits_2_on_invalid_actor_kind(self, tmp_path: Path) -> None:
        env = dict(os.environ)
        env["TASK_REGISTRY_DB_PATH"] = "/tmp/test.db"
        env["TASK_REGISTRY_ACTOR_KIND"] = "invalid_role"
        env["TASK_REGISTRY_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "task_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "invalid" in result.stderr.lower()


# ---------------------------------------------------------------------------
# TestCallerTraceId (Story 9.5 / FR58 MCP)
# ---------------------------------------------------------------------------


class TestCallerTraceIdValidationHelper:
    """Unit tests for the module-level ``_validate_caller_trace_id`` helper."""

    def test_accepts_uuidv7(self) -> None:
        _validate_caller_trace_id(_VALID_TRACE_ID)

    def test_accepts_telegram_form(self) -> None:
        _validate_caller_trace_id(_VALID_TG_TRACE_ID)

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "bad-format",
            "tg:",
            "tg:0",
            "01917e5c-a7d1-7000-8abc-0123456789ab\n",
            " 01917e5c-a7d1-7000-8abc-0123456789ab",
        ],
    )
    def test_rejects_invalid_shapes(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Story 9.1 contract"):
            _validate_caller_trace_id(bad)


class TestCallerTraceIdToolHandlers:
    """AC1 / AC2 / AC6 for the 3 task-registry tools."""

    @pytest.mark.asyncio
    async def test_task_add_note_requires_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        with pytest.raises(TypeError):
            await fn(task_id="t-00000001-0001-7000-8000-000000000001", note="x")

    @pytest.mark.asyncio
    async def test_task_add_note_rejects_invalid_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        with pytest.raises(ValueError, match="Story 9.1 contract"):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                note="x",
                caller_trace_id="bad",
            )

    @pytest.mark.asyncio
    async def test_task_add_note_accepts_uuidv7_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            note="x",
            caller_trace_id=_VALID_TRACE_ID,
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_task_add_note_accepts_telegram_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            note="x",
            caller_trace_id=_VALID_TG_TRACE_ID,
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_task_attach_artifact_requires_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_attach_artifact"].fn
        with pytest.raises(TypeError):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                artifact_url="https://example.com/a",
                artifact_type="log",
            )

    @pytest.mark.asyncio
    async def test_task_attach_artifact_rejects_invalid_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_attach_artifact"].fn
        with pytest.raises(ValueError, match="Story 9.1 contract"):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                artifact_url="https://example.com/a",
                artifact_type="log",
                caller_trace_id="bad",
            )

    @pytest.mark.asyncio
    async def test_task_emit_event_requires_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_emit_event"].fn
        with pytest.raises(TypeError):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                event_type="task.note_added",
                payload={},
            )

    @pytest.mark.asyncio
    async def test_task_emit_event_accepts_telegram_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_emit_event"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            event_type="task.note_added",
            payload={},
            caller_trace_id=_VALID_TG_TRACE_ID,
        )
        assert result == {"ok": True}


class TestCallerTraceIdToolSchemas:
    """AC9: FastMCP-derived input schemas include ``caller_trace_id`` as required."""

    @pytest.mark.asyncio
    async def test_all_tool_schemas_require_caller_trace_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tools = await mcp.list_tools()
        for tool in tools:
            assert "caller_trace_id" in tool.inputSchema["required"], (
                f"tool {tool.name!r} schema missing caller_trace_id"
            )
            assert tool.inputSchema["properties"]["caller_trace_id"]["type"] == "string"
