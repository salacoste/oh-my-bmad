#!/usr/bin/env python3
"""assert_migrated.py — sanity checks on the migrator's v1.0.1 output.

Called by the `just migrator-test-additive` recipe after the Dockerized
migrator runs against the fixture. Verifies: file exists, expected event
count (default 100; override via ``--expected N``), every event has
schema_version=1.0.1 and an `extensions` field. Post-review-fix handles
malformed JSON with a clean FAIL message (no raw traceback).

Story 2.14 bumps the default expected count from 3 → 100 to match the
new fixture; the ``--expected N`` argv lets the smoke recipe pin a
different number when it uses a smaller fixture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="assert_migrated.py",
        description="Validate a migrated v1.0.1 JSONL event log.",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="path to the migrated .v1.0.1.jsonl file",
    )
    parser.add_argument(
        "--expected",
        type=int,
        default=100,
        help="expected event count (default: 100, matching the Story 2.14 fixture)",
    )
    args = parser.parse_args(argv[1:])

    output_path: Path = args.path
    expected: int = args.expected

    if not output_path.is_file():
        print(f"FAIL: output file missing: {output_path}", file=sys.stderr)
        return 1

    events: list[dict[str, object]] = []
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

    if len(events) != expected:
        print(f"FAIL: expected {expected} events, got {len(events)}", file=sys.stderr)
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
