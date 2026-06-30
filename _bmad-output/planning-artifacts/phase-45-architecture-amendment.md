# Phase 45 Architecture Amendment — Task List Sort Vocabulary Boundary

Generated: 2026-06-30T19:35:36Z

## Decision

This architecture amendment is the canonical Phase 45 contract source for the future Story 124.2 API-local sort vocabulary. PRD, epics, story, sprint-status, and feature-status entries are derivative summaries and must point back here if they conflict.

Phase 45 opens one API-local task-list sort vocabulary branch:

- **Family:** read-only aggregate task-list API-local finite sort vocabulary expansion.
- **Exact future route:** `GET /v1/tasks?sort={task_sort}`.
- **Approved future vocabulary:** `updated_at_desc_id_asc` and `created_at_desc_id_asc` only.
- **New sort semantics:** `created_at DESC, id ASC`.
- **Selector source:** one raw ASCII `sort` query key only.
- **Route construction:** standalone sort route only; no status, limit, offset, cursor, page, search, or hidden selector composition.
- **API request:** bodyless GET, JSON response, fixed bounded first page, `next_offset: null` for sort-only reads.

Story 124.1 is docs/status-only. It does not authorize runtime/API source implementation, tests, dashboard JS/HTML, browser control changes, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, search, arbitrary discovery, hidden selectors, row traversal, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family has separately proven API/browser contracts for selector-free reads, status-only reads, limit-only reads, status+limit API/browser, limit+offset API/browser/manual-navigation, status+limit+offset API/browser, API-local singleton sort, and browser singleton sort controls.

The current API-local singleton sort route accepts only `sort=updated_at_desc_id_asc`, returns `selected_sort`, and preserves deterministic `updated_at DESC, id ASC` ordering. The `Task` ORM already exposes `created_at`, `updated_at`, and `id`; using `created_at DESC, id ASC` is a low-risk vocabulary expansion because it uses fields already present in the bounded task summary row and does not require joins, search indexes, priority semantics, session state, event payload inspection, or hidden discovery.

Phase 45 deliberately selects API-local vocabulary expansion before browser vocabulary expansion or sort composition so the server contract can remain explicit, byte-spelled, finite, and regression-isolated.

## Future implementation constraints

A later Story 124.2, if approved by consensus, must remain API-route-local:

1. Expand the accepted `TaskSortSelector` domain only to `updated_at_desc_id_asc | created_at_desc_id_asc`.
2. Preserve exact raw ASCII spellings: `sort=updated_at_desc_id_asc` and `sort=created_at_desc_id_asc` only.
3. Keep sort mutually exclusive with status, limit, offset, cursor, page, search, and arbitrary query keys.
4. Reject GET bodies, repeated keys, encoded keys/values, aliases, arbitrary field/direction syntax, JSON/nested params, Unicode lookalikes, empty segments, leading/trailing empty query separators, and extra query keys.
5. Apply deterministic order-by branches: `updated_at DESC, id ASC` for the existing token and `created_at DESC, id ASC` for the new token.
6. Return the same bounded task summary row shape, freshness, authority, provenance, request/trace/correlation evidence, `selected_sort`, returned count, `has_more`, and `next_offset: null` metadata.
7. Keep dashboard/browser singleton sort controls unchanged until a later browser vocabulary planning story; current controls may continue exposing only `updated_at_desc_id_asc`.
8. Preserve all existing task-list API/dashboard/manual-navigation/singleton-sort-control tests.
9. Avoid services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Deferred surfaces

- browser/dashboard sort vocabulary expansion and UI labeling for `created_at_desc_id_asc`;
- sort composition with status, limit, offset, cursor, page, search, or arbitrary selectors;
- title/status/priority/last-event/session/heartbeat sort values, aliases, direction toggles, and field/direction grammar;
- search/free-text discovery, arbitrary query language, hidden discovery, row-derived selectors, row traversal, and adjacent route drill-down;
- automatic traversal, loops, infinite scroll, URL/hash state, local/session storage, cookies, timers, background refresh, workers, automatic retry, and side channels;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 45 opening selects exactly one API-local sort vocabulary expansion candidate and keeps implementation authorization limited to a later Story 124.2 boundary.
2. Future route construction remains GET-only, bodyless, raw-ASCII-only, one `sort` key, and exact `GET /v1/tasks?sort={task_sort}`.
3. Future API tests prove both accepted sort tokens, deterministic ordering, fail-closed malformed/out-of-vocabulary selectors, and no sort composition.
4. Future tests preserve all completed task-list API/dashboard/manual-navigation/singleton-sort-control contracts.
5. Browser vocabulary expansion, sort composition, arbitrary grammar, search/discovery, hidden selectors, auto traversal, and broad dashboard wiring remain deferred unless separately selected.
