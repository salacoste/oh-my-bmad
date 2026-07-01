# Phase 47 PRD Amendment — Browser Full Selector Composition

Generated: 2026-07-01T22:32:55Z

## Scope statement

Phase 47 opens Epic 126 for the dashboard/browser aggregate task-list full selector composition boundary. The already implemented Story 125.2 API route supports exactly:

`GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}`

Phase 47 may update only the aggregate-task-list browser panel, tests, and status/docs required to expose that existing route from visible controls. It must not add backend/API behavior, search/discovery, broad dashboard rewiring, hidden selectors, automatic traversal, dependencies, deployment changes, credentials, production operations, or mutation/control behavior.

## Product decision

Select the browser/dashboard full selector composition as the next runtime slice. The operator should be able to choose lifecycle status, list limit, list offset, and one of the two finite sort tokens from visible controls, then trigger one explicit read of the canonical route. Existing standalone sort behavior may be retired or folded into the composed read to avoid split authoritative task-list subtrees, as long as the two visible sort choices remain available and full composition is the only new task-list sort browser read.

## Product goals

- Compose existing visible status, limit, offset, and sort controls into the exact canonical API route.
- Reuse existing aggregate-task-list validation/rendering patterns.
- Preserve fail-closed behavior for selector edits, invalid controls, malformed responses, stale/non-authoritative states, and manual previous/next navigation.
- Keep search/discovery and broad dashboard rewiring closed.

## Non-goals

Backend/API changes, route grammar changes, standalone search/discovery, arbitrary query building, URL/hash/storage/cookie state, hidden selectors, automatic traversal/infinite scroll/background refresh, row-driven traversal, broad dashboard cleanup, generated live data, services/MCP changes, dependency/lockfile changes, CI/deployment changes, credentials, production operations, and mutation/control behavior.

## Functional requirements

- **FR388 — Visible full composition controls.** The browser surface must expose status, limit, offset, and finite sort selector values from visible aggregate-task-list controls only.
- **FR389 — Canonical query.** The runtime must fetch exactly `/v1/tasks?status={status}&limit={limit}&offset={offset}&sort={sort}` in that key order.
- **FR390 — Bodyless omitted credentials.** Fetches must be GET requests with no body and `credentials: "omit"`.
- **FR391 — Response metadata validation.** The runtime must validate `route`, selected status/limit/offset/sort, pagination metadata, freshness, authority, provenance, request/trace/correlation, bounded rows, and row status matching the selected status.
- **FR392 — Fail-closed selectors.** Missing, hidden, empty, encoded, alias, unicode/fractional/out-of-range, or otherwise invalid status/limit/offset/sort controls must render non-authoritative invalid state before fetch.
- **FR393 — Manual pagination remains explicit.** Previous/next offset controls may use the same selected sort value and must remain disabled unless the prior authoritative response permits an explicit next/previous read.
- **FR394 — Search/discovery and broad rewiring remain closed.** No search/discovery markers, task-detail traversal, hidden selectors, broad module graph changes, dependencies, or production surfaces may be added.

## Acceptance criteria

1. Planning/status artifacts open Phase 47 / Epic 126 and record exact non-goals.
2. Tests prove default and alternate-sort full-composition fetches use the canonical route, GET/no-body/omit credentials.
3. Tests prove response metadata mismatch for route/selected_sort and invalid sort selector fail closed without authoritative rendering.
4. Tests prove manual previous/next controls preserve the selected sort and still require explicit user action.
5. Existing aggregate-task-list status/limit/offset, sort vocabulary, route allowlist, forbidden marker, and invalid selector tests remain green.
6. No backend/API, dependency/lockfile, services/MCP, deployment, credential, or production files change.
