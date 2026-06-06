"""Container-spawn assertion test for browser-mcp (Story 22.3 / P4-I3).

Verifies that the Playwright subprocess spawn command satisfies all security
invariants: Docker (not npx), resource limits, no host network, no --no-sandbox,
digest-pinned image, headless + isolated mode.
"""

from __future__ import annotations

import pytest
from browser_mcp.adapters.playwright_subprocess import _build_docker_command

_DIGEST_IMAGE = "mcr.microsoft.com/playwright/mcp@sha256:abc123def456"


class TestContainerSpawnCommand:
    """P4-I3: spawn command security assertions."""

    def _cmd(self, **kwargs) -> list[str]:
        return _build_docker_command(_DIGEST_IMAGE, **kwargs)

    def test_starts_with_docker_run(self) -> None:
        cmd = self._cmd()
        assert cmd[:2] == ["docker", "run"]

    def test_contains_interactive_rm_init(self) -> None:
        cmd = self._cmd()
        assert "-i" in cmd
        assert "--rm" in cmd
        assert "--init" in cmd

    def test_contains_memory_limit(self) -> None:
        cmd = self._cmd(memory_limit="256m")
        assert "--memory=256m" in cmd

    def test_contains_cpu_limit(self) -> None:
        cmd = self._cmd(cpu_limit=0.5)
        assert "--cpus=0.5" in cmd

    def test_no_network_host(self) -> None:
        cmd = self._cmd()
        assert "--network host" not in cmd
        # Also check it's not split across elements.
        combined = " ".join(cmd)
        assert "--network" not in combined

    def test_no_no_sandbox(self) -> None:
        cmd = self._cmd()
        assert "--no-sandbox" not in cmd
        combined = " ".join(cmd)
        assert "--no-sandbox" not in combined

    def test_image_digest_pinned(self) -> None:
        cmd = self._cmd()
        # The image arg must contain @sha256:
        image_arg = cmd[cmd.index(_DIGEST_IMAGE)]
        assert "@sha256:" in image_arg

    def test_contains_headless_isolated(self) -> None:
        cmd = self._cmd()
        assert "--headless" in cmd
        assert "--isolated" in cmd

    def test_no_npx(self) -> None:
        """Spawn uses Docker, never bare-metal npx."""
        cmd = self._cmd()
        combined = " ".join(cmd)
        assert "npx" not in combined

    def test_default_caps_core_config(self) -> None:
        cmd = self._cmd()
        combined = " ".join(cmd)
        assert "--caps=core,config" in combined

    def test_extra_caps_appended(self) -> None:
        cmd = self._cmd(extra_caps=["tabs"])
        combined = " ".join(cmd)
        assert "--caps=core,config,tabs" in combined

    def test_blocklisted_caps_not_in_command(self) -> None:
        """storage and network should never appear in --caps."""
        cmd = self._cmd(extra_caps=["tabs", "vision"])
        combined = " ".join(cmd)
        assert "storage" not in combined
        assert "network" not in combined


class TestBlocklistEnforcement:
    """P4-I3: server refuses to spawn with blocklisted caps."""

    def test_storage_blocked(self) -> None:
        """Server build raises RuntimeError when storage cap is requested."""
        from browser_mcp.server import build_server
        from events.clock import FrozenClock

        with pytest.raises(RuntimeError, match="blocklisted"):
            build_server(
                clock=FrozenClock(),
                actor_kind="operator",
                actor_id="test",
                playwright_image=_DIGEST_IMAGE,
                extra_caps=["storage"],
            )

    def test_network_blocked(self) -> None:
        """Server build raises RuntimeError when network cap is requested."""
        from browser_mcp.server import build_server
        from events.clock import FrozenClock

        with pytest.raises(RuntimeError, match="blocklisted"):
            build_server(
                clock=FrozenClock(),
                actor_kind="operator",
                actor_id="test",
                playwright_image=_DIGEST_IMAGE,
                extra_caps=["network"],
            )
