# Story 121.1 — Task Status + Limit + Offset Browser Consumption Planning

Date: 2026-06-29T17:07:16Z
Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only

## Selected route family and exact future candidate

- Selected family: read-only aggregate task-list dashboard/browser bounded selector composition.
- Exact future candidate: canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` only.
- Status selector source: visible aggregate-task-list status control with existing finite lifecycle vocabulary (`pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`).
- Limit selector source: visible aggregate-task-list limit control, 1 through 50 inclusive.
- Offset selector source: visible aggregate-task-list offset control, 0 through 2147483647 inclusive, raw spelling 1-10 digits.
- Canonical order: `status` then `limit` then `offset`; all other orders or extra/repeated/encoded/malformed keys fail closed.
- Current brownfield state: selector-free, status-only, limit-only, status+limit API routes, dashboard status+limit consumption, API-local limit+offset pagination, dashboard limit+offset consumption, manual previous/next controls for limit+offset, and API-local status+limit+offset are implemented and closed. Exact dashboard/browser status+limit+offset consumption remains unimplemented until Story 121.2.

## Non-authorization statement

Story 121.1 is docs/status-only. It does not add runtime implementation, backend/API behavior changes, test-code changes, browser network calls, dashboard JavaScript/HTML behavior changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sorting controls, free-text search, arbitrary query language, automatic traversal, infinite scroll, hidden selectors, row-derived selectors, URL/hash state, local/session storage, cookies, timers/workers/retry/polling side channels, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 121.2 test obligations

A future tests-first implementation story must prove:

1. only canonical browser request construction for `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` is newly accepted;
2. route construction remains GET-only, bodyless, `credentials: "omit"`, and canonical with status then limit then offset;
3. status, limit, and offset are read only from visible aggregate-task-list controls;
4. missing, hidden, malformed, Unicode, encoded, fractional, negative, overlarge, empty, or mismatched control values fail closed before fetch;
5. strict response metadata validation requires selected_status, selected_limit, selected_offset, returned_count, has_more, next_offset/null, freshness, authority, provenance, request/trace/correlation id, and bounded summary rows only;
6. row status mismatches and route/selector metadata mismatches fail closed;
7. manual previous/next behavior, if retained, uses visible status+limit+offset controls and authoritative `has_more`/`next_offset` without automatic traversal;
8. existing selector-free, status-only, limit-only, status+limit, limit+offset, dashboard status+limit, dashboard limit+offset, manual previous/next, and API-local status+limit+offset contracts remain independently green;
9. status+offset without limit, offset-only, reversed order, repeated keys, extra keys, hidden selectors, row-driven traversal, URL/storage state, sorting/search/discovery, backend/API changes, replay/lifecycle mutation, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain rejected/deferred.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-42-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-42-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-42-epics.md`
- `_bmad-output/implementation-artifacts/121-1-task-status-limit-offset-browser-consumption-planning.md`

## Consensus evidence

- Architect review: native agent `019f145a-f91a-7eb0-839e-8f7b00a6bc3f` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []`; persisted at `.omx/artifacts/ralplan/story-121-1-architect-review.md`.
- Critic review: native agent `019f145c-a994-7931-84ee-3230505a1a4e` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []` after the Architect gate; persisted at `.omx/artifacts/ralplan/story-121-1-critic-review.md`.

## Completion evidence

Story 121.1 completes Phase 42 / Epic 121 docs/status-only status+limit+offset browser-consumption planning after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/dashboard/test implementation is now authorized only for the exact Story 121.2 boundary and remains otherwise deferred; backend/API/dependency/CI/deployment/service/MCP/generated-data implementation remains unauthorized.

## Verification plan

- Verify Phase 42 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 42 / Epic 121 without marking implementation complete.
- Verify `docs/feature-status.md` states status+limit+offset browser consumption is selected/opened as a planning candidate, not implemented.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change for Story 121.1.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Completion timestamp

Completed: 2026-06-29T17:13:33Z
