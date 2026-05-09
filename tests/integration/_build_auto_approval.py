"""Idempotent docker-build helper for the ``auto-approval-stub:latest`` fixture image (Story 5.18).

Follows the same SHA-tag caching pattern as ``_build_scripted_worker.py``.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_DOCKERFILE: Path = _REPO_ROOT / "tests" / "fixtures" / "auto_approval_stub" / "Dockerfile"
_LATEST_TAG: str = "auto-approval-stub:latest"

_SOURCE_FILES: tuple[str, ...] = (
    "tests/fixtures/auto_approval_stub/Dockerfile",
    "tests/fixtures/auto_approval_stub/auto_approval_stub.py",
    "tests/fixtures/auto_approval_stub/pyproject.toml",
    "tests/fixtures/auto_approval_stub/__init__.py",
    "tests/fixtures/auto_approval_stub/__main__.py",
    "uv.lock",
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
    always rebuilt.
    """
    sha = _compute_source_sha()
    sha_tag = f"auto-approval-stub:sha-{sha}"

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
            f"error: docker operation failed for auto-approval-stub (rc={exc.returncode}). "
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
