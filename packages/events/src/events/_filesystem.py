"""Shared-volume filesystem helpers (Story 11.3.8 / FR62a).

Mounted under ``packages/events/`` because the ``EventLogWriter`` it serves
already lives in ``registry-state/adapters/event_log.py`` and the
``packages/events/`` namespace is the cross-service home for read/write
helpers around the JSONL event log.

The Story 11.3.8 motivation
---------------------------

Multiple services in the ``omb`` group write to a shared event-log
directory (e.g. ``/var/lib/oh-my-bmad/registry/events/``). The first
service to instantiate :class:`EventLogWriter` (or otherwise ``mkdir``
the path) wins the race and locks in the directory's mode according to
the process umask — typically ``022`` → ``0o755``. Subsequent services
in the ``omb`` group (different uid) then can't write into the
directory, even though setgid'd group ownership propagated correctly
from the parent.

POSIX setgid semantics (``man 2 mkdir`` + ``man 1 chmod``):
  * Setgid bit (the leading ``2`` in ``0o2775``) propagates GROUP
    OWNERSHIP from the directory to files/subdirs created inside.
  * Setgid bit does NOT propagate WRITE MODE — that's controlled by the
    creating process's umask (Python's :func:`Path.mkdir` doesn't override
    umask). Group-write must be set explicitly via ``chmod 0o2775``.

This is why Story 11.3.5's H6a base-image fix (``Dockerfile.base:71-73``
sets ``/var/lib/oh-my-bmad`` to ``2775``) was necessary-but-insufficient:
the BASE path's group-write propagates ownership to subdirs at creation
time, but every NEW subdir created by Python at runtime needs the
explicit ``chmod``.

This helper formalises the pattern that already existed inline at
``services/registry-state/.../app/main.py:_ensure_db_parent_dir`` —
extracting it to a shared location so every event-log mkdir site benefits
consistently (Epic 11 retro L9 mirror-identity canon — filesystem ops are
the 3rd known surface after MCP-env allowlists + HMAC-signing canonical
string).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

# 0o2775 = setgid + rwxrwxr-x (owner+group rwx, others rx).
# * Setgid (leading 2) propagates group ownership to new files/dirs.
# * Group-write (the second 7) lets cross-uid services in the same group
#   write into the directory.
# * Others-rx (the trailing 5) keeps standard read+exec for non-group
#   processes (e.g. operator scrubbing logs without sudo).
_DEFAULT_SHARED_MODE: int = 0o2775


def ensure_shared_dir(path: Path, mode: int = _DEFAULT_SHARED_MODE) -> None:
    """Create ``path`` (parents included) and chmod to ``mode``.

    Idempotent and best-effort:

    * ``mkdir(parents=True, exist_ok=True)`` is idempotent by construction;
      ``exist_ok=True`` means a pre-existing dir is not an error.
    * ``chmod`` is wrapped in :func:`contextlib.suppress` so a dir we don't
      own (e.g. created by another container with a stricter mode) doesn't
      fail-loud — the mkdir already succeeded, so the writer can still
      operate; the chmod failure is the operator's signal to investigate
      volume-permission setup. This best-effort discipline carries forward
      the Story 11.3.5 ``_ensure_db_parent_dir`` pattern (which this helper
      extracts; post-Story-11.3.8 that call site now delegates here).

    ``parents=True`` semantics — intermediate dirs:
        :meth:`Path.mkdir` applies its ``mode`` argument ONLY to the leaf
        directory; intermediates created via ``parents=True`` use the
        default ``0o777 & ~umask`` (typically ``0o755``) and lose
        group-write. To prevent the original Story 11.3.7-Task-7 regression
        recurring one level up for nested shared paths, this helper
        snapshots which ancestors do NOT yet exist, lets ``mkdir`` create
        the full chain, then ``chmod``s every dir it created (intermediates
        + leaf). Pre-existing ancestors above the leaf are LEFT ALONE
        (out-of-scope — the caller asked us to ensure ``path``, not its
        parents' modes). The leaf itself is always chmod'd — if it pre-
        existed with a stale mode, the call self-heals it (idempotent;
        the ``contextlib.suppress`` handles the "we don't own it" case).

    Args:
        path: Directory to create + chmod. Path is resolved as-is (the caller
              is responsible for any tilde-expansion / absolute-path normalization).
        mode: POSIX mode bits to apply. Default ``0o2775`` (setgid +
              rwxrwxr-x) is the canonical shared-volume mode for the
              ``omb`` group on the oh-my-bmad data root. Tests can override
              for non-shared paths.

    Cross-platform:
        On Windows, :meth:`Path.chmod` ignores the POSIX mode bits — the
        chmod call is effectively a no-op. The affected services (registry-*,
        worker-wrapper, clawhip-daemon, metrics-subscriber) are Linux-container
        only, so this is not a deployment concern; co-located unit tests
        should not assert mode on Windows hosts.
    """
    # Snapshot which ancestors do not yet exist — `parents=True` will
    # create them with `umask & 0o777` (typically 0o755), losing group-
    # write. We chmod each one to `mode` after mkdir. The walk stops at
    # the first existing ancestor (pre-existing parents are out of scope;
    # the caller didn't ask us to mutate them).
    new_ancestors: list[Path] = []
    cur = path
    while not cur.exists():
        new_ancestors.append(cur)
        if cur.parent == cur:  # filesystem root — defensive stop, shouldn't happen
            break
        cur = cur.parent

    path.mkdir(parents=True, exist_ok=True)

    # Chmod every dir we just created. Also chmod the leaf if it pre-existed
    # (idempotent self-heal — a sibling service that created the leaf with
    # the wrong mode gets corrected; OSError suppress handles "we don't own
    # it" gracefully). Pre-existing INTERMEDIATES are intentionally skipped.
    targets: list[Path] = new_ancestors if path in new_ancestors else [*new_ancestors, path]
    for p in targets:
        with contextlib.suppress(OSError):
            p.chmod(mode)
