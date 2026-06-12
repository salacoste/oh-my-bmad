"""Idempotent docker-build helper for the ``scripted-worker-stub:latest`` fixture image (Story 5.16).

Follows the same pattern as ``_build_null_orchestrator.py``: probes the
SHA-tagged image, re-tags as ``:latest`` on cache hit, or builds from
repo root context if missing.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_DOCKERFILE: Path = _REPO_ROOT / "tests" / "fixtures" / "scripted_worker_stub" / "Dockerfile"
_LATEST_TAG: str = "scripted-worker-stub:latest"

_SOURCE_FILES: tuple[str, ...] = (
    "tests/fixtures/scripted_worker_stub/Dockerfile",
    "tests/fixtures/scripted_worker_stub/scripted_worker_stub.py",
    "tests/fixtures/scripted_worker_stub/pyproject.toml",
    "tests/fixtures/scripted_worker_stub/__init__.py",
    "tests/fixtures/scripted_worker_stub/__main__.py",
    "uv.lock",
    # The image vendors workspace wheels; changes to the embedded clawhip
    # MCP server must invalidate the fixture image even when uv.lock is
    # unchanged.
    "mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py",
    "mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/__main__.py",
)


def _compute_source_sha() -> str:
    h = hashlib.sha256()
    for rel in _SOURCE_FILES:
        path = _REPO_ROOT / rel
        h.update(path.read_bytes() if path.is_file() else b"")
        h.update(b"\x00")
    return h.hexdigest()[:16]


def build_if_missing(*, force: bool = False) -> None:
    """Build the SHA-tagged image when missing; always (re)tag as ``:latest``.

    When *force* is ``True``, the SHA cache is bypassed so the image is
    always rebuilt (picks up source changes even when the SHA is unchanged).
    """
    sha = _compute_source_sha()
    sha_tag = f"scripted-worker-stub:sha-{sha}"

    try:
        if not force:
            check = subprocess.run(
                ["docker", "images", "-q", sha_tag],
                capture_output=True,
                text=True,
                check=True,
            )
            if check.stdout.strip():
                subprocess.run(["docker", "tag", sha_tag, _LATEST_TAG], check=True)
                return

        subprocess.run(
            ["docker", "build", "-t", sha_tag, "-f", str(_DOCKERFILE), str(_REPO_ROOT)],
            check=True,
        )
        subprocess.run(["docker", "tag", sha_tag, _LATEST_TAG], check=True)
    except subprocess.CalledProcessError as exc:
        print(
            f"error: docker operation failed for scripted-worker-stub (rc={exc.returncode}). "
            f"Is Docker running?\n  cmd: {exc.cmd}",
            file=sys.stderr,
        )
        raise


def main() -> int:
    force = "--force" in sys.argv
    build_if_missing(force=force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
