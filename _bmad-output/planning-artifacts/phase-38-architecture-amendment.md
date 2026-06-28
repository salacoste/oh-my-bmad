# Phase 38 Architecture Amendment — Task List Pagination Planning Boundary

Generated: 2026-06-28T19:13:22Z

## Decision

Phase 38 may proceed from completed Phase 37 browser status+limit closure into one planning-only API branch:

- **Family:** read-only aggregate task-list pagination / next-window API planning.
- **Exact future candidate surface:** canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- **Query order:** exact `limit` key followed by exact `offset` key.

Story 117.1 is docs/status-only. It does not authorize implementation, route changes, dashboard runtime changes, browser controls, tests, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, sorting, search, arbitrary discovery, hidden selectors, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family currently has bounded, separately proven contracts for selector-free reads, status-only reads, limit-only reads, status+limit API composition, and dashboard aggregate-task-list browser consumption of status+limit. None of those contracts authorize offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary discovery, or browser pagination controls.

## Future implementation constraints

A later Story 117.2, if approved by consensus, must remain route-local and API-local:

1. Add only the exact canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` route shape.
2. Reject GET bodies, reversed query order, extra/repeated query keys, nested parameters, empty selectors, non-ASCII digits, negative offsets, fractional/non-integer offsets, and out-of-range limits.
3. Preserve existing selector-free, status-only, limit-only, status+limit, and dashboard status+limit browser contracts.
4. Return bounded rows only with explicit selected-limit/selected-offset metadata, returned_count, has_more, next_offset/null, freshness, authority, provenance, and request/trace/correlation evidence.
5. Avoid browser controls, automatic next-page traversal, infinite scroll, status+offset/status+limit+offset composition, sorting, free-text search, row-driven traversal, background jobs, retries, storage, mutation/control routes, and generated live data.

## Deferred surfaces

- browser pagination controls, automatic next-page traversal, infinite scroll, URL/hash pagination state, local/session storage, cookies, and background refresh;
- cursor/page token variants beyond the exact future offset candidate;
- sorting controls, free-text task search, arbitrary filters, saved searches, and hidden discovery;
- status+offset, status+limit+offset, sort+offset, search+offset, or any broader selector composition;
- task detail/digest/history/trace/replay/session drill-down from rows;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 38 opening selects exactly one planning candidate and records no implementation authorization.
2. Future route construction is GET-only, bodyless, canonical limit-then-offset order, and no extra/repeated keys.
3. Future tests must preserve all completed task-list/dashboard contracts.
4. Future browser/dashboard pagination remains deferred unless separately selected.
