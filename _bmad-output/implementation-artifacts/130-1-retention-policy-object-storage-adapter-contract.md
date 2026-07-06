# Story 130.1 — Retention policy and object-storage adapter contract

Story 130.1 is a static/readiness-only contract slice for Epic 130. It defines
retention policy evidence, object identity requirements, adapter capabilities,
dry-run/apply separation, clock/eventual-consistency semantics, and fail-closed
conditions before any lifecycle automation can run.

## Added evidence

- `docs/retention-policy-object-storage-readiness.json` records the executable
  policy/adapter readiness contract.
- `scripts/check_retention_policy_readiness.py` validates required policy fields,
  manifest-backed object identity, adapter capability prerequisites, docs/status
  wiring, CI/just gate wiring, and absence of new runner/mutation endpoint files.
- `tests/scripts/test_check_retention_policy_readiness.py` covers the checker
  self-test, live contract, missing legal-hold policy evidence, forbidden runner
  file, and missing CI wiring.

## Safety boundary

This slice does not add a scheduler, object-storage client, object delete,
object transition, archive/manifest mutation, backup pruning, production
credential loading, dashboard command surface, registry mutation endpoint, or
runtime audit emitter. Scheduled retention jobs remain disabled and no
object-storage deletion or transition is enabled.

## Future gates

Stories 130.2-130.4 must add dry-run validation, lock/idempotency-protected
scheduler behavior, apply/audit/recovery evidence, and approval-bound execution
before any retention mutation is authorized. Story 130.5 can close the epic only
after readiness/status/observability and drill evidence are recorded.
