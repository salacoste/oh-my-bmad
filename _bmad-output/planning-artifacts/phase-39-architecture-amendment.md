# Phase 39 Architecture Amendment — Task List Pagination Browser Consumption Boundary

Generated: 2026-06-29T00:02:59Z

## Decision

Phase 39 may proceed from completed Phase 38 API-local pagination closure into one planning-only browser/dashboard branch:

- **Family:** read-only aggregate task-list pagination browser consumption planning.
- **Exact future candidate surface:** dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- **Query order:** exact `limit` key followed by exact `offset` key.
- **Selector source:** visible aggregate-task-list panel controls only in a later implementation story.

Story 118.1 is docs/status-only. It does not authorize implementation, route changes, dashboard runtime changes, browser controls, tests, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, sorting, search, arbitrary discovery, hidden selectors, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family currently has bounded, separately proven contracts for selector-free reads, status-only reads, limit-only reads, status+limit API composition, dashboard aggregate-task-list browser consumption of status+limit, and API-local limit+offset pagination. None of those contracts authorize browser pagination controls, automatic next-page traversal, infinite scroll, URL/hash pagination state, status+offset/status+limit+offset composition, sorting controls, free-text search, arbitrary discovery, or broad dashboard mode switching.

## Future implementation constraints

A later Story 118.2, if approved by consensus, must remain dashboard-panel-local and browser-runtime-local:

1. Add only browser consumption/rendering for exact canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
2. Use visible aggregate-task-list controls only for `limit` and `offset`; reject missing, hidden, generated, row-derived, URL-derived, storage-derived, or inferred selector sources.
3. Construct a single GET request with no body, `credentials: "omit"`, canonical limit-then-offset order, and no extra/repeated query keys.
4. Validate server response metadata before authoritative rendering: route, selected_limit, selected_offset, limit, returned_count, has_more, next_offset/null, retrieved_at, freshness_state, display_state, authority_state, provenance, request_id, trace_id, correlation_id, and bounded item rows.
5. Treat `next_offset` as display/provenance metadata only unless a later story separately approves explicit next/previous behavior; no automatic traversal, loops, infinite scroll, retries, timers, workers, storage, or side channels.
6. Preserve existing selector-free, status-only, limit-only, status+limit, dashboard status+limit, and API-local limit+offset contracts.
7. Avoid backend/API changes, status+offset/status+limit+offset composition, sorting, free-text search, row-driven traversal, mutation/control routes, generated live data, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

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

1. Phase 39 opening selects exactly one browser/dashboard planning candidate and records no implementation authorization.
2. Future route construction is GET-only, bodyless, credentials-omitted, canonical limit-then-offset order, and no extra/repeated keys.
3. Future tests must preserve all completed task-list/dashboard contracts and prove fail-closed selector/metadata behavior.
4. Future automatic pagination traversal remains deferred unless separately selected.
