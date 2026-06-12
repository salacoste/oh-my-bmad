# Story 77.1 — Deterministic merged task-history collection

Status: done

## Summary

Task-history collection now uses the existing Phase 13 hot+archive envelope merge contract instead of scanning hot JSONL files directly. This keeps archive parsing, checksum validation, conflict detection, and deterministic monotonic ordering centralized in `replay.archive_manifest.collect_replay_envelopes`.

## Implementation

- `services/registry-api/src/registry_api/routes/replay.py`
  - `_read_task_events_sync()` accepts `archive_manifest_path` and delegates to `collect_replay_envelopes()`.
  - The default no-manifest path remains a hot-only fast path and does not pay archive merge/checksum cost.
  - Filtering by payload `task_id`, response shape, ordering, and pagination stay unchanged.

## Verification

- `uv run pytest services/registry-api/src/registry_api/routes/test_replay.py packages/replay/src/replay/test_engine.py packages/replay/src/replay/test_lifecycle.py packages/replay/src/replay/test_streaming.py packages/replay/tests/test_snapshots.py -q` → 86 passed, 1 warning.
- Post-review fast-path regression included in the same suite: no-manifest task history monkeypatches archive merge to fail and still returns hot-log history.
- `uv run mypy --strict services/registry-api packages/replay` → success.
