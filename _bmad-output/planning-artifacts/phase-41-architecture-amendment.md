# Phase 41 Architecture Amendment — Task Status + Limit + Offset API-local Boundary

Generated: 2026-06-29T13:41:34Z

## Decision

Phase 41 may proceed from completed Phase 40 into one planning-only API-local selector-composition branch:

- **Family:** read-only aggregate task-list API-local bounded selector composition.
- **Exact future candidate surface:** canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.
- **Query order:** exact `status` key, then exact `limit` key, then exact `offset` key.
- **Selector domains:** existing finite lifecycle status vocabulary; ASCII integer limit 1..50; ASCII integer offset 0..2147483647 with 1-10 raw digits.
- **Boundary:** backend/API route-local only; no dashboard/browser status+limit+offset consumption, no automatic traversal, and no broad discovery.

Story 120.1 is docs/status-only. It does not authorize implementation, route behavior changes, tests, dashboard runtime changes, browser controls, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, sorting, search, arbitrary discovery, hidden selectors, row traversal, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family currently has bounded, separately proven contracts for selector-free reads, status-only reads, limit-only reads, status+limit API composition, dashboard aggregate-task-list browser consumption of status+limit, API-local limit+offset pagination, dashboard aggregate-task-list browser consumption of limit+offset, and manual previous/next controls for the existing limit+offset route.

The existing API intentionally rejects `status+limit+offset` as an unplanned broader composition. Phase 41 selects exactly that one composition as the next API-local candidate because it combines only already-approved finite selector domains while preserving a closed raw-query grammar and one bounded filtered window. It does not authorize status+offset without limit, browser consumption, dashboard controls, automatic traversal, search/sort/discovery, hidden selectors, or row-driven adjacent route selection.

## Future implementation constraints

A later Story 120.2, if approved by consensus, must remain API-route-local:

1. Add only canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.
2. Keep GET body rejection and exact raw query validation before normalized query handling.
3. Accept only exact status-then-limit-then-offset order; reject all reversed/partial/additional/repeated/empty/encoded/nested/malformed selector forms.
4. Reuse the existing status vocabulary, limit parser/domain, offset parser/domain, deterministic order `updated_at DESC, id ASC`, task summary row model, freshness/provenance/correlation metadata, and pagination metadata helper.
5. Apply the status filter before the selected offset and selected limit window; fetch at most `limit + 1` rows; emit `next_offset` only when another approved filtered window is reachable and never beyond 2147483647.
6. Return explicit `selected_status`, `selected_limit`, and `selected_offset` metadata alongside existing bounded response metadata.
7. Preserve existing selector-free, status-only, limit-only, status+limit, limit+offset, dashboard status+limit, dashboard limit+offset, and manual navigation contracts.
8. Avoid dashboard/browser changes, status+offset without limit, offset-only reads, sorting, free-text search, arbitrary discovery, cursor/page tokens, row traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Deferred surfaces

- dashboard/browser consumption/rendering of the new status+limit+offset route;
- status+offset without limit, offset-only, cursor/page token variants, sort+offset, search+offset, or arbitrary selector composition;
- automatic traversal, loops, infinite scroll, URL/hash pagination state, local/session storage, cookies, timers, background refresh, workers, automatic retry, and side channels;
- sorting controls, free-text task search, arbitrary filters, saved searches, hidden discovery, and row-derived selectors;
- task detail/digest/history/trace/replay/session drill-down from rows;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 41 opening selects exactly one API-local route-composition candidate and records no implementation authorization.
2. Future route construction remains GET-only, bodyless, canonical status-then-limit-then-offset order, and no extra/repeated keys.
3. Future runtime tests prove filtered pagination semantics and fail-closed selector grammar.
4. Future tests preserve all completed task-list API/dashboard/manual-navigation contracts.
5. Browser consumption and automatic traversal remain deferred unless separately selected.
