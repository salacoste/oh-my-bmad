# Phase 44 Architecture Amendment — Task List Sort Browser Controls Boundary

Generated: 2026-06-30T02:20:23Z

## Decision

Phase 44 proceeded from completed Phase 43 into one browser/dashboard task-list sort branch and is now closed locally by Story 123.3:

- **Family:** read-only aggregate-task-list browser/dashboard singleton sort consumption.
- **Exact future browser candidate:** visible controls that issue `GET /v1/tasks?sort=updated_at_desc_id_asc`.
- **Selector source:** visible aggregate-task-list control state only.
- **Sort vocabulary:** singleton approved value `updated_at_desc_id_asc`.
- **Route construction:** standalone sort route only; no status, limit, offset, cursor, page, search, or hidden selector composition.
- **Browser request:** bodyless GET, `Accept: application/json`, `credentials: "omit"`, one explicit visible action per read.

Story 123.1 is docs/status-only. It does not authorize dashboard JS/HTML implementation, backend/API changes, tests, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, search, arbitrary discovery, hidden selectors, row traversal, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family has separately proven API/browser contracts for selector-free reads, status-only reads, limit-only reads, status+limit API/browser, limit+offset API/browser/manual-navigation, status+limit+offset API/browser, and API-local singleton sort.

The existing API-local singleton sort route from Story 122.2 accepts only raw ASCII `sort=updated_at_desc_id_asc`, returns `selected_sort`, and preserves deterministic `updated_at DESC, id ASC` ordering. The current dashboard aggregate-task-list runtime reads visible status, limit, and offset controls and constructs exact status+limit+offset routes. It intentionally has no browser sort controls yet.

Phase 44 selected browser/dashboard consumption as the next smallest increment because the backend route already existed, the vocabulary was singleton, and the UI could be constrained to a standalone exact route without opening broader sort grammar or status/limit/offset composition. Story 123.2 implemented that exact boundary locally; Story 123.3 records the local closure evidence.

## Future implementation constraints

A later Story 123.2, if approved by consensus, must remain dashboard/browser-local:

1. Add only visible aggregate-task-list sort affordance(s) for `updated_at_desc_id_asc` and one explicit sorted-read action.
2. Construct exactly `/v1/tasks?sort=updated_at_desc_id_asc`; do not append status, limit, offset, cursor, page, search, or arbitrary query keys.
3. Keep GET bodyless with `credentials: "omit"` and JSON accept headers.
4. Strictly validate response metadata, including `selected_sort: "updated_at_desc_id_asc"`, route identity, freshness, authority, provenance, request/trace/correlation evidence, bounded task rows, returned_count, `has_more`, and `next_offset: null`.
5. Render non-authoritative states for missing/malformed/hidden/mutated selector state, malformed JSON, missing sort metadata, non-JSON content, backend unavailable, unauthorized responses, and stale selector edits.
6. Keep existing status/limit/offset/manual previous-next behavior green and independent.
7. Render sorted-read results in a separate singleton-sort subtree and leave existing manual previous/next offset controls and status/limit/offset state unchanged; sort+offset composition is not authorized.
8. Avoid browser storage, URL/hash persistence, hidden controls, generated selectors, timers, polling, retries, workers, background prefetch, row traversal, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Deferred surfaces

- broader sort vocabulary, direction toggles, field/direction grammar, aliases, and multi-column sort controls;
- status+sort, limit+sort, offset+sort, status+limit+sort, limit+offset+sort, status+limit+offset+sort, and any broader selector composition;
- search/free-text discovery, arbitrary query language, hidden discovery, row-derived selectors, row traversal, and adjacent route drill-down;
- automatic traversal, loops, infinite scroll, URL/hash state, local/session storage, cookies, timers, background refresh, workers, automatic retry, and side channels;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 44 opening selected exactly one browser/dashboard singleton sort-control candidate; implementation authorization remained limited to the later Story 123.2 boundary, which is now locally verified and closed by Story 123.3.
2. Future route construction remains GET-only, bodyless, visible-control-only, and exact `/v1/tasks?sort=updated_at_desc_id_asc`.
3. Future dashboard tests prove fail-closed visible selector parsing, strict response metadata, no selector composition, and no automatic traversal.
4. Future tests preserve all completed task-list API/dashboard/manual-navigation contracts.
5. Broader vocabulary, sort composition, search/discovery, hidden selectors, auto traversal, and broad dashboard wiring remain deferred unless separately selected.
