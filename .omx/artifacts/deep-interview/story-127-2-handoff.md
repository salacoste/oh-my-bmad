# Deep Interview Handoff — Story 127.2

## Story
Epic 127 / Story 127.2: API-local Task Search/Discovery Runtime Boundary.

## Decision
No user clarification needed. Story 127.1 committed an explicit product/architecture contract and Story 127.2 acceptance criteria are precise enough to implement the first runtime gate.

## Requirements locked for implementation
- Implement only `/v1/tasks` bodyless GET search/discovery.
- Canonical raw query begins with `field`, `op`, `q`, followed only by approved existing selectors in order: `status`, `limit`, `offset`, `sort`.
- Preserve existing selector routes and fail-closed behavior.
- Add no dashboard/browser traversal, hidden selector, prefetch, adjacent route, mutation, storage, credential, deployment, or dependency behavior.
- Accept only allowlisted fields/operators/bounds from Story 127.1.
- Reject percent-encoding, `+`, raw spaces, controls, Unicode/non-ASCII, empty values, repeated/encoded/reordered/extra keys, unsupported compositions, GET bodies, and `field=status` plus `status=` duplicate semantics.
- Return bounded rows plus selected search metadata, selected selectors, freshness, authority, provenance, request/trace/correlation ids, pagination, redaction state, and explicit display state.

## Implementation target
- Runtime: `services/registry-api/src/registry_api/routes/tasks.py`
- Tests: `services/registry-api/src/registry_api/test_app.py`

## Verification expectation
- Targeted task aggregate/search tests pass.
- Formatting/lint for changed Python files pass.
- `git diff --check` passes.
