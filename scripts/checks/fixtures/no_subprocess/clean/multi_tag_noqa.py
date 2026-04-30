# Fixture: multi-tag noqa suppression — CLEAN.
#
# Story 3.8 review H9: a single line carrying ``# noqa: PLC0415, SHELL001 — reason``
# must suppress BOTH tags. This fixture exercises the path that Story 5.4 will
# rely on (worker-wrapper subprocess call needing both IMP001 cross-service
# import suppression AND SHELL001 shell-escape suppression on the same line).
from __future__ import annotations

import subprocess  # noqa: PLC0415, SHELL001 — fixture: multi-tag suppression path


def run_supervised() -> int:
    proc = subprocess.run(["echo", "ok"], check=False)  # noqa: PLC0415, SHELL001 — multi-tag fixture
    return proc.returncode
