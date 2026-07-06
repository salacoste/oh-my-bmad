# Story 131.1 — production operations runbook and preflight contract

## Scope delivered

Story 131.1 is a docs/status-only Epic 131 slice. It adds
`docs/production-operations.md` as the concrete production operations contract and
links it from the operator runbook. The contract defines operation classes,
required preflight fields, dry-run evidence, approval levels, docs/status audit
fields, emergency-disable evidence, rollback/restore prerequisites, ownership,
future Story 131.2-131.6 boundaries, and unsupported/fail-closed behavior.

## Evidence

- Approved plan: `.omx/specs/epic-131-story-131-1-production-ops-runbook-preflight-plan.md`.
- Approved test spec: `.omx/specs/epic-131-story-131-1-production-ops-runbook-preflight-test-spec.md`.
- Contract: `docs/production-operations.md`.
- Operator link: `docs/operator-runbook.md`.
- Status updates: `docs/feature-status.md` and
  `_bmad-output/implementation-artifacts/sprint-status.yaml`.

## Non-goals preserved

No runtime/source/test code changes were made. Story 131.1 does not add or alter
secrets, credentials, services, MCP tools, dependencies, lockfiles, CI, deployment
recipes, command implementations, lifecycle/retention jobs, GitHub mutation paths,
or production audit emitters.

## Fail-closed boundaries

Real GitHub writes, credentials, deployment mutations, command surfaces,
lifecycle/retention jobs, Epic 129/PR #100 reclassification, and runtime production
audit emitters remain deferred/fail-closed until separate approved stories provide
implementation and verification evidence. The GitHub MCP write-enable variable is
referenced only as a future operator-controlled gate; this artifact does not assert
its value.

## Verification plan

Required verification for this docs/status-only slice:

- `git diff --check`.
- Confirm the changed file set is limited to the five approved files.
- Confirm `docs/production-operations.md` includes concrete required contract terms.
- Confirm `docs/operator-runbook.md` links the contract.
- Confirm sprint status includes Epic 131, Story 131.1, and a docs/status audit entry
  that is explicitly not a runtime production audit event.
- Confirm `docs/feature-status.md` presents Story 131.1 as docs/status-only and keeps
  production credentials, real GitHub writes, deployment changes, command surfaces,
  runtime audit emitters, and retention jobs deferred/fail-closed.
- Confirm no runtime/source/test/dependency/lockfile/CI files changed and no forbidden
  overclaim wording was introduced in changed lines.
