"""S-11 separability test — Codex runtime is fully optional (NFR-M10 / FR98).

Epic 28 / FR98 / NFR-M10.  The Codex runtime is a subprocess adapter
(``CodexRunner``) selected by ``runtime="codex"`` in the factory.  The
toggle is ``WorkerSettings.codex_command`` (default ``"codex"``), driven by
the ``WORKER_CODEX_COMMAND`` env var.  Setting it to ``""`` disables Codex
without modifying any other service's source code — the factory still
returns a ``CodexRunner``, but ``health_check()`` reports ``installed=False``
because ``shutil.which("")`` returns ``None``.

Two states are proven:

1. :func:`test_codex_runner_spawned_when_command_set — ``codex_command``
   set to ``sys.executable`` (a binary that exists) → ``CodexRunner``
   is constructable via the factory, ``health_check()`` reports
   ``installed=True``, and the runner satisfies the ``RuntimeAdapter``
   protocol.

2. :func:`test_codex_runner_absent_when_command_blank — ``codex_command``
   blank → the factory still returns a ``CodexRunner`` (the factory never
   refuses), but ``health_check()`` reports ``installed=False``.  Proves
   the Codex adapter is fully optional: no other service is affected by
   its absence.

No Docker required. No real codex binary required — ``sys.executable``
stands in as a discoverable binary for the ``installed`` probe.
"""

from __future__ import annotations

import sys

import pytest
from worker_wrapper.adapters.codex_runner import CodexRunner
from worker_wrapper.adapters.runtime_factory import get_runtime_adapter
from worker_wrapper.app.config import WorkerSettings


def _settings(*, codex_command: str) -> WorkerSettings:
    """Build WorkerSettings with the given ``codex_command`` toggle.

    Only the codex-specific fields are set; all MCP server commands stay at
    defaults (irrelevant for this adapter-level test).
    """
    return WorkerSettings(codex_command=codex_command)


@pytest.mark.separability
@pytest.mark.asyncio
async def test_codex_runner_spawned_when_command_set() -> None:
    """SPAWNED state: codex_command set to a discoverable binary → installed=True.

    Proves:
    - ``get_runtime_adapter(settings, runtime="codex")`` returns a CodexRunner.
    - ``health_check()`` reports ``installed=True`` when the binary exists.
    - The runner's ``runtime_name`` is ``"codex"`` (protocol compliance).
    """
    settings = _settings(codex_command=sys.executable)

    # Factory resolves runtime="codex" → CodexRunner.
    runner = get_runtime_adapter(settings, runtime="codex")
    assert isinstance(runner, CodexRunner), "factory should return CodexRunner for runtime='codex'"
    assert runner.runtime_name == "codex", "runtime_name should be 'codex'"

    # Health check discovers the binary via shutil.which.
    health = await runner.health_check()
    assert health.installed is True, (
        f"sys.executable ({sys.executable!r}) should be discoverable "
        f"via shutil.which → installed=True"
    )
    # Version is best-effort; sys.executable --version succeeds on CPython.
    # We only assert the field is present (may be empty on some builds).
    assert isinstance(health.version, str)


@pytest.mark.separability
@pytest.mark.asyncio
async def test_codex_runner_absent_when_command_blank() -> None:
    """ABSENT state: codex_command blank → installed=False, factory still works.

    Proves the Codex runtime is fully optional (NFR-M10):
    - The factory still constructs a ``CodexRunner`` (no import error).
    - ``health_check()`` reports ``installed=False`` (shutil.which("") → None).
    - No source-code modification to any other service is required.
    """
    settings = _settings(codex_command="")

    # Factory does NOT refuse — it always constructs the adapter.
    runner = get_runtime_adapter(settings, runtime="codex")
    assert isinstance(runner, CodexRunner), (
        "factory should still return CodexRunner even when codex_command is blank"
    )

    # Health check reports NOT installed — the blank command cannot be found.
    health = await runner.health_check()
    assert health.installed is False, "blank codex_command should yield installed=False"
    # Version should be empty when not installed.
    assert health.version == "", (
        f"version should be empty when installed=False; got {health.version!r}"
    )
