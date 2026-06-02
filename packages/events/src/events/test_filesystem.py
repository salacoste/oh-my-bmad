"""Unit tests for :func:`events.ensure_shared_dir` (Story 11.3.8 / AC4).

Three tests covering the three documented invariants:

1. **happy path** — fresh dir gets the canonical 0o2775 mode + setgid bit
   regardless of process umask.
2. **idempotency** — repeat invocation against the same dir is a no-op
   (no exception; mode preserved).
3. **best-effort chmod** — when ``chmod`` raises ``OSError`` (e.g., dir
   exists but we don't own it), the mkdir still succeeded and the OSError
   is suppressed.

POSIX-only mode assertions: skipped on Windows where ``Path.chmod`` ignores
POSIX bits (the affected services are Linux-container only, so this is
not a deployment gap).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from events import ensure_shared_dir

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits ignored on Windows — Path.chmod is a no-op there",
)


@_POSIX_ONLY
def test_ensure_shared_dir_creates_with_2775_mode(tmp_path: Path) -> None:
    """Fresh dir gets mode 0o2775 (setgid + rwxrwxr-x) regardless of umask.

    Pins umask to 022 (the conventional Linux/macOS default and the value
    Story 11.3.8 documented as the regression trigger) so the test is
    deterministic on CI workers with non-standard umask configuration.
    """
    target = tmp_path / "registry" / "events"
    prev_umask = os.umask(0o022)
    try:
        ensure_shared_dir(target)
    finally:
        os.umask(prev_umask)

    assert target.is_dir(), f"dir should exist after ensure_shared_dir; got {target!r}"
    # Mask to the 12 POSIX permission bits (setuid + setgid + sticky + rwx*3).
    mode_bits = target.stat().st_mode & 0o7777
    assert mode_bits == 0o2775, f"expected mode 0o2775 (setgid + rwxrwxr-x); got {oct(mode_bits)}"
    # Double-check the setgid bit is set (Python's stat.S_ISGID == 0o2000).
    assert target.stat().st_mode & stat.S_ISGID, (
        "setgid bit must be set so group ownership propagates to nested files"
    )


@_POSIX_ONLY
def test_ensure_shared_dir_is_idempotent(tmp_path: Path) -> None:
    """Second call against the same dir is a no-op — mode preserved, no raise."""
    target = tmp_path / "registry" / "events"
    prev_umask = os.umask(0o022)
    try:
        ensure_shared_dir(target)
        first_mode = target.stat().st_mode & 0o7777
        # Second invocation must not raise + must preserve mode.
        ensure_shared_dir(target)
        second_mode = target.stat().st_mode & 0o7777
    finally:
        os.umask(prev_umask)

    assert first_mode == 0o2775
    assert second_mode == 0o2775, f"mode drifted on 2nd call; got {oct(second_mode)}"


def test_ensure_shared_dir_chmod_failure_is_best_effort(tmp_path: Path) -> None:
    """``chmod`` OSError is suppressed — mkdir already succeeded.

    Mirrors the Story 11.3.5 ``_ensure_db_parent_dir`` precedent: a dir we
    don't own (or other chmod failure mode) should not crash the caller —
    the mkdir already gave the caller a usable path. Best-effort discipline.

    Runs on all platforms (the suppress contract is platform-independent;
    the assertion checks behavior, not mode bits).
    """
    target = tmp_path / "events"

    # Mock Path.chmod to raise OSError. The mkdir should still succeed +
    # the OSError must NOT propagate to the caller.
    with patch.object(Path, "chmod", side_effect=OSError("permission denied")):
        # MUST NOT raise.
        ensure_shared_dir(target)

    # Dir was created despite the chmod failure (mkdir ran first).
    assert target.is_dir(), (
        "ensure_shared_dir should create the dir even when chmod fails — "
        "best-effort contract violated"
    )


@_POSIX_ONLY
def test_ensure_shared_dir_intermediates_also_chmodded(tmp_path: Path) -> None:
    """``parents=True`` intermediates get the explicit mode, not just the leaf.

    Reviewer L2 (Story 11.3.8 code-review pass): ``Path.mkdir(mode=...,
    parents=True)`` applies ``mode`` only to the leaf — intermediates created
    by ``parents=True`` use ``0o777 & ~umask`` (typically ``0o755``). If we
    only chmod'd the leaf, the original Story 11.3.7-Task-7 regression would
    recur one level up for nested shared paths (e.g. a future
    ``/var/lib/oh-my-bmad/new-ns/events/`` whose ``new-ns/`` parent didn't
    pre-exist).

    Builds a 3-deep fresh path under ``tmp_path`` (none of the 3 dirs exist
    before the call), then asserts every newly-created dir got the explicit
    mode. ``tmp_path`` itself (pre-existing) MUST be unchanged.
    """
    pre_existing = tmp_path  # pytest creates this; we must not mutate it
    pre_existing_mode = pre_existing.stat().st_mode & 0o7777
    new_a = pre_existing / "a"
    new_b = new_a / "b"
    new_leaf = new_b / "c"

    prev_umask = os.umask(0o022)
    try:
        ensure_shared_dir(new_leaf)
    finally:
        os.umask(prev_umask)

    for created in (new_a, new_b, new_leaf):
        assert created.is_dir(), f"intermediate {created!r} not created"
        got = created.stat().st_mode & 0o7777
        assert got == 0o2775, (
            f"intermediate {created.name!r} kept umask-default mode "
            f"{oct(got)}; expected 0o2775 (L2-fix regression)"
        )

    # Pre-existing dir must not be touched (out-of-scope per helper docstring).
    assert pre_existing.stat().st_mode & 0o7777 == pre_existing_mode, (
        "ensure_shared_dir mutated a pre-existing ancestor it was not asked "
        "to ensure — scope-creep regression"
    )


@_POSIX_ONLY
def test_ensure_shared_dir_self_heals_pre_existing_leaf_with_stale_mode(
    tmp_path: Path,
) -> None:
    """Pre-existing leaf with wrong mode → chmod'd to ``mode`` (self-heal).

    Reviewer M1 (Story 11.3.8 code-review pass): if a sibling service in the
    ``omb`` group created the leaf earlier under umask 022 (mode 0o755),
    calling ``ensure_shared_dir`` MUST chmod it back to 0o2775. Without this,
    the helper's idempotency contract degrades to "creates dirs correctly"
    but loses "fixes pre-existing wrong-mode dirs" — which is the actual
    failure shape that Story 11.3.7's Task-7 produced (the dir already
    existed when registry-api tried to write).
    """
    target = tmp_path / "registry"
    # Simulate the sibling service: create with the BUG mode (0o755 = no
    # group-write, no setgid). This is exactly what bare ``mkdir`` under
    # umask 022 produces.
    target.mkdir(mode=0o755)
    target.chmod(0o755)  # belt-and-suspenders — some platforms honor umask
    assert target.stat().st_mode & 0o7777 == 0o755, (
        f"test setup: expected stale mode 0o755, got {oct(target.stat().st_mode & 0o7777)}"
    )

    ensure_shared_dir(target)

    healed = target.stat().st_mode & 0o7777
    assert healed == 0o2775, (
        f"pre-existing leaf mode not self-healed; got {oct(healed)} "
        f"(M1-fix regression — _ensure_db_parent_dir short-circuit logic)"
    )
