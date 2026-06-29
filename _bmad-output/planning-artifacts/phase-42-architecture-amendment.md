# Phase 42 Architecture Amendment — Task Status + Limit + Offset Browser Boundary

Generated: 2026-06-29T17:07:16Z

## Decision

Phase 42 may proceed from completed Phase 41 into one planning-only dashboard/browser selector-composition branch:

- **Family:** read-only aggregate task-list dashboard/browser bounded selector composition.
- **Exact future candidate surface:** canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.
- **Query order:** exact `status` key, then exact `limit` key, then exact `offset` key.
- **Selector provenance:** visible aggregate-task-list status, limit, and offset controls only.
- **Selector domains:** existing finite lifecycle status vocabulary; ASCII integer limit 1..50; ASCII integer offset 0..2147483647 with 1-10 raw digits.
- **Browser boundary:** aggregate-task-list panel only; bodyless GET with `credentials: "omit"`; strict response validation; fail-closed selector/response states; no automatic traversal.

Story 121.1 is docs/status-only. It does not authorize implementation, route behavior changes, tests, dashboard runtime changes, browser controls, services, MCP changes, dependencies, lockfiles, CI/deployment changes, generated data, sorting, search, arbitrary discovery, hidden selectors, row traversal, mutation/control behavior, or production operations.

## Brownfield context

The task-list read family currently has bounded, separately proven contracts for selector-free reads, status-only reads, limit-only reads, status+limit API composition, dashboard aggregate-task-list browser consumption of status+limit, API-local limit+offset pagination, dashboard aggregate-task-list browser consumption of limit+offset, manual previous/next controls for limit+offset, and API-local status+limit+offset.

The current aggregate-task-list browser runtime intentionally remains on the visible limit+offset route and has no active status control for the status+limit+offset composition. Phase 42 selects exactly one browser/dashboard consumption candidate because the API-local route is already closed and the selector provenance can stay visible, finite, and fail-closed. It does not authorize status+offset without limit, automatic traversal, search/sort/discovery, hidden selectors, URL/storage state, or row-driven adjacent route selection.

## Future implementation constraints

A later Story 121.2, if approved by consensus, must remain dashboard aggregate-task-list local:

1. Add only browser consumption/rendering for canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.
2. Compose the route only from visible status, limit, and offset controls in canonical status-then-limit-then-offset order.
3. Keep GET bodyless with `credentials: "omit"` and no side-channel request state from URL/hash/storage/cookies/timers/workers/retry/polling.
4. Reuse existing status vocabulary, limit parser/domain, offset parser/domain, task summary row model, freshness/provenance/correlation metadata, and manual previous/next in-flight guard where applicable.
5. Validate response metadata strictly: route, selected_status, selected_limit, selected_offset, returned_count, has_more, next_offset/null, freshness, display state, authority, provenance, request/trace/correlation evidence, and bounded rows.
6. Fail closed for missing/hidden/malformed controls, selector mismatch, route mismatch, selected metadata mismatch, unauthorized/degraded/non-authoritative responses, malformed JSON, row status mismatch, malformed rows, and network failures.
7. Preserve one explicit user action per read; previous/next may update the visible offset and load only from visible status+limit+offset controls, not from hidden selectors or row data.
8. Avoid backend/API changes, status+offset without limit, offset-only reads, sorting, free-text search, arbitrary discovery, cursor/page tokens, automatic traversal, row traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Deferred surfaces

- status+offset without limit, offset-only, cursor/page token variants, sort+offset, search+offset, or arbitrary selector composition;
- automatic traversal, loops, infinite scroll, URL/hash pagination state, local/session storage, cookies, timers, background refresh, workers, automatic retry, and side channels;
- sorting controls, free-text task search, arbitrary filters, saved searches, hidden discovery, and row-derived selectors;
- task detail/digest/history/trace/replay/session drill-down from rows;
- replay execution target selection and lifecycle apply/prune/rollback;
- services/MCP/dependencies/CI/deployment modifications unless separately planned;
- production credentials and production operations.

## Architecture acceptance criteria

1. Phase 42 opening selects exactly one dashboard/browser route-composition candidate and records no implementation authorization.
2. Future route construction remains GET-only, bodyless, `credentials: "omit"`, canonical status-then-limit-then-offset order, and visible-control-only.
3. Future runtime tests prove fail-closed selector provenance, strict metadata validation, and no automatic traversal.
4. Future tests preserve all completed task-list API/dashboard/manual-navigation contracts.
5. Search/sort/hidden selectors/row traversal/URL-storage state remain deferred unless separately selected.
