# Phase 39 PRD Amendment — Task List Pagination Browser Consumption Planning

Generated: 2026-06-29T00:02:59Z

## Scope statement

Phase 39 opens the next narrow dashboard branch after Phase 38 / Epic 117 closed the API-local task-list pagination boundary for exact canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.

Story 118.1 is docs/status-only. It selects and constrains one future browser/dashboard consumption candidate for the already implemented API-local pagination route. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sorting controls, free-text search, arbitrary query language, hidden selectors, automatic traversal, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list pagination browser consumption planning.
- **Selected exact future candidate surface:** dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- **Allowed limit selector domain:** one visible ASCII integer task-list limit from 1 through 50 inclusive.
- **Allowed offset selector domain:** one visible ASCII non-negative integer task-list offset from 0 through 2147483647 inclusive, with raw spelling limited to 1-10 ASCII digits.
- **Selector source:** visible aggregate-task-list panel controls only in a future implementation. No URL hash/query-state persistence, cookies, local/session storage, generated selectors, hidden inputs, row-derived selectors, automatic next-page traversal, background jobs, or inferred selector sources.
- **Canonical query order:** limit first, then offset. Reversed order or additional query keys remain unauthorized.

## Product goals

- Select the smallest browser/runtime consumption step for the completed API-local pagination route without entering automatic traversal, infinite scroll, free-text search, arbitrary discovery, sorting, replay execution, lifecycle mutation, or broad dashboard wiring.
- Preserve completed task-list contracts: selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, limit-only `GET /v1/tasks?limit={task_list_limit}`, status+limit `GET /v1/tasks?status={task_status}&limit={task_list_limit}`, dashboard status+limit browser consumption, and API-local limit+offset pagination.
- Require Architect approval followed by Critic approval before any tests-first browser/runtime implementation story.
- Keep all non-selected surfaces fail-closed.

## Non-goals

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, sorting controls, hidden selectors, automatic next-page traversal, infinite scroll, URL/hash pagination state, local/session storage, cookies, generated selectors, row-derived selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Combining offset with status, status+limit, sort, search, cursor, page, or any additional selector in this planning slice.
- Treating browser/dashboard pagination consumption as already implemented.

## Functional requirements

- **FR322 — Selected family.** Story 118.1 selects only read-only aggregate task-list pagination browser consumption planning.
- **FR323 — Exact future candidate.** Story 118.1 selects dashboard aggregate-task-list panel consumption/rendering of exactly `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` as the only future runtime candidate in this phase.
- **FR324 — Visible selector source.** Future Story 118.2 may use only visible aggregate-task-list panel controls as selector input; no hidden, inferred, generated, row-derived, URL-derived, or storage-derived selector source is authorized.
- **FR325 — Selector domains.** Future limit values are limited to ASCII integers from 1 through 50 inclusive; future offset values are limited to ASCII non-negative integers from 0 through 2147483647 inclusive with 1-10 raw digit spelling.
- **FR326 — Canonical request shape.** Future browser fetches must be GET-only, bodyless, credentials-omitted, canonical limit-then-offset query order, and must not include any extra/repeated query key.
- **FR327 — Response authority.** Future rendering must validate and expose source route, selected limit, selected offset, retrieved_at, freshness_state, display_state, authority_state, provenance, request/trace/correlation id where available, returned_count, has_more, and next_offset before marking any row state authoritative.
- **FR328 — Adjacent surfaces remain deferred.** Automatic next-page traversal, previous/next loops, infinite scroll, cursor/page tokens, sort controls, free-text search, arbitrary filters, status+offset/status+limit+offset composition, hidden discovery, automatic task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.

## Non-functional requirements

- **NFR-S60 — Fail-closed pagination consumption.** Missing/invalid controls, malformed limit/offset values, reversed query construction, rejected/failed responses, stale/ambiguous freshness, unexpected response keys, over-limit rows, mismatched selected-limit/selected-offset metadata, and invalid `has_more`/`next_offset` metadata must render non-authoritative fail-closed copy.
- **NFR-O42 — Provenance continuity.** Pagination browser consumption must preserve the Phase 38 response metadata contract and show enough route/selector/provenance evidence to audit exactly which bounded read produced the displayed rows.
- **NFR-M33 — No automatic traversal.** Future work may not silently follow `next_offset`, loop pages, add infinite scroll, use returned rows as traversal triggers, or replace the status+limit browser mode without explicit separate approval.

## Acceptance criteria for Story 118.1

1. Phase 39 PRD, architecture, and epics artifacts exist and define task-list pagination browser-consumption planning scope.
2. Story 118.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 39 / Epic 118, marks Story 118.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 118.2/118.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim browser/dashboard pagination consumption implementation.
5. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 118.1.

## Follow-on story sequence

- Story 118.1: docs/status-only route-selection / UX-boundary consensus for browser consumption of the exact limit+offset pagination route.
- Story 118.2: tests-first dashboard/browser runtime boundary only if Story 118.1 consensus approves the exact boundary.
- Story 118.3: final validation closure with commit and CI evidence after Story 118.2, if implemented.
