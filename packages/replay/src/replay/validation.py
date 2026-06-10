"""Replay validation -- compare replayed state vs live materialized state.

Phase 12 / Story 62-1.

Provides :func:`validate_replay` which replays the event log to produce an
expected state and compares it field-by-field against a live materialized
state snapshot supplied by the caller. Returns a :class:`ValidationResult`
with counts and individual diffs for any mismatches.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from replay.archive_manifest import ArchiveManifestInput
from replay.engine import replay_events

_log = structlog.get_logger(__name__)

# Replay uses max int to replay ALL events (no upper-bound cutoff).
_MAX_INT = sys.maxsize


@dataclass(frozen=True)
class ValidationFieldDiff:
    """A single field-level mismatch between replayed and live state."""

    table: str  # "tasks" or "sessions"
    row_id: str  # the entity's id
    field: str  # the field name
    expected: Any  # replayed value (what it should be)
    actual: Any  # live value (what it currently is)


@dataclass(frozen=True)
class ValidationResult:
    """Summary of a replay validation pass."""

    total_fields: int
    matching_fields: int
    mismatching_fields: int
    diffs: list[ValidationFieldDiff]


def _compare_dicts(
    table: str,
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
) -> list[ValidationFieldDiff]:
    """Compare two lists of row-dicts keyed by ``id``, returning diffs."""
    actual_by_id = {row["id"]: row for row in actual_rows}
    seen_ids: set[str] = set()
    diffs: list[ValidationFieldDiff] = []

    for expected in expected_rows:
        row_id = expected["id"]
        seen_ids.add(row_id)
        actual = actual_by_id.get(row_id)
        if actual is None:
            # Row missing from live state -- report all fields as diffs.
            for field, value in expected.items():
                if field == "id":
                    continue
                diffs.append(
                    ValidationFieldDiff(
                        table=table,
                        row_id=row_id,
                        field=field,
                        expected=value,
                        actual=None,
                    )
                )
            continue

        for field, expected_value in expected.items():
            actual_value = actual.get(field)
            if expected_value != actual_value:
                diffs.append(
                    ValidationFieldDiff(
                        table=table,
                        row_id=row_id,
                        field=field,
                        expected=expected_value,
                        actual=actual_value,
                    )
                )

    # Rows present in live but missing from replayed state.
    for row_id, actual in actual_by_id.items():
        if row_id in seen_ids:
            continue
        for field, value in actual.items():
            if field == "id":
                continue
            diffs.append(
                ValidationFieldDiff(
                    table=table,
                    row_id=row_id,
                    field=field,
                    expected=None,
                    actual=value,
                )
            )

    return diffs


def _count_fields(state: dict[str, list[dict[str, Any]]]) -> int:
    """Count total non-id fields across all tables in *state*."""
    total = 0
    for rows in state.values():
        for row in rows:
            total += len(row) - 1  # exclude 'id' from field count
    return total


async def validate_replay(
    *,
    event_log_dir: Path,
    live_state: dict[str, Any],
    archive_manifest_path: ArchiveManifestInput = None,
) -> ValidationResult:
    """Replay events and compare against live materialized state.

    Args:
        event_log_dir: Directory containing JSONL event-log files.
        live_state: Current materialized state from the live database,
            shaped as ``{"tasks": [...], "sessions": [...]}``.
        archive_manifest_path: Optional archive manifest path or hot-only sentinel.

    Returns:
        :class:`ValidationResult` with field-level comparison details.
    """
    result = await replay_events(
        up_to=_MAX_INT,
        event_log_dir=event_log_dir,
        archive_manifest_path=archive_manifest_path,
    )
    replayed_state = result.state

    diffs: list[ValidationFieldDiff] = []
    for table in ("tasks", "sessions"):
        expected_rows = replayed_state.get(table, [])
        actual_rows = live_state.get(table, [])
        diffs.extend(_compare_dicts(table, expected_rows, actual_rows))

    # Count total fields from the UNION of replayed + live rows.
    merged: dict[str, list[dict[str, Any]]] = {}
    for table in ("tasks", "sessions"):
        replayed = {r["id"]: dict(r) for r in replayed_state.get(table, [])}
        live = {r["id"]: dict(r) for r in live_state.get(table, [])}
        merged[table] = [
            {**replayed.get(rid, {}), **live.get(rid, {})} for rid in replayed.keys() | live.keys()
        ]
    total_fields = _count_fields(merged)

    _log.info(
        "replay_validation_complete",
        total_fields=total_fields,
        mismatching=len(diffs),
    )

    return ValidationResult(
        total_fields=total_fields,
        matching_fields=total_fields - len(diffs),
        mismatching_fields=len(diffs),
        diffs=diffs,
    )


__all__ = [
    "ValidationFieldDiff",
    "ValidationResult",
    "validate_replay",
]
