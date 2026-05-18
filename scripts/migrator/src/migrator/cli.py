"""migrator CLI implementation.

Reads the JSONL event log at ``$EVENT_LOG_PATH`` (default
``/var/lib/oh-my-bmad/registry/events/current.jsonl``), applies the named
migration, writes the migrated output atomically, and archives the original.

Story 1.3 ships the trivial ``v1.0.0-to-v1.0.1`` additive upgrade. Post-review
fixes (commit ``c5...``) added atomic write-rename semantics and input
validation hardening. Story 2.14 lifted this implementation out of
``__main__.py`` so :func:`main` is importable from regular Python (mypy
refuses to resolve ``migrator.__main__`` even via ``mypy_path``).
``__main__.py`` is now a one-line wrapper that delegates to :func:`main`.

Story 2.14 code-review fix C3: :func:`main` returns its exit code rather than
raising :class:`SystemExit` via a ``die()`` helper. Argument-validation
errors return non-zero; the ``__main__`` shim is responsible for
``sys.exit()``. Tests can now assert ``rc == 1`` instead of catching
:class:`SystemExit`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


class MigratorError(ValueError):
    """Migrator-specific validation failure (per-record).

    Story 9.7 pass-1 PH-B2/E2: distinguishes "input data is invalid" from
    other ValueErrors raised by the migrator (e.g., wrong source version
    in :func:`migrate_v1_0_0_to_v1_0_1`). Both are still ValueErrors so
    existing catchers continue to work; the dedicated subclass lets the
    test suite assert the exact failure mode for back-fill mistakes.
    """


# Story 9.1 trace_id shape contract — mirrored locally so the migrator
# does not have to depend on the events package. Two valid shapes:
#   * bare UUIDv7 (lowercase canonical 36-char string with version=7)
#   * "tg:<digits>" where digits ∈ [1, 2^63 - 1]
# Mirror keeps the validator deterministic + dependency-free in the
# minimal migrator image. The authoritative validator lives in
# packages/events/src/events/envelope.py::is_valid_trace_id.
#
# Story 9.7 pass-2 TM-E5: use \A/\Z (Story 9.1 F1 anti-lesson — ^/$ matches
# end-of-line under MULTILINE). _TG_RE rejects ``tg:0`` and leading-zero
# forms (``tg:007``) — keep in sync with packages/events/src/events/envelope.py
# canonical patterns (``_TRACE_ID_TELEGRAM_RE``).
#
# Story 9.7 pass-2 TH-B2 (Q6): the back-fill path additionally accepts
# ``e-<uuidv7>`` (pre-9.1 request_id shape) and strips the ``e-`` prefix
# before storing as ``trace_id``. The Story-9.1 validator only accepts
# bare UUIDv7 / tg:<digits>; the back-fill helper bridges the gap.
_UUIDV7_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_TG_RE = re.compile(r"\Atg:[1-9][0-9]{0,18}\Z")
_BACKFILL_UUIDV7_RE = re.compile(
    r"\A(?:e-)?[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_INT64_MAX = (1 << 63) - 1


def _is_valid_trace_id(candidate: object) -> bool:
    """Local mirror of :func:`events.envelope.is_valid_trace_id` (Story 9.1).

    Story 9.7 pass-2 TM-E5: ``\\A``/``\\Z`` anchors + ``tg:[1-9][0-9]{0,18}``
    canonical pattern (no ``tg:0``, no leading zeros). Mirrors
    ``packages/events/src/events/envelope.py``.
    """
    if not isinstance(candidate, str) or not candidate:
        return False
    if _UUIDV7_RE.match(candidate):
        return True
    m = _TG_RE.match(candidate)
    if m is None:
        return False
    # The regex already constrained digits to [1, 10^19); enforce the int64
    # ceiling post-match (canonical: ≤ 2^63 − 1).
    try:
        return 1 <= int(candidate[3:]) <= _INT64_MAX
    except ValueError:
        return False


def _backfill_trace_id_from_request_id(envelope_dict: dict[str, Any]) -> str | None:
    """Story 9.7 pass-2 TH-B5/TH-B2 — shared back-fill rule for migrator.

    Returns the bare UUIDv7 to assign as ``trace_id``, or ``None`` when
    back-fill is not possible. Q6 decision (a): accepts ``e-<uuidv7>`` shape
    by stripping the ``e-`` prefix.

    NOTE — keep this rule in sync with :func:`events.backfill.backfill_trace_id_from_request_id`.
    The migrator implements its own copy to avoid pulling the events
    package into the minimal migrator container; the test suite enforces
    invariance via ``test_backfill_invariant_migrator_eq_subscriber``.
    """
    raw_request_id = envelope_dict.get("request_id")
    if not isinstance(raw_request_id, str) or not raw_request_id:
        return None
    if not _BACKFILL_UUIDV7_RE.match(raw_request_id):
        return None
    bare_uuid = raw_request_id.removeprefix("e-")
    if not _is_valid_trace_id(bare_uuid):
        return None
    return bare_uuid


def migrate_v1_0_0_to_v1_0_1(event: dict[str, Any]) -> dict[str, Any]:
    """Additive upgrade: add an empty `extensions` object + bump schema_version.

    v1.0.1 introduces an `extensions: {}` field on every event, reserved for
    forward-compatible per-event metadata. No semantic change at the payload level.

    Story 2.14 code-review fix M5: validates that the input event is
    actually v1.0.0. A non-1.0.0 input is a programmer error (the wrong
    migrator was invoked) and would otherwise be silently clobbered to
    ``schema_version="1.0.1"``.

    Story 9.7 extension: also back-fills ``trace_id`` when the event has
    ``null`` or no ``trace_id`` value. Pre-1.1.0 events were emitted without
    a trace_id; migration injects the ``request_id`` as a synthetic trace_id
    so that post-9.7 envelope validation (which requires trace_id) accepts
    migrated records. Using request_id ensures each migrated event carries a
    unique, valid bare UUIDv7 while preserving the original causal identity.
    """
    src_version = event.get("schema_version")
    if src_version != "1.0.0":
        raise ValueError(
            f"migrate_v1_0_0_to_v1_0_1: expected schema_version='1.0.0', "
            f"got {src_version!r}; refusing to mutate"
        )
    migrated = dict(event)
    migrated["schema_version"] = "1.0.1"
    migrated.setdefault("extensions", {})
    # Back-fill trace_id: use request_id as a synthetic bare UUIDv7 so that
    # post-Story-9.7 envelope parsing (which requires trace_id) succeeds.
    # Deterministic: same request_id → same synthetic trace_id on re-run.
    #
    # Story 9.7 pass-1 PH-B2/E2: hard-fail when request_id is missing OR
    # not shape-valid as a trace_id (Story 9.1 contract). The prior code
    # silently emitted ``trace_id=""`` on missing request_id, which then
    # failed downstream envelope validation with a much less actionable
    # error far from the source record.
    if not migrated.get("trace_id"):
        # Story 9.7 pass-2 TH-B2/Q6: accept ``e-<uuidv7>`` request_id (pre-9.1
        # shape) by stripping the ``e-`` prefix before validation. The shared
        # back-fill helper returns the bare UUIDv7 or ``None``.
        backfilled = _backfill_trace_id_from_request_id(migrated)
        if backfilled is None:
            request_id = migrated.get("request_id", "")
            raise MigratorError(
                f"cannot back-fill trace_id: request_id missing or invalid "
                f"(got {request_id!r}); migration requires every input record "
                f"to carry a Story-9.1-valid request_id (UUIDv7, ``e-<uuidv7>``, "
                f"or 'tg:<digits>')"
            )
        migrated["trace_id"] = backfilled
    return migrated


MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "v1.0.0-to-v1.0.1": migrate_v1_0_0_to_v1_0_1,
}


def _err(msg: str) -> None:
    """Emit an error message to stderr (no exit; callers return rc)."""
    print(f"error: {msg}", file=sys.stderr)


def _fsync_dir(path: Path) -> None:
    """Fsync the directory containing *path* so a rename becomes durable.

    Story 2.14 code-review fix M8: ``os.replace`` is atomic w.r.t. concurrent
    readers but is NOT durable across power loss without a directory fsync.
    POSIX-only; the platform is POSIX-only per FR48.
    """
    parent = path.parent
    try:
        fd = os.open(parent, os.O_RDONLY)
    except OSError:
        # Best-effort: some filesystems/sandboxes refuse O_RDONLY on directories.
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv: list[str]) -> int:
    """CLI entrypoint. Returns 0 on success, non-zero on argument/IO errors.

    Code-review fix C3: returns rather than raising :class:`SystemExit` so
    the type signature (``-> int``) reflects actual behavior. Tests can
    assert on the return code instead of catching :class:`SystemExit`. The
    ``__main__`` shim wraps the call with ``sys.exit(main(sys.argv))``.
    """
    if len(argv) != 2:
        _err(
            "usage: python -m migrator <from>-to-<to>   "
            f"(supported: {', '.join(sorted(MIGRATIONS))})"
        )
        return 1
    pair = argv[1]
    if pair not in MIGRATIONS:
        _err(f"unknown migration {pair!r}; supported: {', '.join(sorted(MIGRATIONS))}")
        return 1
    # split with maxsplit to avoid silent unpack errors on future pair names
    # that might legitimately contain multiple `-to-` substrings.
    parts = pair.split("-to-", maxsplit=1)
    if len(parts) != 2:
        _err(f"migration pair must contain exactly one '-to-' separator: {pair!r}")
        return 1
    from_version, to_version = parts

    event_log_path = Path(
        os.environ.get("EVENT_LOG_PATH", "/var/lib/oh-my-bmad/registry/events/current.jsonl")
    )
    if not event_log_path.is_file():
        _err(f"event log not found: {event_log_path}")
        return 1

    migrate = MIGRATIONS[pair]

    output_path = event_log_path.with_suffix(f".{to_version}.jsonl")
    staging_path = output_path.with_suffix(output_path.suffix + ".partial")
    archive_path = event_log_path.with_suffix(f".{from_version}.archive")

    print(f"→ migrating {event_log_path} ({from_version} → {to_version}) → {output_path}")
    count = 0
    # Write to a .partial staging file first; only if the full read-migrate
    # pass completes without error do we fsync + rename to the final path and
    # archive the original. Any crash mid-write leaves .partial (reapable)
    # and the original event log untouched — no data loss.
    #
    # Story 2.14 code-review fix Mn4: open output with ``newline=""`` to
    # disable platform newline translation (POSIX-only project, but defensive
    # against future Windows porting attempts that would otherwise emit \r\n).
    try:
        with (
            event_log_path.open(encoding="utf-8") as inp,
            staging_path.open("w", encoding="utf-8", newline="") as out,
        ):
            for raw in inp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    _err(f"invalid JSONL in {event_log_path} at line {count + 1}: {exc}")
                    return 1
                migrated = migrate(event)
                out.write(json.dumps(migrated, sort_keys=True, separators=(",", ":")))
                out.write("\n")
                count += 1
            out.flush()
            os.fsync(out.fileno())
    except Exception:
        # Clean up the partial file on any failure so a retry is a clean slate.
        if staging_path.exists():
            staging_path.unlink(missing_ok=True)
        raise

    # Atomic rename: the output file either fully exists at output_path or
    # doesn't — it's never a half-written file under the final name.
    # Code-review fix M6: same-fs ``os.replace`` is atomic; ``shutil.move``
    # would fall back to copy+delete on cross-fs and break atomicity. Our
    # archive_path is by construction in the same directory as event_log_path
    # (derived via ``with_suffix``) so ``os.replace`` is correct here.
    os.replace(staging_path, output_path)
    # Only archive the original after the new file is safely in place.
    os.replace(event_log_path, archive_path)
    # Code-review fix M8: fsync the parent directory so the renames are
    # durable across power loss.
    _fsync_dir(output_path)
    print(f"→ migrated {count} events")
    print(f"→ archived original to {archive_path}")
    print(f"✓ migration {pair} complete")
    return 0
