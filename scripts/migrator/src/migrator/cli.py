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
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def migrate_v1_0_0_to_v1_0_1(event: dict[str, Any]) -> dict[str, Any]:
    """Additive upgrade: add an empty `extensions` object + bump schema_version.

    v1.0.1 introduces an `extensions: {}` field on every event, reserved for
    forward-compatible per-event metadata (e.g., trace_id when distributed
    tracing lands in Phase 2). No semantic change.
    """
    migrated = dict(event)
    migrated["schema_version"] = "1.0.1"
    migrated.setdefault("extensions", {})
    return migrated


MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "v1.0.0-to-v1.0.1": migrate_v1_0_0_to_v1_0_1,
}


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        die(
            "usage: python -m migrator <from>-to-<to>   "
            f"(supported: {', '.join(sorted(MIGRATIONS))})"
        )
    pair = argv[1]
    if pair not in MIGRATIONS:
        die(f"unknown migration {pair!r}; supported: {', '.join(sorted(MIGRATIONS))}")
    # split with maxsplit to avoid silent unpack errors on future pair names
    # that might legitimately contain multiple `-to-` substrings.
    parts = pair.split("-to-", maxsplit=1)
    if len(parts) != 2:
        die(f"migration pair must contain exactly one '-to-' separator: {pair!r}")
    from_version, to_version = parts

    event_log_path = Path(
        os.environ.get("EVENT_LOG_PATH", "/var/lib/oh-my-bmad/registry/events/current.jsonl")
    )
    if not event_log_path.is_file():
        die(f"event log not found: {event_log_path}")

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
    try:
        with (
            event_log_path.open(encoding="utf-8") as inp,
            staging_path.open("w", encoding="utf-8") as out,
        ):
            for raw in inp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    die(f"invalid JSONL in {event_log_path} at line {count + 1}: {exc}")
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
    os.replace(staging_path, output_path)
    # Only archive the original after the new file is safely in place.
    shutil.move(event_log_path, archive_path)
    print(f"→ migrated {count} events")
    print(f"→ archived original to {archive_path}")
    print(f"✓ migration {pair} complete")
    return 0
