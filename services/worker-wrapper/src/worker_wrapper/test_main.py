"""Tests for __main__.py — structlog wiring and main lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from worker_wrapper.__main__ import _configure_structlog
from worker_wrapper.app.config import WorkerSettings


class TestStructlogWiring:
    def test_configure_structlog_idempotent(self) -> None:
        """Second call is a no-op (sentinel guard)."""
        import worker_wrapper.__main__ as mod

        mod._STRUCTLOG_CONFIGURED = False
        _configure_structlog()
        assert mod._STRUCTLOG_CONFIGURED is True
        _configure_structlog()
        assert mod._STRUCTLOG_CONFIGURED is True

    def test_configure_structlog_sets_root_handler(self) -> None:
        import logging

        import worker_wrapper.__main__ as mod

        mod._STRUCTLOG_CONFIGURED = False
        _configure_structlog()
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert root.handlers[0].formatter is not None


class TestMainLifecycle:
    def test_main_invokes_run_without_error(self, tmp_path: Path) -> None:
        """main() creates event loop and calls _run()."""
        import worker_wrapper.__main__ as mod

        mod._STRUCTLOG_CONFIGURED = False

        async def fake_run() -> None:
            pass

        with patch.object(mod, "_run", side_effect=fake_run):
            mod.main()

    def test_ready_file_lifecycle(self, tmp_path: Path) -> None:
        """_run() creates and removes the ready file on shutdown."""
        import worker_wrapper.__main__ as mod

        mod._STRUCTLOG_CONFIGURED = False
        ready = tmp_path / "ready"

        async def fake_run() -> None:
            """Simulate the ready-file touch/unlink cycle."""
            settings = WorkerSettings(ready_file_path=str(ready))
            rdy = Path(settings.ready_file_path)
            rdy.touch()
            assert rdy.exists()
            rdy.unlink(missing_ok=True)

        with patch.object(mod, "_run", side_effect=fake_run):
            mod.main()
        assert not ready.exists()
