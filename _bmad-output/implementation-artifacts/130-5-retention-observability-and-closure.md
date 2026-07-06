# Story 130.5 — Retention Observability and Epic 130 Closure

Status: done locally on 2026-07-06.

Story 130.5 adds `packages/replay/src/replay/retention_status.py`, a
package-local read-only status projection for existing Story 130.3 runner and
Story 130.4 apply records. The status helper summarizes already-produced
metadata only. It does not call the dry-run planner, apply adapter, scheduler,
credentials, object storage, command surfaces, registry/dashboard routes, or
runtime audit emitters.

## Implemented contract

- `project_retention_status(...)` reports enabled state, optional caller-supplied
  next run, last observed update time, failure count, skipped/protected blocker
  count, audit count, degraded state, runner/apply status counts, and fixed
  safety booleans showing no live scheduler, credential load, or status-triggered
  mutation.
- `RetentionStatusProjection.to_public_dict()` renders a secret-free public
  dictionary and omits runner input paths, policy/manifest input references,
  operator identities, and raw record internals.
- `assert_no_secret_material(...)` fails closed on obvious secret-like keys or
  values while allowing explicit negative safety booleans such as
  `credential_loaded: false`.
- Partial-failure and safe-retry-required apply records are reported as degraded
  without triggering retry, recovery, apply, planner, adapter, or scheduler work.
- Epic 130 remains documented as package-local/default-disabled capability; live
  production scheduler activation and production object-storage mutation remain
  deferred to later explicitly approved work.

## Verification evidence

- `uv run pytest packages/replay/src/replay/test_retention_status.py -q` — 6 passed.
- Full Story 130 verification is recorded in the final Autopilot report for this
  run and should remain green before merge/PR.

## Non-goals preserved

No cron daemon, live scheduler, production credential loading, object-storage SDK
dependency, ambient external storage call, dashboard command surface, registry
mutation endpoint, archive/manifest mutation, backup pruning, deployment behavior,
or runtime audit emitter is introduced by Story 130.5.
