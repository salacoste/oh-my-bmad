# Phase 13 PRD Amendment — Event Log Lifecycle Management (P13-ELLM)

## Goal
Make Phase 12 replay operationally safe as event logs grow by adding a validated archive manifest, hot+archive replay, package-only progress streaming, and status/docs repair.

## Scope
- IN: sprint-status reconciliation, segment inventory/manifest schema, archive checksum validation, hot+archive replay merge, route-local archive errors for replay/validate, package-only `replay_events_stream`, docs/retro.
- OUT: hot deletion/prune apply, public HTTP streaming endpoint, archived task-history, object storage, scheduled jobs, lossy compaction.

## Functional Requirements
- FR139: Replay can include archived JSONL segments referenced by `lifecycle-manifest.json`.
- FR140: Archive manifest resolution supports `REPLAY_ARCHIVE_MANIFEST` and legacy `EVENT_LOG_ARCHIVE_MANIFEST` with explicit `archive_manifest_path` precedence.
- FR141: Snapshot creation remains hot-only through `HOT_ONLY_REPLAY` even when archive env vars are set.
- FR142: Replay archive errors use route-local ProblemDetails on replay and validate endpoints only.
- FR143: Package API exposes `replay_events_stream` and frozen `ReplayProgress`.

## Acceptance Criteria
- Hot-only behavior remains unchanged when no archive manifest is configured.
- Invalid archive config fails closed for replay/validate and never leaks to `/errors/internal`.
- `get_task_history` remains hot-log only.
- Snapshot create/list remain archive-unaware.
- Streaming emits progress after each applied batch and a terminal `ReplayResult` equivalent to `replay_events`.
