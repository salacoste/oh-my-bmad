# Story 83.1: Replay-validation and rollback-evidence contract

## Status

Done.

## Story

As an operator and maintainer,
I want future destructive lifecycle apply to require replay validation and rollback evidence before mutation,
so that disk reclamation cannot destroy auditability, replay correctness, or recoverability.

## Acceptance criteria

1. Future apply is blocked unless archive manifest validation and replay validation both pass for the retained hot+archive event set.
2. Future apply is blocked unless rollback/restore evidence exists for every affected hot segment.
3. Replay proof and rollback evidence are durable references that can be linked from Story 82.1 authorization evidence.
4. Backup artifacts must be outside the hot event-log directory and must include checksum/size evidence for affected hot segments.
5. Restore instructions and restore drill evidence must be present before future apply, unless an explicit bounded risk-acceptance policy is authored by the future implementation story.
6. Missing, stale, failed, ambiguous, or unverifiable replay/rollback evidence fails closed before mutation.
7. This story introduces no runtime/package/API/deployment behavior.

## Replay-validation proof contract

A future lifecycle apply implementation must require durable replay proof with at least these fields:

| Field | Requirement |
|---|---|
| `replay_validation_ref` | Event id, artifact URI/path, or audit-ledger reference for the replay validation proof; required. |
| `validated_at` | Timestamp of validation; required. |
| `event_log_dir_identity` | Identity of the hot log directory or deployment volume validated; required. |
| `archive_manifest_ref` | Exact manifest path/URI and digest used for validation; required when archive coverage is involved. |
| `archive_manifest_validation` | Must indicate pass; required. |
| `retained_hot_segments` | Segment identities retained after proposed apply; required. |
| `archive_segments` | Segment identities used as archive coverage; required when applicable. |
| `snapshot_policy` | Must identify the snapshot/replay boundary used during validation; required. Current Phase 16/17 snapshot validation uses `HOT_ONLY_REPLAY`, but the durable invariant is that snapshot/archive scope is explicit and cannot be weakened silently. |
| `validation_result` | Must indicate pass; required. |
| `validation_input_digest` | Digest/canonical identity of validation inputs; required. |
| `archive_error_boundary` | Confirmation that archive validation errors fail closed with preserved error evidence; required. Current HTTP replay/task-history routes expose this through route-local archive ProblemDetails, but the durable invariant is fail-closed archive error evidence rather than a specific transport envelope. |

## Rollback/restore evidence contract

A future lifecycle apply implementation must require rollback evidence with at least these fields:

| Field | Requirement |
|---|---|
| `rollback_evidence_ref` | Event id, artifact URI/path, or audit-ledger reference; required. |
| `backup_artifact_ref` | Backup path/URI outside the hot event-log directory; required. |
| `backup_created_at` | Timestamp; required. |
| `backup_storage_location` | Must not be only the hot event-log directory; required. |
| `affected_hot_segments` | Exact segment identities intended for mutation; required. |
| `segment_checksums` | SHA-256 and size evidence for every affected segment; required. |
| `restore_instructions_ref` | Link to documented restore commands/procedure; required. |
| `restore_drill_ref` | Recent restore drill evidence; required unless a future implementation story defines a bounded risk-acceptance exception with explicit expiry, reviewer identity, risk rationale, and affected segment scope. |
| `operator_acknowledgement` | Operator acknowledgement of rollback evidence; required, but not a substitute for restore drill evidence unless the bounded risk-acceptance exception above is present and auditable. |

## Future apply preflight

Immediately before any future mutation, the implementation must:

1. verify replay proof exists and passed;
2. verify archive manifest validation passed for the exact manifest and segment set;
3. verify retained hot+archive segment identities still match the authorized dry-run plan;
4. verify rollback evidence exists for every affected hot segment;
5. verify backup artifacts are outside the hot event-log directory;
6. verify restore instructions and drill evidence are present, or verify an auditable bounded risk-acceptance exception defined by the future implementation story;
7. fail closed before mutation on any missing, stale, mismatched, failed, ambiguous, or unverifiable evidence.

## Fail-closed matrix

Future apply is blocked when any of these are true:

- replay validation is missing or failed;
- archive manifest validation is missing, failed, or points to a different manifest digest;
- any archive segment is missing, has a checksum mismatch, malformed metadata, duplicate key, or sequence overlap;
- retained hot+archive set differs from the authorized dry-run plan;
- snapshot/archive replay boundary is ambiguous or weakened, including any silent weakening of the current `HOT_ONLY_REPLAY` snapshot behavior;
- archive validation error evidence is bypassed or no longer fails closed;
- backup artifact is missing, unreadable, unverifiable, or stored only under the hot event-log directory;
- affected segment checksum/size evidence is incomplete;
- restore instructions are absent;
- restore drill evidence is absent and no bounded risk-acceptance exception with expiry, reviewer identity, rationale, and affected segment scope exists under the future implementation policy;
- rollback evidence does not cover every affected hot segment.

## Non-goals

- No apply/prune command/API/tool is added.
- No backup, restore, replay, archive, manifest, snapshot, database, or hot-log mutation code is added.
- No runtime source, package source, API route, deployment file, CI, dependency, or lockfile changes are introduced.

## Evidence

This is a docs/status contract only. It updates Phase 17 planning/docs so any future destructive lifecycle apply is blocked unless replay proof and rollback evidence are present and verifiable.
