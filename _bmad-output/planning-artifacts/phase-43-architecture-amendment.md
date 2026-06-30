# Phase 43 Architecture Amendment — Task List Sort API-local Boundary

Generated: 2026-06-29T20:58:06Z

## Decision

Phase 43 may proceed from completed Phase 42 into one planning-only API-local task-list sort branch:

- **Family:** read-only aggregate task-list API-local finite sort selection.
- **Exact future candidate surface:** canonical `GET /v1/tasks?sort={task_sort}`.
- **Query shape:** exactly one raw ASCII query segment, `sort=updated_at_desc_id_asc`.
- **Sort vocabulary:** singleton approved value `updated_at_desc_id_asc`.
- **Sort semantics:** `Task.updated_at.desc()`, then `Task.id.asc()` deterministic tie-breaker, matching the current implicit task-list order.
- **API boundary:** route-local bodyless GET; selected sort metadata in response; no dashboard/browser consumption; no composition with status/limit/offset; no automatic traversal.

Story 122.1 is docs/status-only. It does not authorize implementation, route behavior changes, tests, dashboard runtime changes, browser controls, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, search, arbitrary discovery, hidden selectors, row traversal, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family currently has bounded, separately proven contracts for selector-free reads, status-only reads, limit-only reads, status+limit API composition, dashboard aggregate-task-list browser consumption of status+limit, API-local limit+offset pagination, dashboard aggregate-task-list browser consumption of limit+offset, manual previous/next controls for limit+offset, API-local status+limit+offset, and dashboard/browser status+limit+offset.

The current API orders task-list summaries by `updated_at` descending and `id` ascending. Existing tests intentionally reject `sort=updated_at` and sort composition with approved selectors. Phase 43 selects a singleton explicit sort token as the next safest API-local boundary because it exposes no new field-discovery grammar and no browser/UI behavior while preserving every closed selector/composition route.

## Future implementation constraints

A later Story 122.2, if approved by consensus, must remain API-route-local:

1. Add only canonical `GET /v1/tasks?sort={task_sort}` with the single approved value `updated_at_desc_id_asc`.
2. Keep GET bodyless and reject request bodies, repeated keys, encoded keys/values, Unicode lookalikes, empty segments, aliases, field/direction subkeys, JSON values, and extra query keys.
3. Preserve selector-free, status-only, limit-only, status+limit, limit+offset, status+limit+offset, and all dashboard/manual-navigation contracts unchanged.
4. Return explicit `selected_sort: "updated_at_desc_id_asc"` metadata while keeping bounded task summary rows, route, freshness, authority, provenance, request/trace/correlation evidence, fixed limit, returned count, `has_more`, and `next_offset: null` consistent with the selector-free first-page contract.
5. Use deterministic ordering `updated_at DESC, id ASC`; do not introduce arbitrary field names, direction toggles, stable cursor/page tokens, or row-driven adjacent-route hints.
6. Keep sort independent from status/limit/offset composition until separately planned; queries such as status+sort, limit+sort, offset+sort, and status+limit+offset+sort remain rejected.
7. Avoid browser/dashboard controls, search/discovery, hidden selectors, automatic traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Deferred surfaces

- browser/dashboard sort controls or rendering changes;
- additional sort values, multi-column user-selected sorting, direction toggles, arbitrary field/direction grammar, nested sort parameters, and sort aliases;
- status+sort, limit+sort, offset+sort, status+limit+sort, limit+offset+sort, status+limit+offset+sort, or any broader selector composition;
- search/free-text discovery, arbitrary query language, hidden discovery, row-derived selectors, row traversal, and adjacent route drill-down;
- automatic traversal, loops, infinite scroll, URL/hash state, local/session storage, cookies, timers, background refresh, workers, automatic retry, and side channels;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 43 opening selects exactly one API-local sort candidate and records no implementation authorization.
2. Future route construction remains GET-only, bodyless, one raw ASCII `sort=updated_at_desc_id_asc` segment, and finite-vocabulary-only.
3. Future runtime tests prove fail-closed sort selector parsing, deterministic ordering, response metadata, and no selector composition.
4. Future tests preserve all completed task-list API/dashboard/manual-navigation contracts.
5. Browser wiring, search/discovery, hidden selectors, auto traversal, and broader sort vocabulary remain deferred unless separately selected.
