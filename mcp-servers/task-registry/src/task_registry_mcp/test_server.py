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
from task_registry_mcp.handlers.tools import TIER_MAP

# ---------------------------------------------------------------------------
# Local fixtures (inlined — no conftest per project convention)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


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
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_task_add_note_rejects_missing_task(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(task_id="t-nonexistent", note="note")
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_task_add_note_rejects_empty_params(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["task_add_note"].fn
        result = await fn(task_id="", note="some note")
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
            patch("task_registry_mcp.handlers.tools.check_tier", side_effect=CapabilityDenied(
                action="task.add_note", actor_kind="unknown", required_tier=1, reason="test"
            )),
            pytest.raises(CapabilityDenied),
        ):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                note="test",
            )


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
