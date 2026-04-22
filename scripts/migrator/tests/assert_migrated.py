#!/usr/bin/env python3
"""assert_migrated.py — sanity checks on the migrator's v1.0.1 output.

Called by the `just migrator-test-additive` recipe after the Dockerized
migrator runs against the fixture. Verifies: file exists, 3 events, every
event has schema_version=1.0.1 and an `extensions` field. Post-review-fix
handles malformed JSON with a clean FAIL message (no raw traceback).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assert_migrated.py <path-to-v1.0.1.jsonl>", file=sys.stderr)
        return 2

    output_path = Path(argv[1])
    if not output_path.is_file():
        print(f"FAIL: output file missing: {output_path}", file=sys.stderr)
        return 1

    events: list[dict] = []
    for lineno, raw in enumerate(output_path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(
                f"FAIL: invalid JSON in {output_path} at line {lineno}: {exc}",
                file=sys.stderr,
            )
            return 1

    if len(events) != 3:
        print(f"FAIL: expected 3 events, got {len(events)}", file=sys.stderr)
        return 1

    for i, event in enumerate(events):
        if event.get("schema_version") != "1.0.1":
            print(
                f"FAIL: event {i} schema_version != 1.0.1: {event.get('schema_version')}",
                file=sys.stderr,
            )
            return 1
        if "extensions" not in event:
            print(f"FAIL: event {i} missing 'extensions' field", file=sys.stderr)
            return 1

    print(f"✓ migrator-test-additive OK ({len(events)} events, all v1.0.1 with extensions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
