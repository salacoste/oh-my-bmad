# Story 120.1 — Task Status + Limit + Offset API-local Planning

Date: 2026-06-29T13:41:34Z
Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only

## Selected route family and exact future candidate

- Selected family: read-only aggregate task-list API-local bounded selector composition.
- Exact future candidate: canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` only.
- Status selector: existing finite lifecycle vocabulary (`pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`).
- Limit selector: existing ASCII integer row-count selector, 1 through 50 inclusive.
- Offset selector: existing ASCII integer offset selector, 0 through 2147483647 inclusive, raw spelling 1-10 digits.
- Canonical order: `status` then `limit` then `offset`; all other orders or extra/repeated/encoded/malformed keys fail closed.
- Current brownfield state: selector-free, status-only, limit-only, status+limit API routes, API-local limit+offset pagination, dashboard status+limit consumption, dashboard limit+offset consumption, and manual previous/next controls for limit+offset are implemented and closed. Exact status+limit+offset composition is currently rejected and remains unimplemented until Story 120.2.

## Non-authorization statement

Story 120.1 is docs/status-only. It does not add runtime implementation, backend/API behavior changes, test-code changes, browser network calls, dashboard JavaScript/HTML behavior changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sorting controls, free-text search, arbitrary query language, automatic traversal, infinite scroll, hidden selectors, row-derived selectors, URL/hash state, local/session storage, cookies, timers/workers/retry/polling side channels, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 120.2 test obligations

A future tests-first implementation story must prove:

1. only canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` is newly accepted;
2. route construction remains GET-only, bodyless, and raw-query canonical with status then limit then offset;
3. the status domain is exactly the existing finite lifecycle vocabulary;
4. limit accepts only ASCII integers 1..50 and offset accepts only ASCII integers 0..2147483647 with 1-10 raw digits;
5. status filtering occurs before offset windowing and bounded limit application;
6. `has_more` and `next_offset` are computed for the filtered ordered domain and `next_offset` never exceeds 2147483647;
7. response metadata includes route, selected_status, selected_limit, selected_offset, limit, returned_count, has_more, next_offset/null, freshness, display state, authority state, provenance, request/trace/correlation id, and bounded summary rows only;
8. existing selector-free, status-only, limit-only, status+limit, limit+offset, dashboard status+limit, dashboard limit+offset, and manual previous/next contracts remain independently green;
9. status+offset without limit, offset-only, reversed order, repeated keys, extra keys, empty values/segments, encoded keys/values, Unicode digits, fractional/negative/overlarge numbers, nested parameters, GET bodies, sorting/search/discovery, browser/dashboard expansion, automatic traversal, row-driven traversal, replay/lifecycle mutation, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain rejected/deferred.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-41-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-41-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-41-epics.md`
- `_bmad-output/implementation-artifacts/120-1-task-status-limit-offset-api-local-planning.md`

## Consensus evidence

- Architect review: native agent `019f139d-7897-7173-a158-21f9fd5ad8bc` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []`; persisted at `.omx/artifacts/ralplan/story-120-1-architect-review.md`.
- Critic review: native agent `019f13a0-51d6-7141-ac73-61ab68b163e5` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []` after the Architect gate; persisted at `.omx/artifacts/ralplan/story-120-1-critic-review.md`.

## Completion evidence

Story 120.1 completes Phase 41 / Epic 120 docs/status-only status+limit+offset API-local planning after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/API/test implementation is now authorized only for the exact Story 120.2 boundary and remains otherwise deferred; browser/dashboard/dependency/CI/deployment/service/MCP/generated-data implementation remains unauthorized.

## Verification plan

- Verify Phase 41 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 41 / Epic 120 without marking implementation complete.
- Verify `docs/feature-status.md` states status+limit+offset is selected/opened as a planning candidate, not implemented.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change for Story 120.1.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Completion timestamp

Completed: 2026-06-29T13:47:29Z
