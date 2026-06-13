# Phase 17 closure and next BMAD route

## Status

Phase 17 is shipped as a planning/readiness-only safety-contract phase.

## Closure summary

Phase 17 completed Epics 81-85:

- Epic 81: planning and scope lock.
- Epic 82: exact `plan_hash` authorization contract for any future apply.
- Epic 83: replay-validation and rollback-evidence contract for any future apply.
- Epic 84: documentation/status reconciliation and no-runtime proof.
- Epic 85: final verification, independent review, commit/push, and CI.

The phase did **not** implement destructive lifecycle apply. It did not add apply/prune/delete/truncate/move/rewrite/chmod behavior, archive mutation, object-storage lifecycle jobs, scheduled retention workers, credentialed production operations, package/API/runtime/deployment behavior, dependencies, or CI changes beyond documentation/status reconciliation.

## Next BMAD route

The next implementation route is intentionally not automatic. A future destructive lifecycle apply implementation requires a separate explicitly authorized BMAD phase/story after the Phase 17 safety contract, including:

1. product/PRD acceptance of destructive apply scope and non-goals;
2. architecture review of the exact apply surface, operator gate, replay proof, rollback evidence, and audit events;
3. test-first contracts for fail-closed behavior, dry-run/apply separability, exact `plan_hash` re-computation, and rollback evidence validation;
4. independent security/architecture review before any runtime mutation code lands;
5. final CI and operator approval evidence.

Until that future phase exists, the repository route is: keep destructive apply as future work, preserve archive-aware read-only behavior, and use the Phase 17 contracts as the gate for any later implementation planning.

## Verification boundary

This artifact is docs/status only and does not implement runtime behavior. It is safe to commit only if changed-path verification confirms no changes under runtime, package, MCP server, service, script, deployment, dependency, lockfile, or CI paths.
