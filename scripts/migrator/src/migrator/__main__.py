"""migrator entrypoint — `python -m migrator <from>-to-<to>`.

Reads the JSONL event log at `$EVENT_LOG_PATH` (default
/var/lib/oh-my-bmad/registry/events/current.jsonl), applies the named
migration, writes the migrated output to `<path>.<to-version>.jsonl`, and
archives the original with suffix `.<from-version>.archive`.

Story 1.3 supports exactly one migration: `v1.0.0-to-v1.0.1`. Future stories
add more pairs to MIGRATIONS below.
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
        die(
            f"unknown migration {pair!r}; supported: {', '.join(sorted(MIGRATIONS))}"
        )
    from_version, to_version = pair.split("-to-")

    event_log_path = Path(
        os.environ.get(
            "EVENT_LOG_PATH", "/var/lib/oh-my-bmad/registry/events/current.jsonl"
        )
    )
    if not event_log_path.is_file():
        die(f"event log not found: {event_log_path}")

    migrate = MIGRATIONS[pair]

    output_path = event_log_path.with_suffix(f".{to_version}.jsonl")
    archive_path = event_log_path.with_suffix(f".{from_version}.archive")

    print(
        f"→ migrating {event_log_path} ({from_version} → {to_version}) "
        f"→ {output_path}"
    )
    count = 0
    with event_log_path.open(encoding="utf-8") as inp, output_path.open(
        "w", encoding="utf-8"
    ) as out:
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

    shutil.move(event_log_path, archive_path)
    print(f"→ migrated {count} events")
    print(f"→ archived original to {archive_path}")
    print(f"✓ migration {pair} complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
