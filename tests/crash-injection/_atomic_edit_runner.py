"""Standalone subprocess driver for the write-interrupt harness (Story 2.12).

Spawned by ``test_write_interrupt.py`` via :mod:`subprocess`. Reads CLI
args, monkey-patches ``worker_wrapper.domain.atomic_edit._chunked_write``
to call ``os._exit(137)`` after writing exactly ``--kill-after-bytes``
bytes to the tmpfile, then invokes ``atomic_write_bytes(target, data)``.

When ``--kill-after-bytes`` is greater than or equal to the data length,
the patched helper writes everything normally and the script exits 0
(the no-interrupt control path).

Why ``os._exit(137)`` rather than ``sys.exit`` or signal-based kill:

* ``sys.exit`` raises ``SystemExit`` which can be caught + buffer-flushed,
  defeating the simulation.
* ``os.kill(pid, SIGKILL)`` adds racy delivery timing — the next
  instruction may or may not execute first.
* ``os._exit(N)`` is synchronous + bypasses atexit handlers AND C-stdlib
  buffer flushes — the closest in-process simulation of an external
  SIGKILL. The tmpfile fd is closed by the kernel as part of process
  teardown but ``os.fsync`` and ``os.replace`` never run, leaving the
  target file in its pre-edit state (the atomic-edit invariant).

  (Note: this is NOT a real SIGKILL — ``os._exit`` runs in-process and
  the kernel still tears down the tmpfile fd cleanly.  The Mach-style
  "process never gets a chance to clean up" guarantee from a real
  SIGKILL would be stronger but isn't deterministic enough for a
  100-iteration test.  ``os._exit(137)`` is the closest deterministic
  approximation.)

Exit code 137 is the conventional "killed by SIGKILL" code on POSIX
(128 + 9). Tests assert ``returncode == 137`` for the interrupted path.

This script is invoked with ``python _atomic_edit_runner.py ...`` from
the harness; the harness adds ``services/worker-wrapper/src`` to
``sys.path`` via ``PYTHONPATH``.  As a convenience for manual debugging
(``python tests/crash-injection/_atomic_edit_runner.py ...``) the script
also self-augments ``sys.path`` so the import below works without env
wrangling — see Story 2.12 M8.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

# Story 2.12 M8: self-augment sys.path so manual `python ...` invocation
# works without setting PYTHONPATH.  When invoked by the harness this
# is a no-op (PYTHONPATH is already set, so the path is already present
# and the redundant insert is harmless).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WW_SRC = _REPO_ROOT / "services" / "worker-wrapper" / "src"
if str(_WW_SRC) not in sys.path:
    sys.path.insert(0, str(_WW_SRC))

from worker_wrapper.domain import atomic_edit  # noqa: E402 — sys.path setup precedes import


def _build_kill_after_chunked_write(
    kill_after_bytes: int,
) -> Callable[[int, bytes], None]:
    """Return a patched ``_chunked_write`` that os._exit's after N bytes.

    Mirrors the production loop in :func:`atomic_edit._chunked_write`
    (chunked write, advance ``pos`` by the number actually written) but
    with a per-iteration check that calls ``os._exit(137)`` once the
    cumulative byte count crosses ``kill_after_bytes``. The chunk size
    is reduced from the production default to 1 byte so the kill point
    is exactly at ``kill_after_bytes`` rather than at the next
    chunk boundary.
    """

    def patched(fd: int, data: bytes) -> None:
        n = len(data)
        if kill_after_bytes >= n:
            # No interrupt — write everything normally.
            pos = 0
            while pos < n:
                written = os.write(fd, data[pos:])
                if written <= 0:
                    raise OSError(f"os.write returned {written}")
                pos += written
            return
        # Interrupted path — write exactly kill_after_bytes then os._exit.
        pos = 0
        while pos < kill_after_bytes:
            chunk = data[pos : pos + 1]
            written = os.write(fd, chunk)
            if written <= 0:
                raise OSError(f"os.write returned {written}")
            pos += written
        # Flush any buffered output channel before the abrupt exit so
        # diagnostic prints are not lost; tmpfile fd is closed by the
        # kernel as part of process teardown.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(137)

    return patched


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write-interrupt harness driver — interrupts atomic_write_bytes "
            "after N bytes via os._exit(137)."
        ),
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Path to the file to edit atomically.",
    )
    parser.add_argument(
        "--final-content",
        type=Path,
        required=True,
        help="Path to a file whose bytes should replace --target's contents.",
    )
    parser.add_argument(
        "--kill-after-bytes",
        type=int,
        required=True,
        help=(
            "Call os._exit(137) after writing exactly N bytes to the "
            "tmpfile. N >= len(data) writes everything normally."
        ),
    )
    args = parser.parse_args()

    if args.kill_after_bytes < 0:
        parser.error("--kill-after-bytes must be >= 0")

    data = args.final_content.read_bytes()

    # Surgical monkey-patch: only atomic_edit's writes are interrupted.
    # setattr bypasses mypy's def-vs-Callable assignment check (the original
    # function symbol is typed as a function definition, not a variable
    # holding a Callable — both forms are runtime-equivalent).
    setattr(  # noqa: B010 — direct attribute assignment fails mypy strict
        atomic_edit,
        "_chunked_write",
        _build_kill_after_chunked_write(args.kill_after_bytes),
    )

    atomic_edit.atomic_write_bytes(args.target, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
