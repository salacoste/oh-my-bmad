# Phase 13 Retrospective — Event Log Lifecycle Management

Date: 2026-06-10  
Scope: Epics 64-68 / P13-ELLM

## Summary
Phase 13 makes Historical Event Replay operationally safer as logs grow. Replay can now consume validated archived JSONL segments through `lifecycle-manifest.json`, while snapshots and task history intentionally remain hot-log only.

## Shipped
- Archive error hierarchy and manifest loader with checksum, missing-segment, duplicate, and overlap detection.
- `REPLAY_ARCHIVE_MANIFEST` plus legacy `EVENT_LOG_ARCHIVE_MANIFEST` env resolution with explicit `archive_manifest_path` precedence.
- `HOT_ONLY_REPLAY` sentinel so snapshot creation bypasses archive env vars.
- Archive-aware `replay_events` and `validate_replay` without changing the `ReplayResult` return contract.
- Package-only `replay_events_stream` with frozen `ReplayProgress` and terminal `ReplayResult` equivalence.
- Route-local ProblemDetails mapping for replay/validate archive failures; no global handler change.
- Sprint-status reconciliation for Phase 11-12 plus Phase 13 tracking.

## Verification
- `uv run pytest packages/replay/ services/registry-api/src/registry_api/routes/test_replay.py --tb=short -q` → 57 passed.
- `uv run ruff check ...` and `uv run ruff format --check ...` → clean.
- `uv run mypy packages/replay/ --ignore-missing-imports` → clean.
- `uv run mypy services/registry-api/src/registry_api/routes/replay.py --ignore-missing-imports` → clean.
- `uv run python scripts/check_imports.py` → clean.
- `uv run python scripts/check_single_writer.py` → clean.

## Lessons
1. Route-local error mapping preserved global RFC7807 behavior and avoided accidental `/errors/internal` responses.
2. Snapshot creation needed an explicit sentinel; relying on `None` would have leaked env-driven archive behavior into a hot-only surface.
3. Keeping streaming package-only avoided premature public API commitments while still enabling operator progress reporting.

## Carry-forward
- Future destructive prune/apply requires a separate ADR and operator gate.
- Archived task-history remains intentionally out of scope until prune semantics are designed.
- External object storage and scheduled lifecycle jobs remain future candidates.
