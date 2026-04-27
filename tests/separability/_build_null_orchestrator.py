"""Idempotent docker-build helper for the ``null-orchestrator:latest`` fixture image (Story 2.15).

The S-3 separability test (``test_s3_orchestrator_swap.py``) needs the
``null-orchestrator:latest`` image present in the local Docker image store
before booting the compose stack with
``ORCHESTRATOR_IMAGE=null-orchestrator:latest``. Rebuilding the image on
every test invocation is wasteful (the workspace ``uv sync`` step alone
takes 60-90s on a cold runner). This helper checks whether the tag is
already present and only invokes ``docker build`` when missing.

Build context: the ``null-orchestrator`` Dockerfile (multi-stage build
with workspace-resolved deps) requires the **repo root** as context, NOT
``tests/fixtures/null-orchestrator/`` itself. The Dockerfile's stage 1
``COPY pyproject.toml uv.lock packages/ services/ mcp-servers/ src/ ...``
references files that live at the repo root. We pass ``-f`` pointing at
the fixture's Dockerfile so the relative ``COPY`` paths resolve.

This helper is invoked from the test fixture before
``docker compose up`` boots the stack; it is also runnable as a CLI
(``python tests/separability/_build_null_orchestrator.py``) for local
manual rebuilds.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tests/separability/_build_null_orchestrator.py → repo root is parents[2].
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_DOCKERFILE: Path = _REPO_ROOT / "tests" / "fixtures" / "null-orchestrator" / "Dockerfile"
_TAG: str = "null-orchestrator:latest"


def build_if_missing() -> None:
    """Build ``null-orchestrator:latest`` only when not already present.

    Uses ``docker images -q <tag>`` to detect presence; an empty stdout
    means no matching image. The build itself is invoked with the repo
    root as context and ``-f`` pointed at the fixture's Dockerfile
    (multi-stage workspace-deps install requires the repo root).

    Raises:
        subprocess.CalledProcessError: if either the ``docker images``
            probe or the ``docker build`` invocation fails.
    """
    check = subprocess.run(
        ["docker", "images", "-q", _TAG],
        capture_output=True,
        text=True,
        check=True,
    )
    if check.stdout.strip():
        # Image already in the local store — skip rebuild for speed.
        return
    subprocess.run(
        [
            "docker",
            "build",
            "-t",
            _TAG,
            "-f",
            str(_DOCKERFILE),
            str(_REPO_ROOT),
        ],
        check=True,
    )


def main() -> int:
    """CLI entry — manual rebuild trigger.

    Returns 0 on success, propagates the docker exit code on failure
    (via ``CalledProcessError`` raised by ``subprocess.run(check=True)``).
    """
    build_if_missing()
    return 0


if __name__ == "__main__":
    sys.exit(main())
