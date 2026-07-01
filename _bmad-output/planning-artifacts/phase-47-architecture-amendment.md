# Phase 47 Architecture Amendment — Browser Full Selector Composition Boundary

Generated: 2026-07-01T22:32:55Z

## Canonical decision

Phase 47 / Epic 126 implements exactly one dashboard/browser aggregate-task-list runtime boundary over the already approved Story 125.2 API-local route:

`GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}`

The browser boundary is route-family local to `dashboard/static/aggregate-task-list.js` and `dashboard/static/index.html`. It composes only visible controls already present in the aggregate-task-list panel: status, limit, offset, and the finite two-token sort select.

## Brownfield context

- Story 121.2 already consumes visible status+limit+offset controls for `GET /v1/tasks?status=...&limit=...&offset=...`.
- Story 125.1 / commit `a21c998` already exposes visible standalone sort vocabulary controls for `updated_at_desc_id_asc` and `created_at_desc_id_asc`.
- Story 125.2 already implements the backend/API full-composition route.

## Runtime design constraints

1. Build the route from raw validated control values only; do not use `URLSearchParams(location...)`, storage, cookies, row data, hidden inputs, or server-provided route strings.
2. Keep canonical query order: status, limit, offset, sort.
3. Replace the aggregate-task-list primary read with full-composition validation/rendering; do not add a third independent task-list subtree.
4. Response validation must require route `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}`, selected status/limit/offset/sort match, finite sort token, bounded pagination, and rows matching selected status.
5. Manual previous/next controls must carry the same selected sort value and remain one explicit click per read. A visible sort/status/limit/offset edit after an authoritative read invalidates navigation until reloaded.
6. Standalone sort-specific metadata may be removed or left inert only if tests prove no standalone sort fetch remains; preferred simplification is to fold sort display into the primary task-list metadata.
7. Route literal allowlist remains only `/v1/tasks`; dynamic route construction is constrained by tests.

## Deferred surfaces

Search/discovery runtime, arbitrary query grammar, hidden selectors, URL/hash/storage/cookie state, automatic traversal, infinite scroll, polling/timers/workers/retries, row-derived traversal, broad dashboard runtime cleanup/rewiring, backend/API changes, generated live data, replay/session/detail/digest/trace traversal, mutation/control behavior, services/MCP/dependencies/lockfiles/CI/deployment changes, credentials, and production operations remain unavailable.

## Architecture acceptance criteria

- Native Architect then Critic consensus approves this boundary before implementation.
- Tests fail before implementation for the missing sort-composition route and pass after implementation.
- Runtime diff is limited to aggregate-task-list browser code and docs/status/story artifacts.
- Existing dashboard guardrails remain green.
