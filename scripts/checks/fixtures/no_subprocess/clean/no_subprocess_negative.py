# Fixture: subprocess used WITH a `# noqa: SHELL001 <reason>` suppression — CLEAN.
#
# Mirrors the future Story 5.4 worker-wrapper exemption: legitimate
# subprocess use on the request path that explicitly justifies the SHELL001
# escape via a non-empty reason.
from __future__ import annotations

import subprocess  # noqa: SHELL001 — fixture: legitimate Claude Code CLI supervision per FR3


def run_supervised() -> int:
    """Imitates worker-wrapper's supervised CLI invocation."""
    proc = subprocess.run(["echo", "ok"], check=False)  # noqa: SHELL001 — fixture: see module-level
    return proc.returncode
