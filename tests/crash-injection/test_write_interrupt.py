"""Write-interrupt harness — Story 2.12 / FR30 / NFR-R2.

Spawns ``_atomic_edit_runner.py`` as a subprocess with controlled
``--kill-after-bytes`` values and asserts post-mortem that ``target`` is
either byte-identical to the pre-edit content or to the post-edit
content — never a partial mix.

The 100-iteration randomized test is the AC-headline check: with
``random.Random(seed=21242)`` for reproducibility, runs 100 trials at
random byte offsets in ``[0, len(data))`` and asserts the invariant
holds for every trial.

Subprocess strategy:

* The driver imports ``worker_wrapper.domain.atomic_edit`` from
  ``services/worker-wrapper/src`` — added to the child's ``PYTHONPATH``
  via the spawned ``env`` dict.
* Driver exits 137 (POSIX convention for SIGKILL) when interrupted, 0
  on the no-interrupt control path.
* No Docker required — pure subprocess + filesystem. The ``crash`` mark
  groups these with Story 2.11's tests; the ``slow`` mark keeps them out
  of ``just test`` by default.
"""

from __future__ import annotations

import hashlib
import random
import subprocess
import sys
from pathlib import Path

import pytest

# ``tests/crash-injection`` is on ``sys.path`` via the conftest sys.path
# insertion (Story 2.11 pattern). The runner script lives next to this
# file; we resolve its absolute path at import time.
_RUNNER_PATH: Path = Path(__file__).parent / "_atomic_edit_runner.py"
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_WORKER_WRAPPER_SRC: Path = _REPO_ROOT / "services" / "worker-wrapper" / "src"

# Pre/post-edit fixtures. ``post_edit_content`` is large enough that
# random byte offsets in [0, len(data)) span a meaningful range.
_PRE_EDIT_CONTENT: bytes = b"original\n"
_POST_EDIT_CONTENT: bytes = b"the new contents go here\n" * 50  # 1250 bytes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spawn_runner(
    target: Path,
    final_content_path: Path,
    kill_after_bytes: int,
    *,
    timeout_s: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    """Spawn ``_atomic_edit_runner.py`` with controlled args + PYTHONPATH."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(_WORKER_WRAPPER_SRC),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [
            sys.executable,
            str(_RUNNER_PATH),
            "--target",
            str(target),
            "--final-content",
            str(final_content_path),
            "--kill-after-bytes",
            str(kill_after_bytes),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=env,
    )


def _setup_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    """Seed the target with pre-edit content; write post-edit content separately."""
    target = tmp_path / "atomic-edit-target.txt"
    final_content = tmp_path / "post-edit-content.bin"
    target.write_bytes(_PRE_EDIT_CONTENT)
    final_content.write_bytes(_POST_EDIT_CONTENT)
    return target, final_content


@pytest.mark.crash
@pytest.mark.slow
def test_atomic_edit_unmolested_completes_normally(tmp_path: Path) -> None:
    """No interrupt — target must contain full post-edit content."""
    target, final_content = _setup_fixture_files(tmp_path)
    proc = _spawn_runner(
        target,
        final_content,
        kill_after_bytes=len(_POST_EDIT_CONTENT) + 100,  # > len: no kill
    )
    assert proc.returncode == 0, (
        f"unmolested run unexpectedly failed: rc={proc.returncode}, stderr={proc.stderr!r}"
    )
    assert target.read_bytes() == _POST_EDIT_CONTENT


@pytest.mark.crash
@pytest.mark.slow
def test_atomic_edit_interrupted_at_zero_bytes_preserves_original(
    tmp_path: Path,
) -> None:
    """Kill before any byte is written — target must be unchanged."""
    target, final_content = _setup_fixture_files(tmp_path)
    proc = _spawn_runner(target, final_content, kill_after_bytes=0)
    assert proc.returncode == 137, (
        f"expected SIGKILL-equivalent rc=137, got rc={proc.returncode}, stderr={proc.stderr!r}"
    )
    assert target.read_bytes() == _PRE_EDIT_CONTENT


@pytest.mark.crash
@pytest.mark.slow
def test_atomic_edit_interrupted_mid_write_target_unchanged(tmp_path: Path) -> None:
    """Kill after writing N bytes (0 < N < len) — target must be pre-edit content."""
    target, final_content = _setup_fixture_files(tmp_path)
    mid = len(_POST_EDIT_CONTENT) // 2
    proc = _spawn_runner(target, final_content, kill_after_bytes=mid)
    assert proc.returncode == 137
    # The os.replace call never ran, so target is still the pre-edit bytes.
    assert target.read_bytes() == _PRE_EDIT_CONTENT


@pytest.mark.crash
@pytest.mark.slow
def test_atomic_edit_100_randomized_interruption_points(tmp_path: Path) -> None:
    """AC-headline test — 100 random kill points; invariant must hold for ALL.

    For each trial: spawn the driver with a random ``kill_after_bytes`` in
    ``[0, len(data))``; assert the post-mortem ``target`` hash matches
    EITHER the pre-edit hash OR the post-edit hash; NEVER any other
    value. Track per-outcome counts for diagnostics.
    """
    rng = random.Random(21242)
    pre_hash = _sha256(_PRE_EDIT_CONTENT)
    post_hash = _sha256(_POST_EDIT_CONTENT)
    n_total = len(_POST_EDIT_CONTENT)

    pre_count = 0
    post_count = 0
    other_outcomes: list[tuple[int, str]] = []  # (kill_after_bytes, hash)

    for trial in range(100):
        # Fresh target file per trial — each iteration starts from
        # pre-edit baseline. Use unique paths to avoid cross-trial bleed.
        target = tmp_path / f"target-{trial}.txt"
        final_content = tmp_path / f"final-{trial}.bin"
        target.write_bytes(_PRE_EDIT_CONTENT)
        final_content.write_bytes(_POST_EDIT_CONTENT)

        kill_after = rng.randrange(0, n_total)
        proc = _spawn_runner(target, final_content, kill_after_bytes=kill_after)

        # Driver should exit 137 (interrupted) since kill_after < n_total.
        assert proc.returncode == 137, (
            f"trial={trial} kill_after={kill_after}: expected rc=137, "
            f"got rc={proc.returncode}, stderr={proc.stderr!r}"
        )

        actual = target.read_bytes()
        actual_hash = _sha256(actual)
        if actual_hash == pre_hash:
            pre_count += 1
        elif actual_hash == post_hash:
            post_count += 1
        else:
            other_outcomes.append((kill_after, actual_hash))

    # Print per-outcome breakdown for visibility (pytest -s shows it).
    print(
        f"\n[write-interrupt] 100 trials: "
        f"{pre_count} pre-edit, {post_count} post-edit, "
        f"{len(other_outcomes)} partial"
    )

    assert other_outcomes == [], (
        f"FR30 violation: {len(other_outcomes)} trials produced partial content. "
        f"First 5: {other_outcomes[:5]}"
    )
    assert pre_count + post_count == 100
