# Story 119.1 — Manual Task-List Pagination Navigation Planning

Date: 2026-06-29T01:43:57Z
Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only

## Selected route family and exact future candidate

- Selected family: read-only aggregate task-list manual pagination navigation planning.
- Exact future candidate: visible manual previous-offset and next-offset controls inside the existing dashboard aggregate-task-list panel.
- Underlying route: existing canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` only; no backend/API route expansion.
- Selector/provenance source: existing visible limit and offset controls plus authoritative response metadata for `next_offset` only. No hidden, generated, row-derived, URL/hash-derived, storage-derived, cookie-derived, timer/worker-derived, or inferred selectors.
- Manual next candidate: enabled only after authoritative `has_more: true` and valid numeric `next_offset`; one user activation may perform at most one canonical load with current visible limit and validated next offset.
- Manual previous candidate: enabled only when current visible offset is greater than zero; one user activation may compute `max(current_offset - current_limit, 0)` and perform at most one canonical load.
- Current brownfield state: selector-free, status-only, limit-only, status+limit API routes, status+limit dashboard consumption, API-local limit+offset pagination, and dashboard limit+offset browser consumption are implemented and closed. Manual previous/next controls, automatic traversal, infinite scroll, sorting, free-text search, arbitrary discovery, status+offset/status+limit+offset composition, browser storage/URL pagination state, hidden selectors, row-driven traversal, and broad dashboard wiring remain deferred until approved.

## Non-authorization statement

Story 119.1 is docs/status-only. It does not add runtime implementation, backend/API route implementation, browser network calls, dashboard JavaScript/HTML behavior changes, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sorting controls, free-text search, arbitrary query language, offset/cursor/page automatic traversal, infinite scroll, hidden selectors, row-derived selectors, URL/hash state, local/session storage, cookies, timers/workers/retry/polling side channels, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 119.2 test obligations

A future tests-first implementation story must prove:

1. only visible manual previous-offset and next-offset controls are newly reachable in the aggregate-task-list panel;
2. the only route used remains canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` with GET, no request body, `credentials: "omit"`, and canonical limit-then-offset query order;
3. next activation is possible only after an authoritative response has `has_more: true` and a valid numeric `next_offset` within 0..2147483647;
4. previous activation is possible only when visible current offset is greater than zero and computes exactly `max(current_offset - current_limit, 0)`;
5. one user activation of previous or next performs at most one load and does not loop, prefetch, retry, poll, use timers/workers, or follow returned `next_offset` automatically;
6. existing visible limit and offset controls remain the authoritative selector state and fail closed for hidden, missing, generated, URL/hash-derived, storage-derived, cookie-derived, row-derived, Unicode digit, fractional, negative, overlarge, repeated, extra-key, reversed-order, body-bearing, and malformed selector states;
7. response metadata validation still gates authoritative rendering: selected limit, selected offset, returned_count, has_more, next_offset/null, freshness, authority, provenance, and request/trace/correlation id;
8. existing selector-free, status-only, limit-only, status+limit, dashboard status+limit, API-local limit+offset, and dashboard limit+offset contracts remain independently green;
9. no backend/API route changes, sorting/search/discovery, status+offset/status+limit+offset composition, row-driven traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, production credentials, or production operations are introduced.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-40-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-40-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-40-epics.md`
- `_bmad-output/implementation-artifacts/119-1-manual-task-list-pagination-navigation-planning.md`

## Consensus evidence

- Architect review: native agent `019f110c-7d68-7d72-b5fb-5664ae56f36a` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []`; persisted at `.omx/artifacts/ralplan/story-119-1-architect-review.md`.
- Critic review: native agent `019f110e-901b-7e63-9329-18ab15a9a7b3` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []` after the Architect gate; persisted at `.omx/artifacts/ralplan/story-119-1-critic-review.md`.

## Completion evidence

Story 119.1 completes Phase 40 / Epic 119 docs/status-only manual task-list pagination navigation planning after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/API/test/browser/dashboard/dependency/CI/deployment/service/MCP/generated-data implementation is now authorized only for the exact Story 119.2 boundary and remains otherwise deferred.

## Verification plan

- Verify Phase 40 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 40 / Epic 119 without marking implementation complete.
- Verify `docs/feature-status.md` states manual pagination navigation is selected/opened as a planning candidate, not implemented.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Completion timestamp

Completed: 2026-06-29T01:48:11Z
