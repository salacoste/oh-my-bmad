# Phase 42 PRD Amendment — Task Status + Limit + Offset Browser Consumption Planning

Generated: 2026-06-29T17:07:16Z

## Scope statement

Phase 42 opens the next narrow dashboard/browser consumption branch after Phase 41 / Epic 120 closed the API-local `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` route.

Story 121.1 is docs/status-only. It selects and constrains one future dashboard aggregate-task-list runtime candidate: exact canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`, composed only from visible aggregate-task-list status, limit, and offset controls. It does not add runtime implementation, backend/API behavior changes, test-code changes, dashboard JavaScript/HTML changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, automatic traversal, infinite scroll, sorting controls, free-text search, arbitrary query language, hidden selectors, row-derived traversal, URL/hash state, local/session storage, cookies, timers/workers/retry/polling side channels, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list dashboard/browser bounded selector composition.
- **Selected exact future candidate surface:** canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` only.
- **Status selector source:** one visible aggregate-task-list status control with one approved lifecycle value: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
- **Limit selector source:** one visible aggregate-task-list limit control with ASCII integer 1 through 50 inclusive.
- **Offset selector source:** one visible aggregate-task-list offset control with ASCII non-negative integer from 0 through 2147483647 inclusive, raw spelling limited to 1-10 ASCII digits.
- **Canonical query order:** status first, then limit, then offset. Reversed order, omitted middle selectors, repeated keys, percent-encoded keys/values, Unicode digits, empty segments, or additional query keys remain unauthorized.
- **Runtime boundary:** aggregate-task-list panel only; one explicit user action/control event per read; no hidden controls, row-derived selectors, automatic traversal, URL/storage state, background fetch, or adjacent route wiring.

## Product goals

- Select the smallest dashboard/browser continuation after independently implemented status+limit browser consumption, limit+offset browser consumption, manual limit+offset previous/next controls, and API-local status+limit+offset.
- Allow a user to request one bounded task-list window within one lifecycle status using visible aggregate-task-list controls only.
- Preserve completed task-list contracts: selector-free `GET /v1/tasks`, status-only, limit-only, status+limit API/browser, limit+offset API/browser/manual-navigation, and API-local status+limit+offset.
- Require Architect approval followed by Critic approval before any tests-first runtime implementation story.
- Keep all non-selected surfaces fail-closed.

## Non-goals

Dashboard routes outside aggregate-task-list, backend/API changes, status+offset without limit, offset-only reads, cursor/page tokens, automatic traversal, automatic next-page loops, infinite scroll, background prefetch, timer/worker retry, URL/hash pagination state, local/session storage, cookies, generated selectors, hidden selectors, row-derived selectors, automatic row drill-down, free-text search, arbitrary query language, sorting controls, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, and production operations.

## Functional requirements

- **FR345 — Selected family.** Story 121.1 selects only read-only aggregate task-list dashboard/browser bounded selector composition planning.
- **FR346 — Exact future candidate.** The only future runtime candidate selected by this phase is canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.
- **FR347 — Visible selector provenance.** Future browser work may compose the route only from visible aggregate-task-list status, limit, and offset controls; hidden inputs, row-derived values, storage, URL/hash state, and generated selectors are unauthorized.
- **FR348 — Browser request contract.** Future browser work must issue bodyless GET requests with `credentials: "omit"`, `Accept: application/json`, and no retry/polling/background traversal side channels.
- **FR349 — Strict response validation.** Future browser work must fail closed unless response metadata and rows match the selected route, status, limit, offset, authority, provenance, freshness, pagination, and bounded row-shape contract.
- **FR350 — Existing contract preservation.** Existing dashboard aggregate-task-list limit+offset rendering and manual previous/next controls must remain explicit, visible, bounded, and one-read-at-a-time while switching to the approved status+limit+offset route after Story 121.2.
- **FR351 — Adjacent surfaces remain deferred.** Automatic traversal, infinite scroll, search/sort/discovery, status+offset without limit, hidden selectors, row traversal, services/MCP/dependency/CI/deployment changes, credentials, and production operations remain unauthorized.

## Non-functional requirements

- **NFR-S63 — Fail-closed browser selectors.** Missing, hidden, malformed, disabled-inappropriately, or out-of-domain visible controls must prevent fetch and render a non-authoritative failed state.
- **NFR-O45 — One explicit read at a time.** The panel must preserve the in-flight guard and avoid automatic traversal even when `has_more`/`next_offset` exists.
- **NFR-M36 — Regression isolation.** Future tests must prove the new browser composition without weakening existing read-only dashboard and API route contracts.

## Acceptance criteria for Story 121.1

1. Phase 42 PRD, architecture, and epics artifacts exist and define exact status+limit+offset browser-consumption planning scope.
2. Story 121.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 42 / Epic 121, marks Story 121.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 121.2/121.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim status+limit+offset browser runtime implementation.
5. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 121.1.

## Follow-on story sequence

- Story 121.1: docs/status-only exact status+limit+offset dashboard/browser planning and consensus.
- Story 121.2: tests-first aggregate-task-list dashboard/runtime boundary only if Story 121.1 consensus approves the exact boundary.
- Story 121.3: final validation closure with implementation commit and CI evidence after Story 121.2, if implemented.
