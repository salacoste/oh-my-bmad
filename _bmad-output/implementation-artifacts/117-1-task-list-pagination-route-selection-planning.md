# Story 117.1 — Task List Pagination Route-Selection Planning

Date: 2026-06-28T19:13:22Z
Status: opened; consensus and implementation pending
Scope: docs/status-only

## Selected route family and exact future candidate

- Selected family: read-only aggregate task-list pagination / next-window API planning.
- Exact future candidate: canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- Selector source: explicit query selectors only in a future API-local implementation; browser/dashboard pagination controls remain deferred.
- Allowed limit domain: ASCII integer values 1 through 50 inclusive.
- Allowed offset domain: ASCII non-negative integer values, with final maximum/large-offset behavior pending later implementation-plan approval.
- Current brownfield state: selector-free, status-only, limit-only, status+limit API routes and status+limit dashboard consumption are implemented and closed. Offset/cursor/page traversal, sorting, free-text search, arbitrary discovery, status+offset/status+limit+offset composition, browser pagination controls, and broad dashboard wiring remain deferred.

## Non-authorization statement

Story 117.1 is docs/status-only. It does not add runtime implementation, backend/API route implementation, browser network calls, dashboard JavaScript/HTML behavior changes, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sorting controls, free-text search, arbitrary query language, offset/cursor/page runtime behavior, browser pagination controls, hidden selectors, row-derived selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 117.2 test obligations

A future tests-first implementation story must prove:

1. only canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` is newly reachable;
2. requests are GET-only, bodyless, canonical limit-then-offset query order;
3. limit values are ASCII integers from 1 through 50 inclusive;
4. offset values are ASCII non-negative integers within the later approved bound;
5. empty, negative, fractional, non-integer, Unicode digit, encoded/nested, very large, repeated, reversed-order, extra-key, body-bearing, and malformed selectors fail closed;
6. response metadata exposes selected limit, selected offset, returned_count, has_more, next_offset/null, freshness, authority, provenance, and correlation/request/trace id before authoritative display/use;
7. existing selector-free, status-only, limit-only, status+limit, and dashboard status+limit contracts remain independently green;
8. no browser pagination controls, automatic traversal, sort/search/discovery, status+offset/status+limit+offset composition, row-driven traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, production credentials, or production operations are introduced.

## Opening evidence

- `_bmad-output/planning-artifacts/phase-38-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-38-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-38-epics.md`
- `_bmad-output/implementation-artifacts/117-1-task-list-pagination-route-selection-planning.md`

## Verification plan

- Verify Phase 38 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 38 / Epic 117 without marking implementation complete.
- Verify `docs/feature-status.md` states pagination is selected/opened as a planning candidate, not implemented.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.
