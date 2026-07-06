# Story 130.2 — Object-storage lifecycle dry-run and manifest validation

Story 130.2 adds a package-local, non-mutating retention dry-run planner in
`packages/replay/src/replay/retention.py`. It loads local policy and object
manifest JSON files, validates manifest-backed object identity, and returns a
metadata-only `RetentionDryRunPlan` with deterministic canonical JSON and plan
hash.

## Implemented boundary

- Policy domains are explicit; every object domain must be declared.
- Per-domain `allowed_actions` is required, must include `retain`, and has no
  default inference.
- Top-level `default_action` must be `retain` for Story 130.2.
- `last_modified_at_utc` is the deterministic age basis; strict UTC timestamps,
  no future timestamps, generated-at freshness, and `created_at_utc <=
  last_modified_at_utc` are enforced.
- Any repeated `(domain, object_key)` fails closed; versioned multi-entry object
  semantics remain deferred.
- Holds and exact-key exclusions produce blockers instead of actions.
- Planned `transition` and `delete` decisions are dry-run metadata only.

## Safety boundary

This story does not add a scheduler, retention job runner, external object
storage call, object deletion, object transition, archive or lifecycle-manifest
mutation, backup pruning, production credential loading, dashboard command
surface, registry mutation endpoint, deployment behavior, or runtime audit
emitter.

## Verification targets

- `packages/replay/src/replay/test_retention.py` covers valid deterministic plans,
  missing checksum/version, invalid object keys, repeated keys with different
  version/checksum, unknown domains, required `allowed_actions`, action conflicts,
  invalid intervals, exact-key exclusion misuse, future/stale timestamps,
  `created_at_utc <= last_modified_at_utc`, and source-file non-mutation.
- `scripts/check_retention_policy_readiness.py` extends Story 130.1 static checks
  with Story 130.2 evidence while preserving the original fail-closed contract.
