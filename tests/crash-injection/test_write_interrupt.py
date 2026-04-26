"""Write-interrupt harness — Story 2.12 / FR30 / NFR-R2.

Spawns ``_atomic_edit_runner.py`` as a subprocess with controlled
``--kill-after-bytes`` values and asserts post-mortem that ``target`` is
either byte-identical to the pre-edit content or to the post-edit
content — never a partial mix.

The 100-iteration randomized test is the AC-headline check: with
``random.Random(seed=21242)`` for reproducibility, runs 100 trials at
random byte offsets and asserts the invariant holds for every trial.
The randrange is deliberately widened past ``len(data)`` so ~7% of
trials land in the no-interrupt regime, exercising the post-edit
reconstruction path in addition to the pre-edit one (Story 2.12 M3).

Subprocess strategy:

* The driver imports ``worker_wrapper.domain.atomic_edit`` from
  ``services/worker-wrapper/src`` — added to the child's ``PYTHONPATH``
  via the spawned ``env`` dict.
* Driver exits 137 (POSIX convention for SIGKILL) when interrupted, 0
  on the no-interrupt control path.
* No Docker required — pure subprocess + filesystem. The ``crash`` mark
  groups these with Story 2.11's tests; the ``slow`` mark keeps them out
  of ``just test`` by default.

Subprocess env strategy (Story 2.12 M7):
We INHERIT the parent's ``os.environ`` and overlay only the entries the
runner needs (``PYTHONPATH`` PREPEND so it composes with any inherited
value; ``PYTHONDONTWRITEBYTECODE`` for hermeticity).  Inheriting
``HOME`` / ``TMPDIR`` / ``LANG`` / ``LC_ALL`` is necessary for CI
portability — a fully minimal env breaks subprocess startup on macOS
runners and on Linux containers using non-default locales.
"""

from __future__ import annotations

import hashlib
import os
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

# Story 2.12 Mn3: assert runner script presence at import time so a
# missing/renamed script fails fast with a clear message rather than a
# subprocess "no such file" 50ms later.
assert _RUNNER_PATH.exists(), f"runner script missing: {_RUNNER_PATH}"

# Pre/post-edit fixtures.  Story 2.12 M2: post-edit content widened from
# 1250 bytes (single 64KB production chunk) to 100,000 bytes (~1.5
# production chunks) so randomized kill points actually exercise
# multi-chunk boundaries inside ``_chunked_write``.  The harness driver
# uses 1-byte chunks so it can interrupt at exact byte offsets, but the
# RANGE of offsets the production chunked-write loop must handle widens
# to span at least one chunk boundary.
_PRE_EDIT_CONTENT: bytes = b"original\n"
_POST_EDIT_CONTENT: bytes = b"the new contents go here\n" * (50 * 80)  # ~100,000 bytes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spawn_runner(
    target: Path,
    final_content_path: Path,
    kill_after_bytes: int,
    *,
    timeout_s: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    """Spawn ``_atomic_edit_runner.py`` with controlled args + PYTHONPATH.

    Inherits ``os.environ`` so HOME / TMPDIR / LANG / LC_ALL flow through
    to the child (CI portability).  Overlays:

    * ``PYTHONPATH`` — prepended (not overridden) so the runner imports
      worker_wrapper from the local checkout while preserving any
      already-set PYTHONPATH from the test environment.
    * ``PYTHONDONTWRITEBYTECODE`` — keeps tmp_path tidy (no __pycache__).
    """
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_WORKER_WRAPPER_SRC}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(_WORKER_WRAPPER_SRC)
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"

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
        encoding="utf-8",
        errors="replace",
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

    Story 2.12 M3: ``randrange`` widened to ``[0, n_total + 100)`` so a
    fraction of trials (~``100 / (n_total + 100)``, but for small n that
    becomes meaningful — here 100/100100 → not enough, so we use a
    larger overshoot) land in the no-interrupt regime where the driver
    exits 0 and the post-edit content lands.  This way both pre-edit
    AND post-edit reconstruction outcomes are exercised in every CI run.

    Implementation: split the 100 trials evenly between
    "kill-during-write" (``randrange(0, n_total)``) and
    "kill-after-or-not" (``randrange(0, n_total * 2)`` — half land
    past n_total).  Both branches assert the FR30 invariant
    (target is EITHER pre-edit OR post-edit, never partial).
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

        # Widened range — values in [0, n_total) interrupt mid-write
        # (rc=137 → pre-edit); values in [n_total, 2*n_total) write
        # everything (rc=0 → post-edit).  ~50/50 split exercises both.
        kill_after = rng.randrange(0, n_total * 2)
        proc = _spawn_runner(target, final_content, kill_after_bytes=kill_after)

        # Two valid exit codes: 137 (interrupted) or 0 (unmolested).
        assert proc.returncode in (0, 137), (
            f"trial={trial} kill_after={kill_after}: unexpected rc={proc.returncode}, "
            f"stderr={proc.stderr!r}"
        )

        actual = target.read_bytes()
        actual_hash = _sha256(actual)
        if actual_hash == pre_hash:
            pre_count += 1
            # Sanity: kill_after < n_total → must have been interrupted.
            assert proc.returncode == 137 or kill_after >= n_total, (
                f"trial={trial} pre-edit content but kill_after={kill_after} "
                f"and rc={proc.returncode} — inconsistent"
            )
        elif actual_hash == post_hash:
            post_count += 1
            # Sanity: only valid when the runner ran to completion.
            assert proc.returncode == 0, (
                f"trial={trial} post-edit content but rc={proc.returncode} "
                f"(kill_after={kill_after}, n_total={n_total}) — inconsistent"
            )
        else:
            other_outcomes.append((kill_after, actual_hash))

    # Print per-outcome breakdown for visibility (pytest -s shows it).
    print(
        f"\n[write-interrupt] 100 trials (M3 widened, n_total={n_total}): "
        f"{pre_count} pre-edit, {post_count} post-edit, "
        f"{len(other_outcomes)} partial"
    )

    assert other_outcomes == [], (
        f"FR30 violation: {len(other_outcomes)} trials produced partial content. "
        f"First 5: {other_outcomes[:5]}"
    )
    assert pre_count + post_count == 100
    # Story 2.12 M3: BOTH branches must be exercised.  With seeded RNG +
    # 50/50 range split this is deterministic; if either is 0, randomness
    # got pathologically unlucky and we want to know.
    assert pre_count > 0 and post_count > 0, (
        f"M3 widening regression: pre_count={pre_count} post_count={post_count} — "
        "both should be non-zero given the deterministic seed and range split"
    )
