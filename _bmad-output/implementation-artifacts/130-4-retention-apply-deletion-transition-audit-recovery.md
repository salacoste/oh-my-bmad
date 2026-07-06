# Story 130.4 — Retention Apply, Deletion/Transition Audit, and Recovery Evidence

Status: done locally on 2026-07-06.

Story 130.4 adds `packages/replay/src/replay/retention_apply.py`, a package-local
approval-bound apply boundary for Story 130.2 retention dry-run plans. The API is
adapter-injected and default-disabled. It does not import object-storage SDKs,
load production credentials, start schedulers, add command surfaces, add
dashboard/registry mutation endpoints, mutate archive/manifests, prune backups,
change deployment behavior, or make runtime audit emitters live.

## Implemented contract

- `RetentionApplyConfig(enabled=False)` keeps apply disabled by default.
- `RetentionApplyApprovalEvidence` binds approval to an exact dry-run
  `plan_hash` and dry-run `generated_at`, non-empty operator identity,
  non-empty approval event/reference, approval timestamp, and expiry timestamp.
- `apply_retention_plan(...)` requires an idempotency key, fresh plan evidence,
  unexpired matching approval, blocker-free dry-run plan, and recovery evidence
  for every planned transition/delete action.
- Before replay or mutation, the submitted dry-run plan's canonical payload is
  re-hashed and must match `plan_hash`; tampered decisions, blockers, or object
  identities fail closed without adapter calls.
- The injected `RetentionApplyAdapter` verifies each object identity before any
  transition/delete call. Identity mismatch records failure and performs no
  mutation for that object.
- Per-action audit evidence records object identity, manifest/policy basis,
  planned action, adapter response, trace id, idempotency key, operator identity,
  recovery status, and failure details when applicable.
- Completed apply replays from the ledger without duplicate adapter mutation
  calls only when the submitted dry-run `plan_hash`, dry-run `generated_at`,
  approval `plan_hash`, and approval `plan_generated_at` match the canonical
  ledger record.
- Partial failure persists degraded state, blocks further destructive work for
  that plan, and requires explicit safe retry/review evidence before another
  destructive attempt. Safe retry skips already-succeeded destructive object
  identities from the prior partial failure and records that skip with the safe
  retry event reference.

## Verification evidence

- `uv run ruff format packages/replay/src/replay/retention_apply.py packages/replay/src/replay/test_retention_apply.py packages/replay/src/replay/__init__.py` — clean after formatting.
- `uv run ruff check packages/replay/src/replay/retention_apply.py packages/replay/src/replay/test_retention_apply.py packages/replay/src/replay/__init__.py` — all checks passed.
- `uv run pytest packages/replay/src/replay/test_retention_apply.py -q` — 19 passed.
- `uv run pytest packages/replay/src/replay/test_retention.py packages/replay/src/replay/test_retention_runner.py packages/replay/src/replay/test_retention_apply.py -q` — 52 passed.
- `uv run python scripts/check_retention_policy_readiness.py --verbose` — retention policy/object-storage readiness OK (10 fail-closed conditions).
- `python -m py_compile packages/replay/src/replay/retention_apply.py packages/replay/src/replay/test_retention_apply.py packages/replay/src/replay/__init__.py` — passed.
- `git diff --check` — passed.

## Non-goals preserved

No cron daemon, live scheduler, production credential loading, object-storage SDK
dependency, ambient external storage call, dashboard command surface, registry
mutation endpoint, archive/manifest mutation, backup pruning, deployment behavior,
or runtime audit emitter is introduced by Story 130.4. Epic 130 remains
in-progress until Story 130.5 adds read-only observability/status and closure
evidence.
