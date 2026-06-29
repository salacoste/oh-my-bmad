# Phase 40 PRD Amendment — Manual Task-List Pagination Navigation Planning

Generated: 2026-06-29T01:43:57Z

## Scope statement

Phase 40 opens the next narrow dashboard branch after Phase 39 / Epic 118 closed dashboard/browser consumption of canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` from visible aggregate-task-list controls.

Story 119.1 is docs/status-only. It selects and constrains one future manual dashboard pagination-navigation candidate for the already implemented browser-consumed limit+offset route. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, automatic traversal, infinite scroll, sorting controls, free-text search, arbitrary query language, hidden selectors, row-derived traversal, URL/hash state, local/session storage, cookies, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list manual pagination navigation planning.
- **Selected exact future candidate surface:** visible manual previous-offset and next-offset controls inside the existing aggregate-task-list panel, using only the already approved browser-consumed canonical route `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- **Limit selector source:** the existing visible aggregate-task-list limit control only; ASCII integer 1 through 50 inclusive.
- **Current offset selector source:** the existing visible aggregate-task-list offset control only; ASCII non-negative integer from 0 through 2147483647 inclusive, with raw spelling limited to 1-10 ASCII digits and no leading-zero ambiguity except literal `0`.
- **Manual next-offset behavior candidate:** after an authoritative response with `has_more: true` and numeric `next_offset`, a visible operator-initiated next control may copy/use exactly that validated `next_offset` with the current visible limit and then perform one explicit load for the canonical limit+offset route.
- **Manual previous-offset behavior candidate:** a visible operator-initiated previous control may compute `max(current_offset - current_limit, 0)` from currently visible validated decimal controls and then perform one explicit load for the canonical limit+offset route.
- **Selector/state source restriction:** no URL hash/query-state persistence, cookies, local/session storage, generated selectors, hidden inputs, row-derived selectors, background jobs, timers, workers, or inferred selector sources.
- **Canonical query order:** limit first, then offset. Reversed order or additional query keys remain unauthorized.

## Product goals

- Select the smallest manual navigation UX step after the completed limit+offset browser consumption boundary.
- Let an operator explicitly move to the adjacent bounded task-list window without typing offset values manually, while preserving visible selector provenance and one-click-at-a-time reads.
- Preserve completed task-list contracts: selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, limit-only `GET /v1/tasks?limit={task_list_limit}`, status+limit `GET /v1/tasks?status={task_status}&limit={task_list_limit}`, dashboard status+limit browser consumption, API-local limit+offset pagination, and dashboard limit+offset browser consumption.
- Require Architect approval followed by Critic approval before any tests-first browser/runtime implementation story.
- Keep all non-selected surfaces fail-closed.

## Non-goals

- Automatic traversal, automatic next-page loops, infinite scroll, background prefetch, timer/worker retry, URL/hash pagination state, local/session storage, cookies, generated selectors, hidden selectors, row-derived selectors, automatic row drill-down, free-text search, arbitrary query language, sorting controls, cursor/page-token variants, status+offset composition, status+limit+offset composition, backend/API route changes, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating returned `next_offset` as an automatic traversal instruction.
- Replacing the current visible limit/offset/load contract with opaque pagination state.
- Adding row-click, task-detail, digest, history, trace, replay, lifecycle, or session traversal.

## Functional requirements

- **FR329 — Selected family.** Story 119.1 selects only read-only aggregate task-list manual pagination navigation planning.
- **FR330 — Exact future candidate.** Story 119.1 selects visible manual previous-offset and next-offset controls inside the existing aggregate-task-list panel as the only future runtime candidate in this phase.
- **FR331 — Existing route only.** Future manual controls may use only the already approved canonical browser route `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`; no backend/API route or query-composition expansion is authorized.
- **FR332 — Visible selector provenance.** Future manual controls must derive all reads from visible validated panel state: the current limit control, current offset control, and authoritative response metadata for `next_offset` only. No hidden, inferred, generated, row-derived, URL-derived, or storage-derived selector source is authorized.
- **FR333 — Manual next semantics.** Future next control is enabled only after authoritative metadata proves `has_more: true` and numeric `next_offset` within 0..2147483647; activation performs at most one explicit GET for that offset and current visible limit.
- **FR334 — Manual previous semantics.** Future previous control is enabled only when current visible validated offset is greater than zero; activation computes exactly `max(current_offset - current_limit, 0)` and performs at most one explicit GET for that offset and current visible limit.
- **FR335 — Fail-closed controls.** Missing/invalid/hidden controls, stale/non-authoritative responses, malformed `has_more`/`next_offset`, invalid current limit/offset, or disabled edge states must render controls inert/non-authoritative rather than guessing selectors.
- **FR336 — Adjacent surfaces remain deferred.** Automatic traversal, infinite scroll, cursor/page tokens, sort controls, free-text search, arbitrary filters, status+offset/status+limit+offset composition, hidden discovery, automatic task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.

## Non-functional requirements

- **NFR-S61 — One explicit action equals at most one read.** Future previous/next activations must be user-initiated and bounded to one canonical request; no loops, prefetch, background polling, or chained reads.
- **NFR-O43 — Provenance continuity.** Manual navigation must preserve route, selected limit, selected offset, returned_count, has_more, next_offset/null, freshness, authority, provenance, and request/trace/correlation evidence for each displayed window.
- **NFR-M34 — Existing contract preservation.** Future work must keep selector-free, status-only, limit-only, status+limit, dashboard status+limit, API-local limit+offset, and current dashboard limit+offset contracts independently green.

## Acceptance criteria for Story 119.1

1. Phase 40 PRD, architecture, and epics artifacts exist and define manual task-list pagination navigation planning scope.
2. Story 119.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 40 / Epic 119, marks Story 119.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 119.2/119.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim manual pagination navigation implementation.
5. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 119.1.

## Follow-on story sequence

- Story 119.1: docs/status-only manual pagination navigation planning and consensus.
- Story 119.2: tests-first dashboard/browser runtime boundary only if Story 119.1 consensus approves the exact boundary.
- Story 119.3: final validation closure with commit and CI evidence after Story 119.2, if implemented.
