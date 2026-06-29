# Phase 40 Architecture Amendment — Manual Task-List Pagination Navigation Boundary

Generated: 2026-06-29T01:43:57Z

## Decision

Phase 40 may proceed from completed Phase 39 dashboard/browser limit+offset consumption into one planning-only manual navigation branch:

- **Family:** read-only aggregate task-list manual pagination navigation planning.
- **Exact future candidate surface:** visible manual previous-offset and next-offset controls in the aggregate-task-list panel.
- **Underlying route:** existing canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` only.
- **Query order:** exact `limit` key followed by exact `offset` key.
- **Selector/state source:** visible aggregate-task-list panel state only: current visible limit, current visible offset, and authoritative response metadata for `next_offset`.

Story 119.1 is docs/status-only. It does not authorize implementation, route changes, dashboard runtime changes, browser controls, tests, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, sorting, search, arbitrary discovery, hidden selectors, row traversal, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family currently has bounded, separately proven contracts for selector-free reads, status-only reads, limit-only reads, status+limit API composition, dashboard aggregate-task-list browser consumption of status+limit, API-local limit+offset pagination, and dashboard aggregate-task-list browser consumption of limit+offset. Phase 39 explicitly treated `next_offset` as inert metadata. Phase 40 selects exactly one deferred continuation: manual operator-triggered adjacent-window controls that still use the same canonical limit+offset route and visible selector provenance.

None of the completed contracts authorize automatic next-page traversal, infinite scroll, URL/hash pagination state, local/session storage, status+offset/status+limit+offset composition, sorting controls, free-text search, arbitrary discovery, row-driven traversal, or broad dashboard mode switching.

## Future implementation constraints

A later Story 119.2, if approved by consensus, must remain dashboard-panel-local and browser-runtime-local:

1. Add only visible previous-offset and next-offset controls for the aggregate-task-list panel.
2. Use only the existing canonical route `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` with GET, no body, `credentials: "omit"`, canonical limit-then-offset order, and no extra/repeated query keys.
3. Keep visible current limit and offset controls as the authoritative selector state; manual controls may update/use that visible state but must not introduce hidden, URL, storage, cookie, generated, or row-derived state.
4. Enable next only when the latest response is authoritative, `has_more` is true, and `next_offset` is a valid integer within 0..2147483647; activation performs at most one explicit load using current visible limit and that validated next offset.
5. Enable previous only when current visible offset and limit are valid and offset is greater than zero; activation computes `max(offset - limit, 0)` and performs at most one explicit load using current visible limit.
6. Disable/fail closed for invalid selectors, hidden/missing controls, non-authoritative responses, malformed pagination metadata, overflow, stale state, concurrent/in-flight ambiguity, or edge states.
7. Preserve existing selector-free, status-only, limit-only, status+limit, dashboard status+limit, API-local limit+offset, and dashboard limit+offset contracts.
8. Avoid backend/API changes, status+offset/status+limit+offset composition, sorting, free-text search, row-driven traversal, mutation/control routes, generated live data, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Deferred surfaces

- automatic next-page traversal, previous/next loops, infinite scroll, URL/hash pagination state, local/session storage, cookies, timers, background refresh, workers, automatic retry, and side channels;
- cursor/page token variants beyond the exact limit+offset route;
- sorting controls, free-text task search, arbitrary filters, saved searches, and hidden discovery;
- status+offset, status+limit+offset, sort+offset, search+offset, or any broader selector composition;
- task detail/digest/history/trace/replay/session drill-down from rows;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 40 opening selects exactly one manual browser/dashboard navigation candidate and records no implementation authorization.
2. Future route construction remains GET-only, bodyless, credentials-omitted, canonical limit-then-offset order, and no extra/repeated keys.
3. Future controls are operator-triggered; one activation yields at most one canonical request.
4. Future tests must preserve all completed task-list/dashboard contracts and prove fail-closed selector/metadata/edge-state behavior.
5. Automatic pagination traversal remains deferred unless separately selected.
