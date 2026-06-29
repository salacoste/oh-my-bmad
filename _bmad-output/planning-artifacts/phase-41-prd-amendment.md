# Phase 41 PRD Amendment — Task Status + Limit + Offset API-local Route Composition Planning

Generated: 2026-06-29T13:41:34Z

## Scope statement

Phase 41 opens the next narrow API-local task-list selector-composition branch after Phase 40 / Epic 119 closed dashboard manual previous/next controls for canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.

Story 120.1 is docs/status-only. It selects and constrains one future API-local runtime candidate: exact canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`. It does not add runtime implementation, backend/API behavior changes, test-code changes, dashboard JavaScript/HTML behavior changes, browser network calls, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, automatic traversal, infinite scroll, sorting controls, free-text search, arbitrary query language, hidden selectors, row-derived traversal, URL/hash state, local/session storage, cookies, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list API-local bounded selector composition.
- **Selected exact future candidate surface:** canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` only.
- **Status selector:** exactly one `status` query key with one approved lifecycle value: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
- **Limit selector:** exactly one `limit` query key with ASCII integer 1 through 50 inclusive.
- **Offset selector:** exactly one `offset` query key with ASCII non-negative integer from 0 through 2147483647 inclusive, raw spelling limited to 1-10 ASCII digits.
- **Canonical query order:** status first, then limit, then offset. Reversed order, omitted middle selectors, repeated keys, percent-encoded keys/values, Unicode digits, empty segments, or additional query keys remain unauthorized.
- **Runtime boundary:** API-local only; no dashboard/browser consumption, URL/storage state, automatic traversal, manual navigation extension, row-derived selection, or adjacent route wiring.

## Product goals

- Select the smallest API-local continuation after independently implemented status+limit and limit+offset task-list contracts.
- Allow a caller to request one bounded page within one finite lifecycle status while preserving explicit selector domains and deterministic task-summary ordering.
- Preserve completed task-list contracts: selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, limit-only `GET /v1/tasks?limit={task_list_limit}`, status+limit `GET /v1/tasks?status={task_status}&limit={task_list_limit}`, API-local limit+offset `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`, dashboard status+limit consumption, dashboard limit+offset consumption, and manual previous/next controls for limit+offset.
- Require Architect approval followed by Critic approval before any tests-first runtime implementation story.
- Keep all non-selected surfaces fail-closed.

## Non-goals

- Dashboard/browser consumption of status+limit+offset, status+offset without limit, offset-only reads, cursor/page tokens, automatic traversal, automatic next-page loops, infinite scroll, background prefetch, timer/worker retry, URL/hash pagination state, local/session storage, cookies, generated selectors, hidden selectors, row-derived selectors, automatic row drill-down, free-text search, arbitrary query language, sorting controls, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Changing existing query order for status+limit or limit+offset routes.
- Treating `next_offset` as an automatic traversal instruction.
- Adding response links, row actions, adjacent route affordances, or browser controls.

## Functional requirements

- **FR337 — Selected family.** Story 120.1 selects only read-only aggregate task-list API-local bounded selector composition planning.
- **FR338 — Exact future candidate.** The only future runtime candidate selected by this phase is canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.
- **FR339 — Canonical selector order.** Future runtime work may accept only exact `status` then `limit` then `offset` query order. All other orders, repeated keys, empty segments, encoded keys/values, Unicode numeric spellings, omitted selectors, or additional keys must fail closed.
- **FR340 — Finite selector domains.** Future runtime work must reuse the existing finite status vocabulary, limit domain 1..50, and offset domain 0..2147483647 with 1-10 ASCII digits.
- **FR341 — Filtered bounded window semantics.** Future runtime work must filter by selected status first, preserve existing task-summary ordering, apply the selected offset inside the filtered ordered domain, fetch at most `limit + 1`, return at most selected limit rows, and derive `has_more` / `next_offset` only inside the approved offset domain.
- **FR342 — Response metadata.** Future runtime response must expose route, selected_status, selected_limit, selected_offset, limit, returned_count, has_more, next_offset/null, freshness, display state, authority state, provenance, request_id, trace_id, correlation_id, and bounded summary rows only.
- **FR343 — Existing contract preservation.** Selector-free, status-only, limit-only, status+limit, limit+offset, dashboard consumption, and manual navigation contracts must remain independently green.
- **FR344 — Adjacent surfaces remain deferred.** Browser/dashboard status+limit+offset consumption, automatic traversal, infinite scroll, cursor/page tokens, sort controls, free-text search, arbitrary filters, hidden discovery, row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.

## Non-functional requirements

- **NFR-S62 — API-local closed selector grammar.** Future implementation must validate raw query spelling before framework-normalized semantics can broaden the accepted surface.
- **NFR-O44 — Bounded pagination metadata.** `next_offset` must never exceed 2147483647 and must be null when no approved next window exists.
- **NFR-M35 — Regression isolation.** Future tests must prove the new exact composition without weakening existing closed-route rejection tests.

## Acceptance criteria for Story 120.1

1. Phase 41 PRD, architecture, and epics artifacts exist and define exact status+limit+offset API-local planning scope.
2. Story 120.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 41 / Epic 120, marks Story 120.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 120.2/120.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim status+limit+offset runtime implementation.
5. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 120.1.

## Follow-on story sequence

- Story 120.1: docs/status-only exact status+limit+offset API-local planning and consensus.
- Story 120.2: tests-first API-local runtime boundary only if Story 120.1 consensus approves the exact boundary.
- Story 120.3: final validation closure with implementation commit and CI evidence after Story 120.2, if implemented.
