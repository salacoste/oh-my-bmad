"""Tests for session-registry MCP server (Story 5.9 AC-9).

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
from registry_state.schema import Base, Session, Task  # noqa: IMP001 — test file
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from session_registry_mcp.app.main import build_server
from session_registry_mcp.handlers.tools import _check_tier as tools_check_tier

# ---------------------------------------------------------------------------
# Local fixtures (inlined — no conftest per project convention)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
_NOW_PLUS = datetime(2026, 1, 15, 13, 0, 0, tzinfo=UTC)


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

    # Seed tasks first (Sessions have FK to tasks)
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
        session.add_all([t1, t2])
        await session.commit()

    # Seed sessions (after tasks committed — FK constraint)
    async with session_maker() as session:
        s1 = Session(
            id="s-00000001-0001-7000-8000-000000000001",
            task_id="t-00000001-0001-7000-8000-000000000001",
            worker_kind="claude-code",
            worktree_path="/tmp/worktree-1",
            status="active",
            started_at=_NOW,
            ended_at=None,
            last_heartbeat_at=_NOW_PLUS,
        )
        s2 = Session(
            id="s-00000002-0001-7000-8000-000000000002",
            task_id="t-00000001-0001-7000-8000-000000000001",
            worker_kind="claude-code",
            worktree_path="/tmp/worktree-2",
            status="active",
            started_at=_NOW_PLUS,
            ended_at=None,
            last_heartbeat_at=None,
        )
        s3 = Session(
            id="s-00000003-0001-7000-8000-000000000003",
            task_id="t-00000002-0001-7000-8000-000000000002",
            worker_kind="claude-code",
            worktree_path="/tmp/worktree-3",
            status="finished",
            started_at=_NOW,
            ended_at=_NOW_PLUS,
            last_heartbeat_at=_NOW_PLUS,
        )
        session.add_all([s1, s2, s3])
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
            "session_register",
            "session_heartbeat",
            "session_close",
        }

    @pytest.mark.asyncio
    async def test_build_server_registers_3_resources(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        templates = await mcp.list_resource_templates()
        template_uris = {t.uriTemplate for t in templates}
        resources = await mcp.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        # session/detail has a URI template param → shows as template
        assert "session://detail/{session_id}" in template_uris
        # Static resources (no params) → show as resources
        assert "session://active" in resource_uris
        assert "session://heartbeats" in resource_uris

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
    """Tests for the 3 read-only MCP resources."""

    @pytest.mark.asyncio
    async def test_session_active_returns_active_only(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        res_obj = mcp._resource_manager._resources["session://active"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert len(data) == 2
        assert all(s["status"] == "active" for s in data)
        # Ordered by started_at desc — s2 (13:00) comes first
        assert data[0]["id"] == "s-00000002-0001-7000-8000-000000000002"

    @pytest.mark.asyncio
    async def test_session_active_empty_when_no_active(self, tmp_path: Path) -> None:
        """With no active sessions, active resource returns empty list."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        # Seed a task + finished session
        async with sm() as session:
            session.add(
                Task(
                    id="t-solo-001",
                    status="completed",
                    created_at=_NOW,
                    updated_at=_NOW,
                    actor_kind="operator",
                    actor_id="op-1",
                    title="Solo task",
                )
            )
            await session.commit()
        async with sm() as session:
            session.add(
                Session(
                    id="s-solo-001",
                    task_id="t-solo-001",
                    worker_kind="claude-code",
                    worktree_path=None,
                    status="finished",
                    started_at=_NOW,
                    ended_at=_NOW_PLUS,
                    last_heartbeat_at=None,
                )
            )
            await session.commit()

        mcp = _build(sm)
        res_obj = mcp._resource_manager._resources["session://active"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert json.loads(text) == []

    @pytest.mark.asyncio
    async def test_session_detail_returns_specific_session(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tpl = mcp._resource_manager._templates["session://detail/{session_id}"]
        sid = "s-00000001-0001-7000-8000-000000000001"
        res = await tpl.create_resource(f"session://detail/{sid}", {"session_id": sid})
        raw = await res.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        assert data["id"] == sid
        assert data["status"] == "active"
        assert data["task_id"] == "t-00000001-0001-7000-8000-000000000001"
        assert data["worker_kind"] == "claude-code"

    @pytest.mark.asyncio
    async def test_session_detail_returns_empty_for_missing_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        tpl = mcp._resource_manager._templates["session://detail/{session_id}"]
        res = await tpl.create_resource(
            "session://detail/s-nonexistent", {"session_id": "s-nonexistent"}
        )
        raw = await res.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert text == ""

    @pytest.mark.asyncio
    async def test_session_heartbeats_returns_sessions_with_heartbeats(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        res_obj = mcp._resource_manager._resources["session://heartbeats"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        data = json.loads(text)
        # s1 and s3 have heartbeats; s2 has None
        assert len(data) == 2
        assert {s["id"] for s in data} == {
            "s-00000001-0001-7000-8000-000000000001",
            "s-00000003-0001-7000-8000-000000000003",
        }

    @pytest.mark.asyncio
    async def test_session_heartbeats_empty_when_none(self, tmp_path: Path) -> None:
        """With no heartbeat timestamps, heartbeats returns empty list."""
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
        async with sm() as session:
            session.add(
                Session(
                    id="s-solo-002",
                    task_id="t-solo-002",
                    worker_kind="claude-code",
                    worktree_path=None,
                    status="active",
                    started_at=_NOW,
                    ended_at=None,
                    last_heartbeat_at=None,
                )
            )
            await session.commit()

        mcp = _build(sm)
        res_obj = mcp._resource_manager._resources["session://heartbeats"]
        raw = await res_obj.read()
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        assert json.loads(text) == []


# ---------------------------------------------------------------------------
# TestToolHandlers
# ---------------------------------------------------------------------------


class TestToolHandlers:
    """Tests for the 3 bounded-write tool stubs."""

    @pytest.mark.asyncio
    async def test_session_register_succeeds(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_register"].fn
        result = await fn(
            task_id="t-00000001-0001-7000-8000-000000000001",
            worker_kind="claude-code",
            worktree_path="/tmp/wt",
        )
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_session_register_rejects_missing_task(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_register"].fn
        result = await fn(
            task_id="t-nonexistent",
            worker_kind="claude-code",
            worktree_path="/tmp/wt",
        )
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_session_register_rejects_empty_params(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_register"].fn
        result = await fn(task_id="", worker_kind="claude-code", worktree_path="/tmp/wt")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_session_heartbeat_succeeds(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_heartbeat"].fn
        result = await fn(session_id="s-00000001-0001-7000-8000-000000000001")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_session_heartbeat_rejects_missing_session(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_heartbeat"].fn
        result = await fn(session_id="s-nonexistent")
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_session_heartbeat_rejects_empty_session_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_heartbeat"].fn
        result = await fn(session_id="")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_session_close_succeeds(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_close"].fn
        result = await fn(session_id="s-00000001-0001-7000-8000-000000000001")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_session_close_rejects_missing_session(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_close"].fn
        result = await fn(session_id="s-nonexistent")
        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_session_close_rejects_empty_session_id(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_close"].fn
        result = await fn(session_id="")
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# TestTierEnforcement
# ---------------------------------------------------------------------------


class TestTierEnforcement:
    """AC-3: Tier placeholder returns True (NO-OP)."""

    def test_check_tier_returns_true(self) -> None:
        assert tools_check_tier("worker", "session_register") is True
        assert tools_check_tier("operator", "session_close") is True

    @pytest.mark.asyncio
    async def test_tool_raises_permission_error_when_tier_denies(
        self, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """Patch _check_tier to return False and verify PermissionError."""
        from unittest.mock import patch

        mcp = _build(db_session_maker)
        fn = mcp._tool_manager._tools["session_register"].fn
        with (
            patch("session_registry_mcp.handlers.tools._check_tier", return_value=False),
            pytest.raises(PermissionError, match="not authorized"),
        ):
            await fn(
                task_id="t-00000001-0001-7000-8000-000000000001",
                worker_kind="claude-code",
                worktree_path="/tmp/wt",
            )


# ---------------------------------------------------------------------------
# TestEntryPoint
# ---------------------------------------------------------------------------


class TestEntryPoint:
    """AC-6 entry point env-var validation tests."""

    def test_main_exits_2_on_missing_db_path(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "SESSION_REGISTRY_DB_PATH"}
        env["SESSION_REGISTRY_ACTOR_KIND"] = "worker"
        env["SESSION_REGISTRY_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "session_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "SESSION_REGISTRY_DB_PATH" in result.stderr

    def test_main_exits_2_on_missing_actor_kind(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "SESSION_REGISTRY_ACTOR_KIND"}
        env["SESSION_REGISTRY_DB_PATH"] = "/tmp/test.db"
        env["SESSION_REGISTRY_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "session_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "SESSION_REGISTRY_ACTOR_KIND" in result.stderr

    def test_main_exits_2_on_missing_actor_id(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "SESSION_REGISTRY_ACTOR_ID"}
        env["SESSION_REGISTRY_DB_PATH"] = "/tmp/test.db"
        env["SESSION_REGISTRY_ACTOR_KIND"] = "worker"
        result = subprocess.run(
            [sys.executable, "-m", "session_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "SESSION_REGISTRY_ACTOR_ID" in result.stderr

    def test_main_exits_2_on_invalid_actor_kind(self, tmp_path: Path) -> None:
        env = dict(os.environ)
        env["SESSION_REGISTRY_DB_PATH"] = "/tmp/test.db"
        env["SESSION_REGISTRY_ACTOR_KIND"] = "invalid_role"
        env["SESSION_REGISTRY_ACTOR_ID"] = "test-id"
        result = subprocess.run(
            [sys.executable, "-m", "session_registry_mcp"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "invalid" in result.stderr.lower()
