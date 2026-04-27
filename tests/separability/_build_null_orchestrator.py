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
``tests/fixtures/null_orchestrator/`` itself. The Dockerfile's stage 1
``COPY pyproject.toml uv.lock packages/ services/ mcp-servers/ src/ ...``
references files that live at the repo root. We pass ``-f`` pointing at
the fixture's Dockerfile so the relative ``COPY`` paths resolve.

Source-aware caching (M8 round 2): we hash the fixture's source files
(Dockerfile + Python sources + pyproject.toml) into a SHA-256 prefix and
tag the built image as ``null-orchestrator:sha-<first16hex>``. Subsequent
invocations probe the SHA-tagged image first; if present we re-tag it as
``:latest`` (cheap label-only rewrite) and return. If absent we build with
the SHA tag, then re-tag as ``:latest`` for compose. This means a fixture
source edit causes a true rebuild on the next test run, while a no-op
source state hits the cache.

This helper is invoked from the test fixture before
``docker compose up`` boots the stack; it is also runnable as a CLI
(``python tests/separability/_build_null_orchestrator.py``) for local
manual rebuilds.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

# tests/separability/_build_null_orchestrator.py → repo root is parents[2].
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_DOCKERFILE: Path = _REPO_ROOT / "tests" / "fixtures" / "null_orchestrator" / "Dockerfile"
_LATEST_TAG: str = "null-orchestrator:latest"

# Source files whose contents drive the SHA tag. A change to ANY of these
# yields a different SHA → fresh rebuild.
_SOURCE_FILES: tuple[str, ...] = (
    "tests/fixtures/null_orchestrator/Dockerfile",
    "tests/fixtures/null_orchestrator/null_orchestrator.py",
    "tests/fixtures/null_orchestrator/pyproject.toml",
    "tests/fixtures/null_orchestrator/__init__.py",
    "tests/fixtures/null_orchestrator/__main__.py",
)


def _compute_source_sha() -> str:
    """Compute the first 16 hex chars of SHA-256 over the fixture's source files.

    Files are read in deterministic order; missing files contribute an
    empty byte sequence (so a future deletion still produces a stable
    digest). A NUL byte separator between files prevents collisions where
    moving content between files would otherwise yield identical hashes.
    """
    h = hashlib.sha256()
    for rel in _SOURCE_FILES:
        path = _REPO_ROOT / rel
        h.update(path.read_bytes() if path.is_file() else b"")
        h.update(b"\x00")
    return h.hexdigest()[:16]


def build_if_missing() -> None:
    """Build the SHA-tagged image when missing; always (re)tag as ``:latest``.

    Probes ``docker images -q null-orchestrator:sha-<sha>`` first. If the
    SHA-tagged image already exists the build is skipped — we only re-tag
    it as ``:latest`` so compose's ``ORCHESTRATOR_IMAGE=null-orchestrator:latest``
    resolves. If the SHA-tagged image is missing we build it with that
    tag, then re-tag as ``:latest`` for compose.

    Raises:
        subprocess.CalledProcessError: if either the ``docker images``
            probe, the ``docker build`` invocation, or the ``docker tag``
            invocation fails.
    """
    sha = _compute_source_sha()
    sha_tag = f"null-orchestrator:sha-{sha}"

    # Check the SHA-tagged image — empty stdout means "not present".
    check = subprocess.run(
        ["docker", "images", "-q", sha_tag],
        capture_output=True,
        text=True,
        check=True,
    )
    if check.stdout.strip():
        # Cache hit — re-tag as :latest so compose can resolve it.
        subprocess.run(
            ["docker", "tag", sha_tag, _LATEST_TAG],
            check=True,
        )
        return

    # Cache miss — build with the SHA tag.
    subprocess.run(
        [
            "docker",
            "build",
            "-t",
            sha_tag,
            "-f",
            str(_DOCKERFILE),
            str(_REPO_ROOT),
        ],
        check=True,
    )
    # Tag as :latest for compose's ORCHESTRATOR_IMAGE override.
    subprocess.run(
        ["docker", "tag", sha_tag, _LATEST_TAG],
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
